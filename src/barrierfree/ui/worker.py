"""분석을 QThread에서 실행하고, 진행·결과·취소·자막 영역 확인을 시그널로 UI에 전달한다.

- 워커는 파이프라인을 직접 만들지 않고 `runner(cancel, confirm, progress)`를 받는다.
- 자막 영역 확인: 분석 스레드가 `awaiting_region`으로 UI에 판단을 요청하고, UI가
  `provide_decision`으로 답할 때까지 멈춘다. 기다리는 중에 취소하면 곧바로 풀린다.
- 오류가 나도 앱 전체를 종료하지 않는다. 예상하지 못한 예외는 ERR-99로 알린다.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from barrierfree.analysis.subtitle_region import RegionEstimate
from barrierfree.cancel import CancelledError, CancelToken
from barrierfree.pipeline import (
    PipelineError,
    PipelineResult,
    RegionDecision,
    StageEvent,
)

Runner = Callable[
    [CancelToken, Callable[[RegionEstimate], RegionDecision], Callable[[StageEvent], None]],
    PipelineResult,
]

_POLL_S = 0.1
logger = logging.getLogger(__name__)


class AnalysisWorker(QThread):
    progress = pyqtSignal(object)  # StageEvent
    awaiting_region = pyqtSignal(object)  # RegionEstimate
    succeeded = pyqtSignal(object)  # PipelineResult
    failed = pyqtSignal(object)  # PipelineError
    cancelled = pyqtSignal()

    def __init__(self, runner: Runner, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._runner = runner
        self._cancel = CancelToken()
        self._answered = threading.Event()
        self._decision: RegionDecision | None = None

    # ----- UI 스레드에서 호출 -----

    def request_cancel(self) -> None:
        self._cancel.cancel()

    def provide_decision(self, decision: RegionDecision) -> None:
        self._decision = decision
        self._answered.set()

    # ----- 분석 스레드 -----

    def run(self) -> None:
        try:
            result = self._runner(self._cancel, self._confirm_region, self.progress.emit)
        except CancelledError:
            self.cancelled.emit()
        except PipelineError as error:
            self.failed.emit(error)
        except Exception:  # noqa: BLE001 - 어떤 예외도 앱을 종료시키지 않는다
            logger.exception("분석 중 예상하지 못한 오류")  # 원인 추적용(사용자에게는 코드만 보임)
            self.failed.emit(PipelineError("ERR-99", "분석 중 알 수 없는 오류가 발생했습니다."))
        else:
            self.succeeded.emit(result)

    def _confirm_region(self, estimate: RegionEstimate) -> RegionDecision:
        self._decision = None
        self._answered.clear()
        self.awaiting_region.emit(estimate)
        while not self._answered.wait(_POLL_S):
            self._cancel.raise_if_cancelled()
        self._cancel.raise_if_cancelled()
        assert self._decision is not None
        return self._decision

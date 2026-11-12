"""메인 윈도우: 등록 → 분석 진행 → 검수 3단계를 한 창 안에서 전환한다.

(docs/project/ui-spec.md 3장·4장).

- 분석 진행 중에는 중복 분석과 설정 변경을 막는다.
- 취소하면 등록 화면으로 돌아간다. 오류가 나면 진행 화면에 머물며 재시도/돌아가기를 제공한다.
- 분석 중 창을 닫으려 하면 확인한다. 검수 상태는 바뀔 때마다 저장되므로 새 분석 때는
  남은 미검토 항목만 알려 주고 확인한다.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PyQt6.QtCore import QEvent, QTimer
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from barrierfree.analysis.subtitle_region import RegionEstimate
from barrierfree.cancel import CancelToken
from barrierfree.config import AnalysisConfig
from barrierfree.engines.device import DeviceInfo, ModelRecommendation
from barrierfree.media.ffmpeg import VideoInfo
from barrierfree.media.subtitles import LoadedSubtitle, load_subtitle_file
from barrierfree.pipeline import (
    PipelineError,
    PipelineResult,
    RegionDecision,
    StageEvent,
)
from barrierfree.review_state import ReviewState
from barrierfree.settings import AppSettings, effective_model, load_settings, save_settings
from barrierfree.storage.repository import Repository
from barrierfree.ui.dialogs import Dialogs
from barrierfree.ui.progress_page import ProgressPage
from barrierfree.ui.register_page import RegisterPage
from barrierfree.ui.review_page import PlayerLike, ReviewPage
from barrierfree.ui.review_timer import ActiveTimeTracker
from barrierfree.ui.worker import AnalysisWorker

WINDOW_TITLE = "배리어프리 영상 AI 점검 도구"
FLUSH_INTERVAL_MS = 30_000  # 검수 시간을 이 주기로 저장해, 비정상 종료에도 대부분 남게 한다
PLAYBACK_TICK_MS = 1_000  # 영상을 보고 있는 시간도 검수 활동으로 세기 위한 확인 주기
CLOSE_WAIT_MS = 10_000  # 종료 시 분석 스레드가 멈추기를 기다리는 최대 시간
STAGE_REGISTER, STAGE_ANALYZE, STAGE_REVIEW = "등록", "분석", "검수"


class RunnablePipeline(Protocol):
    def run(
        self, video: Path, subtitle: Path | None, cancel: CancelToken
    ) -> PipelineResult: ...


@dataclass
class AppServices:
    """창이 필요로 하는 바깥 세계. 실제 앱은 `__main__`이, 테스트는 가짜를 채워 넣는다."""

    repo: Repository
    data_dir: Path
    settings_path: Path
    device: DeviceInfo
    recommendation: ModelRecommendation
    pipeline_factory: Callable[
        [AppSettings, Callable[[RegionEstimate], RegionDecision], Callable[[StageEvent], None]],
        RunnablePipeline,
    ]
    dialogs: Dialogs
    player_factory: Callable[[], PlayerLike]
    region_prompt: Callable[[RegionEstimate], RegionDecision]
    edit_settings: Callable[[AppSettings], AppSettings | None]
    video_validator: Callable[[Path], VideoInfo] | None = None
    subtitle_loader: Callable[[Path], LoadedSubtitle] = load_subtitle_file
    clock: Callable[[], float] = time.monotonic  # 검수 시간 측정용(테스트에서 교체)


class MainWindow(QMainWindow):
    def __init__(self, services: AppServices) -> None:
        super().__init__()
        self._services = services
        self._settings = load_settings(services.settings_path)
        self._worker: AnalysisWorker | None = None
        self._last_inputs: tuple[Path, Path | None] | None = None
        self._review_state: ReviewState | None = None
        self._timer: ActiveTimeTracker | None = None
        # 저장에 실패한 검수 시간(ms)을 세션별로 보관한다. 성공할 때까지 계속 다시 보낸다.
        self._pending_review_ms: dict[str, int] = {}
        self._window_active = True
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(FLUSH_INTERVAL_MS)
        self._flush_timer.timeout.connect(self.flush_review_time)
        self._playback_timer = QTimer(self)
        self._playback_timer.setInterval(PLAYBACK_TICK_MS)
        self._playback_timer.timeout.connect(self.tick_playback)
        self.close_wait_ms = CLOSE_WAIT_MS
        self.setWindowTitle(WINDOW_TITLE)

        self.product_label = QLabel(WINDOW_TITLE)
        self.work_label = QLabel("")
        self.stage_label = QLabel("")
        self.header_settings_button = QPushButton("설정")

        self.register_page = RegisterPage(
            services.dialogs, services.video_validator, services.subtitle_loader
        )
        self.progress_page = ProgressPage()
        self.review_page = ReviewPage(services.dialogs, services.player_factory)
        self.stack = QStackedWidget()
        for page in (self.register_page, self.progress_page, self.review_page):
            self.stack.addWidget(page)

        header = QHBoxLayout()
        for widget in (self.product_label, self.work_label, self.stage_label):
            header.addWidget(widget)
        header.addStretch(1)
        header.addWidget(self.header_settings_button)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addLayout(header)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.header_settings_button.clicked.connect(self._open_settings)
        self.register_page.settings_requested.connect(self._open_settings)
        self.register_page.start_requested.connect(self._start)
        self.progress_page.cancel_requested.connect(self._cancel)
        self.progress_page.retry_requested.connect(self._retry)
        self.progress_page.back_requested.connect(self._back_to_register)
        self.review_page.new_analysis_requested.connect(self._new_analysis)
        self.review_page.activity.connect(self._on_review_activity)

        self._show_register()
        self._refresh_settings_summary()

    # ----- 화면 전환 -----

    @property
    def is_analyzing(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _set_stage(self, stage: str, work: str = "") -> None:
        self.stage_label.setText(f"현재 단계: {stage}")
        self.work_label.setText(f"작업: {work}" if work else "")

    def _show_register(self) -> None:
        self.review_page.pause_playback()  # 등록 화면에서 영상 소리가 계속 나면 안 된다
        self._end_review_timing()  # 검수 화면을 떠나면 측정을 멈추고 저장한다
        self.stack.setCurrentWidget(self.register_page)
        self.register_page.set_busy(False)
        self.header_settings_button.setEnabled(True)
        video = self.register_page.video_path
        self._set_stage(STAGE_REGISTER, video.name if video else "")

    def _back_to_register(self) -> None:
        self._show_register()

    def _refresh_settings_summary(self) -> None:
        s = self._services
        model = effective_model(self._settings, s.recommendation.model)
        device = "GPU" if s.recommendation.device == "cuda" else "CPU"
        self.register_page.set_settings_summary(f"모델: {model} · 실행 장치: {device}")

    # ----- 설정 -----

    def _open_settings(self) -> None:
        if self.is_analyzing:
            return  # 분석 중에는 설정을 바꿀 수 없다
        chosen = self._services.edit_settings(self._settings)
        if chosen is None:
            return
        # 저장을 먼저 시도하고, 성공한 뒤에만 실행 설정과 화면을 바꾼다
        # (저장된 파일과 화면의 설정이 어긋나지 않게).
        try:
            save_settings(self._services.settings_path, chosen)
        except OSError:
            self._services.dialogs.warn(
                "설정 저장 실패",
                "설정을 저장하지 못했습니다. 저장 위치의 권한과 여유 공간을 확인해 주세요. "
                "기존 설정을 그대로 사용합니다.",
            )
            return
        self._settings = chosen
        self._refresh_settings_summary()

    # ----- 분석 -----

    def _start(self, video: Path, subtitle: Path | None) -> None:
        if self.is_analyzing:
            return
        self._last_inputs = (video, subtitle)
        settings = self._settings
        services = self._services

        def runner(
            cancel: CancelToken,
            confirm: Callable[[RegionEstimate], RegionDecision],
            progress: Callable[[StageEvent], None],
        ) -> PipelineResult:
            pipeline = services.pipeline_factory(settings, confirm, progress)
            return pipeline.run(video, subtitle, cancel)

        worker = AnalysisWorker(runner, self)
        worker.progress.connect(self.progress_page.on_event)
        worker.awaiting_region.connect(self._ask_region)
        worker.succeeded.connect(self._on_succeeded)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        self._worker = worker

        self.register_page.set_busy(True)
        self.header_settings_button.setEnabled(False)
        self.progress_page.start(video.name)
        self.stack.setCurrentWidget(self.progress_page)
        self._set_stage(STAGE_ANALYZE, video.name)
        worker.start()

    def _ask_region(self, estimate: RegionEstimate) -> None:
        if self._worker is not None:
            self._worker.provide_decision(self._services.region_prompt(estimate))

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()

    def _retry(self) -> None:
        if self._last_inputs is not None and not self.is_analyzing:
            self._start(*self._last_inputs)

    def _finish_worker(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.wait(5000)
            worker.deleteLater()

    def _on_succeeded(self, result: PipelineResult) -> None:
        self._finish_worker()
        state = ReviewState(self._services.repo, result.session_id, extra_notices=result.notices)
        self._review_state = state
        video = self._last_inputs[0] if self._last_inputs else None
        self.review_page.load(state, video)
        self.stack.setCurrentWidget(self.review_page)
        self._begin_review_timing(state)
        self.register_page.set_busy(False)
        self.header_settings_button.setEnabled(True)
        self._set_stage(STAGE_REVIEW, video.name if video else "")

    def _on_failed(self, error: PipelineError) -> None:
        self._finish_worker()
        self.progress_page.show_error(error)  # 진행 화면에 머물며 재시도/돌아가기를 제공한다
        self.header_settings_button.setEnabled(True)

    def _on_cancelled(self) -> None:
        self._finish_worker()
        self._show_register()
        self._services.dialogs.info("분석 취소", "분석이 취소되었습니다.")

    # ----- 검수 화면에서 -----

    def _new_analysis(self) -> None:
        state = self._review_state
        if state is not None:
            pending = state.summary().unreviewed
            if pending and not self._services.dialogs.confirm(
                "새 영상 분석",
                f"아직 검수하지 않은 항목이 {pending}건 있습니다. "
                "검수한 내용은 이미 저장되어 있습니다. 새 영상을 분석할까요?",
            ):
                return
        self.register_page.clear_video()
        self._show_register()  # 여기서 검수 시간을 저장하므로, 상태는 그 뒤에 비운다
        self._review_state = None

    # ----- 검수 시간 측정 (결정 Q11) -----

    def _begin_review_timing(self, state: ReviewState) -> None:
        self._end_review_timing()
        self._timer = ActiveTimeTracker(
            AnalysisConfig().idle_threshold_s, self._services.clock
        )
        if self._window_active:
            self._timer.start()
        self._flush_timer.start()
        self._playback_timer.start()

    def _end_review_timing(self) -> None:
        """측정을 멈추고 확정된 시간을 저장한다. 끝내 저장하지 못하면 사용자에게 알린다."""
        self._flush_timer.stop()
        self._playback_timer.stop()
        was_reviewing = self._timer is not None
        if self._timer is not None:
            self._timer.stop()
        self.flush_review_time()
        self._timer = None
        if was_reviewing and self._pending_review_ms:
            self._services.dialogs.warn(
                "검수 시간 저장 실패",
                "검수 시간을 저장하지 못했습니다. 검수한 항목은 그대로 저장되어 있습니다. "
                "프로그램이 켜져 있는 동안에는 다시 저장을 시도합니다.",
            )

    def _on_review_activity(self) -> None:
        if self._timer is not None:
            self._timer.activity()

    def tick_playback(self) -> None:
        """영상을 재생 중이면 보고 있는 것이므로 활동으로 센다(조작이 없어도 유휴가 아니다)."""
        if self._timer is not None and self.review_page.is_playing():
            self._timer.activity()

    def set_window_active(self, active: bool) -> None:
        """창이 활성/비활성이 될 때 호출한다. 비활성인 동안의 시간은 세지 않는다."""
        self._window_active = active
        if self._timer is None:
            return
        if active:
            self._timer.start()
        else:
            self._timer.stop()
            self.flush_review_time()

    def flush_review_time(self) -> None:
        """확정된 검수 시간을 저장한다.

        저장에 실패해도 검수는 막지 않는다. 못 저장한 시간은 세션별로 보관했다가, 새 입력이 없어도
        다음 호출에서(검수 화면을 떠난 뒤에도) 다시 저장을 시도한다.
        """
        if self._timer is not None and self._review_state is not None:
            delta = self._timer.drain_ms()
            if delta > 0:
                session_id = self._review_state.session_id
                self._pending_review_ms[session_id] = (
                    self._pending_review_ms.get(session_id, 0) + delta
                )
        for session_id, ms in list(self._pending_review_ms.items()):
            try:
                self._services.repo.add_review_time(session_id, ms)
            except sqlite3.Error:
                continue
            del self._pending_review_ms[session_id]

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt 이름
        if event.type() == QEvent.Type.ActivationChange:
            self.set_window_active(self.isActiveWindow())
        super().changeEvent(event)

    # ----- 창 닫기 -----

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt 이름
        if self.is_analyzing:
            if not self._services.dialogs.confirm(
                "종료", "분석이 진행 중입니다. 취소하고 종료할까요?"
            ):
                event.ignore()
                return
            if self._worker is not None:
                self._worker.request_cancel()
                if not self._worker.wait(self.close_wait_ms):
                    # 모델 다운로드나 긴 추론 호출은 취소를 바로 못 받을 수 있다. 실행 중인 스레드가
                    # 있는 채로 창과 저장소를 닫으면 충돌하므로 종료를 보류하고 다시 시도하게 한다.
                    self._services.dialogs.warn(
                        "종료 보류",
                        "분석을 정리하는 중입니다. 잠시 후 다시 종료해 주세요.",
                    )
                    event.ignore()
                    return
        self._end_review_timing()  # 종료 직전에 검수 시간을 저장한다
        event.accept()

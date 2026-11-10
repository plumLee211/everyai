"""ui/worker.py 테스트: 분석을 QThread에서 실행하고 진행·결과·취소·영역 확인을 시그널로 전달한다."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pytestqt.qtbot import QtBot

from barrierfree.analysis.subtitle_region import RegionEstimate, region_rect
from barrierfree.cancel import CancelledError, CancelToken
from barrierfree.models import SubtitleSource
from barrierfree.pipeline import (
    PipelineError,
    PipelineResult,
    RegionAction,
    RegionDecision,
    Stage,
    StageEvent,
)
from barrierfree.ui.worker import AnalysisWorker

Confirm = Callable[[RegionEstimate], RegionDecision]
Progress = Callable[[StageEvent], None]

RESULT = PipelineResult("session-1", 3, SubtitleSource.FILE, ["안내"])
ESTIMATE = RegionEstimate(True, 0.8, region_rect(0.25), 10, 12.0, 0.9, 0.7)


def test_success_emits_result_and_finished(qtbot: QtBot) -> None:
    worker = AnalysisWorker(lambda cancel, confirm, progress: RESULT)
    with qtbot.waitSignal(worker.succeeded, timeout=5000) as blocker:
        worker.start()
    assert blocker.args == [RESULT]
    worker.wait(5000)
    assert worker.isFinished()


def test_progress_events_are_forwarded_in_order(qtbot: QtBot) -> None:
    events = [
        StageEvent(Stage.STT, "RUNNING", None, "음성"),
        StageEvent(Stage.OCR, "RUNNING", 0.5, "화면"),
    ]

    def runner(cancel: CancelToken, confirm: Any, progress: Progress) -> PipelineResult:
        for event in events:
            progress(event)
        return RESULT

    worker = AnalysisWorker(runner)
    received: list[StageEvent] = []
    worker.progress.connect(received.append)
    with qtbot.waitSignal(worker.succeeded, timeout=5000):
        worker.start()
    worker.wait(5000)
    assert received == events


def test_pipeline_error_is_reported_with_its_code(qtbot: QtBot) -> None:
    error = PipelineError("ERR-07", "음성 인식에 실패했습니다.", stage=Stage.STT)

    def runner(cancel: CancelToken, confirm: Any, progress: Any) -> PipelineResult:
        raise error

    worker = AnalysisWorker(runner)
    with qtbot.waitSignal(worker.failed, timeout=5000) as blocker:
        worker.start()
    assert blocker.args[0] is error
    worker.wait(5000)


def test_unexpected_exception_becomes_err_99_instead_of_crashing(qtbot: QtBot) -> None:
    def runner(cancel: CancelToken, confirm: Any, progress: Any) -> PipelineResult:
        raise ZeroDivisionError("bug")

    worker = AnalysisWorker(runner)
    with qtbot.waitSignal(worker.failed, timeout=5000) as blocker:
        worker.start()
    assert blocker.args[0].code == "ERR-99"
    worker.wait(5000)


def test_cancelled_error_emits_cancelled_not_failed(qtbot: QtBot) -> None:
    def runner(cancel: CancelToken, confirm: Any, progress: Any) -> PipelineResult:
        raise CancelledError("취소")

    worker = AnalysisWorker(runner)
    failures: list[object] = []
    worker.failed.connect(failures.append)
    with qtbot.waitSignal(worker.cancelled, timeout=5000):
        worker.start()
    worker.wait(5000)
    assert failures == []


def test_request_cancel_reaches_the_runner_token(qtbot: QtBot) -> None:
    entered = []

    def runner(cancel: CancelToken, confirm: Any, progress: Any) -> PipelineResult:
        entered.append(True)
        while not cancel.wait(0.05):
            pass
        cancel.raise_if_cancelled()
        return RESULT

    worker = AnalysisWorker(runner)
    with qtbot.waitSignal(worker.cancelled, timeout=5000):
        worker.start()
        qtbot.waitUntil(lambda: bool(entered), timeout=5000)
        worker.request_cancel()
    worker.wait(5000)


def test_region_confirmation_blocks_until_the_ui_answers(qtbot: QtBot) -> None:
    answers: list[RegionDecision] = []

    def runner(cancel: CancelToken, confirm: Confirm, progress: Any) -> PipelineResult:
        answers.append(confirm(ESTIMATE))
        return RESULT

    worker = AnalysisWorker(runner)
    decision = RegionDecision(RegionAction.CONFIRMED, region_rect(0.25))

    def on_request(estimate: RegionEstimate) -> None:
        assert estimate is ESTIMATE
        assert answers == []  # 아직 답하지 않았으므로 분석은 멈춰 있다
        worker.provide_decision(decision)

    worker.awaiting_region.connect(on_request)
    with qtbot.waitSignal(worker.succeeded, timeout=5000):
        worker.start()
    worker.wait(5000)
    assert answers == [decision]


def test_cancel_while_waiting_for_the_region_answer_unblocks_the_analysis(qtbot: QtBot) -> None:
    def runner(cancel: CancelToken, confirm: Confirm, progress: Any) -> PipelineResult:
        confirm(ESTIMATE)  # 답이 오지 않는다
        return RESULT

    worker = AnalysisWorker(runner)
    with qtbot.waitSignal(worker.cancelled, timeout=5000):
        worker.awaiting_region.connect(lambda _e: worker.request_cancel())
        worker.start()
    worker.wait(5000)


def test_each_run_starts_with_a_fresh_cancel_token(qtbot: QtBot) -> None:
    tokens: list[CancelToken] = []

    def runner(cancel: CancelToken, confirm: Any, progress: Any) -> PipelineResult:
        tokens.append(cancel)
        return RESULT

    first = AnalysisWorker(runner)
    with qtbot.waitSignal(first.succeeded, timeout=5000):
        first.start()
    first.wait(5000)
    first.request_cancel()
    second = AnalysisWorker(runner)
    with qtbot.waitSignal(second.succeeded, timeout=5000):
        second.start()
    second.wait(5000)
    assert tokens[0] is not tokens[1]
    assert tokens[0].cancelled and not tokens[1].cancelled


def test_provide_decision_without_a_pending_request_is_harmless(qtbot: QtBot) -> None:
    worker = AnalysisWorker(lambda cancel, confirm, progress: RESULT)
    worker.provide_decision(RegionDecision(RegionAction.NONE))  # 대기 중이 아니어도 예외가 없다

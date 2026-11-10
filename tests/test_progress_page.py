"""ui/progress_page.py 테스트: 분석 진행 화면(SCR-02, 세부구현.md 6장)."""

from __future__ import annotations

from pytestqt.qtbot import QtBot

from barrierfree.pipeline import PipelineError, Stage, StageEvent
from barrierfree.ui.progress_page import ProgressPage

CORE_STAGES = [
    Stage.MEDIA_PREPARE,
    Stage.STT,
    Stage.OCR,
    Stage.SUBTITLE_CHECK,
    Stage.SCREEN_CHECK,
    Stage.RESULT_SAVE,
]


def make_page(qtbot: QtBot) -> ProgressPage:
    page = ProgressPage()
    qtbot.addWidget(page)
    page.start("안내 영상.mp4")
    return page


def event(stage: Stage, state: str, fraction: float | None = None, message: str = "") -> StageEvent:
    return StageEvent(stage, state, fraction, message)


def test_start_shows_the_video_and_all_core_stages_waiting(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    assert "안내 영상.mp4" in page.video_label.text()
    assert page.visible_stages() == CORE_STAGES
    assert all(page.stage_state(s) == "대기" for s in CORE_STAGES)
    assert page.cancel_button.isEnabled()
    assert page.error_panel.isHidden()


def test_model_preparation_stage_appears_only_when_needed(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    assert Stage.MODEL_PREPARE not in page.visible_stages()
    page.on_event(event(Stage.MODEL_PREPARE, "RUNNING", None, "모델을 내려받는 중"))
    assert page.visible_stages()[0] is Stage.MODEL_PREPARE  # 맨 앞에 끼워 넣는다
    assert page.stage_state(Stage.MODEL_PREPARE) == "진행 중"


def test_stage_states_follow_events(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    page.on_event(event(Stage.MEDIA_PREPARE, "RUNNING"))
    assert page.stage_state(Stage.MEDIA_PREPARE) == "진행 중"
    page.on_event(event(Stage.MEDIA_PREPARE, "DONE", 1.0))
    page.on_event(event(Stage.STT, "RUNNING", None, "음성을 인식하는 중입니다."))
    assert page.stage_state(Stage.MEDIA_PREPARE) == "완료"
    assert page.stage_state(Stage.STT) == "진행 중"
    assert page.stage_state(Stage.OCR) == "대기"
    assert "음성을 인식하는 중입니다." in page.status_label.text()


def test_state_is_written_in_words_not_only_in_symbols_or_color(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    page.on_event(event(Stage.STT, "RUNNING"))
    row = dict(page.stage_rows())[Stage.STT]
    assert "음성 인식" in row and "진행 중" in row


def test_measurable_progress_shows_a_real_percentage(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    page.on_event(event(Stage.OCR, "RUNNING", 0.4))
    assert (page.progress_bar.minimum(), page.progress_bar.maximum()) == (0, 100)
    assert page.progress_bar.value() == 40


def test_unmeasurable_progress_is_indeterminate_not_a_made_up_percentage(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    page.on_event(event(Stage.STT, "RUNNING", None))
    assert (page.progress_bar.minimum(), page.progress_bar.maximum()) == (0, 0)


def test_fraction_is_clamped_to_valid_range(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    page.on_event(event(Stage.OCR, "RUNNING", 1.7))
    assert page.progress_bar.value() == 100
    page.on_event(event(Stage.OCR, "RUNNING", -0.5))
    assert page.progress_bar.value() == 0


def test_cancel_disables_the_button_and_shows_cancelling(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    with qtbot.waitSignal(page.cancel_requested):
        page.cancel_button.click()
    assert not page.cancel_button.isEnabled()  # 중복 클릭 방지
    assert "취소 중" in page.status_label.text()


def test_cancel_signal_is_emitted_only_once(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    count: list[int] = []
    page.cancel_requested.connect(lambda: count.append(1))
    page.cancel_button.click()
    page.cancel_button.click()
    assert count == [1]


def test_error_panel_shows_stage_reason_code_and_actions(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    page.on_event(event(Stage.STT, "RUNNING"))
    page.show_error(PipelineError("ERR-07", "음성 인식에 실패했습니다.", stage=Stage.STT))
    assert not page.error_panel.isHidden()
    text = page.error_label.text()
    assert "음성 인식" in text and "ERR-07" in text and "음성 인식에 실패했습니다." in text
    assert page.stage_state(Stage.STT) == "실패"
    assert page.cancel_button.isHidden()
    assert page.retry_button.isEnabled() and page.back_button.isEnabled()


def test_retry_and_back_emit_their_signals(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    page.show_error(PipelineError("ERR-08", "화면 글자 인식에 실패했습니다."))
    with qtbot.waitSignal(page.retry_requested):
        page.retry_button.click()
    with qtbot.waitSignal(page.back_requested):
        page.back_button.click()


def test_error_without_a_stage_still_renders(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    page.show_error(PipelineError("ERR-99", "알 수 없는 오류"))
    assert "ERR-99" in page.error_label.text()


def test_restarting_resets_everything(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    page.on_event(event(Stage.MODEL_PREPARE, "RUNNING"))
    page.on_event(event(Stage.STT, "DONE", 1.0))
    page.show_error(PipelineError("ERR-07", "실패", stage=Stage.STT))
    page.start("다른 영상.mp4")
    assert page.visible_stages() == CORE_STAGES
    assert all(page.stage_state(s) == "대기" for s in CORE_STAGES)
    assert page.error_panel.isHidden() and not page.cancel_button.isHidden()
    assert page.cancel_button.isEnabled()

"""ui/main_window.py 테스트: 등록 → 분석 진행 → 검수 흐름 (docs/project/ui-spec.md 3장·4장)."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from PyQt6.QtGui import QCloseEvent
from pytestqt.qtbot import QtBot

from barrierfree.analysis.subtitle_region import RegionEstimate, region_rect
from barrierfree.cancel import CancelToken
from barrierfree.config import AnalysisConfig
from barrierfree.engines.device import DeviceInfo, ModelRecommendation
from barrierfree.media.ffmpeg import VideoInfo
from barrierfree.media.subtitles import LoadedSubtitle
from barrierfree.models import (
    Issue,
    IssueKind,
    IssueSource,
    ReviewStatus,
    SubtitleCue,
    SubtitleSource,
    Verdict,
)
from barrierfree.pipeline import (
    PipelineError,
    PipelineResult,
    RegionAction,
    RegionDecision,
    Stage,
    StageEvent,
)
from barrierfree.settings import AppSettings, load_settings
from barrierfree.storage.repository import Repository
from barrierfree.ui.main_window import AppServices, MainWindow
from barrierfree.ui.register_page import RegisterPage
from tests.fakes import FakeDialogs, FakePlayer

DEVICE = DeviceInfo("Intel i5", 8, 16.0, False)
RECOMMENDED = ModelRecommendation("small", "cpu", "int8")
ESTIMATE = RegionEstimate(True, 0.8, region_rect(0.25), 10, 12.0, 0.9, 0.7, ("자막",))


def validate_video(path: Path) -> VideoInfo:
    return VideoInfo(path, 125.0, 640, 360, 10.0, True, True)


def load_subtitle(path: Path) -> LoadedSubtitle:
    return LoadedSubtitle([SubtitleCue(0, 1, "안녕")], "utf-8", "SRT")


class FakePipeline:
    """repo에 세션과 지적을 실제로 저장하는 가짜 파이프라인. 동작은 mode로 바꾼다."""

    def __init__(self, repo: Repository, plan: dict[str, Any], on_progress: Callable[..., None],
                 confirm: Callable[[RegionEstimate], RegionDecision]) -> None:
        self.repo, self.plan, self.on_progress, self.confirm = repo, plan, on_progress, confirm

    def run(self, video: Path, subtitle: Path | None, cancel: CancelToken) -> PipelineResult:
        plan = self.plan
        plan.setdefault("runs", []).append((video, subtitle))
        self.on_progress(StageEvent(Stage.STT, "RUNNING", None, "음성을 인식하는 중입니다."))
        if plan.get("ask_region"):
            plan["decision"] = self.confirm(ESTIMATE)
        if plan.get("block"):
            while not cancel.wait(0.05):
                pass
            cancel.raise_if_cancelled()
        if plan.get("stubborn"):  # 취소 신호를 무시하고 풀어 줄 때까지 버틴다
            while not plan["stubborn"].wait(0.02):
                pass
        failures = plan.get("failures", 0)
        if failures:
            plan["failures"] = failures - 1
            raise PipelineError("ERR-07", "음성 인식에 실패했습니다.", stage=Stage.STT)
        session_id = self.repo.create_session(
            video=VideoInfo(video, 125.0, 640, 360, 10.0, True, True),
            video_sha256="a" * 64,
            subtitle_source=SubtitleSource.FILE,
            subtitle_path=None,
            config=AnalysisConfig(),
            stt_model_name="small",
            ocr_engine_name="easyocr",
            compute_device="cpu",
        )
        issues = [
            Issue(source=IssueSource.M1, kind=IssueKind.VALUE_MISMATCH, verdict=Verdict.ERROR,
                  start_s=10, end_s=12, reason="날짜가 다릅니다"),
            Issue(source=IssueSource.M2, kind=IssueKind.SCREEN_ONLY_INFO, verdict=Verdict.ERROR,
                  start_s=30, end_s=33, reason="전화번호 누락"),
        ]
        self.repo.complete_session(session_id, issues)
        self.on_progress(StageEvent(Stage.RESULT_SAVE, "DONE", 1.0, "분석을 마쳤습니다."))
        return PipelineResult(session_id, len(issues), SubtitleSource.FILE, ["안내 문구"])


class Env:
    def __init__(self, qtbot: QtBot, repo: Repository, tmp_path: Path, **plan: Any) -> None:
        self.repo = repo
        self.dialogs = FakeDialogs()
        self.plan: dict[str, Any] = dict(plan)
        self.settings_path = tmp_path / "settings.json"
        if self.plan.get("settings_blocked"):  # 부모가 파일이라 저장할 수 없다
            (tmp_path / "blocked").write_text("파일", encoding="utf-8")
            self.settings_path = tmp_path / "blocked" / "settings.json"
        self.settings_edit: AppSettings | None = None
        self.region_prompts: list[RegionEstimate] = []
        self.players: list[FakePlayer] = []

        def pipeline_factory(settings: AppSettings, confirm: Any, progress: Any) -> FakePipeline:
            self.plan["settings_seen"] = settings
            return FakePipeline(repo, self.plan, progress, confirm)

        def region_prompt(estimate: RegionEstimate) -> RegionDecision:
            self.region_prompts.append(estimate)
            return RegionDecision(RegionAction.CONFIRMED, estimate.rect)

        def player_factory() -> FakePlayer:
            player = FakePlayer()
            self.players.append(player)
            return player

        services = AppServices(
            repo=repo,
            data_dir=tmp_path / "data",
            settings_path=self.settings_path,
            device=DEVICE,
            recommendation=RECOMMENDED,
            pipeline_factory=pipeline_factory,
            dialogs=self.dialogs,
            player_factory=player_factory,
            region_prompt=region_prompt,
            edit_settings=lambda current: self.settings_edit,
            video_validator=validate_video,
            subtitle_loader=load_subtitle,
            clock=self.plan.get("clock", time.monotonic),
        )
        self.window = MainWindow(services)
        qtbot.addWidget(self.window)
        self.qtbot = qtbot

    def start_analysis(self, name: str = "안내 영상.mp4") -> None:
        self.window.register_page.set_video(Path("/v") / name)
        self.window.register_page.start_button.click()

    def wait_for(self, page: str) -> None:
        widget = getattr(self.window, page)
        self.qtbot.waitUntil(lambda: self.window.stack.currentWidget() is widget, timeout=8000)


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[Repository]:
    r = Repository(tmp_path / "app.db")
    yield r
    r.close_all()


def summary_text(page: RegisterPage) -> str:
    return page.device_value_label.text() + " " + page.model_value_label.text()


def make_env(qtbot: QtBot, repo: Repository, tmp_path: Path, **plan: Any) -> Env:
    return Env(qtbot, repo, tmp_path, **plan)


# ---------- 시작 상태 ----------


def test_starts_on_the_register_page_with_a_settings_summary(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env = make_env(qtbot, repo, tmp_path)
    w = env.window
    assert w.stack.currentWidget() is w.register_page
    assert "등록" in w.stage_label.text()
    assert "small" in summary_text(w.register_page)
    assert "CPU" in summary_text(w.register_page)
    assert w.header_settings_button.isEnabled()


# ---------- 정상 흐름 ----------


def test_full_flow_register_progress_review(qtbot: QtBot, repo: Repository, tmp_path: Path) -> None:
    env = make_env(qtbot, repo, tmp_path)
    env.start_analysis("안내 영상.mp4")
    assert env.window.stack.currentWidget() is env.window.progress_page
    env.wait_for("review_page")
    w = env.window
    assert "검수" in w.stage_label.text()
    assert "안내 영상.mp4" in w.work_label.text()
    assert w.review_page.table_view.model().rowCount() == 2
    assert "안내 문구" in w.review_page.notices_label.text()  # 파이프라인 안내가 전달된다


def test_progress_events_reach_the_progress_page(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env = make_env(qtbot, repo, tmp_path, block=True)
    env.start_analysis()
    qtbot.waitUntil(
        lambda: env.window.progress_page.stage_state(Stage.STT) == "진행 중", timeout=8000
    )
    env.window.progress_page.cancel_button.click()
    env.wait_for("register_page")


def test_header_shows_the_work_name_and_stage_while_analyzing(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env = make_env(qtbot, repo, tmp_path, block=True)
    env.start_analysis("긴 안내.mp4")
    assert "긴 안내.mp4" in env.window.work_label.text()
    assert "분석" in env.window.stage_label.text()
    env.window.progress_page.cancel_button.click()
    env.wait_for("register_page")


def test_settings_are_blocked_while_analysis_runs(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env = make_env(qtbot, repo, tmp_path, block=True)
    env.start_analysis()
    assert not env.window.header_settings_button.isEnabled()
    assert not env.window.register_page.start_button.isEnabled()  # 중복 분석 불가
    env.window.progress_page.cancel_button.click()
    env.wait_for("register_page")
    assert env.window.header_settings_button.isEnabled()


def test_start_request_while_busy_is_ignored(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env = make_env(qtbot, repo, tmp_path, block=True)
    env.start_analysis()
    env.window.register_page.start_requested.emit(Path("/v/다른.mp4"), None)
    env.window.progress_page.cancel_button.click()
    env.wait_for("register_page")
    assert len(env.plan["runs"]) == 1


# ---------- 자막 영역 확인 ----------


def test_region_confirmation_is_asked_and_answered(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env = make_env(qtbot, repo, tmp_path, ask_region=True)
    env.start_analysis()
    env.wait_for("review_page")
    assert env.region_prompts == [ESTIMATE]
    assert env.plan["decision"].action is RegionAction.CONFIRMED


# ---------- 실패·재시도·취소 ----------


def test_failure_stays_on_the_progress_page_and_retry_runs_again(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env = make_env(qtbot, repo, tmp_path, failures=1)
    env.start_analysis()
    qtbot.waitUntil(lambda: not env.window.progress_page.error_panel.isHidden(), timeout=8000)
    assert env.window.stack.currentWidget() is env.window.progress_page
    assert "ERR-07" in env.window.progress_page.error_label.text()
    env.window.progress_page.retry_button.click()
    env.wait_for("review_page")
    assert len(env.plan["runs"]) == 2


def test_back_after_failure_returns_to_register_with_inputs_kept(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env = make_env(qtbot, repo, tmp_path, failures=1)
    env.start_analysis("실패 영상.mp4")
    qtbot.waitUntil(lambda: not env.window.progress_page.error_panel.isHidden(), timeout=8000)
    env.window.progress_page.back_button.click()
    assert env.window.stack.currentWidget() is env.window.register_page
    assert env.window.register_page.video_path == Path("/v/실패 영상.mp4")
    assert env.window.register_page.start_button.isEnabled()


def test_cancel_returns_to_the_register_page_and_says_so(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env = make_env(qtbot, repo, tmp_path, block=True)
    env.start_analysis()
    page = env.window.progress_page
    qtbot.waitUntil(lambda: page.stage_state(Stage.STT) == "진행 중", timeout=8000)
    env.window.progress_page.cancel_button.click()
    env.wait_for("register_page")
    assert env.dialogs.infos and "취소" in env.dialogs.infos[-1][1]
    assert env.window.register_page.start_button.isEnabled()


# ---------- 설정 ----------


def test_applying_settings_saves_them_and_updates_the_summary(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env = make_env(qtbot, repo, tmp_path)
    env.settings_edit = AppSettings("medium", None, 0.3)
    env.window.header_settings_button.click()
    assert load_settings(env.settings_path) == AppSettings("medium", None, 0.3)
    assert "medium" in summary_text(env.window.register_page)


def test_cancelling_the_settings_dialog_changes_nothing(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env = make_env(qtbot, repo, tmp_path)
    env.settings_edit = None  # 취소
    env.window.header_settings_button.click()
    assert not env.settings_path.exists()


def test_new_settings_apply_to_the_next_analysis_only(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env = make_env(qtbot, repo, tmp_path)
    env.settings_edit = AppSettings("base", None, 0.2)
    env.window.register_page.settings_button.click()
    env.start_analysis()
    env.wait_for("review_page")
    assert env.plan["settings_seen"] == AppSettings("base", None, 0.2)


# ---------- 새 분석 ----------


def finish_one_analysis(env: Env) -> None:
    env.start_analysis()
    env.wait_for("review_page")


def test_new_analysis_with_unreviewed_items_asks_first(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env = make_env(qtbot, repo, tmp_path)
    finish_one_analysis(env)
    env.dialogs.confirm_answer = False
    env.window.review_page.new_analysis_button.click()
    assert env.dialogs.confirms and "2건" in env.dialogs.confirms[-1][1]
    assert env.window.stack.currentWidget() is env.window.review_page
    env.dialogs.confirm_answer = True
    env.window.review_page.new_analysis_button.click()
    assert env.window.stack.currentWidget() is env.window.register_page
    assert env.window.register_page.video_path is None


def test_new_analysis_after_reviewing_everything_does_not_ask(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env = make_env(qtbot, repo, tmp_path)
    finish_one_analysis(env)
    model = env.window.review_page.table_view.model()
    ids = [model.issue_at(row).id for row in range(model.rowCount())]  # 먼저 id를 모은다
    assert len(ids) == 2
    for issue_id in ids:
        model.set_status(issue_id, ReviewStatus.CONFIRMED)
    env.window.review_page.new_analysis_button.click()
    assert env.dialogs.confirms == []
    assert env.window.stack.currentWidget() is env.window.register_page


# ---------- 검수 시간 측정 (결정 Q11) ----------


class TickClock:
    def __init__(self) -> None:
        self.now = 5000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def review_ms(env: Env, window_session: str) -> int:
    return env.repo.get_session(window_session).review_duration_ms


def timed_env(qtbot: QtBot, repo: Repository, tmp_path: Path) -> tuple[Env, TickClock]:
    clock = TickClock()
    env = make_env(qtbot, repo, tmp_path, clock=clock)
    return env, clock


def current_session_id(env: Env) -> str:
    return str(env.repo.connection().execute("SELECT id FROM analysis_session").fetchone()[0])


def test_review_time_counts_interactions_and_is_saved_when_leaving(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env, clock = timed_env(qtbot, repo, tmp_path)
    finish_one_analysis(env)
    page = env.window.review_page
    clock.advance(30)
    page.table_view.selectRow(0)  # 입력 -> 30초가 확정된다
    clock.advance(20)
    page.confirm_button.click()  # 입력 -> 20초가 확정된다
    env.dialogs.confirm_answer = True
    page.new_analysis_button.click()  # 화면을 떠나면 저장한다
    assert review_ms(env, current_session_id(env)) == 50_000


def test_idle_stretch_while_reviewing_is_not_counted(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env, clock = timed_env(qtbot, repo, tmp_path)
    finish_one_analysis(env)
    page = env.window.review_page
    clock.advance(40)
    page.table_view.selectRow(0)
    clock.advance(600)  # 자리를 비웠다
    page.table_view.selectRow(1)
    env.dialogs.confirm_answer = True
    page.new_analysis_button.click()
    assert review_ms(env, current_session_id(env)) == 40_000


def test_inactive_window_pauses_the_measurement(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env, clock = timed_env(qtbot, repo, tmp_path)
    finish_one_analysis(env)
    page = env.window.review_page
    clock.advance(10)
    env.window.set_window_active(False)  # 다른 창으로 이동
    clock.advance(500)
    env.window.set_window_active(True)
    clock.advance(15)
    page.table_view.selectRow(0)
    env.dialogs.confirm_answer = True
    page.new_analysis_button.click()
    assert review_ms(env, current_session_id(env)) == 25_000


def test_review_time_is_saved_when_closing_the_window(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env, clock = timed_env(qtbot, repo, tmp_path)
    finish_one_analysis(env)
    clock.advance(33)
    env.window.review_page.table_view.selectRow(0)
    event = QCloseEvent()
    env.window.closeEvent(event)
    assert review_ms(env, current_session_id(env)) == 33_000


def test_periodic_flush_saves_committed_time_without_waiting_for_exit(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env, clock = timed_env(qtbot, repo, tmp_path)
    finish_one_analysis(env)
    clock.advance(25)
    env.window.review_page.table_view.selectRow(0)
    env.window.flush_review_time()
    assert review_ms(env, current_session_id(env)) == 25_000
    env.window.flush_review_time()  # 두 번 불러도 중복 저장하지 않는다
    assert review_ms(env, current_session_id(env)) == 25_000


def test_no_time_is_counted_outside_the_review_page(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env, clock = timed_env(qtbot, repo, tmp_path)
    clock.advance(1000)
    env.window.flush_review_time()  # 검수 화면이 아니면 아무 일도 없다
    assert env.repo.connection().execute("SELECT COUNT(*) FROM analysis_session").fetchone()[0] == 0


def test_watching_the_video_counts_as_review_time(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    # 130초 동안 영상을 보기만 해도(다른 입력 없이) 유휴로 버려지면 안 된다.
    env, clock = timed_env(qtbot, repo, tmp_path)
    finish_one_analysis(env)
    env.window.review_page.table_view.selectRow(0)
    env.players[-1].playing = True
    for _ in range(130):
        clock.advance(1)
        env.window.tick_playback()
    env.dialogs.confirm_answer = True
    env.window.review_page.new_analysis_button.click()
    assert review_ms(env, current_session_id(env)) >= 130_000


def test_not_playing_and_no_input_is_still_idle(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env, clock = timed_env(qtbot, repo, tmp_path)
    finish_one_analysis(env)
    env.window.review_page.table_view.selectRow(0)
    for _ in range(130):
        clock.advance(1)
        env.window.tick_playback()  # 재생 중이 아니면 활동이 아니다
    env.dialogs.confirm_answer = True
    env.window.review_page.new_analysis_button.click()
    assert review_ms(env, current_session_id(env)) == 0


def test_player_controls_count_as_activity(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env, clock = timed_env(qtbot, repo, tmp_path)
    finish_one_analysis(env)
    clock.advance(40)
    env.players[-1].activity.emit()  # 재생 버튼이나 탐색 슬라이더 조작
    env.dialogs.confirm_answer = True
    env.window.review_page.new_analysis_button.click()
    assert review_ms(env, current_session_id(env)) == 40_000


# ---------- 검수 시간 저장 실패 (Codex 2차 리뷰 반영) ----------


def fail_review_time_writes(env: Env, monkeypatch: pytest.MonkeyPatch) -> dict[str, bool]:
    """add_review_time을 실패시키는 스위치. state["fail"]을 False로 바꾸면 다시 저장된다."""
    import sqlite3

    state = {"fail": True}
    original = env.repo.add_review_time

    def flaky(session_id: str, delta_ms: int) -> None:
        if state["fail"]:
            raise sqlite3.OperationalError("database is locked")
        original(session_id, delta_ms)

    monkeypatch.setattr(env.repo, "add_review_time", flaky)
    return state


def test_review_time_that_failed_to_save_is_kept_and_saved_by_the_next_flush(
    qtbot: QtBot, repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, clock = timed_env(qtbot, repo, tmp_path)
    finish_one_analysis(env)
    session_id = current_session_id(env)
    switch = fail_review_time_writes(env, monkeypatch)
    clock.advance(30)
    env.window.review_page.table_view.selectRow(0)
    env.window.flush_review_time()  # 실패한다
    assert review_ms(env, session_id) == 0
    switch["fail"] = False
    env.window.flush_review_time()  # 새 입력이 없어도 이전에 못 저장한 30초를 다시 보낸다
    assert review_ms(env, session_id) == 30_000
    env.window.flush_review_time()
    assert review_ms(env, session_id) == 30_000  # 중복 저장하지 않는다


def test_time_lost_while_leaving_review_is_saved_later_and_the_user_is_told(
    qtbot: QtBot, repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, clock = timed_env(qtbot, repo, tmp_path)
    finish_one_analysis(env)
    session_id = current_session_id(env)
    switch = fail_review_time_writes(env, monkeypatch)
    clock.advance(50)
    env.window.review_page.table_view.selectRow(0)
    env.dialogs.confirm_answer = True
    env.window.review_page.new_analysis_button.click()  # 떠나는 순간 저장에 실패한다
    assert review_ms(env, session_id) == 0
    assert any("검수 시간" in message for _title, message in env.dialogs.warnings)
    switch["fail"] = False
    env.window.flush_review_time()  # 검수 화면이 아니어도, 남은 시간은 다시 저장을 시도한다
    assert review_ms(env, session_id) == 50_000


def test_unsaved_time_of_a_previous_session_survives_starting_another_review(
    qtbot: QtBot, repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, clock = timed_env(qtbot, repo, tmp_path)
    finish_one_analysis(env)
    first = current_session_id(env)
    switch = fail_review_time_writes(env, monkeypatch)
    clock.advance(20)
    env.window.review_page.table_view.selectRow(0)
    env.dialogs.confirm_answer = True
    env.window.review_page.new_analysis_button.click()
    switch["fail"] = False
    finish_one_analysis(env)  # 두 번째 분석과 검수가 시작된다
    env.window.flush_review_time()
    assert review_ms(env, first) == 20_000


def test_closing_with_unsaved_time_warns_but_does_not_block_closing(
    qtbot: QtBot, repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, clock = timed_env(qtbot, repo, tmp_path)
    finish_one_analysis(env)
    fail_review_time_writes(env, monkeypatch)
    clock.advance(33)
    env.window.review_page.table_view.selectRow(0)
    event = QCloseEvent()
    env.window.closeEvent(event)
    assert event.isAccepted()
    assert any("검수 시간" in message for _title, message in env.dialogs.warnings)


def test_leaving_the_review_page_pauses_the_video(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env, _clock = timed_env(qtbot, repo, tmp_path)
    finish_one_analysis(env)
    player = env.players[-1]
    player.playing = True
    env.dialogs.confirm_answer = True
    env.window.review_page.new_analysis_button.click()
    assert player.playing is False  # 등록 화면에서 소리가 계속 나면 안 된다


# ---------- 설정 저장 실패 (Codex 리뷰 반영) ----------


def test_failed_settings_save_keeps_the_old_settings_and_tells_the_user(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env = make_env(qtbot, repo, tmp_path, settings_blocked=True)
    env.settings_edit = AppSettings("medium", None, 0.3)
    env.window.header_settings_button.click()
    assert env.dialogs.warnings and "저장" in env.dialogs.warnings[0][1]
    assert "small" in summary_text(env.window.register_page)
    assert "medium" not in summary_text(env.window.register_page)


def test_settings_apply_to_memory_only_after_the_save_succeeds(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env = make_env(qtbot, repo, tmp_path, settings_blocked=True)
    env.settings_edit = AppSettings("base", None, 0.2)
    env.window.header_settings_button.click()
    env.start_analysis()
    env.wait_for("review_page")
    assert env.plan["settings_seen"] == AppSettings()  # 저장 실패한 설정은 분석에 쓰이지 않는다


# ---------- 종료 보류 (Codex 리뷰 반영) ----------


def test_close_is_deferred_while_the_worker_will_not_stop(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    import threading

    release = threading.Event()
    env = make_env(qtbot, repo, tmp_path, stubborn=release)  # 취소 신호를 무시하는 작업
    env.start_analysis()
    qtbot.waitUntil(lambda: "runs" in env.plan, timeout=8000)
    env.window.close_wait_ms = 150
    env.dialogs.confirm_answer = True
    event = QCloseEvent()
    env.window.closeEvent(event)
    assert not event.isAccepted()  # 스레드가 살아 있는 채로 닫지 않는다
    assert env.dialogs.warnings and "종료" in env.dialogs.warnings[0][0]

    release.set()
    qtbot.waitUntil(lambda: not env.window.is_analyzing, timeout=8000)
    again = QCloseEvent()
    env.window.closeEvent(again)
    assert again.isAccepted()


# ---------- 창 닫기 ----------


def test_closing_during_analysis_asks_and_can_be_declined(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env = make_env(qtbot, repo, tmp_path, block=True)
    env.start_analysis()
    env.dialogs.confirm_answer = False
    event = QCloseEvent()
    env.window.closeEvent(event)
    assert not event.isAccepted()
    env.window.progress_page.cancel_button.click()
    env.wait_for("register_page")


def test_closing_during_analysis_cancels_it_when_confirmed(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env = make_env(qtbot, repo, tmp_path, block=True)
    env.start_analysis()
    env.dialogs.confirm_answer = True
    event = QCloseEvent()
    env.window.closeEvent(event)
    assert event.isAccepted()


def test_closing_when_idle_needs_no_confirmation(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env = make_env(qtbot, repo, tmp_path)
    event = QCloseEvent()
    env.window.closeEvent(event)
    assert event.isAccepted() and env.dialogs.confirms == []


# ---------- 사이드바·도움말 (Figma 시안) ----------


def recent_buttons(env: Env) -> list[str]:
    box = env.window.recent_box
    return [box.itemAt(i).widget().text() for i in range(box.count())]


def test_recent_works_lists_finished_analyses_and_opens_one_in_review(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env = make_env(qtbot, repo, tmp_path)
    assert recent_buttons(env) == []
    env.start_analysis("안내 영상.mp4")
    env.wait_for("review_page")
    env.window.review_page.new_analysis_button.click()  # 등록 화면으로
    assert env.window.stack.currentWidget() is env.window.register_page
    assert recent_buttons(env) == ["안내 영상.mp4"]

    env.window.recent_box.itemAt(0).widget().click()
    assert env.window.stack.currentWidget() is env.window.review_page
    assert env.window.review_page.table_view.model().rowCount() == 2
    assert "안내 영상.mp4" in env.window.work_label.text()


def test_recent_works_cannot_be_opened_while_analyzing(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env = make_env(qtbot, repo, tmp_path)
    env.start_analysis("안내 영상.mp4")
    env.wait_for("review_page")
    env.window.review_page.new_analysis_button.click()
    env.plan["block"] = True
    env.start_analysis("두번째.mp4")
    assert not env.window.recent_box.itemAt(0).widget().isEnabled()
    env.window.progress_page.cancel_button.click()
    env.wait_for("register_page")
    assert env.window.recent_box.itemAt(0).widget().isEnabled()


def test_sidebar_new_work_returns_to_register_and_help_explains_the_flow(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env = make_env(qtbot, repo, tmp_path)
    env.start_analysis()
    env.wait_for("review_page")
    env.window.new_work_button.click()  # 검수 중에는 새 분석 확인 절차를 따른다
    assert env.window.stack.currentWidget() is env.window.register_page
    env.window.help_button.click()
    assert env.dialogs.infos and env.dialogs.infos[-1][0] == "도움말"


def test_brand_and_default_work_title_follow_the_design(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    env = make_env(qtbot, repo, tmp_path)
    assert env.window.product_label.text() == "영상의 틈"
    assert env.window.work_label.text() == "새 영상 점검"

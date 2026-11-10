"""ui/review_page.py 테스트: 검수 화면(SCR-03, 세부구현.md 7장)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from barrierfree.models import (
    Issue,
    IssueKind,
    IssueSource,
    ReviewStatus,
    SubtitleSource,
    Verdict,
)
from barrierfree.review_state import KindGroup, ReviewState
from barrierfree.storage.repository import Repository
from barrierfree.ui.review_page import ReviewPage
from tests.fakes import FakeDialogs, FakePlayer, make_state


def issue(
    start: float,
    *,
    source: IssueSource = IssueSource.M1,
    kind: IssueKind = IssueKind.VALUE_MISMATCH,
    verdict: Verdict = Verdict.ERROR,
    confidence: float | None = 0.9,
    reason: str = "음성과 자막의 날짜가 다릅니다",
) -> Issue:
    return Issue(
        source=source, kind=kind, verdict=verdict, start_s=start, end_s=start + 3,
        reason=reason, speech_text=f"음성 {start}", comparison_text=f"비교 {start}",
        confidence=confidence,
    )


ISSUES = [
    issue(10),
    issue(20, verdict=Verdict.REVIEW_REQUIRED, kind=IssueKind.EXPRESSION_DIFFERENCE),
    issue(30, source=IssueSource.M2, kind=IssueKind.SCREEN_ONLY_INFO, reason="화면 전화번호 누락"),
]


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[Repository]:
    r = Repository(tmp_path / "app.db")
    yield r
    r.close_all()


class Env:
    def __init__(self, qtbot: QtBot, repo: Repository, issues: list[Issue], **kw: object) -> None:
        self.dialogs = FakeDialogs()
        self.player = FakePlayer()
        self.page = ReviewPage(self.dialogs, lambda: self.player)
        qtbot.addWidget(self.page)
        self.repo = repo
        self.state: ReviewState = make_state(repo, issues, **kw)  # type: ignore[arg-type]
        self.video = repo.get_session(self.state.session_id).video_path
        self.page.load(self.state, Path(self.video))

    def select(self, row: int) -> None:
        self.page.table_view.selectRow(row)


@pytest.fixture
def env(qtbot: QtBot, repo: Repository) -> Env:
    return Env(qtbot, repo, list(ISSUES))


# ---------- 요약·목록 ----------


def test_summary_shows_counts_and_video_name(env: Env) -> None:
    page = env.page
    assert "안내 영상.mp4" in page.video_name_label.text()
    assert "3" in page.total_label.text()
    assert "3" in page.unreviewed_label.text()
    assert "0" in page.confirmed_label.text() and "0" in page.dismissed_label.text()
    assert page.table_view.model().rowCount() == 3


def test_verdict_breakdown_uses_soft_wording(env: Env) -> None:
    text = env.page.verdict_summary_label.text()
    # ISSUES: 문제 의심(ERROR) 2건(10초, 30초), 검수 필요(REVIEW_REQUIRED) 1건(20초)
    assert "문제 의심 2" in text and "검수 필요 1" in text and "판단 불가 0" in text
    assert "오류" not in text  # '오류 확정'으로 읽히는 표현을 쓰지 않는다


def test_empty_state_before_any_selection(env: Env) -> None:
    assert "문제를 선택" in env.page.detail_hint_label.text()
    for button in (env.page.confirm_button, env.page.dismiss_button, env.page.reset_button):
        assert not button.isEnabled()


# ---------- 선택 -> 영상 이동 + 상세 ----------


def test_selecting_an_issue_moves_the_video_and_does_not_autoplay(env: Env) -> None:
    env.select(1)
    assert env.player.seeks == [20.0]
    assert env.player.playing is False


def test_detail_panel_shows_the_comparison(env: Env) -> None:
    env.select(0)
    page = env.page
    assert "주요 정보 불일치" in page.kind_label.text()
    assert "00:10" in page.span_label.text() and "00:13" in page.span_label.text()
    assert "날짜가 다릅니다" in page.reason_label.text()
    assert "음성 10" in page.speech_label.text()
    assert "비교 10" in page.compare_label.text()
    assert "자막" in page.compare_title_label.text()
    assert "문제 의심" in page.verdict_label.text()
    assert page.confirm_button.isEnabled()


def test_screen_issue_labels_the_comparison_as_screen_text(env: Env) -> None:
    env.select(2)
    assert "화면 문자" in env.page.compare_title_label.text()


@pytest.mark.parametrize(
    ("confidence", "verdict", "must_have", "must_not_have"),
    [
        (0.92, Verdict.ERROR, ["참고값", "92%"], ["불확실"]),
        (0.30, Verdict.ERROR, ["참고값", "30%", "불확실"], []),
        (None, Verdict.ERROR, ["참고값", "—"], ["불확실"]),
        (0.95, Verdict.UNDETERMINED, ["불확실"], []),
    ],
)
def test_confidence_is_a_reference_value_and_flags_uncertainty(
    qtbot: QtBot,
    repo: Repository,
    confidence: float | None,
    verdict: Verdict,
    must_have: list[str],
    must_not_have: list[str],
) -> None:
    e = Env(qtbot, repo, [issue(5, confidence=confidence, verdict=verdict)])
    e.select(0)
    text = e.page.confidence_label.text()
    assert all(word in text for word in must_have)
    assert not any(word in text for word in must_not_have)


def test_confidence_text_never_presents_itself_as_error_probability(env: Env) -> None:
    env.select(0)
    assert "오류 확률이 아닙니다" in env.page.confidence_label.text()


# ---------- 검수 ----------


def test_confirm_updates_status_counts_and_persists_without_moving_on(env: Env) -> None:
    env.select(1)
    env.page.confirm_button.click()
    assert "확정" in env.page.status_label.text()
    assert "1" in env.page.confirmed_label.text() and "2" in env.page.unreviewed_label.text()
    model = env.page.table_view.model()
    assert env.page.selected_issue_id == model.issue_at(1).id  # 자동으로 다음 항목으로 가지 않는다
    assert env.repo.list_issues(env.state.session_id)[1].review_status is ReviewStatus.CONFIRMED


def test_dismiss_then_confirm_switches_the_decision(env: Env) -> None:
    env.select(0)
    env.page.dismiss_button.click()
    assert "기각" in env.page.status_label.text()
    env.page.confirm_button.click()
    assert "확정" in env.page.status_label.text()
    assert "0" in env.page.dismissed_label.text() and "1" in env.page.confirmed_label.text()


def test_clicking_the_same_decision_again_changes_nothing(env: Env) -> None:
    env.select(0)
    env.page.confirm_button.click()
    env.page.confirm_button.click()
    assert "1" in env.page.confirmed_label.text()


def test_reset_returns_to_unreviewed(env: Env) -> None:
    env.select(0)
    env.page.dismiss_button.click()
    env.page.reset_button.click()
    assert "미검토" in env.page.status_label.text()
    assert "3" in env.page.unreviewed_label.text()


def test_ai_verdict_is_untouched_by_review(env: Env) -> None:
    env.select(1)
    env.page.dismiss_button.click()
    assert "검수 필요" in env.page.verdict_label.text()


def test_save_failure_is_reported_and_status_stays(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(env.repo, "set_review_status", boom)
    env.select(0)
    env.page.confirm_button.click()
    assert env.dialogs.warnings and "저장" in env.dialogs.warnings[0][1]
    assert "미검토" in env.page.status_label.text()
    assert "0" in env.page.confirmed_label.text()


# ---------- 필터 ----------


def test_group_filter_limits_rows_without_changing_counts(env: Env) -> None:
    index = env.page.group_filter.findData(KindGroup.SCREEN_TEXT)
    env.page.group_filter.setCurrentIndex(index)
    assert env.page.table_view.model().rowCount() == 1
    assert "3" in env.page.total_label.text()  # 필터는 표시만 바꾼다


def test_filter_that_excludes_the_selection_clears_the_detail(env: Env) -> None:
    env.select(0)
    env.page.group_filter.setCurrentIndex(env.page.group_filter.findData(KindGroup.SCREEN_TEXT))
    assert env.page.selected_issue_id is None
    assert "문제를 선택" in env.page.detail_hint_label.text()
    assert not env.page.confirm_button.isEnabled()


def test_filter_with_no_matches_says_so(env: Env) -> None:
    env.page.status_filter.setCurrentIndex(env.page.status_filter.findData(ReviewStatus.CONFIRMED))
    assert env.page.table_view.model().rowCount() == 0
    assert "조건에 해당하는 항목이 없습니다" in env.page.empty_label.text()


def test_resetting_filters_restores_every_row(env: Env) -> None:
    env.page.group_filter.setCurrentIndex(env.page.group_filter.findData(KindGroup.SCREEN_TEXT))
    env.page.group_filter.setCurrentIndex(env.page.group_filter.findData(KindGroup.ALL))
    assert env.page.table_view.model().rowCount() == 3
    assert env.page.empty_label.text() == ""


def test_reviewing_under_a_status_filter_removes_the_row_and_clears_selection(env: Env) -> None:
    env.page.status_filter.setCurrentIndex(env.page.status_filter.findData(ReviewStatus.UNREVIEWED))
    env.select(0)
    env.page.confirm_button.click()
    assert env.page.table_view.model().rowCount() == 2
    assert env.page.selected_issue_id is None


# ---------- 빈 결과 / 안내 ----------


def test_zero_issues_is_handled_and_does_not_claim_compliance(
    qtbot: QtBot, repo: Repository
) -> None:
    e = Env(qtbot, repo, [])
    assert e.page.table_view.model().rowCount() == 0
    text = e.page.notices_label.text()
    assert "탐지된 문제가 없습니다" in text and "의미하지 않" in text
    assert e.page.export_button.isEnabled()  # 0건도 내보내기는 허용한다


def test_no_subtitle_video_shows_a_session_level_notice(qtbot: QtBot, repo: Repository) -> None:
    e = Env(qtbot, repo, [issue(5, source=IssueSource.M2, kind=IssueKind.SCREEN_ONLY_INFO)],
            source=SubtitleSource.NONE)
    assert "자막이 없는 영상" in e.page.notices_label.text()


def test_pipeline_notices_are_displayed(qtbot: QtBot, repo: Repository) -> None:
    e = Env(qtbot, repo, [issue(5)], extra_notices=["영상에 오디오 트랙이 없습니다."])
    assert "오디오 트랙이 없습니다" in e.page.notices_label.text()


# ---------- 내보내기 / 새 분석 ----------


def test_export_writes_a_csv_with_a_sensible_default_name(env: Env, tmp_path: Path) -> None:
    env.dialogs.csv = tmp_path / "결과.csv"
    env.page.export_button.click()
    assert env.dialogs.csv_default_names == ["안내 영상_검수결과.csv"]
    assert (tmp_path / "결과.csv").is_file()
    assert env.dialogs.infos and "결과.csv" in env.dialogs.infos[0][1]


def test_export_cancel_does_nothing(env: Env) -> None:
    env.dialogs.csv = None
    env.page.export_button.click()
    assert env.dialogs.infos == [] and env.dialogs.warnings == []


def test_export_failure_is_explained_and_retryable(env: Env, tmp_path: Path) -> None:
    env.dialogs.csv = tmp_path / "없는폴더" / "x.csv"
    (tmp_path / "없는폴더").write_text("파일이라 폴더를 만들 수 없다")
    env.page.export_button.click()
    assert env.dialogs.warnings and "CSV" in env.dialogs.warnings[0][1]


def test_database_error_while_exporting_is_explained_not_raised(
    env: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(env.repo, "get_session", boom)
    env.dialogs.csv = tmp_path / "x.csv"
    env.page.export_button.click()  # 예외가 슬롯 밖으로 새면 여기서 테스트가 실패한다
    assert env.dialogs.warnings and "CSV" in env.dialogs.warnings[0][1]


def test_new_analysis_button_emits_signal(env: Env, qtbot: QtBot) -> None:
    with qtbot.waitSignal(env.page.new_analysis_requested):
        env.page.new_analysis_button.click()


# ---------- 영상 재생 ----------


def test_existing_video_is_loaded_into_the_player(
    qtbot: QtBot, repo: Repository, tmp_path: Path
) -> None:
    video = tmp_path / "안내 영상.mp4"
    video.write_bytes(b"x")
    e = Env(qtbot, repo, list(ISSUES))
    e.page.load(e.state, video)
    assert e.player.loaded == video
    assert e.page.player_error_label.text() == ""


def test_missing_video_file_is_reported_without_loading_it(env: Env) -> None:
    # env는 존재하지 않는 경로로 불러왔다: 재생기에 넘기지 않고 재생 실패로 안내한다.
    assert env.player.loaded is None
    assert "재생할 수 없습니다" in env.page.player_error_label.text()
    assert "찾을 수 없습니다" in env.page.player_error_label.text()


def test_playback_failure_is_reported_but_results_stay_usable(env: Env) -> None:
    env.player.playback_failed.emit("codec not supported")
    assert "재생할 수 없습니다" in env.page.player_error_label.text()
    assert "ERR-10" in env.page.player_error_label.text()
    env.select(0)
    env.page.confirm_button.click()
    assert "확정" in env.page.status_label.text()


def test_loading_another_session_resets_filters_and_selection(
    qtbot: QtBot, repo: Repository, env: Env
) -> None:
    env.select(0)
    env.page.group_filter.setCurrentIndex(env.page.group_filter.findData(KindGroup.SCREEN_TEXT))
    other = make_state(repo, [issue(1), issue(2)], video_name="다른 영상.mp4")
    env.page.load(other, Path("/videos/다른 영상.mp4"))
    assert env.page.selected_issue_id is None
    assert env.page.table_view.model().rowCount() == 2
    assert env.page.group_filter.currentData() is KindGroup.ALL
    assert "다른 영상.mp4" in env.page.video_name_label.text()

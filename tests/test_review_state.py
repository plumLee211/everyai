"""review_state.py 테스트: 검수 화면의 데이터 규칙 (세부구현.md 7.3~7.9). Qt 없이 검증한다."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from barrierfree.config import AnalysisConfig
from barrierfree.media.ffmpeg import VideoInfo
from barrierfree.models import (
    Issue,
    IssueKind,
    IssueSource,
    ReviewStatus,
    SubtitleSource,
    Verdict,
)
from barrierfree.review_state import (
    ExportError,
    IssueFilter,
    KindGroup,
    ReviewState,
    SaveError,
)
from barrierfree.storage.repository import Repository


def make_issue(
    start: float,
    *,
    source: IssueSource = IssueSource.M1,
    kind: IssueKind = IssueKind.VALUE_MISMATCH,
    verdict: Verdict = Verdict.ERROR,
) -> Issue:
    return Issue(
        source=source, kind=kind, verdict=verdict, start_s=start, end_s=start + 2, reason="사유"
    )


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[Repository]:
    r = Repository(tmp_path / "app.db")
    yield r
    r.close_all()


def new_state(
    repo: Repository,
    issues: list[Issue],
    *,
    source: SubtitleSource = SubtitleSource.FILE,
    extra: list[str] | None = None,
) -> ReviewState:
    session_id = repo.create_session(
        video=VideoInfo(Path("/v/a.mp4"), 60.0, 640, 360, 10.0, True, True),
        video_sha256="a" * 64,
        subtitle_source=source,
        subtitle_path=None,
        config=AnalysisConfig(),
        stt_model_name="small",
        ocr_engine_name="easyocr",
        compute_device="cpu",
    )
    repo.complete_session(session_id, issues)
    return ReviewState(repo, session_id, extra_notices=extra)


MIXED = [
    make_issue(30, source=IssueSource.M2, kind=IssueKind.SCREEN_ONLY_INFO),
    make_issue(10),
    make_issue(20, verdict=Verdict.REVIEW_REQUIRED, kind=IssueKind.EXPRESSION_DIFFERENCE),
    make_issue(40, source=IssueSource.M2_SCREEN_DEPENDENT, kind=IssueKind.SCREEN_DEPENDENT,
               verdict=Verdict.REVIEW_REQUIRED),
]


# ---------- 요약 ----------


def test_issues_are_listed_in_time_order(repo: Repository) -> None:
    state = new_state(repo, list(MIXED))
    assert [i.start_s for i in state.issues] == [10, 20, 30, 40]


def test_summary_total_equals_sum_of_review_states(repo: Repository) -> None:
    state = new_state(repo, list(MIXED))
    first, second = state.issues[0], state.issues[1]
    state.set_status(first.id, ReviewStatus.CONFIRMED)
    state.set_status(second.id, ReviewStatus.DISMISSED)
    s = state.summary()
    assert (s.total, s.unreviewed, s.confirmed, s.dismissed) == (4, 2, 1, 1)
    assert s.total == s.unreviewed + s.confirmed + s.dismissed
    assert s.by_verdict[Verdict.REVIEW_REQUIRED] == 2


# ---------- 상태 전환표 (7.8) ----------


@pytest.mark.parametrize(
    ("start", "action", "expected", "changed"),
    [
        (ReviewStatus.UNREVIEWED, ReviewStatus.CONFIRMED, ReviewStatus.CONFIRMED, True),
        (ReviewStatus.UNREVIEWED, ReviewStatus.DISMISSED, ReviewStatus.DISMISSED, True),
        (ReviewStatus.CONFIRMED, ReviewStatus.DISMISSED, ReviewStatus.DISMISSED, True),
        (ReviewStatus.DISMISSED, ReviewStatus.CONFIRMED, ReviewStatus.CONFIRMED, True),
        (ReviewStatus.CONFIRMED, ReviewStatus.CONFIRMED, ReviewStatus.CONFIRMED, False),
        (ReviewStatus.DISMISSED, ReviewStatus.DISMISSED, ReviewStatus.DISMISSED, False),
        (ReviewStatus.CONFIRMED, ReviewStatus.UNREVIEWED, ReviewStatus.UNREVIEWED, True),
        (ReviewStatus.DISMISSED, ReviewStatus.UNREVIEWED, ReviewStatus.UNREVIEWED, True),
        (ReviewStatus.UNREVIEWED, ReviewStatus.UNREVIEWED, ReviewStatus.UNREVIEWED, False),
    ],
)
def test_status_transitions(
    repo: Repository,
    start: ReviewStatus,
    action: ReviewStatus,
    expected: ReviewStatus,
    changed: bool,
) -> None:
    state = new_state(repo, [make_issue(1)])
    issue_id = state.issues[0].id
    if start is not ReviewStatus.UNREVIEWED:
        state.set_status(issue_id, start)
    assert state.set_status(issue_id, action) is changed
    assert state.get(issue_id).review_status is expected
    persisted = repo.list_issues(state.session_id)[0]
    assert persisted.review_status is expected  # 화면과 저장소가 같다


def test_verdict_is_never_touched_by_review(repo: Repository) -> None:
    state = new_state(repo, [make_issue(1, verdict=Verdict.REVIEW_REQUIRED)])
    issue_id = state.issues[0].id
    state.set_status(issue_id, ReviewStatus.DISMISSED)
    assert repo.list_issues(state.session_id)[0].verdict is Verdict.REVIEW_REQUIRED


def test_unchanged_transition_does_not_write(repo: Repository) -> None:
    state = new_state(repo, [make_issue(1)])
    issue_id = state.issues[0].id
    state.set_status(issue_id, ReviewStatus.CONFIRMED)
    before = repo.connection().execute("SELECT updated_at FROM issue").fetchone()[0]
    state.set_status(issue_id, ReviewStatus.CONFIRMED)
    assert repo.connection().execute("SELECT updated_at FROM issue").fetchone()[0] == before


def test_review_state_survives_reopening(repo: Repository) -> None:
    state = new_state(repo, [make_issue(1)])
    issue_id = state.issues[0].id
    state.set_status(issue_id, ReviewStatus.CONFIRMED)
    reopened = ReviewState(repo, state.session_id)
    assert reopened.get(issue_id).review_status is ReviewStatus.CONFIRMED


def test_save_failure_keeps_state_unchanged_and_reports(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = new_state(repo, [make_issue(1)])
    issue_id = state.issues[0].id

    def boom(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(repo, "set_review_status", boom)
    with pytest.raises(SaveError):
        state.set_status(issue_id, ReviewStatus.CONFIRMED)
    assert state.get(issue_id).review_status is ReviewStatus.UNREVIEWED
    assert state.summary().confirmed == 0


def test_unknown_issue_raises_key_error(repo: Repository) -> None:
    state = new_state(repo, [make_issue(1)])
    with pytest.raises(KeyError):
        state.set_status("missing", ReviewStatus.CONFIRMED)


# ---------- 필터 (7.5) ----------


@pytest.mark.parametrize(
    ("flt", "expected_starts"),
    [
        (IssueFilter(), [10, 20, 30, 40]),
        (IssueFilter(group=KindGroup.SPEECH_SUBTITLE), [10, 20]),
        (IssueFilter(group=KindGroup.SCREEN_TEXT), [30]),
        (IssueFilter(group=KindGroup.SCREEN_DEPENDENT), [40]),
        (IssueFilter(verdict=Verdict.REVIEW_REQUIRED), [20, 40]),
        (IssueFilter(verdict=Verdict.ERROR, group=KindGroup.SPEECH_SUBTITLE), [10]),
        (IssueFilter(status=ReviewStatus.CONFIRMED), []),
    ],
)
def test_filters_select_without_changing_data(
    repo: Repository, flt: IssueFilter, expected_starts: list[float]
) -> None:
    state = new_state(repo, list(MIXED))
    before = state.summary()
    assert [i.start_s for i in state.visible(flt)] == expected_starts
    assert state.summary() == before  # 필터는 표시 결과만 바꾼다
    assert len(state.issues) == 4


def test_status_filter_follows_review_changes(repo: Repository) -> None:
    state = new_state(repo, list(MIXED))
    issue_id = state.issues[1].id
    state.set_status(issue_id, ReviewStatus.DISMISSED)
    assert [i.id for i in state.visible(IssueFilter(status=ReviewStatus.DISMISSED))] == [issue_id]
    assert len(state.visible(IssueFilter(status=ReviewStatus.UNREVIEWED))) == 3


def test_selection_dropped_when_filter_excludes_it(repo: Repository) -> None:
    state = new_state(repo, list(MIXED))
    selected = state.issues[0].id  # 10초, M1
    assert state.keep_selection(selected, IssueFilter(group=KindGroup.SPEECH_SUBTITLE)) == selected
    assert state.keep_selection(selected, IssueFilter(group=KindGroup.SCREEN_TEXT)) is None
    assert state.keep_selection(None, IssueFilter()) is None


# ---------- 안내 문구 (화면 기획 유의사항) ----------


def test_zero_issues_does_not_claim_accessibility_compliance(repo: Repository) -> None:
    notices = new_state(repo, []).notices()
    assert any("탐지된 문제가 없습니다" in n for n in notices)
    assert any("충족" in n and "의미하지 않" in n for n in notices)


def test_no_subtitle_video_is_a_session_level_notice(repo: Repository) -> None:
    notices = new_state(repo, [], source=SubtitleSource.NONE).notices()
    assert any("자막이 없는 영상" in n for n in notices)


def test_ocr_subtitle_session_says_results_are_ocr_based(repo: Repository) -> None:
    notices = new_state(repo, [make_issue(1)], source=SubtitleSource.OCR).notices()
    assert any("OCR" in n for n in notices)


def test_pipeline_notices_are_kept(repo: Repository) -> None:
    notices = new_state(repo, [make_issue(1)], extra=["영상에 오디오 트랙이 없습니다."]).notices()
    assert "영상에 오디오 트랙이 없습니다." in notices


# ---------- 내보내기 ----------


def test_database_errors_during_export_become_export_errors(
    repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = new_state(repo, list(MIXED))

    def boom(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repo, "list_issues", boom)
    with pytest.raises(ExportError):
        state.export_csv(tmp_path / "x.csv")


def test_export_includes_dismissed_items(repo: Repository, tmp_path: Path) -> None:
    state = new_state(repo, list(MIXED))
    state.set_status(state.issues[0].id, ReviewStatus.DISMISSED)
    out = state.export_csv(tmp_path / "결과.csv")
    lines = out.read_text(encoding="utf-8-sig").strip().splitlines()
    assert len(lines) == 1 + 4
    assert any("기각" in line for line in lines)

"""ui/issue_model.py 테스트: 문제 목록 테이블 모델 (docs/project/ui-spec.md 7.5)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from pytestqt.qtbot import QtBot

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
from barrierfree.review_state import IssueFilter, KindGroup, ReviewState
from barrierfree.storage.repository import Repository
from barrierfree.ui.issue_model import COLUMNS, IssueTableModel


@pytest.fixture
def state(tmp_path: Path) -> Iterator[ReviewState]:
    repo = Repository(tmp_path / "app.db")
    sid = repo.create_session(
        video=VideoInfo(Path("/v/a.mp4"), 600.0, 640, 360, 10.0, True, True),
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
              start_s=65.5, end_s=70, reason="음성과 자막의 날짜가 다릅니다"),
        Issue(source=IssueSource.M2, kind=IssueKind.SCREEN_ONLY_INFO, verdict=Verdict.ERROR,
              start_s=3725.0, end_s=3730, reason="화면의 전화번호를 음성에서 확인하지 못했습니다"),
    ]
    repo.complete_session(sid, issues)
    yield ReviewState(repo, sid)
    repo.close_all()


def display(model: IssueTableModel, row: int, col: int) -> str:
    return str(model.data(model.index(row, col), Qt.ItemDataRole.DisplayRole))


def test_header_labels(state: ReviewState, qtbot: QtBot) -> None:
    model = IssueTableModel(state)
    labels = [
        str(model.headerData(c, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole))
        for c in range(model.columnCount())
    ]
    assert labels == list(COLUMNS) == ["시간", "유형", "내용", "AI 판정", "상태"]


def test_rows_show_time_kind_reason_verdict_and_status(state: ReviewState, qtbot: QtBot) -> None:
    model = IssueTableModel(state)
    assert model.rowCount() == 2
    assert display(model, 0, 0) == "01:05"
    assert display(model, 0, 1) == "음성·자막"
    assert display(model, 0, 2) == "음성과 자막의 날짜가 다릅니다"
    assert display(model, 0, 3) == "문제 의심"
    assert display(model, 0, 4) == "미검토"
    assert display(model, 1, 0) == "1:02:05"  # 1시간 이상은 시:분:초
    assert display(model, 1, 1) == "화면 문자"


def test_full_reason_is_available_as_tooltip(state: ReviewState, qtbot: QtBot) -> None:
    model = IssueTableModel(state)
    tip = model.data(model.index(1, 2), Qt.ItemDataRole.ToolTipRole)
    assert "전화번호" in str(tip)


def test_issue_lookup_by_row_and_id(state: ReviewState, qtbot: QtBot) -> None:
    model = IssueTableModel(state)
    issue = model.issue_at(1)
    assert model.row_of(issue.id) == 1
    assert model.row_of("missing") is None
    with pytest.raises(IndexError):
        model.issue_at(5)


def test_filter_changes_rows_not_data(state: ReviewState, qtbot: QtBot) -> None:
    model = IssueTableModel(state)
    with qtbot.waitSignal(model.modelReset):
        model.set_filter(IssueFilter(group=KindGroup.SCREEN_TEXT))
    assert model.rowCount() == 1 and model.issue_at(0).source is IssueSource.M2
    model.set_filter(IssueFilter())
    assert model.rowCount() == 2
    assert len(state.issues) == 2


def test_status_change_emits_data_changed_for_that_row(state: ReviewState, qtbot: QtBot) -> None:
    model = IssueTableModel(state)
    issue = model.issue_at(0)
    with qtbot.waitSignal(model.dataChanged) as blocker:
        assert model.set_status(issue.id, ReviewStatus.CONFIRMED) is True
    top_left = blocker.args[0]
    assert top_left.row() == 0
    assert display(model, 0, 4) == "확정"


def test_status_change_hides_row_when_status_filter_no_longer_matches(
    state: ReviewState, qtbot: QtBot
) -> None:
    model = IssueTableModel(state)
    model.set_filter(IssueFilter(status=ReviewStatus.UNREVIEWED))
    issue = model.issue_at(0)
    model.set_status(issue.id, ReviewStatus.DISMISSED)
    assert model.rowCount() == 1  # 미검토만 보기에서 기각한 항목은 사라진다
    assert state.get(issue.id).review_status is ReviewStatus.DISMISSED


def test_unchanged_status_emits_nothing(state: ReviewState, qtbot: QtBot) -> None:
    model = IssueTableModel(state)
    issue = model.issue_at(0)
    model.set_status(issue.id, ReviewStatus.CONFIRMED)
    with qtbot.assertNotEmitted(model.dataChanged):
        assert model.set_status(issue.id, ReviewStatus.CONFIRMED) is False


def test_model_is_read_only(state: ReviewState, qtbot: QtBot) -> None:
    model = IssueTableModel(state)
    flags = model.flags(model.index(0, 0))
    assert not flags & Qt.ItemFlag.ItemIsEditable
    assert flags & Qt.ItemFlag.ItemIsSelectable

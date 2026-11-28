"""대조 보기 화면: 행 표시, 접힌 글자, 지적 표시, 영상 이동과 지적으로 가기."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from barrierfree.analysis.compare_rows import CompareData
from barrierfree.models import (
    AnalysisNote,
    Issue,
    IssueKind,
    IssueSource,
    NoteCode,
    NoteSource,
    ScreenText,
    SpeechSegment,
    SubtitleCue,
    Verdict,
)
from barrierfree.review_state import KindGroup, ReviewState
from barrierfree.storage.compare_data import save_compare_data
from barrierfree.storage.repository import Repository
from barrierfree.ui.compare_view import CompareView
from barrierfree.ui.review_page import ReviewPage
from tests.fakes import FakeDialogs, FakePlayer, make_state


def st(start: float, end: float, text: str, conf: float = 0.9) -> ScreenText:
    return ScreenText(text=text, start_s=start, end_s=end, confidence=conf, box=None)


DATA = CompareData(
    speech=[SpeechSegment(0, 5, "접수는 이일까지입니다", None, None),
            SpeechSegment(8, 12, "문의는 담당 부서로", None, None)],
    screen=[st(0, 12, "기관 이름"), st(0, 4, "접수 <b>마감</b> & 안내")],
    uncertain=[st(1, 3, "흐린 글자 하나", 0.2), st(2, 4, "흐린 글자 둘", 0.3)],
    cues=[SubtitleCue(0, 5, "접수는 2일까지")],
)


def issue(start: float, reason: str = "사유") -> Issue:
    return Issue(source=IssueSource.M2, kind=IssueKind.SCREEN_ONLY_INFO,
                 verdict=Verdict.REVIEW_REQUIRED, start_s=start, end_s=start + 2, reason=reason)


@pytest.fixture
def view(qtbot: QtBot) -> CompareView:
    widget = CompareView()
    qtbot.addWidget(widget)
    return widget


def _text(view: CompareView) -> str:
    return view.browser.toPlainText()


def test_rows_show_speech_screen_and_subtitle_together(view: CompareView) -> None:
    view.set_content(DATA, [], [])
    text = _text(view)
    for expected in ("접수는 이일까지입니다", "기관 이름", "접수는 2일까지", "00:00~00:05"):
        assert expected in text


def test_screen_text_is_shown_as_text_not_markup(view: CompareView) -> None:
    view.set_content(DATA, [], [])
    assert "접수 <b>마감</b> & 안내" in _text(view)


def test_the_view_never_claims_a_match_or_a_normal_row(view: CompareView) -> None:
    view.set_content(DATA, [], [])
    assert "일치" not in _text(view) and "정상" not in _text(view)
    assert "일치나 정상을 뜻하지 않습니다" in view.summary_label.text()


def test_counts_are_always_visible(view: CompareView) -> None:
    view.set_content(DATA, [], [])
    summary = view.summary_label.text()
    assert "화면 글자 2건" in summary and "불확실 2건" in summary


def test_uncertain_text_is_folded_with_its_count_and_can_be_opened(view: CompareView) -> None:
    view.set_content(DATA, [], [])
    assert "불확실 2건 [보기]" in _text(view) and "흐린 글자 하나" not in _text(view)
    view.handle_link("uncertain:0")
    assert "흐린 글자 하나" in _text(view) and "불확실 2건 [접기]" in _text(view)
    view.handle_link("uncertain:0")
    assert "흐린 글자 하나" not in _text(view)


def test_text_that_stays_is_counted_in_later_rows_and_can_be_opened(view: CompareView) -> None:
    view.set_content(DATA, [], [])
    assert _text(view).count("기관 이름") == 1
    assert "계속 표시 1건 [보기]" in _text(view)
    view.handle_link("carried:1")
    assert _text(view).count("기관 이름") == 2


def test_a_row_with_findings_shows_how_many_and_leads_to_them(
    view: CompareView, qtbot: QtBot
) -> None:
    first, second = issue(1.0), issue(2.0)
    view.set_content(DATA, [first, second], [])
    assert "지적 2" in _text(view)
    with qtbot.waitSignal(view.issue_requested) as signal:
        view.handle_link("issue:0")
    assert signal.args == [first.id]


def test_rows_in_a_suspected_gap_say_so(view: CompareView) -> None:
    note = AnalysisNote(NoteCode.SPEECH_MAYBE_MISSED, NoteSource.STT, "x", 9.0, 11.0)
    view.set_content(DATA, [], [note])
    assert _text(view).count("음성 누락 의심") == 1


def test_a_screen_finding_is_marked_on_the_row_that_shows_its_text(view: CompareView) -> None:
    data = CompareData(
        [SpeechSegment(0, 10, "앞", None, None), SpeechSegment(10, 20, "뒤", None, None)],
        [st(9.8, 14, "새 글자 3월 9일")], [], [],
    )
    finding = Issue(source=IssueSource.M2, kind=IssueKind.SCREEN_ONLY_INFO,
                    verdict=Verdict.REVIEW_REQUIRED, start_s=9.8, end_s=14, reason="x",
                    comparison_text="새 글자 3월 9일")
    view.set_content(data, [finding], [])
    first, second = _text(view).split("00:10~00:20")
    assert "지적" not in first and "지적 1" in second and "새 글자 3월 9일" in second


def test_a_suspected_gap_in_a_silence_is_still_shown(view: CompareView) -> None:
    data = CompareData(
        [SpeechSegment(0, 4, "앞", None, None), SpeechSegment(20, 24, "뒤", None, None)],
        [], [], [],
    )
    note = AnalysisNote(NoteCode.SPEECH_MAYBE_MISSED, NoteSource.STT, "x", 8.0, 16.0)
    view.set_content(data, [], [note])
    assert "음성 누락 의심" in _text(view)


def test_uncertain_text_stays_marked_when_listed_as_continuing(view: CompareView) -> None:
    data = CompareData(
        [SpeechSegment(0, 4, "앞", None, None), SpeechSegment(4, 8, "뒤", None, None)],
        [st(0, 8, "확실한 글자")], [st(0, 8, "흐린 것", 0.2)], [],
    )
    view.set_content(data, [], [])
    view.handle_link("carried:1")
    assert "(불확실) 흐린 것" in _text(view) and "(불확실) 확실한 글자" not in _text(view)


@pytest.mark.parametrize("link", ["uncertain:0", "carried:1", "seek:0"])
def test_using_the_view_counts_as_review_activity(
    view: CompareView, qtbot: QtBot, link: str
) -> None:
    view.set_content(DATA, [], [])
    with qtbot.waitSignal(view.activity):
        view.handle_link(link)


def test_clicking_a_time_asks_to_move_the_video(view: CompareView, qtbot: QtBot) -> None:
    view.set_content(DATA, [], [])
    with qtbot.waitSignal(view.seek_requested) as signal:
        view.handle_link("seek:1")
    assert signal.args == [8.0]


@pytest.mark.parametrize("link", ["", "seek:99", "seek:x", "issue:0", "unknown:0", "seek:-1"])
def test_unexpected_links_are_ignored(view: CompareView, qtbot: QtBot, link: str) -> None:
    view.set_content(DATA, [], [])
    before = _text(view)
    with qtbot.assertNotEmitted(view.seek_requested), qtbot.assertNotEmitted(view.issue_requested):
        with qtbot.assertNotEmitted(view.activity):
            view.handle_link(link)
    assert _text(view) == before


def test_an_analysis_without_view_data_says_why(view: CompareView) -> None:
    view.set_content(None, [], [])
    assert "대조 자료가 없습니다" in _text(view) and "다시 분석" in _text(view)


# ---------- 검수 화면 ----------


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[Repository]:
    r = Repository(tmp_path / "app.db")
    yield r
    r.close_all()


@pytest.fixture
def page(qtbot: QtBot, repo: Repository, tmp_path: Path):  # noqa: ANN201
    player = FakePlayer()
    widget = ReviewPage(FakeDialogs(), lambda: player)
    qtbot.addWidget(widget)
    base = make_state(repo, [issue(1.0, "첫째"), issue(9.0, "둘째")])
    save_compare_data(tmp_path, base.session_id, DATA)
    widget.load(ReviewState(repo, base.session_id, data_dir=tmp_path), None)
    return widget, player


def test_the_review_page_opens_on_the_issue_list(page) -> None:  # noqa: ANN001
    widget, _ = page
    assert widget.tabs.currentIndex() == 0
    titles = [widget.tabs.tabText(i) for i in range(widget.tabs.count())]
    assert titles == ["문제 목록", "대조 보기"]
    assert not widget.detail_card.isHidden()


def test_the_detail_panel_belongs_to_the_issue_list_tab(page) -> None:  # noqa: ANN001
    widget, _ = page
    widget.tabs.setCurrentIndex(1)
    assert widget.detail_card.isHidden()
    assert "접수는 이일까지입니다" in widget.compare_view.browser.toPlainText()
    widget.tabs.setCurrentIndex(0)
    assert not widget.detail_card.isHidden()


def test_a_time_in_the_view_moves_the_video(page) -> None:  # noqa: ANN001
    widget, player = page
    widget.tabs.setCurrentIndex(1)
    widget.compare_view.handle_link("seek:1")
    assert player.seeks[-1] == 8.0 and not player.playing


def test_a_finding_link_opens_that_issue_even_if_a_filter_hid_it(page) -> None:  # noqa: ANN001
    widget, _ = page
    widget.group_filter.setCurrentIndex(widget.group_filter.findData(KindGroup.SPEECH_SUBTITLE))
    widget.tabs.setCurrentIndex(1)
    widget.compare_view.handle_link("issue:1")
    assert widget.tabs.currentIndex() == 0
    assert widget.selected_issue_id is not None
    assert "둘째" in widget.reason_label.text()


def test_loading_another_analysis_returns_to_the_list_and_folds_everything(
    page, repo: Repository, tmp_path: Path  # noqa: ANN001
) -> None:
    widget, _ = page
    widget.tabs.setCurrentIndex(1)
    widget.compare_view.handle_link("uncertain:0")
    other = make_state(repo, [issue(1.0)])
    save_compare_data(tmp_path, other.session_id, DATA)
    widget.load(ReviewState(repo, other.session_id, data_dir=tmp_path), None)
    assert widget.tabs.currentIndex() == 0
    assert "흐린 글자 하나" not in widget.compare_view.browser.toPlainText()

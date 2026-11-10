"""ui/register_page.py 테스트: 영상 등록 화면(SCR-01, 세부구현.md 5장)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from barrierfree.media.ffmpeg import (
    UnsupportedVideoError,
    VideoFileNotFoundError,
    VideoInfo,
    VideoTooLongError,
)
from barrierfree.media.subtitles import LoadedSubtitle, SubtitleParseError
from barrierfree.models import SubtitleCue
from barrierfree.ui.register_page import RegisterPage
from tests.fakes import FakeDialogs


def video_validator(path: Path) -> VideoInfo:
    name = path.name
    if "긴" in name:
        raise VideoTooLongError(1500.0, 1200.0)
    if "없음" in name:
        raise VideoFileNotFoundError(name)
    if "형식" in name:
        raise UnsupportedVideoError(name)
    return VideoInfo(path, 125.0, 640, 360, 10.0, True, True)


def subtitle_loader(path: Path) -> LoadedSubtitle:
    if "깨진" in path.name:
        raise SubtitleParseError("bad")
    skipped = 2 if "일부" in path.name else 0
    return LoadedSubtitle([SubtitleCue(0, 1, "안녕")], "utf-8", "SRT", skipped)


def make_page(qtbot: QtBot, dialogs: FakeDialogs | None = None) -> RegisterPage:
    page = RegisterPage(dialogs or FakeDialogs(), video_validator, subtitle_loader)
    qtbot.addWidget(page)
    return page


# ---------- 초기 상태 ----------


def test_initially_start_is_disabled_and_the_hint_asks_for_a_video(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    assert not page.start_button.isEnabled()
    assert "영상 파일을 선택" in page.hint_label.text()
    assert page.video_path is None and page.subtitle_path is None


def test_no_subtitle_notice_explains_automatic_extraction(qtbot: QtBot) -> None:
    page = make_page(qtbot)  # 변수에 담아야 위젯이 해제되지 않는다
    text = page.notice_label.text()
    assert "자막 파일이 없습니다" in text and "자동 추출" in text


# ---------- 영상 ----------


def test_valid_video_shows_name_and_duration_and_enables_start(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    page.set_video(Path("/v/안내 영상.mp4"))
    assert "안내 영상.mp4" in page.video_info_label.text()
    assert "02:05" in page.video_info_label.text()
    assert page.start_button.isEnabled()
    assert page.video_path == Path("/v/안내 영상.mp4")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("긴 영상.mp4", "20분을 초과"),
        ("없음.mp4", "찾을 수 없습니다"),
        ("형식.xyz", "지원하지 않는"),
    ],
)
def test_invalid_video_is_rejected_with_a_message_and_code(
    qtbot: QtBot, name: str, expected: str
) -> None:
    page = make_page(qtbot)
    page.set_video(Path("/v") / name)
    assert expected in page.video_error_label.text()
    assert "ERR-" in page.video_error_label.text()
    assert page.video_path is None
    assert not page.start_button.isEnabled()


def test_invalid_video_keeps_the_previously_registered_one(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    page.set_video(Path("/v/첫번째.mp4"))
    page.set_video(Path("/v/긴 영상.mp4"))
    assert page.video_path == Path("/v/첫번째.mp4")
    assert page.start_button.isEnabled()


def test_a_valid_video_after_an_error_clears_the_error(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    page.set_video(Path("/v/긴 영상.mp4"))
    page.set_video(Path("/v/정상.mp4"))
    assert page.video_error_label.text() == ""


def test_removing_the_video_disables_start(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    page.set_video(Path("/v/a.mp4"))
    page.video_remove_button.click()
    assert page.video_path is None and not page.start_button.isEnabled()


def test_registering_a_new_video_clears_the_previous_subtitle(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    page.set_video(Path("/v/a.mp4"))
    page.set_subtitle(Path("/v/a.srt"))
    page.set_video(Path("/v/b.mp4"))
    assert page.subtitle_path is None  # 서로 다른 영상의 자막이 잘못 연결되지 않게 한다


# ---------- 자막 ----------


def test_valid_subtitle_switches_the_notice_and_shows_the_file(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    page.set_video(Path("/v/a.mp4"))
    page.set_subtitle(Path("/v/자막.srt"))
    assert "자막.srt" in page.subtitle_info_label.text()
    assert "등록한 자막 파일을 기준으로" in page.notice_label.text()
    assert page.subtitle_path == Path("/v/자막.srt")
    assert page.start_button.isEnabled()


def test_invalid_subtitle_blocks_start_until_fixed_or_removed(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    page.set_video(Path("/v/a.mp4"))
    page.set_subtitle(Path("/v/깨진.srt"))
    assert "ERR-03" in page.subtitle_error_label.text()
    assert page.subtitle_path is None
    assert not page.start_button.isEnabled()  # 잘못된 자막은 분석 시작을 차단한다

    page.subtitle_remove_button.click()
    assert page.subtitle_error_label.text() == ""
    assert page.start_button.isEnabled()


def test_valid_subtitle_after_an_invalid_one_unblocks_start(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    page.set_video(Path("/v/a.mp4"))
    page.set_subtitle(Path("/v/깨진.srt"))
    page.set_subtitle(Path("/v/정상.srt"))
    assert page.start_button.isEnabled() and page.subtitle_error_label.text() == ""


def test_partially_unreadable_subtitle_warns_but_does_not_block(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    page.set_video(Path("/v/a.mp4"))
    page.set_subtitle(Path("/v/일부.srt"))
    assert "2개 구간" in page.subtitle_info_label.text()
    assert page.start_button.isEnabled()


def test_subtitle_without_a_video_asks_for_the_video_first(qtbot: QtBot) -> None:
    # 영상 없이 자막을 받으면, 이어서 영상을 등록할 때 자막이 말없이 사라지는 혼란이 생긴다.
    page = make_page(qtbot)
    page.set_subtitle(Path("/v/a.srt"))
    assert page.subtitle_path is None
    assert "먼저 영상" in page.subtitle_error_label.text()
    assert not page.start_button.isEnabled()
    page.set_video(Path("/v/a.mp4"))
    assert page.subtitle_error_label.text() == ""  # 영상을 등록하면 안내가 사라진다


# ---------- 시작 / 설정 / 파일 선택 ----------


def test_start_emits_video_and_subtitle(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    page.set_video(Path("/v/a.mp4"))
    page.set_subtitle(Path("/v/a.srt"))
    with qtbot.waitSignal(page.start_requested) as blocker:
        page.start_button.click()
    assert blocker.args == [Path("/v/a.mp4"), Path("/v/a.srt")]


def test_start_without_subtitle_emits_none(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    page.set_video(Path("/v/a.mp4"))
    with qtbot.waitSignal(page.start_requested) as blocker:
        page.start_button.click()
    assert blocker.args == [Path("/v/a.mp4"), None]


def test_disabled_start_emits_nothing(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    with qtbot.assertNotEmitted(page.start_requested):
        page.start_button.click()


def test_busy_page_disables_every_input(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    page.set_video(Path("/v/a.mp4"))
    page.set_busy(True)
    for button in (page.start_button, page.video_button, page.subtitle_button,
                   page.video_remove_button, page.subtitle_remove_button, page.settings_button):
        assert not button.isEnabled()
    page.set_busy(False)
    assert page.start_button.isEnabled() and page.settings_button.isEnabled()


def test_settings_button_and_summary(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    page.set_settings_summary("모델: small · 실행 장치: CPU")
    assert "small" in page.settings_summary_label.text()
    with qtbot.waitSignal(page.settings_requested):
        page.settings_button.click()


def test_pick_buttons_use_the_dialogs_and_ignore_cancel(qtbot: QtBot) -> None:
    dialogs = FakeDialogs()
    page = make_page(qtbot, dialogs)
    page.video_button.click()  # 사용자가 취소 -> 아무 일도 없다
    assert page.video_path is None
    dialogs.video = Path("/v/선택.mp4")
    page.video_button.click()
    assert page.video_path == Path("/v/선택.mp4")
    dialogs.subtitle = Path("/v/선택.srt")
    page.subtitle_button.click()
    assert page.subtitle_path == Path("/v/선택.srt")

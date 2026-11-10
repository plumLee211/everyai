"""analysis/subtitle_extract.py 테스트: 확인된 자막 영역의 OCR 글자를 자막(SubtitleCue)으로."""

from __future__ import annotations

from pathlib import Path

from barrierfree.analysis.subtitle_extract import extract_cues, split_by_region
from barrierfree.analysis.subtitle_region import region_rect
from barrierfree.media.subtitles import load_subtitle_file, write_srt
from barrierfree.models import ScreenText, SubtitleCue


def st(
    text: str,
    start: float,
    end: float,
    y: float | None = 0.85,
    x: float = 0.2,
) -> ScreenText:
    box = None if y is None else (x, y, 0.6, 0.05)
    return ScreenText(text=text, start_s=start, end_s=end, confidence=0.9, box=box)


# ---------- 영역 분리 ----------


def test_split_by_region_keeps_excluded_text_for_screen_check() -> None:
    inside = st("신청은 홈페이지에서", 0, 3, y=0.85)
    name_bar = st("홍길동 팀장", 5, 20, y=0.2)  # 자막 영역 밖
    no_box = st("위치를 모르는 글자", 1, 2, y=None)
    subtitles, others = split_by_region([inside, name_bar, no_box], region_rect(0.25))
    assert subtitles == [inside]
    assert others == [name_bar, no_box]  # 순서를 유지하고, M2의 화면 글자로 남는다


# ---------- 추출 ----------


def test_single_line_becomes_one_cue() -> None:
    assert extract_cues([st("신청은 홈페이지에서", 1.0, 3.5)]) == [
        SubtitleCue(1.0, 3.5, "신청은 홈페이지에서")
    ]


def test_two_stacked_lines_of_one_subtitle_are_joined_top_to_bottom() -> None:
    lines = [st("둘째 줄입니다", 1.0, 3.0, y=0.90), st("첫째 줄입니다", 1.0, 3.0, y=0.84)]
    assert extract_cues(lines) == [SubtitleCue(1.0, 3.0, "첫째 줄입니다 둘째 줄입니다")]


def test_lines_with_slightly_different_end_times_are_still_joined() -> None:
    lines = [st("첫째 줄입니다", 1.0, 3.0, y=0.84), st("둘째 줄입니다", 1.0, 3.5, y=0.90)]
    [cue] = extract_cues(lines)
    assert cue.text == "첫째 줄입니다 둘째 줄입니다"
    assert (cue.start_s, cue.end_s) == (1.0, 3.5)


def test_upper_line_read_one_sample_late_is_still_joined_above_the_lower_line() -> None:
    # OCR이 윗줄을 늦게 읽어 시작 시각이 더 뒤다. 그래도 두 줄은 한 자막이어야 한다.
    lower = ScreenText("아랫줄입니다", 1.0, 4.0, 0.9, (0.2, 0.90, 0.6, 0.04))
    upper = ScreenText("윗줄입니다", 1.5, 4.0, 0.9, (0.2, 0.84, 0.6, 0.04))
    cues = extract_cues([lower, upper])
    assert len(cues) == 1
    assert cues[0].text == "윗줄입니다 아랫줄입니다"  # 위에서 아래 순서
    assert (cues[0].start_s, cues[0].end_s) == (1.0, 4.0)


def test_three_lines_arriving_in_any_order_become_one_cue_top_to_bottom() -> None:
    lines = [
        ScreenText("가운데", 1.0, 4.0, 0.9, (0.2, 0.84, 0.6, 0.04)),
        ScreenText("맨 아래", 1.1, 4.0, 0.9, (0.2, 0.90, 0.6, 0.04)),
        ScreenText("맨 위", 1.2, 4.0, 0.9, (0.2, 0.78, 0.6, 0.04)),
    ]
    [cue] = extract_cues(lines)
    assert cue.text == "맨 위 가운데 맨 아래"


def test_consecutive_subtitles_are_not_merged() -> None:
    cues = extract_cues([st("첫 번째 자막", 0.0, 2.0), st("두 번째 자막", 2.0, 4.0)])
    assert [c.text for c in cues] == ["첫 번째 자막", "두 번째 자막"]


def test_vertically_distant_texts_at_same_time_are_separate_cues() -> None:
    cues = extract_cues([st("하단 자막", 1.0, 3.0, y=0.90), st("떨어진 글자", 1.0, 3.0, y=0.60)])
    assert len(cues) == 2


def test_cues_are_sorted_by_start_time() -> None:
    cues = extract_cues([st("나중", 5.0, 6.0), st("먼저", 1.0, 2.0)])
    assert [c.text for c in cues] == ["먼저", "나중"]


def test_no_texts_means_no_cues() -> None:
    assert extract_cues([]) == []


def test_extracted_cues_roundtrip_through_srt(tmp_path: Path) -> None:
    cues = extract_cues([st("신청은 홈페이지에서", 1.0, 3.5), st("문의는 02-1234-5678", 4.0, 6.0)])
    out = write_srt(cues, tmp_path / "ocr.srt")
    assert load_subtitle_file(out).cues == cues

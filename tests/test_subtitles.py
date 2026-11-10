"""media/subtitles.py 테스트: SRT/SMI 파싱, 인코딩 감지, SRT 작성."""

from __future__ import annotations

from pathlib import Path

import pytest

from barrierfree.media.subtitles import (
    SubtitleParseError,
    UnsupportedSubtitleError,
    decode_bytes,
    format_srt,
    load_subtitle_file,
    parse_smi,
    parse_srt,
    write_srt,
)
from barrierfree.models import SubtitleCue

SRT = """1
00:00:01,000 --> 00:00:03,500
신청은 3월 15일부터입니다.

2
00:00:04,000 --> 00:00:06,000
문의: 02-1234-5678
"""


def test_parse_basic_srt() -> None:
    cues = parse_srt(SRT)
    assert cues == [
        SubtitleCue(1.0, 3.5, "신청은 3월 15일부터입니다."),
        SubtitleCue(4.0, 6.0, "문의: 02-1234-5678"),
    ]


def test_parse_srt_handles_bom_and_crlf() -> None:
    text = "﻿" + SRT.replace("\n", "\r\n")
    assert len(parse_srt(text)) == 2


def test_parse_srt_without_index_and_with_dot_separator() -> None:
    cues = parse_srt("00:00:01.250 --> 00:00:02.000\n안내입니다\n")
    assert cues == [SubtitleCue(1.25, 2.0, "안내입니다")]


def test_parse_srt_joins_lines_and_strips_markup() -> None:
    cues = parse_srt("1\n00:00:01,000 --> 00:00:02,000\n{\\an8}<i>첫째 줄</i>\n둘째 &amp; 줄\n")
    assert cues[0].text == "첫째 줄 둘째 & 줄"


def test_parse_srt_skips_broken_blocks_but_keeps_valid_ones() -> None:
    text = (
        "1\n00:00:01,000 --> 00:00:02,000\n정상\n\n"
        "2\n깨진 시간 줄\n내용\n\n"
        "3\n00:00:09,000 --> 00:00:05,000\n끝이 시작보다 빠름\n\n"
        "4\n00:00:10,000 --> 00:00:11,000\n정상 둘\n"
    )
    assert [c.text for c in parse_srt(text)] == ["정상", "정상 둘"]


@pytest.mark.parametrize(
    "timing",
    [
        "00:00:99,000 --> 00:01:40,000",  # 초가 59를 넘는다
        "00:60:00,000 --> 00:61:00,000",  # 분이 59를 넘는다
        "00:00:01,000 --> 00:00:60,000",
    ],
)
def test_parse_srt_rejects_out_of_range_clock_fields(timing: str) -> None:
    text = f"1\n{timing}\n범위 밖\n\n2\n00:00:01,000 --> 00:00:02,000\n정상\n"
    assert [c.text for c in parse_srt(text)] == ["정상"]


def test_load_reports_how_many_blocks_were_skipped(tmp_path: Path) -> None:
    f = tmp_path / "mixed.srt"
    f.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\n정상\n\n"
        "2\n깨진 시간 줄\n내용\n\n"
        "3\n00:00:09,000 --> 00:00:05,000\n역전\n\n"
        "4\n00:00:10,000 --> 00:00:11,000\n정상 둘\n\n"
        "5\n00:00:12,000 --> 00:00:13,000\n",  # 빈 자막은 건너뛴 것으로 세지 않는다
        encoding="utf-8",
    )
    loaded = load_subtitle_file(f)
    assert [c.text for c in loaded.cues] == ["정상", "정상 둘"]
    assert loaded.skipped_blocks == 2


def test_clean_file_reports_no_skipped_blocks(tmp_path: Path) -> None:
    f = tmp_path / "clean.srt"
    f.write_text(SRT, encoding="utf-8")
    assert load_subtitle_file(f).skipped_blocks == 0


def test_parse_srt_sorts_by_start() -> None:
    text = "1\n00:00:05,000 --> 00:00:06,000\nB\n\n2\n00:00:01,000 --> 00:00:02,000\nA\n"
    assert [c.text for c in parse_srt(text)] == ["A", "B"]


@pytest.mark.parametrize("text", ["", "   \n\n", "그냥 텍스트 파일입니다", "1\n깨짐\n"])
def test_parse_srt_without_any_valid_cue_raises(text: str) -> None:
    with pytest.raises(SubtitleParseError):
        parse_srt(text)


SMI = """<SAMI><BODY>
<SYNC Start=1000><P Class=KRCC>안녕하세요<br>여러분
<SYNC Start=3000><P Class=KRCC>&nbsp;
<SYNC Start=4000><P Class=KRCC>신청 마감: 3월 15일
<SYNC Start=6500><P Class=KRCC>&nbsp;
</BODY></SAMI>
"""


def test_parse_smi_uses_next_sync_as_end_and_nbsp_as_gap() -> None:
    cues = parse_smi(SMI)
    assert cues == [
        SubtitleCue(1.0, 3.0, "안녕하세요 여러분"),
        SubtitleCue(4.0, 6.5, "신청 마감: 3월 15일"),
    ]


def test_parse_smi_picks_first_language_class_with_text() -> None:
    text = (
        "<SAMI><BODY>"
        "<SYNC Start=1000><P Class=ENCC>&nbsp;<P Class=KRCC>한국어"
        "<SYNC Start=2000><P Class=ENCC>English<P Class=KRCC>&nbsp;"
        "<SYNC Start=3000><P Class=KRCC>&nbsp;"
        "</BODY></SAMI>"
    )
    # 텍스트가 처음 나타난 클래스(KRCC)만 사용한다.
    assert parse_smi(text) == [SubtitleCue(1.0, 2.0, "한국어")]


def test_parse_smi_last_cue_gets_default_duration() -> None:
    cues = parse_smi("<SAMI><BODY><SYNC Start=1000><P Class=KRCC>마지막</BODY></SAMI>")
    assert cues == [SubtitleCue(1.0, 3.0, "마지막")]


def test_parse_smi_without_cues_raises() -> None:
    with pytest.raises(SubtitleParseError):
        parse_smi("<SAMI><BODY></BODY></SAMI>")


@pytest.mark.parametrize(
    ("encoding", "name"),
    [("utf-8", "a.srt"), ("utf-8-sig", "b.srt"), ("cp949", "c.srt"), ("utf-16", "d.srt")],
)
def test_load_detects_encoding(tmp_path: Path, encoding: str, name: str) -> None:
    f = tmp_path / name
    f.write_bytes(SRT.encode(encoding))
    loaded = load_subtitle_file(f)
    assert [c.text for c in loaded.cues][0] == "신청은 3월 15일부터입니다."
    assert len(loaded.cues) == 2


def test_load_smi_in_cp949(tmp_path: Path) -> None:
    f = tmp_path / "한글 자막.smi"
    f.write_bytes(SMI.encode("cp949"))
    loaded = load_subtitle_file(f)
    assert loaded.format == "SMI"
    assert loaded.cues[0].text == "안녕하세요 여러분"


def test_load_rejects_undecodable_bytes(tmp_path: Path) -> None:
    f = tmp_path / "bad.srt"
    f.write_bytes(b"\xff\xfe\xfd" + b"\x81" * 10)
    with pytest.raises(SubtitleParseError):
        load_subtitle_file(f)


@pytest.mark.parametrize(
    "data",
    [b"\xef\xbb\xbf\xff", b"\xef\xbb\xbf\x81\x81", b"\xff\xfe\x00", b"\xfe\xff\xd8\x00"],
)
def test_corrupt_bom_input_raises_domain_error(data: bytes) -> None:
    with pytest.raises(SubtitleParseError):
        decode_bytes(data)


def test_load_rejects_unsupported_extension(tmp_path: Path) -> None:
    f = tmp_path / "sub.vtt"
    f.write_text("WEBVTT", encoding="utf-8")
    with pytest.raises(UnsupportedSubtitleError):
        load_subtitle_file(f)


def test_load_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SubtitleParseError):
        load_subtitle_file(tmp_path / "nope.srt")


def test_load_rejects_oversized_file(tmp_path: Path) -> None:
    f = tmp_path / "huge.srt"
    f.write_bytes(b"a" * (5 * 1024 * 1024 + 1))
    with pytest.raises(SubtitleParseError):
        load_subtitle_file(f)


def test_srt_roundtrip_preserves_korean_text(tmp_path: Path) -> None:
    cues = [SubtitleCue(1.0, 2.5, "안녕하세요"), SubtitleCue(61.25, 3725.5, "한 시간 넘는 구간")]
    out = write_srt(cues, tmp_path / "out" / "r.srt")
    assert load_subtitle_file(out).cues == cues


def test_format_srt_rounds_milliseconds_with_carry() -> None:
    text = format_srt([SubtitleCue(0.0, 1.9996, "올림")])
    assert "00:00:00,000 --> 00:00:02,000" in text
    assert text.startswith("1\n")

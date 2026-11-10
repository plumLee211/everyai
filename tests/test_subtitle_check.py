"""analysis/subtitle_check.py 테스트: M1 음성↔자막 판정 (값 비교 → 문장 분류 2단 구조)."""

from __future__ import annotations

import pytest

from barrierfree.analysis.subtitle_check import check_subtitles
from barrierfree.config import AnalysisConfig
from barrierfree.models import (
    IssueKind,
    IssueSource,
    SpeechSegment,
    SubtitleCue,
    Verdict,
)

CFG = AnalysisConfig()


def seg(
    start: float, end: float, text: str, logprob: float | None = -0.2, nsp: float | None = 0.01
) -> SpeechSegment:
    return SpeechSegment(start, end, text, logprob, nsp)


def cue(start: float, end: float, text: str) -> SubtitleCue:
    return SubtitleCue(start, end, text)


# ---------- planv2 M1 예시 4건 ----------


def test_example1_same_date_in_different_wording_is_normal() -> None:
    issues = check_subtitles(
        [seg(0, 3, "3월 15일부터 신청하실 수 있습니다")], [cue(0, 3, "신청 시작: 3월 15일")], CFG
    )
    assert issues == []


def test_example2_spoken_and_digit_time_are_equal() -> None:
    issues = check_subtitles(
        [seg(0, 3, "오후 세 시 삼십 분까지")], [cue(0, 3, "15:30까지")], CFG
    )
    assert issues == []


def test_example3_date_mismatch_is_error_with_evidence() -> None:
    issues = check_subtitles(
        [seg(0, 3, "신청 기간은 3월 15일까지입니다")], [cue(0, 3, "신청 마감: 3월 16일")], CFG
    )
    [issue] = issues
    assert issue.kind is IssueKind.VALUE_MISMATCH
    assert issue.verdict is Verdict.ERROR
    assert issue.source is IssueSource.M1
    assert "날짜" in issue.reason
    assert issue.speech_text == "신청 기간은 3월 15일까지입니다"
    assert issue.comparison_text == "신청 마감: 3월 16일"
    assert (issue.start_s, issue.end_s) == (0, 3)


def test_example4_expression_difference_is_review_required_not_error() -> None:
    issues = check_subtitles(
        [seg(0, 3, "신청은 홈페이지에서 가능합니다")], [cue(0, 3, "온라인 신청 가능")], CFG
    )
    [issue] = issues
    assert issue.kind is IssueKind.EXPRESSION_DIFFERENCE
    assert issue.verdict is Verdict.REVIEW_REQUIRED


# ---------- 값 비교 ----------


def test_value_missing_in_subtitle() -> None:
    [issue] = check_subtitles(
        [seg(0, 3, "문의는 02-1234-5678로 하세요")], [cue(0, 3, "문의하세요")], CFG
    )
    assert issue.kind is IssueKind.VALUE_MISSING
    assert issue.verdict is Verdict.ERROR
    assert "전화번호" in issue.reason


def test_values_matching_overrides_low_text_similarity() -> None:
    issues = check_subtitles(
        [seg(0, 4, "접수는 홈페이지를 통해 3월 15일에 시작됩니다")],
        [cue(0, 4, "3월 15일 접수")],
        CFG,
    )
    assert issues == []


def test_meridiem_omitted_in_subtitle_is_not_a_mismatch() -> None:
    assert check_subtitles([seg(0, 3, "오후 3시에 시작합니다")], [cue(0, 3, "3시 시작")], CFG) == []


def test_value_split_across_candidate_cues_is_found() -> None:
    issues = check_subtitles(
        [seg(0, 6, "신청은 3월 15일까지이며 문의는 홈페이지로")],
        [cue(0, 3, "신청 안내"), cue(3, 6, "마감 3월 15일")],
        CFG,
    )
    assert issues == []


def test_extra_values_only_in_subtitle_are_ignored() -> None:
    issues = check_subtitles(
        [seg(0, 3, "3월 15일까지 신청하세요")],
        [cue(0, 3, "3월 15일까지 신청, 문의 02-1234-5678")],
        CFG,
    )
    assert issues == []


@pytest.mark.parametrize("subtitle", ["신청: 3.15 ~ 3.20", "3/15 ~ 3/20 신청"])
def test_dot_and_slash_dates_in_subtitles_match_spoken_dates(subtitle: str) -> None:
    issues = check_subtitles(
        [seg(0, 5, "신청 기간은 삼월 십오일부터 삼월 이십일까지입니다")], [cue(0, 5, subtitle)], CFG
    )
    assert issues == []


def test_dot_style_date_with_different_day_is_still_a_mismatch() -> None:
    [issue] = check_subtitles(
        [seg(0, 3, "신청 마감은 삼월 십오일입니다")], [cue(0, 3, "마감 3.16")], CFG
    )
    assert issue.kind is IssueKind.VALUE_MISMATCH


@pytest.mark.parametrize("spoken", ["백분율을 확인하세요", "사분면을 확인하세요"])
def test_compound_nouns_do_not_create_phantom_values(spoken: str) -> None:
    issues = check_subtitles([seg(0, 3, spoken)], [cue(0, 3, spoken)], CFG)
    assert issues == []
    issues = check_subtitles([seg(0, 3, spoken)], [cue(0, 3, "그래프를 확인하세요")], CFG)
    assert all(i.kind is not IssueKind.VALUE_MISSING for i in issues)


def test_compound_money_mismatch_is_reported() -> None:
    [issue] = check_subtitles(
        [seg(0, 4, "3월 15일 지원금은 1억 5천만원입니다")],
        [cue(0, 4, "3월 15일 지원금 2억원")],
        CFG,
    )
    assert issue.kind is IssueKind.VALUE_MISMATCH
    assert "금액" in issue.reason


@pytest.mark.parametrize(
    ("spoken", "subtitle"),
    [
        ("증가율은 3.0%입니다", "증가율은 3%입니다"),
        ("30일 동안 신청하세요", "30일동안 신청하세요"),
        ("지원금은 5천만원입니다", "지원금 오천만 원"),
    ],
)
def test_notation_variants_of_same_value_are_not_issues(spoken: str, subtitle: str) -> None:
    assert check_subtitles([seg(0, 3, spoken)], [cue(0, 3, subtitle)], CFG) == []


def test_24_hour_time_in_speech_vs_opposite_meridiem_in_subtitle_is_mismatch() -> None:
    [issue] = check_subtitles(
        [seg(0, 3, "15시에 시작합니다")], [cue(0, 3, "오전 3시 시작")], CFG
    )
    assert issue.kind is IssueKind.VALUE_MISMATCH


def test_morning_speech_vs_afternoon_colon_subtitle_is_mismatch() -> None:
    [issue] = check_subtitles(
        [seg(0, 3, "오전 3시 시작합니다")], [cue(0, 3, "오후 3:00 시작")], CFG
    )
    assert issue.kind is IssueKind.VALUE_MISMATCH


# ---------- 자막 누락과 시간 창 ----------


def test_no_subtitle_at_all_is_subtitle_missing() -> None:
    [issue] = check_subtitles([seg(10, 12, "안내 영상을 소개합니다")], [], CFG)
    assert issue.kind is IssueKind.SUBTITLE_MISSING
    assert issue.verdict is Verdict.ERROR
    assert issue.comparison_text is None


def test_every_segment_without_subtitle_is_reported() -> None:
    issues = check_subtitles(
        [seg(0, 2, "첫 번째 안내입니다"), seg(5, 7, "두 번째 안내입니다")], [], CFG
    )
    assert [i.kind for i in issues] == [IssueKind.SUBTITLE_MISSING] * 2


def test_subtitle_shifted_by_3s_is_still_found_via_wide_window() -> None:
    issues = check_subtitles(
        [seg(10, 12, "오늘은 안내 영상을 소개합니다")],
        [cue(15, 17, "오늘은 안내 영상을 소개합니다")],
        CFG,
    )
    assert issues == []


def test_subtitle_shifted_by_8s_is_missing() -> None:
    [issue] = check_subtitles(
        [seg(10, 12, "오늘은 안내 영상을 소개합니다")],
        [cue(20, 22, "오늘은 안내 영상을 소개합니다")],
        CFG,
    )
    assert issue.kind is IssueKind.SUBTITLE_MISSING


# ---------- 노이즈 억제 ----------


def test_high_no_speech_probability_segments_are_skipped() -> None:
    assert check_subtitles([seg(0, 3, "배경 음악 환각 문장", nsp=0.9)], [], CFG) == []


def test_very_short_segments_are_skipped() -> None:
    assert check_subtitles([seg(0, 1, "네"), seg(1, 2, " . ")], [], CFG) == []


def test_no_speech_means_no_issues() -> None:
    assert check_subtitles([], [cue(0, 3, "자막만 있음")], CFG) == []


# ---------- STT 신뢰도: 판단 불가로 낮춘다 ----------


def test_uncertain_stt_downgrades_problem_to_undetermined() -> None:
    [issue] = check_subtitles(
        [seg(0, 3, "신청 기간은 3월 15일까지입니다", logprob=-1.5)],
        [cue(0, 3, "신청 마감: 3월 16일")],
        CFG,
    )
    assert issue.kind is IssueKind.STT_UNCERTAIN
    assert issue.verdict is Verdict.UNDETERMINED
    assert "음성 확인" in issue.reason
    assert issue.evidence["downgraded_from"] == "VALUE_MISMATCH"


def test_uncertain_stt_with_matching_subtitle_raises_nothing() -> None:
    issues = check_subtitles(
        [seg(0, 3, "신청 기간은 3월 15일까지입니다", logprob=-1.5)],
        [cue(0, 3, "신청 마감: 3월 15일")],
        CFG,
    )
    assert issues == []


def test_uncertain_stt_and_missing_subtitle_is_undetermined() -> None:
    [issue] = check_subtitles([seg(0, 3, "잘 들리지 않는 문장입니다", logprob=-2.0)], [], CFG)
    assert issue.verdict is Verdict.UNDETERMINED
    assert issue.evidence["downgraded_from"] == "SUBTITLE_MISSING"


# ---------- OCR 생성 자막: 오류로 판정하지 않는다 ----------


def test_ocr_generated_subtitles_never_produce_error_verdicts() -> None:
    issues = check_subtitles(
        [
            seg(0, 3, "신청 기간은 3월 15일까지입니다"),
            seg(10, 12, "안내 영상을 소개합니다"),
        ],
        [cue(0, 3, "신청 마감: 3월 16일")],
        CFG,
        ocr_generated=True,
    )
    assert [i.kind for i in issues] == [IssueKind.VALUE_MISMATCH, IssueKind.SUBTITLE_MISSING]
    assert {i.verdict for i in issues} == {Verdict.REVIEW_REQUIRED}


def test_ocr_mode_keeps_undetermined_for_uncertain_stt() -> None:
    [issue] = check_subtitles(
        [seg(0, 3, "신청 기간은 3월 15일까지입니다", logprob=-1.5)],
        [cue(0, 3, "신청 마감: 3월 16일")],
        CFG,
        ocr_generated=True,
    )
    assert issue.verdict is Verdict.UNDETERMINED


# ---------- 출력 형식 ----------


def test_issues_are_sorted_by_time_and_carry_reference_confidence() -> None:
    issues = check_subtitles(
        [seg(20, 22, "두 번째 안내입니다"), seg(5, 7, "첫 번째 안내입니다", logprob=-0.2)],
        [],
        CFG,
    )
    assert [i.start_s for i in issues] == [5, 20]
    assert issues[0].confidence == pytest.approx(0.819, abs=0.001)
    assert all(0.0 <= (i.confidence or 0.0) <= 1.0 for i in issues)


def test_confidence_is_none_when_stt_gives_no_logprob() -> None:
    [issue] = check_subtitles([seg(0, 3, "안내 영상을 소개합니다", logprob=None)], [], CFG)
    assert issue.confidence is None

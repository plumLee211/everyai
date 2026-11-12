"""analysis/screen_check.py 테스트: M2 화면 글자 ↔ 음성.

(docs/project/implementation-plan.md Task 11 케이스).
"""

from __future__ import annotations

import pytest

from barrierfree.analysis.screen_check import check_screen
from barrierfree.config import AnalysisConfig
from barrierfree.models import (
    IssueKind,
    IssueSource,
    ScreenText,
    SpeechSegment,
    SubtitleCue,
    Verdict,
)

CFG = AnalysisConfig()
DURATION = 300.0
CORNER = (0.85, 0.02, 0.12, 0.05)


def txt(
    text: str,
    start: float,
    end: float,
    conf: float = 0.9,
    box: tuple[float, float, float, float] | None = (0.1, 0.4, 0.4, 0.06),
) -> ScreenText:
    return ScreenText(text=text, start_s=start, end_s=end, confidence=conf, box=box)


def sp(start: float, end: float, text: str, logprob: float | None = -0.2) -> SpeechSegment:
    return SpeechSegment(start, end, text, logprob, 0.01)


def check(
    texts: list[ScreenText],
    speech: list[SpeechSegment],
    cues: list[SubtitleCue] | None = None,
    uncertain: list[ScreenText] | None = None,
    duration: float = DURATION,
) -> list:  # type: ignore[type-arg]
    return check_screen(texts, uncertain or [], speech, cues or [], duration, CFG)


PHONE = "문의 02-1234-5678"


# ---------- 정상 ----------


def test_value_spoken_while_shown_is_normal() -> None:
    assert check([txt(PHONE, 60, 65)], [sp(62, 64, "문의는 02-1234-5678로 하세요")]) == []


def test_value_spoken_within_near_window_is_normal() -> None:
    # 표시 종료(65) 후 5초 안인 70초에 말해도 가까운 창이다.
    assert check([txt(PHONE, 60, 65)], [sp(68, 70, "문의는 02-1234-5678입니다")]) == []


def test_text_without_important_information_is_ignored() -> None:
    assert check([txt("복지 서비스 안내", 10, 20)], [sp(10, 20, "안녕하세요")]) == []


def test_spoken_number_in_words_matches_screen_digits() -> None:
    screen = [txt("접수 3월 15일", 10, 15)]
    assert check(screen, [sp(11, 14, "삼월 십오일까지 접수합니다")]) == []


# ---------- 먼 창: 다른 시점에 언급 ----------


def test_value_spoken_only_far_away_is_review_required_with_gap() -> None:
    [issue] = check([txt(PHONE, 60, 65)], [sp(100, 102, "문의는 02-1234-5678입니다")])
    assert issue.kind is IssueKind.MENTIONED_ELSEWHERE
    assert issue.verdict is Verdict.REVIEW_REQUIRED
    assert issue.source is IssueSource.M2
    assert issue.evidence["gap_s"] == pytest.approx(35.0)
    assert "35초" in issue.reason


# ---------- 화면 전용 / 값 불일치 ----------


def test_value_never_spoken_is_screen_only_error() -> None:
    [issue] = check([txt(PHONE, 60, 65)], [sp(62, 64, "궁금한 점은 담당 부서로 문의하세요")])
    assert issue.kind is IssueKind.SCREEN_ONLY_INFO
    assert issue.verdict is Verdict.ERROR
    assert "전화번호" in issue.reason
    assert issue.comparison_text == PHONE
    assert issue.speech_text == "궁금한 점은 담당 부서로 문의하세요"
    assert (issue.start_s, issue.end_s) == (60, 65)


def test_screen_only_when_there_is_no_speech_nearby() -> None:
    [issue] = check([txt(PHONE, 60, 65)], [sp(200, 202, "다른 이야기")])
    assert issue.kind is IssueKind.SCREEN_ONLY_INFO
    assert issue.speech_text is None


def test_different_value_of_same_kind_nearby_is_value_mismatch() -> None:
    [issue] = check([txt(PHONE, 60, 65)], [sp(62, 64, "문의는 02-1234-5679로 하세요")])
    assert issue.kind is IssueKind.SCREEN_VALUE_MISMATCH
    assert issue.verdict is Verdict.ERROR
    assert "5678" in issue.reason and "5679" in issue.reason


def test_value_spoken_elsewhere_beats_a_different_nearby_value() -> None:
    # 가까이서 다른 번호를 말했어도, 화면의 번호가 다른 곳에서 말해졌다면 단정하지 않는다.
    speech = [
        sp(62, 64, "대표 번호는 02-9999-0000입니다"),
        sp(150, 152, "02-1234-5678로 전화하세요"),
    ]
    [issue] = check([txt(PHONE, 60, 65)], speech)
    assert issue.kind is IssueKind.MENTIONED_ELSEWHERE


def test_graph_numbers_missing_from_speech_are_reported_once() -> None:
    screen = [txt("2025년 30%  2026년 60%", 100, 110)]
    [issue] = check(screen, [sp(101, 108, "매출이 상당히 증가했습니다")])
    assert issue.kind is IssueKind.SCREEN_ONLY_INFO
    missing = {m["raw"] for m in issue.evidence["unmatched"]}  # type: ignore[attr-defined]
    assert any("30%" in raw for raw in missing) and any("60%" in raw for raw in missing)


# ---------- 제외 규칙 ----------


def test_text_identical_to_overlapping_subtitle_is_excluded() -> None:
    cues = [SubtitleCue(60, 65, "문의 02-1234-5678")]
    assert check([txt(PHONE, 60, 65)], [sp(62, 64, "문의하세요")], cues) == []


def test_subtitle_that_merely_contains_the_text_is_excluded() -> None:
    cues = [SubtitleCue(60, 65, "자세한 문의 02-1234-5678 로 연락 주세요")]
    assert check([txt(PHONE, 60, 65)], [sp(62, 64, "문의하세요")], cues) == []


def test_similar_subtitle_with_a_different_number_does_not_hide_the_screen_mismatch() -> None:
    # 화면은 5678, 음성과 자막은 5679. 글자 유사도가 높아도 값이 다르면 점검에서 빼면 안 된다.
    screen = [txt("문의 02-1234-5678", 10, 15)]
    cues = [SubtitleCue(10, 15, "문의 02-1234-5679")]
    speech = [sp(11, 14, "문의는 02-1234-5679입니다")]
    [issue] = check(screen, speech, cues)
    assert issue.kind is IssueKind.SCREEN_VALUE_MISMATCH


@pytest.mark.parametrize(
    ("screen_text", "cue_text"),
    [
        ("접수 3월 15일", "접수 3월 16일"),
        ("참가비 5천 원", "참가비 6천 원"),
        ("증가율 30%", "증가율 60%"),
    ],
)
def test_a_subtitle_with_different_values_never_excludes_the_screen_text(
    screen_text: str, cue_text: str
) -> None:
    issues = check([txt(screen_text, 10, 15)], [sp(11, 14, "다른 이야기")],
                   [SubtitleCue(10, 15, cue_text)])
    assert issues, "값이 다른 자막은 화면 글자를 점검 대상에서 빼지 못한다"


def test_subtitle_with_the_same_values_still_excludes_it() -> None:
    cues = [SubtitleCue(10, 15, "자세한 문의 02-1234-5678 로 연락 주세요")]
    assert check([txt(PHONE, 10, 15)], [sp(11, 14, "문의하세요")], cues) == []


def test_subtitle_far_in_time_does_not_exclude() -> None:
    cues = [SubtitleCue(200, 205, "문의 02-1234-5678")]
    [issue] = check([txt(PHONE, 60, 65)], [sp(62, 64, "문의하세요")], cues)
    assert issue.kind is IssueKind.SCREEN_ONLY_INFO


def test_small_corner_logo_without_values_is_ignored_even_if_always_visible() -> None:
    logo = txt("서울복지재단", 0, 300, box=CORNER)
    assert check([logo], [sp(10, 12, "안녕하세요")]) == []


# ---------- 영상 내내 표시 ----------


def test_always_visible_phone_never_spoken_is_one_review_required_issue() -> None:
    screen = [txt("문의 02-1234-5678", 0, 290, box=CORNER)]
    [issue] = check(screen, [sp(10, 12, "안녕하세요 반갑습니다")])
    assert issue.kind is IssueKind.ALWAYS_ON_SCREEN
    assert issue.verdict is Verdict.REVIEW_REQUIRED
    assert "내내" in issue.reason


def test_always_visible_phone_spoken_anywhere_is_fine() -> None:
    screen = [txt("문의 02-1234-5678", 0, 290, box=CORNER)]
    assert check(screen, [sp(250, 253, "문의는 02-1234-5678입니다")]) == []


# ---------- 신뢰도 ----------


def test_uncertain_ocr_with_unspoken_value_is_undetermined() -> None:
    blurry = txt("문의 02-1234-5678", 60, 65, conf=0.3)
    [issue] = check([], [sp(62, 64, "문의하세요")], uncertain=[blurry])
    assert issue.kind is IssueKind.OCR_UNCERTAIN
    assert issue.verdict is Verdict.UNDETERMINED
    assert "화면 확인" in issue.reason


def test_uncertain_ocr_whose_value_is_spoken_is_silent() -> None:
    blurry = txt("문의 02-1234-5678", 60, 65, conf=0.3)
    assert check([], [sp(62, 64, "문의는 02-1234-5678입니다")], uncertain=[blurry]) == []


def test_uncertain_ocr_without_values_is_silent() -> None:
    assert check([], [], uncertain=[txt("흐릿한 글자", 1, 2, conf=0.2)]) == []


def test_uncertain_stt_nearby_downgrades_error_to_undetermined() -> None:
    [issue] = check([txt(PHONE, 60, 65)], [sp(62, 64, "문의는 담당 부서로", logprob=-1.6)])
    assert issue.kind is IssueKind.STT_UNCERTAIN
    assert issue.verdict is Verdict.UNDETERMINED
    assert issue.evidence["downgraded_from"] == "SCREEN_ONLY_INFO"


# ---------- 출력 형식 ----------


def test_issues_are_sorted_and_carry_ocr_confidence() -> None:
    screen = [txt("접수 4월 1일", 200, 205, conf=0.8), txt(PHONE, 60, 65, conf=0.7)]
    issues = check(screen, [sp(300, 301, "다른 이야기")])
    assert [i.start_s for i in issues] == [60, 200]
    assert issues[0].confidence == pytest.approx(0.7)


def test_no_inputs_no_issues() -> None:
    assert check([], []) == []

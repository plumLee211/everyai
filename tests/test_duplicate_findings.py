"""화면에 새겨진 자막의 같은 문제를 음성↔자막(M1)과 화면 정보(M2) 비교가 두 번 지적하지 않는다."""

from __future__ import annotations

import pytest

from barrierfree.analysis.duplicates import merge_screen_duplicates
from barrierfree.analysis.screen_check import check_screen
from barrierfree.analysis.subtitle_check import check_subtitles
from barrierfree.config import AnalysisConfig
from barrierfree.models import (
    Issue,
    IssueKind,
    ScreenText,
    SpeechSegment,
    SubtitleCue,
    Verdict,
)

CFG = AnalysisConfig()


def _both(spoken: str, caption: str, screen: str | None = None, *,
          cue: str | None = None) -> tuple[list[Issue], list[Issue]]:
    speech = [SpeechSegment(0.0, 4.0, spoken, -0.2, 0.0)]
    cues = [SubtitleCue(0.0, 4.0, cue or caption)]
    shown = ScreenText(screen or caption, 0.0, 4.0, 0.9, (0.2, 0.85, 0.6, 0.06))
    return (
        check_subtitles(speech, cues, CFG),
        check_screen([shown], [], speech, cues, 60.0, CFG),
    )


def test_the_same_wrong_date_is_reported_once_with_the_screen_attached() -> None:
    m1, m2 = _both("접수는 3월 2일에 마감합니다", "접수 마감 3월 9일")
    assert len(m1) == 1 and len(m2) == 1  # 합치기 전에는 두 번이다
    merged_m1, merged_m2 = merge_screen_duplicates(m1, m2)
    [issue] = merged_m1
    assert merged_m2 == []
    assert issue.id == m1[0].id and issue.verdict is m1[0].verdict
    [screen] = issue.evidence["screens"]  # type: ignore[misc]
    assert screen["text"] == "접수 마감 3월 9일" and screen["kind"] == m2[0].kind.value
    assert "화면 글자 '접수 마감 3월 9일'" in issue.reason


def test_findings_about_the_same_caption_become_one_and_lose_nothing() -> None:
    # 화면 지적에는 음성↔자막 지적에 없는 전화번호가 있다. 합치되 그 사유를 그대로 남긴다.
    caption = "접수 마감 3월 9일 문의 02-1234-5678"
    m1, m2 = _both("접수는 3월 2일에 마감합니다", caption)
    [issue], rest = merge_screen_duplicates(m1, m2)
    assert rest == []
    assert caption in issue.reason and m2[0].reason in issue.reason


def test_a_caption_flagged_only_for_wording_also_takes_the_screen_finding() -> None:
    m1, m2 = _both("행사에 오시면 선물을 드려요", "행사 선물 5000원 증정")
    assert m1[0].kind is IssueKind.EXPRESSION_DIFFERENCE
    [issue], rest = merge_screen_duplicates(m1, m2)
    assert rest == [] and "5000원" in issue.reason


def test_two_readings_of_the_same_caption_are_both_recorded() -> None:
    m1, first = _both("접수는 3월 2일에 마감합니다", "접수 마감 3월 9일")
    _, second = _both("접수는 3월 2일에 마감합니다", "접수 마감 3월 9일")
    [issue], rest = merge_screen_duplicates(m1, first + second)
    screens = issue.evidence["screens"]
    assert rest == [] and len(screens) == 2  # type: ignore[arg-type]
    assert issue.reason.count("화면 글자 '접수 마감 3월 9일'") == 1  # 같은 문장은 한 번만


def test_a_value_mentioned_only_elsewhere_on_the_same_caption_is_also_merged() -> None:
    m1, m2 = _both("행사에 오시면 선물을 드려요", "행사 선물 5000원 증정")
    m2[0].kind = IssueKind.MENTIONED_ELSEWHERE
    _, rest = merge_screen_duplicates(m1, m2)
    assert rest == []


def test_another_caption_at_the_same_time_with_other_values_is_kept() -> None:
    m1, _ = _both("접수는 3월 2일에 마감합니다", "접수 마감 3월 9일")
    _, m2 = _both("접수는 3월 2일에 마감합니다", "시민회관 대강당 4월 5일")
    assert merge_screen_duplicates(m1, m2) == (m1, m2)


def test_a_screen_finding_not_in_the_subtitle_is_kept() -> None:
    m1, m2 = _both("접수는 3월 2일에 마감합니다", "접수 마감 3월 9일",
                   screen="현장 접수 4월 5일", cue="접수 마감 3월 9일")
    assert merge_screen_duplicates(m1, m2) == (m1, m2)


def test_findings_at_different_times_are_not_merged() -> None:
    m1, _ = _both("접수는 3월 2일에 마감합니다", "접수 마감 3월 9일")
    later = ScreenText("접수 마감 3월 9일", 40.0, 44.0, 0.9, None)
    m2 = check_screen([later], [], [SpeechSegment(40, 44, "안녕하세요", -0.2, 0.0)],
                      [SubtitleCue(40, 44, "접수 마감 3월 9일")], 60.0, CFG)
    assert merge_screen_duplicates(m1, m2) == (m1, m2)


@pytest.mark.parametrize("kind", [IssueKind.ALWAYS_ON_SCREEN, IssueKind.OCR_UNCERTAIN])
def test_other_screen_findings_are_never_merged(kind: IssueKind) -> None:
    m1, m2 = _both("접수는 3월 2일에 마감합니다", "접수 마감 3월 9일")
    m2[0].kind = kind
    assert merge_screen_duplicates(m1, m2) == (m1, m2)


def test_a_downgraded_screen_finding_is_not_merged() -> None:
    m1, m2 = _both("접수는 3월 2일에 마감합니다", "접수 마감 3월 9일")
    m2[0].kind, m2[0].verdict = IssueKind.STT_UNCERTAIN, Verdict.UNDETERMINED
    assert merge_screen_duplicates(m1, m2) == (m1, m2)

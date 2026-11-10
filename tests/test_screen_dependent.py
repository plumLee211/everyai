"""analysis/screen_dependent.py 테스트: M2-2 화면 의존 표현 구간 안내(규칙 기반)."""

from __future__ import annotations

import pytest

from barrierfree.analysis.screen_dependent import check_screen_dependent
from barrierfree.config import AnalysisConfig
from barrierfree.models import Issue, IssueKind, IssueSource, SpeechSegment, Verdict

CFG = AnalysisConfig()


def sp(start: float, end: float, text: str, nsp: float | None = 0.01) -> SpeechSegment:
    return SpeechSegment(start, end, text, -0.2, nsp)


def existing(start: float, end: float) -> Issue:
    return Issue(
        source=IssueSource.M2,
        kind=IssueKind.SCREEN_ONLY_INFO,
        verdict=Verdict.ERROR,
        start_s=start,
        end_s=end,
        reason="화면의 수치 '30%'을(를) 음성에서 확인하지 못했습니다.",
    )


@pytest.mark.parametrize(
    "text",
    [
        "보시는 것처럼 상당히 증가했습니다",
        "이쪽으로 이동하시면 됩니다",
        "보시다시피 신청 절차는 간단합니다",
        "다음과 같이 진행됩니다",
        "화면에서 확인하실 수 있습니다",
        "보시는 바와 같이 변경되었습니다",
    ],
)
def test_screen_dependent_phrases_are_flagged_for_review(text: str) -> None:
    [issue] = check_screen_dependent([sp(10, 14, text)], [], CFG)
    assert issue.kind is IssueKind.SCREEN_DEPENDENT
    assert issue.source is IssueSource.M2_SCREEN_DEPENDENT
    assert issue.verdict is Verdict.REVIEW_REQUIRED  # 누락을 판정하지 않고 확인 대상을 안내한다
    assert "화면" in issue.reason and "화면해설" in issue.reason
    assert issue.speech_text == text
    assert (issue.start_s, issue.end_s) == (10, 14)


@pytest.mark.parametrize(
    "text",
    [
        "여기서 만나요",
        "이렇게 해요",
        "그렇게 하세요",
        "안녕하세요 반갑습니다",
        "신청은 홈페이지에서",
    ],
)
def test_everyday_expressions_are_not_flagged(text: str) -> None:
    assert check_screen_dependent([sp(0, 3, text)], [], CFG) == []


def test_spacing_variants_are_matched() -> None:
    assert len(check_screen_dependent([sp(0, 3, "보시는것처럼 늘었습니다")], [], CFG)) == 1
    assert len(check_screen_dependent([sp(0, 3, "이 쪽으로 오세요")], [], CFG)) == 1


def test_multiple_phrases_in_one_segment_make_one_issue() -> None:
    [issue] = check_screen_dependent([sp(0, 5, "보시는 것처럼 이쪽으로 이동하세요")], [], CFG)
    assert set(issue.evidence["phrases"]) == {"보시는 것처럼", "이쪽으로"}  # type: ignore[arg-type]


def test_phrases_come_from_config() -> None:
    cfg = AnalysisConfig(screen_dependent_phrases=("저기 보이는",))
    assert len(check_screen_dependent([sp(0, 3, "저기 보이는 건물입니다")], [], cfg)) == 1
    assert check_screen_dependent([sp(0, 3, "보시는 것처럼")], [], cfg) == []


def test_noise_segments_are_skipped() -> None:
    assert check_screen_dependent([sp(0, 3, "보시는 것처럼 환각", nsp=0.9)], [], CFG) == []


def test_issues_are_sorted_by_time() -> None:
    issues = check_screen_dependent(
        [sp(20, 22, "이쪽으로 오세요"), sp(5, 7, "보시다시피 간단합니다")], [], CFG
    )
    assert [i.start_s for i in issues] == [5, 20]


# ---------- M2 지적과의 중복 ----------


def test_overlapping_existing_m2_issue_gets_the_phrase_instead_of_a_duplicate() -> None:
    prior = existing(8, 16)
    new = check_screen_dependent([sp(10, 14, "보시는 것처럼 30퍼센트 늘었습니다")], [prior], CFG)
    assert new == []
    assert prior.evidence["screen_dependent_phrases"] == ["보시는 것처럼"]
    assert "보시는 것처럼" in prior.reason
    assert prior.verdict is Verdict.ERROR  # 기존 판정은 바뀌지 않는다


def test_non_overlapping_existing_issue_does_not_absorb_the_phrase() -> None:
    prior = existing(100, 110)
    [issue] = check_screen_dependent([sp(10, 14, "이쪽으로 오세요")], [prior], CFG)
    assert issue.kind is IssueKind.SCREEN_DEPENDENT
    assert "screen_dependent_phrases" not in prior.evidence


def test_only_m2_issues_absorb_phrases() -> None:
    m1 = Issue(
        source=IssueSource.M1,
        kind=IssueKind.VALUE_MISMATCH,
        verdict=Verdict.ERROR,
        start_s=8,
        end_s=16,
        reason="r",
    )
    [issue] = check_screen_dependent([sp(10, 14, "이쪽으로 오세요")], [m1], CFG)
    assert issue.kind is IssueKind.SCREEN_DEPENDENT
    assert "screen_dependent_phrases" not in m1.evidence


def test_repeated_calls_do_not_duplicate_appended_phrases() -> None:
    prior = existing(8, 16)
    segment = sp(10, 14, "보시는 것처럼 늘었습니다")
    check_screen_dependent([segment], [prior], CFG)
    check_screen_dependent([segment], [prior], CFG)
    assert prior.evidence["screen_dependent_phrases"] == ["보시는 것처럼"]
    assert prior.reason.count("보시는 것처럼") == 1

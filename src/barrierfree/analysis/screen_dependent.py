"""M2-2: 음성이 화면을 전제로 말하는 구간("보시는 것처럼", "이쪽으로")을 찾아 안내한다.

화면해설 누락을 *판정*하지 않고 사람이 *확인할 대상*을 안내한다(항상 검수 필요). 장면은 분석하지
않고 받아쓰기 텍스트만 쓴다. 표현 목록은 `AnalysisConfig`에 두고 좁게 시작한다. "여기서",
"이렇게" 같은 일상 표현은 목록에 넣지 않는다.
"""

from __future__ import annotations

import re

from barrierfree.analysis.normalize import normalize_text
from barrierfree.config import AnalysisConfig
from barrierfree.models import Issue, IssueKind, IssueSource, SpeechSegment, Verdict

_REASON = "화면을 봐야 이해되는 구간입니다. 화면해설이 필요한지 확인하세요."


def _squash(text: str) -> str:
    """띄어쓰기 차이("보시는것처럼" / "보시는 것처럼")를 흡수하려고 공백을 지운다."""
    return re.sub(r"\s+", "", text)


def _overlaps(issue: Issue, segment: SpeechSegment) -> bool:
    return issue.start_s <= segment.end_s and issue.end_s >= segment.start_s


def _absorb(issue: Issue, phrases: list[str]) -> None:
    """같은 구간의 기존 M2 지적에 화면 의존 표현을 덧붙인다(판정은 바꾸지 않는다)."""
    recorded = issue.evidence.setdefault("screen_dependent_phrases", [])
    assert isinstance(recorded, list)
    added = [p for p in phrases if p not in recorded]
    if not added:
        return
    recorded.extend(added)
    quoted = ", ".join(f"'{p}'" for p in added)
    issue.reason += f" 이 구간에는 {quoted} 같은 화면 의존 표현도 있어 화면 확인이 필요합니다."


def check_screen_dependent(
    speech: list[SpeechSegment], existing_issues: list[Issue], config: AnalysisConfig
) -> list[Issue]:
    """새로 올릴 지적을 돌려준다.

    같은 구간에 M2 지적이 있으면 새로 만들지 않고 그 지적에 덧붙인다.

    주의: `existing_issues` 중 M2 지적의 `evidence`와 `reason`을 제자리에서 갱신한다.
    """
    phrases = [(p, _squash(p)) for p in config.screen_dependent_phrases]
    m2_issues = [i for i in existing_issues if i.source is IssueSource.M2]
    created: list[Issue] = []

    for segment in sorted(speech, key=lambda s: (s.start_s, s.end_s)):
        no_speech = segment.no_speech_prob
        if no_speech is not None and no_speech > config.no_speech_prob_max:
            continue
        if len(normalize_text(segment.text)) < config.min_speech_chars:
            continue
        squashed = _squash(segment.text)
        found = [original for original, key in phrases if key and key in squashed]
        if not found:
            continue

        host = next((i for i in m2_issues if _overlaps(i, segment)), None)
        if host is not None:
            _absorb(host, found)
            continue
        created.append(
            Issue(
                source=IssueSource.M2_SCREEN_DEPENDENT,
                kind=IssueKind.SCREEN_DEPENDENT,
                verdict=Verdict.REVIEW_REQUIRED,
                start_s=segment.start_s,
                end_s=segment.end_s,
                reason=_REASON,
                speech_text=segment.text,
                evidence={"phrases": found},
            )
        )
    return created

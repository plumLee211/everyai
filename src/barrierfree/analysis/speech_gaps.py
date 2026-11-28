"""받아쓰기가 말소리를 놓쳤을 수 있는 구간을 찾고, 그 구간에 기대는 판정을 낮춘다.

받아쓰기 모델이 문장을 통째로 빠뜨려도 신뢰도 값은 높게 나올 수 있다. 말소리 탐지(VAD)가 말이
있다고 본 시간과 받아쓴 결과를 비교해 두 모습을 찾는다.
1. 말소리가 있는데 덮는 받아쓰기 구간이 없다.
2. 구간은 있지만 그 안의 말소리 시간에 비해 글자가 너무 적다(구간이 늘어나 누락을 가린다).

**의심일 뿐이다.** 말소리 탐지가 말이 아닌 소리를 말로 볼 수 있고, 여기서 걸리지 않는 누락도 있다.
기준값은 누락이 확인된 영상 한 편에서 골랐으며 검증하지 않았다.
"""

from __future__ import annotations

from dataclasses import replace

from barrierfree.config import AnalysisConfig
from barrierfree.models import Issue, IssueKind, IssueSource, SpeechSegment, Verdict

Span = tuple[float, float]

_REASON = (
    "자동 판단 불가: 음성 확인 필요 (이 구간의 음성은 일부를 인식하지 못했을 수 있습니다)."
)
# 화면 정보 비교 가운데 '가까운 음성에서 확인하지 못함'에 기대는 유형
_M2_KINDS = {
    IssueKind.SCREEN_ONLY_INFO,
    IssueKind.SCREEN_VALUE_MISMATCH,
    IssueKind.MENTIONED_ELSEWHERE,
}


def _merge(spans: list[Span]) -> list[Span]:
    merged: list[Span] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _covered(start: float, end: float, spans: list[Span]) -> float:
    return sum(max(0.0, min(end, y) - max(start, x)) for x, y in spans)


def suspected_gaps(
    speech_spans: list[Span], segments: list[SpeechSegment], config: AnalysisConfig
) -> list[Span]:
    """받아쓰기가 놓쳤을 수 있는 구간(시간순, 겹치지 않음)."""
    spoken = _merge(speech_spans)
    written = _merge([(s.start_s, s.end_s) for s in segments])
    flags: list[Span] = []
    for start, end in spoken:
        cursor = start
        for x, y in written:
            if y <= cursor or x >= end:
                continue
            if x - cursor >= config.stt_gap_min_s:
                flags.append((cursor, x))
            cursor = max(cursor, y)
        if end - cursor >= config.stt_gap_min_s:
            flags.append((cursor, end))
    for segment in segments:
        seconds = _covered(segment.start_s, segment.end_s, spoken)
        if seconds < config.stt_gap_min_s:
            continue
        characters = len("".join(segment.text.split()))
        if characters / seconds < config.stt_sparse_chars_per_s:
            flags.append((segment.start_s, segment.end_s))
    return _merge(flags)


def _relies_on_gap(issue: Issue, gaps: list[Span], config: AnalysisConfig) -> bool:
    if issue.source is IssueSource.M1 and issue.kind is not IssueKind.STT_UNCERTAIN:
        margin = 0.0
    elif issue.source is IssueSource.M2 and issue.kind in _M2_KINDS:
        margin = config.screen_near_window_s
    else:
        return False
    return any(issue.start_s - margin < end and issue.end_s + margin > start for start, end in gaps)


def downgrade_in_gaps(
    issues: list[Issue], gaps: list[Span], config: AnalysisConfig
) -> list[Issue]:
    """누락 의심 구간의 음성에 기대는 지적을 '판단 불가'로 낮춘다. 지적은 지우지 않는다."""
    result: list[Issue] = []
    for issue in issues:
        if not _relies_on_gap(issue, gaps, config):
            result.append(issue)
            continue
        evidence = dict(issue.evidence)
        evidence.update(
            downgraded_from=issue.kind.value,
            original_reason=issue.reason,
            speech_maybe_missed=True,
        )
        result.append(
            replace(
                issue,
                kind=IssueKind.STT_UNCERTAIN,
                verdict=Verdict.UNDETERMINED,
                reason=_REASON,
                evidence=evidence,
            )
        )
    return result

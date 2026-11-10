"""M1: 음성 ↔ 자막 판정. 값 비교(1단)로 오류를 정하고, 문장 분류(2단)로 검수 필요를 정한다.

글자 일치율은 판정 기준이 아니라 보조 신호다. 자막은 발화를 그대로 옮기지 않을 수 있어서,
핵심 값이 모두 일치하면 글자 차이가 커도 정상이다(planv2.md).

STT 신뢰도가 낮은 구간은 판정을 끝까지 수행한 뒤, 문제가 나올 경우에만 "판단 불가"로 낮춘다.
음성 인식이 불확실하면 자막이 틀렸다고 말할 근거도 부족하기 때문이다. 문제가 없는 구간까지
판단 불가로 만들면 검수 목록이 부풀어 정밀도를 스스로 깎는다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from barrierfree.analysis.entities import (
    ENTITY_KIND_LABELS,
    Entity,
    compare_entities,
    extract_entities,
)
from barrierfree.analysis.normalize import normalize_text
from barrierfree.config import AnalysisConfig
from barrierfree.models import (
    Issue,
    IssueKind,
    IssueSource,
    SpeechSegment,
    SubtitleCue,
    Verdict,
)

_MIN_MATCH_BLOCK = 2  # 한 글자 우연 일치(조사·어미)는 일치율에 세지 않는다


@dataclass(frozen=True)
class _Finding:
    kind: IssueKind
    verdict: Verdict
    reason: str
    evidence: dict[str, object] = field(default_factory=dict)


def _candidates(
    segment: SpeechSegment, cues: list[SubtitleCue], config: AnalysisConfig
) -> list[SubtitleCue]:
    """[시작-창, 끝+창]과 겹치는 자막. 없으면 넓은 창으로 한 번 더 찾는다."""
    for window in (config.candidate_window_s, config.candidate_window_wide_s):
        found = [
            c
            for c in cues
            if c.end_s >= segment.start_s - window and c.start_s <= segment.end_s + window
        ]
        if found:
            return found
    return []


def _coverage(speech: str, subtitle: str) -> float:
    """음성 글자 중 자막과 이어서 일치하는 비율(2글자 이상 블록만)."""
    if not speech:
        return 1.0
    blocks = SequenceMatcher(None, speech, subtitle, autojunk=False).get_matching_blocks()
    return sum(b.size for b in blocks if b.size >= _MIN_MATCH_BLOCK) / len(speech)


def _entity_payload(entity: Entity) -> dict[str, str]:
    return {"kind": entity.kind.value, "value": entity.value, "raw": entity.raw}


def _analyze(
    segment: SpeechSegment, cues: list[SubtitleCue], config: AnalysisConfig
) -> _Finding | None:
    candidates = _candidates(segment, cues, config)
    if not candidates:
        return _Finding(
            IssueKind.SUBTITLE_MISSING,
            Verdict.ERROR,
            "이 구간의 음성에 대응하는 자막을 찾지 못했습니다.",
        )

    target_text = " ".join(c.text for c in candidates)
    source_entities = extract_entities(segment.text)
    # 자막의 '3.15', '3/15' 표기를 날짜로 읽는다(자막에 추가로 읽힌 값은 무시되므로 안전하다).
    diff = compare_entities(source_entities, extract_entities(target_text, lenient_dates=True))

    if diff.mismatched or diff.missing:
        parts: list[str] = []
        if diff.mismatched:
            pairs = [
                f"{ENTITY_KIND_LABELS[s.kind]}: 음성 '{s.raw}' / 자막 '{t.raw}'"
                for s, t in diff.mismatched
            ]
            parts.append("음성과 자막의 주요 정보가 다릅니다 — " + "; ".join(pairs))
        if diff.missing:
            names = ", ".join(f"{ENTITY_KIND_LABELS[e.kind]} '{e.raw}'" for e in diff.missing)
            parts.append(f"음성의 {names}이(가) 자막에서 확인되지 않습니다.")
        return _Finding(
            IssueKind.VALUE_MISMATCH if diff.mismatched else IssueKind.VALUE_MISSING,
            Verdict.ERROR,
            " / ".join(parts),
            {
                "speech_entities": [_entity_payload(e) for e in source_entities],
                "mismatched": [
                    {"speech": _entity_payload(s), "subtitle": _entity_payload(t)}
                    for s, t in diff.mismatched
                ],
                "missing": [_entity_payload(e) for e in diff.missing],
            },
        )

    if source_entities:
        return None  # 핵심 값이 모두 일치하면 글자 일치율이 낮아도 정상이다.

    coverage = _coverage(normalize_text(segment.text), normalize_text(target_text))
    if coverage < config.expression_similarity_min:
        return _Finding(
            IssueKind.EXPRESSION_DIFFERENCE,
            Verdict.REVIEW_REQUIRED,
            "음성과 자막의 표현이 다릅니다. 같은 내용을 전달하는지 확인하세요.",
            {"coverage": round(coverage, 3)},
        )
    return None


def _confidence(segment: SpeechSegment) -> float | None:
    """STT 평균 로그확률에서 만든 참고값. 오류 확률이 아니다."""
    if segment.avg_logprob is None:
        return None
    return round(min(1.0, max(0.0, math.exp(segment.avg_logprob))), 3)


def check_subtitles(
    speech: list[SpeechSegment],
    cues: list[SubtitleCue],
    config: AnalysisConfig,
    *,
    ocr_generated: bool = False,
) -> list[Issue]:
    """음성 구간마다 자막과 대조해 지적 목록을 만든다(시간순).

    `ocr_generated=True`이면 자막 자체에 OCR 오류가 섞여 있으므로 오류(ERROR)로 판정하지 않고
    모두 검수 필요로 표시한다.
    """
    issues: list[Issue] = []
    for segment in sorted(speech, key=lambda s: (s.start_s, s.end_s)):
        if len(normalize_text(segment.text)) < config.min_speech_chars:
            continue
        no_speech = segment.no_speech_prob
        if no_speech is not None and no_speech > config.no_speech_prob_max:
            continue
        finding = _analyze(segment, cues, config)
        if finding is None:
            continue

        evidence = dict(finding.evidence)
        kind, verdict, reason = finding.kind, finding.verdict, finding.reason
        uncertain = (
            segment.avg_logprob is not None and segment.avg_logprob < config.stt_avg_logprob_min
        )
        if uncertain:
            evidence["downgraded_from"] = kind.value
            evidence["original_reason"] = reason
            kind, verdict = IssueKind.STT_UNCERTAIN, Verdict.UNDETERMINED
            reason = "자동 판단 불가: 음성 확인 필요 (음성 인식이 불확실합니다)."
        elif ocr_generated and verdict is Verdict.ERROR:
            verdict = Verdict.REVIEW_REQUIRED
            evidence["ocr_generated_subtitles"] = True

        candidates = _candidates(segment, cues, config)
        issues.append(
            Issue(
                source=IssueSource.M1,
                kind=kind,
                verdict=verdict,
                start_s=segment.start_s,
                end_s=segment.end_s,
                reason=reason,
                speech_text=segment.text,
                comparison_text=" ".join(c.text for c in candidates) if candidates else None,
                confidence=_confidence(segment),
                evidence=evidence,
            )
        )
    return issues

"""M2: 화면 글자 ↔ 음성. 화면에 표시된 중요 정보가 음성으로 전달되는지 점검한다.

점검 범위는 '화면에 표시되는 주요 문자 정보의 음성 전달'로 한정한다. 화면해설 누락(행동,
몸짓, 그래프의 의미)은 판정하지 않는다. 판정 순서(docs/project/implementation-plan.md):
1. 가까운 창(표시 구간 ±5초)에서 값이 말해지면 정상.
2. 영상 다른 곳에서만 말했으면 "다른 시점에 언급됨"(검수 필요) + 표시와 설명 시점의 간격.
3. 영상 전체에서 말하지 않았으면, 같은 종류의 다른 값을 가까이서 말했을 때 값 불일치(오류),
   아니면 화면 전용 정보(오류).
영상 내내 표시되는 중요 정보는 구간마다 반복하지 않고 한 건만 올린다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from difflib import SequenceMatcher

from barrierfree.analysis.entities import (
    ENTITY_KIND_LABELS,
    Entity,
    compare_entities,
    entities_equal,
    extract_entities,
)
from barrierfree.analysis.normalize import normalize_text
from barrierfree.config import AnalysisConfig
from barrierfree.models import (
    Issue,
    IssueKind,
    IssueSource,
    ScreenText,
    SpeechSegment,
    SubtitleCue,
    Verdict,
)


@dataclass(frozen=True)
class _SpeechEntities:
    segment: SpeechSegment
    entities: list[Entity]


@dataclass(frozen=True)
class _Item:
    """화면 값 하나의 점검 결과. kind: ONLY(화면 전용) / MISMATCH(값 불일치) / ELSEWHERE(먼 곳)."""

    kind: str
    entity: Entity
    counterpart: Entity | None = None
    gap_s: float | None = None


def _gap(text: ScreenText, segment: SpeechSegment) -> float:
    return max(0.0, segment.start_s - text.end_s, text.start_s - segment.end_s)


def _matches_subtitle(
    text: ScreenText,
    entities: list[Entity],
    cues: list[SubtitleCue],
    config: AnalysisConfig,
) -> bool:
    """같은 시점에 자막으로 이미 전달되는 글자는 점검 대상이 아니다.

    글자가 비슷해도 자막의 값(전화번호·날짜 등)이 화면과 다르면 제외하지 않는다.
    """
    norm = normalize_text(text.text)
    if not norm:
        return False
    for cue in cues:
        if cue.end_s < text.start_s or cue.start_s > text.end_s:
            continue
        cue_norm = normalize_text(cue.text)
        similar = norm in cue_norm or (
            SequenceMatcher(None, norm, cue_norm, autojunk=False).ratio()
            >= config.screen_merge_similarity
        )
        if not similar:
            continue
        diff = compare_entities(entities, extract_entities(cue.text, lenient_dates=True))
        if not diff.missing and not diff.mismatched:
            return True
    return False


def _classify(
    text: ScreenText,
    entities: list[Entity],
    near: list[Entity],
    speech: list[_SpeechEntities],
) -> list[_Item]:
    diff = compare_entities(entities, near)
    unmatched: list[tuple[Entity, Entity | None]] = [(s, t) for s, t in diff.mismatched]
    unmatched += [(e, None) for e in diff.missing]
    items: list[_Item] = []
    for entity, counterpart in unmatched:
        gaps = [
            _gap(text, se.segment)
            for se in speech
            if any(entities_equal(entity, other) for other in se.entities)
        ]
        if gaps:
            items.append(_Item("ELSEWHERE", entity, counterpart, min(gaps)))
        elif counterpart is not None:
            items.append(_Item("MISMATCH", entity, counterpart))
        else:
            items.append(_Item("ONLY", entity))
    return items


def _describe(item: _Item) -> str:
    label = ENTITY_KIND_LABELS[item.entity.kind]
    if item.kind == "MISMATCH" and item.counterpart is not None:
        return f"{label}이(가) 다릅니다: 화면 '{item.entity.raw}' / 음성 '{item.counterpart.raw}'"
    if item.kind == "ELSEWHERE":
        gap = item.gap_s or 0.0
        return (
            f"화면의 {label} '{item.entity.raw}'은(는) 표시 구간 주변이 아닌 "
            f"{gap:.0f}초 떨어진 곳에서 언급됩니다. 표시된 시점에 전달되는지 확인하세요."
        )
    return f"화면의 {label} '{item.entity.raw}'을(를) 음성에서 확인하지 못했습니다."


def _evidence(entities: list[Entity], items: list[_Item]) -> dict[str, object]:
    unmatched: list[dict[str, object]] = []
    for item in items:
        record: dict[str, object] = {
            "type": item.kind,
            "kind": item.entity.kind.value,
            "value": item.entity.value,
            "raw": item.entity.raw,
        }
        if item.counterpart is not None:
            record["speech_value"] = item.counterpart.value
            record["speech_raw"] = item.counterpart.raw
        if item.gap_s is not None:
            record["gap_s"] = item.gap_s
        unmatched.append(record)
    return {
        "entities": [{"kind": e.kind.value, "value": e.value, "raw": e.raw} for e in entities],
        "unmatched": unmatched,
    }


def _confidence(text: ScreenText) -> float:
    return round(min(1.0, max(0.0, text.confidence)), 3)


def check_screen(
    texts: list[ScreenText],
    uncertain: list[ScreenText],
    speech: list[SpeechSegment],
    cues: list[SubtitleCue],
    duration_s: float,
    config: AnalysisConfig,
) -> list[Issue]:
    """화면 글자마다 중요 정보가 음성으로 전달되는지 점검한다(시간순)."""
    speech_entities = [
        _SpeechEntities(s, extract_entities(s.text, lenient_dates=True)) for s in speech
    ]
    all_speech = [e for se in speech_entities for e in se.entities]
    issues: list[Issue] = []

    for text in texts:
        entities = extract_entities(text.text)
        if not entities or _matches_subtitle(text, entities, cues, config):
            continue

        if (text.end_s - text.start_s) / max(duration_s, 1.0) >= config.always_visible_ratio:
            missing = [e for e in entities if not any(entities_equal(e, o) for o in all_speech)]
            if missing:
                items = [_Item("ONLY", e) for e in missing]
                names = ", ".join(f"{ENTITY_KIND_LABELS[e.kind]} '{e.raw}'" for e in missing)
                issues.append(
                    Issue(
                        source=IssueSource.M2,
                        kind=IssueKind.ALWAYS_ON_SCREEN,
                        verdict=Verdict.REVIEW_REQUIRED,
                        start_s=text.start_s,
                        end_s=text.end_s,
                        reason=f"영상 내내 표시되는 {names}을(를) 음성에서 확인하지 못했습니다.",
                        comparison_text=text.text,
                        confidence=_confidence(text),
                        evidence=_evidence(entities, items),
                    )
                )
            continue

        window = config.screen_near_window_s
        near_segments = [
            se
            for se in speech_entities
            if se.segment.end_s >= text.start_s - window
            and se.segment.start_s <= text.end_s + window
        ]
        near_entities = [e for se in near_segments for e in se.entities]
        items = _classify(text, entities, near_entities, speech_entities)
        if not items:
            continue

        kinds = {item.kind for item in items}
        kind = (
            IssueKind.SCREEN_VALUE_MISMATCH
            if "MISMATCH" in kinds
            else IssueKind.SCREEN_ONLY_INFO
            if "ONLY" in kinds
            else IssueKind.MENTIONED_ELSEWHERE
        )
        elsewhere_only = kind is IssueKind.MENTIONED_ELSEWHERE
        verdict = Verdict.REVIEW_REQUIRED if elsewhere_only else Verdict.ERROR
        reason = " / ".join(_describe(item) for item in items)
        evidence = _evidence(entities, items)
        gaps = [item.gap_s for item in items if item.gap_s is not None]
        if gaps:
            evidence["gap_s"] = min(gaps)

        stt_uncertain = any(
            se.segment.avg_logprob is not None
            and se.segment.avg_logprob < config.stt_avg_logprob_min
            for se in near_segments
        )
        if verdict is Verdict.ERROR and stt_uncertain:
            evidence["downgraded_from"] = kind.value
            evidence["original_reason"] = reason
            kind, verdict = IssueKind.STT_UNCERTAIN, Verdict.UNDETERMINED
            reason = "자동 판단 불가: 음성 확인 필요 (주변 음성 인식이 불확실합니다)."

        issues.append(
            Issue(
                source=IssueSource.M2,
                kind=kind,
                verdict=verdict,
                start_s=text.start_s,
                end_s=text.end_s,
                reason=reason,
                speech_text=" ".join(se.segment.text for se in near_segments) or None,
                comparison_text=text.text,
                confidence=_confidence(text),
                evidence=evidence,
            )
        )

    # 신뢰도가 낮게 읽힌 글자: 값이 있고 음성에도 없을 때만 '화면 확인 필요'로 올린다.
    for text in uncertain:
        entities = extract_entities(text.text)
        if not entities or not math.isfinite(text.confidence):
            continue
        if all(any(entities_equal(e, o) for o in all_speech) for e in entities):
            continue
        issues.append(
            Issue(
                source=IssueSource.M2,
                kind=IssueKind.OCR_UNCERTAIN,
                verdict=Verdict.UNDETERMINED,
                start_s=text.start_s,
                end_s=text.end_s,
                reason="자동 판단 불가: 화면 확인 필요 (글자 인식이 불확실합니다).",
                comparison_text=text.text,
                confidence=_confidence(text),
                evidence={"entities": [{"kind": e.kind.value, "raw": e.raw} for e in entities]},
            )
        )

    return sorted(issues, key=lambda i: (i.start_s, i.end_s))

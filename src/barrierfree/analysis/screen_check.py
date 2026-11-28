"""M2: 화면 글자 ↔ 음성. 화면에 표시된 중요 정보가 음성으로 전달되는지 점검한다.

점검 범위는 '화면에 표시되는 주요 문자 정보의 음성 전달'로 한정한다. 화면해설 누락(행동,
몸짓, 그래프의 의미)은 판정하지 않는다. 판정 순서(docs/project/implementation-plan.md):
1. 가까운 창(표시 구간 ±5초)에서 값이 말해지면 정상.
2. 영상 다른 곳에서만 말했으면 "다른 시점에 언급됨"(검수 필요) + 표시와 설명 시점의 간격.
   멀리서 한 번 말했다는 이유로 조용히 통과시키지 않는다. 영상 내내 표시되는 글자와 인식이
   불확실한 글자도 같다.
3. 영상 전체에서 말하지 않았으면, 같은 종류의 다른 값을 가까이서 말했을 때 값 확인 필요,
   아니면 화면 전용 정보. 둘 다 검수 필요다.
영상 내내 표시되는 중요 정보는 구간마다 반복하지 않고 한 건만 올린다.

자막(외부 자막 파일이나 화면에서 추출한 자막)에 같은 글자가 있어도 음성 확인은 건너뛰지 않는다.
자막에 있다는 것은 음성으로 전달됐다는 뜻이 아니다. 지적에는 자막에도 있다는 사실을 덧붙인다.

이 점검은 오류(ERROR)를 내지 않는다. 값의 종류만 비교하므로 화면의 값과 가까이서 말한 값이 같은
대상인지 알 수 없고(소득 기준액과 인상액도 둘 다 금액이다), 음성에 없는 화면 정보가 반드시
전달되어야 하는 정보인지도 판단하지 않는다.
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
    """화면 값 하나의 점검 결과.

    kind: ONLY(화면 전용) / MISMATCH(가까이서 같은 종류의 다른 값) / ELSEWHERE(먼 곳에서 언급).
    """

    kind: str
    entity: Entity
    counterpart: Entity | None = None
    gap_s: float | None = None


def _gap(text: ScreenText, segment: SpeechSegment) -> float:
    return max(0.0, segment.start_s - text.end_s, text.start_s - segment.end_s)


def _in_subtitle(
    text: ScreenText,
    entities: list[Entity],
    cues: list[SubtitleCue],
    config: AnalysisConfig,
) -> bool:
    """같은 시점의 자막에도 이 글자가 있는가. 점검에서 빼는 데 쓰지 않고 지적에 덧붙인다.

    글자가 비슷해도 자막의 값(전화번호·날짜 등)이 화면과 다르면 같은 내용으로 보지 않는다.
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
        return (
            f"화면의 {label} '{item.entity.raw}'을(를) 음성에서 확인하지 못했습니다. "
            f"가까이서 말한 같은 종류의 값은 '{item.counterpart.raw}'입니다. "
            "같은 대상인지 확인하세요."
        )
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


_ALSO_IN_SUBTITLE = " 같은 내용이 자막에도 있습니다. 음성으로도 전달되는지 확인하세요."


def _near(
    text: ScreenText, speech: list[_SpeechEntities], window: float
) -> list[_SpeechEntities]:
    """표시 구간 ±window초와 겹치는 음성."""
    return [
        se
        for se in speech
        if se.segment.end_s >= text.start_s - window
        and se.segment.start_s <= text.end_s + window
    ]


def _confidence(text: ScreenText) -> float:
    return round(min(1.0, max(0.0, text.confidence)), 3)


def _no_speech_issue(text: ScreenText, entities: list[Entity], in_subtitle: bool) -> Issue:
    """음성이 하나도 없으면(오디오 없음, 빈 받아쓰기) '전달되지 않았다'고 말할 근거가 없다."""
    names = ", ".join(f"{ENTITY_KIND_LABELS[e.kind]} '{e.raw}'" for e in entities)
    reason = (
        f"자동 판단 불가: 비교할 음성이 없어 화면의 {names}이(가) "
        "음성으로 전달되는지 확인하지 못했습니다."
    )
    evidence: dict[str, object] = {
        "entities": [{"kind": e.kind.value, "value": e.value, "raw": e.raw} for e in entities]
    }
    if in_subtitle:
        evidence["also_in_subtitle"] = True
        reason += _ALSO_IN_SUBTITLE
    return Issue(
        source=IssueSource.M2,
        kind=IssueKind.NO_SPEECH_TO_COMPARE,
        verdict=Verdict.UNDETERMINED,
        start_s=text.start_s,
        end_s=text.end_s,
        reason=reason,
        comparison_text=text.text,
        confidence=_confidence(text),
        evidence=evidence,
    )


def _unspoken_while_shown(
    text: ScreenText,
    entities: list[Entity],
    near: list[_SpeechEntities],
    speech: list[_SpeechEntities],
) -> list[_Item]:
    """영상 내내 표시되는 값 가운데 표시되는 동안(±창) 말하지 않은 것."""
    near_entities = [e for se in near for e in se.entities]
    items: list[_Item] = []
    for entity in entities:
        if any(entities_equal(entity, other) for other in near_entities):
            continue
        gaps = [
            _gap(text, se.segment)
            for se in speech
            if any(entities_equal(entity, other) for other in se.entities)
        ]
        items.append(
            _Item("ELSEWHERE", entity, gap_s=min(gaps)) if gaps else _Item("ONLY", entity)
        )
    return items


def _always_visible_issue(
    text: ScreenText, entities: list[Entity], items: list[_Item], in_subtitle: bool
) -> Issue:
    """상시 표시 정보는 값이 여럿이어도 한 건이다."""
    unspoken = [item.entity for item in items if item.kind == "ONLY"]
    parts: list[str] = []
    if unspoken:
        names = ", ".join(f"{ENTITY_KIND_LABELS[e.kind]} '{e.raw}'" for e in unspoken)
        parts.append(f"영상 내내 표시되는 {names}을(를) 음성에서 확인하지 못했습니다.")
    parts += [_describe(item) for item in items if item.kind == "ELSEWHERE"]
    reason = " / ".join(parts)
    evidence = _evidence(entities, items)
    gaps = [item.gap_s for item in items if item.gap_s is not None]
    if gaps:
        evidence["gap_s"] = min(gaps)
    if in_subtitle:
        evidence["also_in_subtitle"] = True
        reason += _ALSO_IN_SUBTITLE
    return Issue(
        source=IssueSource.M2,
        kind=IssueKind.ALWAYS_ON_SCREEN if unspoken else IssueKind.MENTIONED_ELSEWHERE,
        verdict=Verdict.REVIEW_REQUIRED,
        start_s=text.start_s,
        end_s=text.end_s,
        reason=reason,
        comparison_text=text.text,
        confidence=_confidence(text),
        evidence=evidence,
    )


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
        if not entities:
            continue
        in_subtitle = _in_subtitle(text, entities, cues, config)
        if not speech:
            issues.append(_no_speech_issue(text, entities, in_subtitle))
            continue
        near_segments = _near(text, speech_entities, config.screen_near_window_s)

        if (text.end_s - text.start_s) / max(duration_s, 1.0) >= config.always_visible_ratio:
            items = _unspoken_while_shown(text, entities, near_segments, speech_entities)
            if items:
                issues.append(_always_visible_issue(text, entities, items, in_subtitle))
            continue

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
        unconfirmed = kind is not IssueKind.MENTIONED_ELSEWHERE  # 영상 어디서도 확인 못 한 값
        verdict = Verdict.REVIEW_REQUIRED
        reason = " / ".join(_describe(item) for item in items)
        evidence = _evidence(entities, items)
        if in_subtitle:
            evidence["also_in_subtitle"] = True
            reason += _ALSO_IN_SUBTITLE
        gaps = [item.gap_s for item in items if item.gap_s is not None]
        if gaps:
            evidence["gap_s"] = min(gaps)

        stt_uncertain = any(
            se.segment.avg_logprob is not None
            and se.segment.avg_logprob < config.stt_avg_logprob_min
            for se in near_segments
        )
        if unconfirmed and stt_uncertain:
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

    # 신뢰도가 낮게 읽힌 글자: 값이 있고 표시 구간 주변의 음성에 없을 때 '화면 확인 필요'로 올린다.
    for text in uncertain:
        entities = extract_entities(text.text)
        if not entities or not math.isfinite(text.confidence):
            continue
        near = [
            e
            for se in _near(text, speech_entities, config.screen_near_window_s)
            for e in se.entities
        ]
        if all(any(entities_equal(e, o) for o in near) for e in entities):
            continue
        reason = "자동 판단 불가: 화면 확인 필요 (글자 인식이 불확실합니다)."
        noted: dict[str, object] = {
            "entities": [{"kind": e.kind.value, "raw": e.raw} for e in entities]
        }
        if all(any(entities_equal(e, o) for o in all_speech) for e in entities):
            noted["spoken_elsewhere_only"] = True
            reason += " 같은 값은 다른 시점의 음성에서만 확인됩니다."
        issues.append(
            Issue(
                source=IssueSource.M2,
                kind=IssueKind.OCR_UNCERTAIN,
                verdict=Verdict.UNDETERMINED,
                start_s=text.start_s,
                end_s=text.end_s,
                reason=reason,
                comparison_text=text.text,
                confidence=_confidence(text),
                evidence=noted,
            )
        )

    return sorted(issues, key=lambda i: (i.start_s, i.end_s))

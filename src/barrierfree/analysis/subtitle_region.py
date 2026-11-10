"""하단 영역이 자막 트랙인지 '영역 전체'로 판단한다(자막 파일이 없을 때).

하단에는 자막뿐 아니라 담당자 이름, 기관명, 행사 장소 같은 글자도 뜬다. 구분 신호는 다음 순서다.
1. 글자가 바뀌는 빈도: 자막은 자주 바뀌고, 이름·기관명 바는 드물게 뜨거나 오래 머문다.
2. 표시 구간이 발화 구간과 겹치는 정도.
3. 영역 전체의 내용이 음성과 얼마나 같은가(보조 신호).

줄 단위로 STT와 맞는지 보고 자막 여부를 정하지 않는다. 틀린 자막이 '자막 아님'으로 빠져
점검 대상에서 사라지는 순환 판정이 되기 때문이다. 내용 일치도가 0이어도 자주 바뀌고 발화와
겹치면 자막 트랙이다.
"""

from __future__ import annotations

from dataclasses import dataclass

from barrierfree.analysis.normalize import normalize_text
from barrierfree.config import AnalysisConfig
from barrierfree.models import ScreenText, SpeechSegment

Rect = tuple[float, float, float, float]  # 정규화 (x, y, w, h)
_SAMPLE_COUNT = 5


@dataclass(frozen=True)
class RegionEstimate:
    is_subtitle: bool
    score: float
    rect: Rect
    text_count: int
    changes_per_minute: float
    speech_overlap: float  # 표시 시간 중 발화와 겹친 비율
    agreement: float  # 영역 전체 글자의 2-gram 중 음성에도 나타나는 비율
    # 확인 화면에서 편집자에게 보여 줄, 영역에서 읽은 글자 예시(시간순 앞쪽 몇 건)
    samples: tuple[str, ...] = ()


def region_rect(ratio: float) -> Rect:
    """프레임 높이의 하단 `ratio`를 차지하는 띠."""
    return (0.0, 1.0 - ratio, 1.0, ratio)


def in_rect(text: ScreenText, rect: Rect) -> bool:
    """글자 박스의 중심이 rect 안에 있는가. 위치를 모르는 글자는 영역 밖으로 본다."""
    if text.box is None:
        return False
    cx = text.box[0] + text.box[2] / 2
    cy = text.box[1] + text.box[3] / 2
    return rect[0] <= cx <= rect[0] + rect[2] and rect[1] <= cy <= rect[1] + rect[3]


def _union(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _overlap(start: float, end: float, spans: list[tuple[float, float]]) -> float:
    return sum(max(0.0, min(end, b) - max(start, a)) for a, b in spans)


def _bigrams(text: str) -> set[str]:
    norm = normalize_text(text)
    return {norm[i : i + 2] for i in range(len(norm) - 1)}


def estimate_subtitle_region(
    texts: list[ScreenText],
    speech: list[SpeechSegment],
    duration_s: float,
    config: AnalysisConfig,
    *,
    ratio: float | None = None,
) -> RegionEstimate:
    rect = region_rect(ratio if ratio is not None else config.subtitle_region_ratio)
    inside = [t for t in texts if in_rect(t, rect)]
    if not inside:
        return RegionEstimate(False, 0.0, rect, 0, 0.0, 0.0, 0.0)

    changes_per_minute = len(inside) / (max(duration_s, 1.0) / 60.0)

    speech_spans = _union([(s.start_s, s.end_s) for s in speech])
    shown = sum(t.end_s - t.start_s for t in inside)
    overlap = (
        sum(_overlap(t.start_s, t.end_s, speech_spans) for t in inside) / shown
        if shown > 0 and speech_spans
        else 0.0
    )

    region_grams = _bigrams(" ".join(t.text for t in inside))
    speech_grams = _bigrams(" ".join(s.text for s in speech))
    agreement = len(region_grams & speech_grams) / len(region_grams) if region_grams else 0.0

    frequency = min(1.0, changes_per_minute / config.subtitle_changes_for_full_score)
    score = 0.4 * overlap + 0.3 * frequency + 0.3 * agreement
    is_subtitle = (
        len(inside) >= config.subtitle_min_texts
        and changes_per_minute >= config.subtitle_min_changes_per_minute
        and score >= config.subtitle_region_score_min
    )
    samples = tuple(t.text for t in sorted(inside, key=lambda t: t.start_s)[:_SAMPLE_COUNT])
    return RegionEstimate(
        is_subtitle, score, rect, len(inside), changes_per_minute, overlap, agreement, samples
    )

"""대조 보기의 행: 음성 구간을 기준으로 같은 시간의 화면 글자와 자막을 나란히 놓는다.

**대응이나 일치를 판단하지 않는다.** 같은 시간에 있었다는 사실만 모은다. 화면 글자는 위치나 자막과의
일치 때문에 빼지 않으며, 모든 글자가 처음 보이는 행에 한 번 나타난다. 이후의 행에는 '계속 표시'로
남겨 개수를 알 수 있게 한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from barrierfree.models import ScreenText, SpeechSegment, SubtitleCue

_VISIBLE_S = 0.5  # 이만큼(또는 표시 시간의 절반) 겹쳐야 그 행에서 보인 것으로 친다
_MIN_SILENCE_S = 1.0  # 이보다 짧은 말 사이는 따로 행을 만들지 않는다
_SILENCE_CHUNK_S = 10.0  # 긴 무음은 이 길이로 나눠 찾아갈 수 있게 한다


@dataclass(frozen=True)
class CompareData:
    speech: list[SpeechSegment]
    screen: list[ScreenText]
    uncertain: list[ScreenText]
    cues: list[SubtitleCue]


@dataclass
class Row:
    start_s: float
    end_s: float
    speech: str | None  # None이면 말이 없는 시간
    screen: list[str] = field(default_factory=list)  # 이 행에서 처음 보인 글자
    uncertain: list[str] = field(default_factory=list)  # 〃 인식이 불확실한 글자
    carried: list[str] = field(default_factory=list)  # 앞 행부터 계속 보이는 확실한 글자
    carried_uncertain: list[str] = field(default_factory=list)
    cues: list[str] = field(default_factory=list)


def _silences(speech: Sequence[SpeechSegment], end_s: float) -> list[Row]:
    rows: list[Row] = []
    cursor = 0.0
    for start, end in [*sorted((s.start_s, s.end_s) for s in speech), (end_s, end_s)]:
        if start - cursor >= _MIN_SILENCE_S:
            at = cursor
            while at < start:
                until = min(start, at + _SILENCE_CHUNK_S)
                rows.append(Row(at, until, None))
                at = until
        cursor = max(cursor, end)
    return rows


def _visible_rows(start: float, end: float, rows: list[Row]) -> list[Row]:
    """그 시간에 보인 행(시간순). 하나도 없으면 시작 시각에 가장 가까운 행 하나."""
    needed = min(_VISIBLE_S, (end - start) / 2)
    found = [
        r for r in rows
        if min(end, r.end_s) - max(start, r.start_s) >= needed
        and min(end, r.end_s) > max(start, r.start_s)
    ]
    if found:
        return found
    nearest = min(rows, key=lambda r: (max(r.start_s - start, start - r.end_s, 0.0), r.start_s))
    return [nearest]


def row_for(rows: list[Row], start: float, end: float, text: str | None = None) -> int:
    """그 시간의 것이 속하는 행 번호. 화면 글자를 주면 그 글자가 처음 보이는 행이다."""
    if text is not None:
        for index, row in enumerate(rows):
            near = row.end_s > start - 1.0 and row.start_s < end + 1.0
            if near and (text in row.screen or text in row.uncertain):
                return index
    first = _visible_rows(start, end, rows)[0]
    return next(index for index, row in enumerate(rows) if row is first)


def build_rows(data: CompareData, keep: Sequence[tuple[float, float]] = ()) -> list[Row]:
    """시간순의 행.

    말이 있는 행은 모두 낸다. 말이 없는 행은 새로 보이는 것이 있거나 `keep`(받아쓰기 누락 의심
    구간)과 겹칠 때만 낸다.
    """
    items = [*data.screen, *data.uncertain]
    spans = [(i.start_s, i.end_s) for i in items] + [(c.start_s, c.end_s) for c in data.cues]
    spans_end = [e for _, e in spans] + [e for _, e in keep]
    last = max(spans_end + [s.end_s for s in data.speech], default=0.0)
    # 길이가 0인 항목만 있어도 놓을 행이 있게 한다
    end_s = max(last, max([s for s, _ in spans], default=0.0) + _MIN_SILENCE_S)
    spoken = [Row(s.start_s, s.end_s, s.text) for s in data.speech]
    silent = _silences(data.speech, end_s) if spans or keep else []
    rows = sorted(spoken + silent, key=lambda r: (r.start_s, r.speech is None))
    if not rows:
        return []

    for texts, new, carried in (
        (data.screen, "screen", "carried"),
        (data.uncertain, "uncertain", "carried_uncertain"),
    ):
        for item in sorted(texts, key=lambda t: (t.start_s, t.end_s)):
            first, *rest = _visible_rows(item.start_s, item.end_s, rows)
            getattr(first, new).append(item.text)
            for row in rest:
                getattr(row, carried).append(item.text)

    for cue in sorted(data.cues, key=lambda c: (c.start_s, c.end_s)):
        visible = _visible_rows(cue.start_s, cue.end_s, rows)
        with_speech = [r for r in visible if r.speech is not None]
        for row in with_speech or visible[:1]:
            row.cues.append(cue.text)

    return [
        r
        for r in rows
        if r.speech is not None
        or r.screen
        or r.uncertain
        or r.cues
        or any(r.start_s < b and r.end_s > a for a, b in keep)
    ]

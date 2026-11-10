"""확인된 자막 영역의 OCR 글자를 자막(`SubtitleCue`)으로 바꾼다.

영역에서 제외된 글자(이름, 기관명, 장소 등)는 M2의 화면 글자로 남긴다.
"""

from __future__ import annotations

from barrierfree.analysis.subtitle_region import Rect, in_rect
from barrierfree.models import ScreenText, SubtitleCue

_SAME_SUBTITLE_OVERLAP = 0.6  # 짧은 쪽 표시 시간 중 이만큼 겹치면 같은 시점의 자막으로 본다
_MAX_LINE_GAP = 0.08  # 같은 자막의 윗줄과 아랫줄 사이 최대 간격(프레임 높이 비율)


def split_by_region(
    texts: list[ScreenText], rect: Rect
) -> tuple[list[ScreenText], list[ScreenText]]:
    """(영역 안 글자, 영역 밖 글자). 입력 순서를 유지한다."""
    inside = [t for t in texts if in_rect(t, rect)]
    outside = [t for t in texts if not in_rect(t, rect)]
    return inside, outside


def _time_overlap(a: ScreenText, b: ScreenText) -> float:
    shorter = min(a.end_s - a.start_s, b.end_s - b.start_s)
    if shorter <= 0:
        return 0.0
    return max(0.0, min(a.end_s, b.end_s) - max(a.start_s, b.start_s)) / shorter


def _top(t: ScreenText) -> float:
    return t.box[1] if t.box else 0.0


def _bottom(t: ScreenText) -> float:
    return t.box[1] + t.box[3] if t.box else 0.0


def _stacked(a: ScreenText, b: ScreenText) -> bool:
    """두 글자가 같은 시점에 위아래로 붙어 있는가(어느 쪽이 위든 상관없다)."""
    if _time_overlap(a, b) < _SAME_SUBTITLE_OVERLAP:
        return False
    gap = _top(b) - _bottom(a) if _top(b) >= _top(a) else _top(a) - _bottom(b)
    return 0.0 <= gap <= _MAX_LINE_GAP


def extract_cues(subtitle_texts: list[ScreenText]) -> list[SubtitleCue]:
    """같은 시점에 위아래로 쌓인 줄은 하나의 자막으로 합치고, 시간순으로 돌려준다.

    OCR이 윗줄을 더 늦게 읽을 수 있으므로, 묶음의 어느 줄이든 위나 아래에 붙으면 합친다.
    """
    groups: list[list[ScreenText]] = []
    for text in sorted(subtitle_texts, key=lambda t: (t.start_s, _top(t))):
        for group in groups:
            if any(_stacked(member, text) for member in group):
                group.append(text)
                break
        else:
            groups.append([text])

    cues = [
        SubtitleCue(
            start_s=min(t.start_s for t in group),
            end_s=max(t.end_s for t in group),
            text=" ".join(t.text for t in sorted(group, key=_top)),
        )
        for group in groups
    ]
    return sorted(cues, key=lambda c: (c.start_s, c.end_s))

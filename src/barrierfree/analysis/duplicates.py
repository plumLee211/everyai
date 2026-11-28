"""화면에 새겨진 자막의 같은 문제를 두 번 내지 않는다.

화면 글자는 위치나 자막과의 일치 때문에 화면 정보 비교(M2)에서 빼지 않는다. 그래서 같은 자막
문장을 음성↔자막 비교(M1)와 M2가 각각 지적할 수 있다. 자막에도 있는 화면 글자의 M2 지적은,
같은 시간에 **그 자막 문장을 비교한 M1 지적**이 있으면(또는 확인하지 못한 값이 모두 M1의 자막 쪽
값이면) 따로 내지 않고 M1 지적에 덧붙인다. M2의 사유와 화면 글자는 모두 남긴다.

자막 파일 없이 분석하면 M1의 자막과 화면 글자를 서로 다른 OCR 실행에서 얻어 글자가 조금씩
다르므로, 같은 문장인지는 이어서 일치하는 글자의 비율로 본다.
"""

from __future__ import annotations

from dataclasses import replace
from difflib import SequenceMatcher
from typing import Any

from barrierfree.analysis.normalize import normalize_text
from barrierfree.models import Issue, IssueKind

# 영상 내내 표시되는 정보, 화면 인식 불확실, 판단 불가로 낮춘 지적은 합치지 않는다.
_M2_KINDS = {
    IssueKind.SCREEN_VALUE_MISMATCH,
    IssueKind.SCREEN_ONLY_INFO,
    IssueKind.MENTIONED_ELSEWHERE,
}
_SAME_CAPTION_SHARE = 0.7  # 화면 글자 중 M1이 비교한 자막과 이어서 일치하는 비율


def _screen_values(issue: Issue) -> set[tuple[str, str]]:
    unmatched: Any = issue.evidence.get("unmatched", [])
    return {(item["kind"], item["value"]) for item in unmatched}


def _wrong_subtitle_values(issue: Issue) -> set[tuple[str, str]]:
    mismatched: Any = issue.evidence.get("mismatched", [])
    return {(pair["subtitle"]["kind"], pair["subtitle"]["value"]) for pair in mismatched}


def _overlaps(a: Issue, b: Issue) -> bool:
    return a.start_s < b.end_s and b.start_s < a.end_s


def _same_caption(screen: str | None, subtitle: str | None) -> bool:
    part, whole = normalize_text(screen or ""), normalize_text(subtitle or "")
    if not part or not whole:
        return False
    if part in whole:
        return True
    blocks = SequenceMatcher(None, part, whole, autojunk=False).get_matching_blocks()
    return sum(b.size for b in blocks if b.size >= 2) / len(part) >= _SAME_CAPTION_SHARE


def _host(screen: Issue, findings: list[Issue]) -> int | None:
    values = _screen_values(screen)
    if screen.kind not in _M2_KINDS or not screen.evidence.get("also_in_subtitle") or not values:
        return None
    overlapping = [i for i, issue in enumerate(findings) if _overlaps(issue, screen)]
    # 값이 같은 '값 불일치'가 있으면 그것에, 아니면 같은 자막 문장을 비교한 지적에 덧붙인다.
    for index in overlapping:
        issue = findings[index]
        if issue.kind is IssueKind.VALUE_MISMATCH and values <= _wrong_subtitle_values(issue):
            return index
    same = [
        i for i in overlapping
        if _same_caption(screen.comparison_text, findings[i].comparison_text)
    ]
    mismatches = [i for i in same if findings[i].kind is IssueKind.VALUE_MISMATCH]
    return next(iter(mismatches or same), None)


def merge_screen_duplicates(m1: list[Issue], m2: list[Issue]) -> tuple[list[Issue], list[Issue]]:
    """(M1 지적, 남는 M2 지적). 합친 M1 지적은 같은 id를 가지며 M2의 내용을 모두 담는다."""
    merged = list(m1)
    kept: list[Issue] = []
    for screen in m2:
        index = _host(screen, merged)
        if index is None:
            kept.append(screen)
            continue
        issue = merged[index]
        record = {
            "text": screen.comparison_text,
            "reason": screen.reason,
            "kind": screen.kind.value,
            "start_s": screen.start_s,
            "end_s": screen.end_s,
        }
        previous: Any = issue.evidence.get("screens", [])
        line = f" / 화면 글자 '{screen.comparison_text}': {screen.reason}"
        merged[index] = replace(
            issue,
            reason=issue.reason if line in issue.reason else issue.reason + line,
            evidence={**issue.evidence, "screens": [*previous, record]},
        )
    return merged, kept

"""검수 결과 CSV 내보내기.

모든 탐지 결과를 내보내고 검수 상태를 함께 기록한다(기각 항목도 제거하지 않는다).
Excel에서 한글이 깨지지 않도록 UTF-8 BOM으로 저장하고, 수식 주입을 막기 위해
`= + - @` 로 시작하는 셀 앞에 작은따옴표를 붙인다.
"""

from __future__ import annotations

import csv
from pathlib import Path

from barrierfree.models import ISSUE_KIND_LABELS, REVIEW_STATUS_LABELS, VERDICT_LABELS
from barrierfree.storage.repository import Repository

COLUMNS = [
    "문제 ID",
    "영상 파일명",
    "시작 시간",
    "종료 시간",
    "문제 유형",
    "자동 판정",
    "탐지 사유",
    "음성 인식 내용",
    "비교 대상 내용",
    "신뢰도",
    "검수 상태",
]

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def format_time(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"


def _safe(value: str | None) -> str:
    text = value or ""
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def export_csv(repo: Repository, session_id: str, out_path: str | Path) -> Path:
    session = repo.get_session(session_id)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        for issue in repo.list_issues(session_id):
            writer.writerow(
                [
                    _safe(issue.id),
                    _safe(session.video_name),
                    format_time(issue.start_s),
                    format_time(issue.end_s),
                    _safe(ISSUE_KIND_LABELS[issue.kind]),
                    _safe(VERDICT_LABELS[issue.verdict]),
                    _safe(issue.reason),
                    _safe(issue.speech_text),
                    _safe(issue.comparison_text),
                    "" if issue.confidence is None else f"{issue.confidence:.2f}",
                    _safe(REVIEW_STATUS_LABELS[issue.review_status]),
                ]
            )
    return out

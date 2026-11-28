"""검수 결과 CSV 내보내기.

모든 탐지 결과를 내보내고 검수 상태를 함께 기록한다(기각 항목도 제거하지 않는다).
Excel에서 한글이 깨지지 않도록 UTF-8 BOM으로 저장하고, 수식 주입을 막기 위해
`= + - @` 로 시작하는 셀 앞에 작은따옴표를 붙인다.

분석 상태(점검하지 못한 구간과 안내)는 지적 CSV의 형식을 바꾸지 않도록 같은 폴더의
`<이름>_분석상태.csv`에 따로 저장한다. 이 파일은 사용자가 저장 대화상자에서 고른 것이 아니므로,
우리가 만든 형식이 아닌 같은 이름의 파일은 덮어쓰지 않는다.
"""

from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path

from barrierfree.models import (
    ISSUE_KIND_LABELS,
    NOTE_SOURCE_LABELS,
    REVIEW_STATUS_LABELS,
    VERDICT_LABELS,
)
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

NOTE_COLUMNS = ["영상 파일명", "대상", "시작 시간", "종료 시간", "내용"]
_NO_NOTES = "기록된 안내가 없습니다. 상태를 기록하지 않던 이전 버전의 분석일 수도 있습니다."

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


def notes_csv_path(results_path: str | Path) -> Path:
    path = Path(results_path)
    return path.with_name(f"{path.stem}_분석상태{path.suffix or '.csv'}")


def _is_state_file(path: Path) -> bool:
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            return next(csv.reader(f), None) == NOTE_COLUMNS
    except (UnicodeDecodeError, csv.Error):
        return False


def _state_rows(repo: Repository, session_id: str) -> list[list[str]]:
    video = _safe(repo.get_session(session_id).video_name)
    notes = repo.list_notes(session_id)
    if not notes:
        return [[video, "", "", "", _NO_NOTES]]
    return [
        [
            video,
            NOTE_SOURCE_LABELS[note.source],
            "" if note.start_s is None else format_time(note.start_s),
            "" if note.end_s is None else format_time(note.end_s),
            _safe(note.message),
        ]
        for note in notes
    ]


def export_notes_csv(repo: Repository, session_id: str, results_path: str | Path) -> Path:
    """지적 CSV 옆에 분석 상태를 저장한다. 다 쓴 뒤에 바꿔 넣어 반쪽 파일을 남기지 않는다."""
    target = notes_csv_path(results_path)
    if target.is_file() and not _is_state_file(target):
        raise FileExistsError(f"다른 내용의 파일이 이미 있습니다: {target.name}")
    rows = _state_rows(repo, session_id)
    handle, temp = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(NOTE_COLUMNS)
            writer.writerows(rows)
        os.replace(temp, target)
    finally:
        Path(temp).unlink(missing_ok=True)
    return target


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

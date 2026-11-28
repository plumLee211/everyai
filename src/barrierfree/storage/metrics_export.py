"""실증 지표 CSV: 분석 한 건의 처리·검수 시간과 건수.

**영상의 내용은 넣지 않는다.** 발화, 화면 글자, 지적 사유, 영상 파일 이름과 경로가 없어야 하며,
같은 영상인지 알아볼 영상 지문은 앞부분만 쓴다. 참가자가 이 파일만 보낼 수 있게 하려는 것이다.
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from barrierfree.analysis.compare_rows import CompareData, build_rows
from barrierfree.models import AnalysisNote, NoteCode
from barrierfree.storage.repository import Repository

COLUMNS = ["항목", "값"]
_UNKNOWN = "기록 없음"


def _union_seconds(notes: list[AnalysisNote]) -> float:
    total, last_end = 0.0, 0.0
    for start, end in sorted(
        (n.start_s, n.end_s) for n in notes if n.start_s is not None and n.end_s is not None
    ):
        total += max(0.0, end - max(start, last_end))
        last_end = max(last_end, end)
    return round(total, 1)


def _rows(
    repo: Repository, session_id: str, data: CompareData | None
) -> list[tuple[str, object]]:
    session = repo.get_session(session_id)
    snapshot = session.config_snapshot
    rows: list[tuple[str, object]] = [
        ("분석 ID", session_id[:8]),
        ("영상 지문", session.video_sha256[:12]),
        ("분석 일시", session.created_at),
        ("영상 길이(초)", session.video_duration_s),
        ("앱 버전", snapshot.get("app_version", _UNKNOWN)),
        ("규칙 버전", snapshot.get("rules_version", _UNKNOWN)),
        ("OCR 생략 방식", snapshot.get("ocr_frame_skip", _UNKNOWN)),
        ("음성 인식 모델", session.stt_model_name or _UNKNOWN),
        ("화면 글자 인식", session.ocr_engine_name or _UNKNOWN),
        ("장치", session.compute_device or _UNKNOWN),
        ("자막 출처", session.subtitle_source.value),
        ("분석 상태", session.status.value),
        ("검수 시간(초)", round(session.review_duration_ms / 1000)),
    ]
    stages: dict[str, list[tuple[int, bool]]] = {}
    for metric in repo.list_stage_metrics(session_id):
        stages.setdefault(metric.stage, []).append((metric.duration_ms, metric.cache_hit))
    for stage, values in stages.items():
        rows.append((f"처리 시간(ms) {stage}", sum(ms for ms, _ in values)))
        rows.append((f"캐시 사용 {stage}", "예" if any(hit for _, hit in values) else "아니오"))

    issues = repo.list_issues(session_id)
    rows.append(("지적 수", len(issues)))
    kinds = Counter(
        f"{i.source.value}/{i.kind.value}/{i.verdict.value}/{i.review_status.value}"
        for i in issues
    )
    rows += [(f"지적 {key}", count) for key, count in sorted(kinds.items())]

    notes = repo.list_notes(session_id)
    codes = Counter(n.code.value for n in notes)
    rows += [(f"안내 수 {code}", count) for code, count in sorted(codes.items())]
    suspected = [n for n in notes if n.code is NoteCode.SPEECH_MAYBE_MISSED]
    rows.append(("누락 의심 시간(초)", _union_seconds(suspected)))
    unchecked = [n for n in notes if n.start_s is not None and n not in suspected]
    for source in sorted({n.source for n in unchecked}):
        seconds = _union_seconds([n for n in unchecked if n.source is source])
        rows.append((f"미점검 시간(초) {source.value}", seconds))

    if data is None:
        rows.append(("대조 자료", "없음"))
    else:
        rows += [
            ("대조 보기 행 수", len(build_rows(data))),
            ("화면 글자 수", len(data.screen)),
            ("불확실 화면 글자 수", len(data.uncertain)),
        ]
    return rows


def export_metrics_csv(
    repo: Repository, session_id: str, data: CompareData | None, out_path: str | Path
) -> Path:
    rows = _rows(repo, session_id, data)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        writer.writerows(rows)
    return out

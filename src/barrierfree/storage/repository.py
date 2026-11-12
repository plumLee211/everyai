"""저장소 계층. SQL은 UI/분석 코드에서 직접 실행하지 않고 여기서만 다룬다.

스레드마다 자기 SQLite 연결을 갖는다(QThread 워커와 UI 스레드가 연결을 공유하지 않음).
시간 구간은 ms 정수, 시각은 UTC ISO 8601(`...Z`)로 저장한다.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from barrierfree.config import AnalysisConfig
from barrierfree.media.ffmpeg import VideoInfo
from barrierfree.models import (
    Issue,
    IssueKind,
    IssueSource,
    ReviewStatus,
    SessionStatus,
    SubtitleSource,
    Verdict,
)
from barrierfree.storage.db import connect, migrate

_TERMINAL = {SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _ms(seconds: float) -> int:
    return round(seconds * 1000)


@dataclass(frozen=True)
class SessionRecord:
    id: str
    video_path: str
    video_name: str
    video_sha256: str
    video_duration_s: float
    subtitle_source: SubtitleSource
    subtitle_path: str | None
    generated_srt_path: str | None
    subtitle_region: dict[str, object] | None
    status: SessionStatus
    stt_model_name: str | None
    ocr_engine_name: str | None
    compute_device: str | None
    config_snapshot: dict[str, object]
    review_duration_ms: int
    error_code: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None


@dataclass(frozen=True)
class ReviewCounts:
    total: int
    unreviewed: int
    confirmed: int
    dismissed: int
    by_verdict: dict[Verdict, int] = field(default_factory=dict)


@dataclass(frozen=True)
class StageMetric:
    stage: str
    attempt_no: int
    duration_ms: int
    processed_count: int | None
    processed_unit: str | None
    cache_hit: bool


class Repository:
    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        self._lock = threading.Lock()
        migrate(self.connection())

    # ----- connection management -----

    def connection(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            # 각 연결은 만든 스레드만 사용한다. close_all()이 다른 스레드에서 닫을 수 있게 완화.
            conn = connect(self._path, check_same_thread=False)
            self._local.conn = conn
            with self._lock:
                self._all.append(conn)
        return conn

    def close_all(self) -> None:
        """모든 스레드의 연결을 닫는다.

        호출 전에 DB를 쓰는 워커 스레드가 모두 종료되어 있어야 한다. 이 클래스는
        진행 중인 트랜잭션과 종료를 동기화하지 않는다(앱 종료 시 워커 join은 호출자 책임).
        """
        with self._lock:
            for conn in self._all:
                conn.close()
            self._all.clear()
        self._local = threading.local()

    # ----- session -----

    def create_session(
        self,
        *,
        video: VideoInfo,
        video_sha256: str,
        subtitle_source: SubtitleSource,
        subtitle_path: str | None,
        config: AnalysisConfig,
        stt_model_name: str | None,
        ocr_engine_name: str | None,
        compute_device: str | None,
    ) -> str:
        session_id = str(uuid4())
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO analysis_session (id, video_path, video_name, video_sha256,"
                " video_duration_ms, subtitle_source, subtitle_path, status, stt_model_name,"
                " ocr_engine_name, compute_device, config_snapshot_json, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    str(video.path),
                    video.path.name,
                    video_sha256,
                    _ms(video.duration_s),
                    subtitle_source.value,
                    subtitle_path,
                    SessionStatus.PREPARING.value,
                    stt_model_name,
                    ocr_engine_name,
                    compute_device,
                    json.dumps(config.to_snapshot(), ensure_ascii=False),
                    _now(),
                ),
            )
        return session_id

    def recent_sessions(self, limit: int = 5) -> list[SessionRecord]:
        """분석이 끝난 최근 세션(최신순). 사이드바의 '최근 작업'에 쓴다."""
        rows = self.connection().execute(
            "SELECT id FROM analysis_session WHERE status = ?"
            " ORDER BY COALESCE(finished_at, created_at) DESC, rowid DESC LIMIT ?",
            (SessionStatus.COMPLETED.value, limit),
        ).fetchall()
        return [self.get_session(row["id"]) for row in rows]

    def get_session(self, session_id: str) -> SessionRecord:
        row = self.connection().execute(
            "SELECT * FROM analysis_session WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise KeyError(session_id)
        region = row["subtitle_region_json"]
        return SessionRecord(
            id=row["id"],
            video_path=row["video_path"],
            video_name=row["video_name"],
            video_sha256=row["video_sha256"],
            video_duration_s=row["video_duration_ms"] / 1000,
            subtitle_source=SubtitleSource(row["subtitle_source"]),
            subtitle_path=row["subtitle_path"],
            generated_srt_path=row["generated_srt_path"],
            subtitle_region=json.loads(region) if region else None,
            status=SessionStatus(row["status"]),
            stt_model_name=row["stt_model_name"],
            ocr_engine_name=row["ocr_engine_name"],
            compute_device=row["compute_device"],
            config_snapshot=json.loads(row["config_snapshot_json"]),
            review_duration_ms=row["review_duration_ms"],
            error_code=row["error_code"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )

    def set_status(
        self, session_id: str, status: SessionStatus, *, error_code: str | None = None
    ) -> None:
        now = _now()
        with self.connection() as conn:
            if status is SessionStatus.ANALYZING:
                conn.execute(
                    "UPDATE analysis_session SET status = ?, started_at = COALESCE(started_at, ?)"
                    " WHERE id = ?",
                    (status.value, now, session_id),
                )
            elif status in _TERMINAL:
                conn.execute(
                    "UPDATE analysis_session SET status = ?, error_code = ?, finished_at = ?"
                    " WHERE id = ?",
                    (status.value, error_code, now, session_id),
                )
            else:
                conn.execute(
                    "UPDATE analysis_session SET status = ? WHERE id = ?",
                    (status.value, session_id),
                )

    def set_subtitle(
        self,
        session_id: str,
        source: SubtitleSource,
        *,
        generated_srt_path: str | None = None,
        region: dict[str, object] | None = None,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE analysis_session SET subtitle_source = ?, generated_srt_path = ?,"
                " subtitle_region_json = ? WHERE id = ?",
                (
                    source.value,
                    generated_srt_path,
                    json.dumps(region, ensure_ascii=False) if region is not None else None,
                    session_id,
                ),
            )

    def add_review_time(self, session_id: str, delta_ms: int) -> None:
        if delta_ms < 0:
            raise ValueError("delta_ms must be >= 0")
        with self.connection() as conn:
            conn.execute(
                "UPDATE analysis_session SET review_duration_ms = review_duration_ms + ?"
                " WHERE id = ?",
                (delta_ms, session_id),
            )

    # ----- issues -----

    def complete_session(self, session_id: str, issues: Iterable[Issue]) -> None:
        """지적 목록 저장과 COMPLETED 전환을 하나의 트랜잭션으로 처리한다."""
        now = _now()
        rows = [
            (
                i.id,
                session_id,
                i.source.value,
                i.kind.value,
                i.verdict.value,
                i.review_status.value,
                i.severity,
                i.confidence,
                _ms(i.start_s),
                _ms(i.end_s),
                i.reason,
                i.speech_text,
                i.comparison_text,
                json.dumps(i.evidence, ensure_ascii=False) if i.evidence else None,
                now,
                now,
            )
            for i in issues
        ]
        with self.connection() as conn:
            conn.executemany(
                "INSERT INTO issue (id, session_id, source, issue_kind, verdict, review_status,"
                " severity, confidence, start_ms, end_ms, reason, speech_text, comparison_text,"
                " evidence_json, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.execute(
                "UPDATE analysis_session SET status = ?, finished_at = ? WHERE id = ?",
                (SessionStatus.COMPLETED.value, now, session_id),
            )

    def list_issues(self, session_id: str) -> list[Issue]:
        cur = self.connection().execute(
            "SELECT * FROM issue WHERE session_id = ? ORDER BY start_ms, created_at, id",
            (session_id,),
        )
        return [
            Issue(
                id=r["id"],
                source=IssueSource(r["source"]),
                kind=IssueKind(r["issue_kind"]),
                verdict=Verdict(r["verdict"]),
                review_status=ReviewStatus(r["review_status"]),
                severity=r["severity"],
                confidence=r["confidence"],
                start_s=r["start_ms"] / 1000,
                end_s=r["end_ms"] / 1000,
                reason=r["reason"],
                speech_text=r["speech_text"],
                comparison_text=r["comparison_text"],
                evidence=json.loads(r["evidence_json"]) if r["evidence_json"] else {},
            )
            for r in cur.fetchall()
        ]

    def set_review_status(self, issue_id: str, status: ReviewStatus) -> None:
        """사람의 검수 상태만 바꾼다. AI 판정(verdict)은 절대 수정하지 않는다."""
        with self.connection() as conn:
            cur = conn.execute(
                "UPDATE issue SET review_status = ?, updated_at = ? WHERE id = ?",
                (status.value, _now(), issue_id),
            )
            if cur.rowcount == 0:
                raise KeyError(issue_id)

    def review_counts(self, session_id: str) -> ReviewCounts:
        conn = self.connection()
        by_status = {
            r["review_status"]: r["n"]
            for r in conn.execute(
                "SELECT review_status, COUNT(*) AS n FROM issue WHERE session_id = ?"
                " GROUP BY review_status",
                (session_id,),
            )
        }
        by_verdict = {
            Verdict(r["verdict"]): r["n"]
            for r in conn.execute(
                "SELECT verdict, COUNT(*) AS n FROM issue WHERE session_id = ? GROUP BY verdict",
                (session_id,),
            )
        }
        unreviewed = by_status.get(ReviewStatus.UNREVIEWED.value, 0)
        confirmed = by_status.get(ReviewStatus.CONFIRMED.value, 0)
        dismissed = by_status.get(ReviewStatus.DISMISSED.value, 0)
        return ReviewCounts(
            total=unreviewed + confirmed + dismissed,
            unreviewed=unreviewed,
            confirmed=confirmed,
            dismissed=dismissed,
            by_verdict=by_verdict,
        )

    # ----- stage metrics -----

    def record_stage_metric(
        self,
        session_id: str,
        stage: str,
        *,
        duration_ms: int,
        processed_count: int | None = None,
        unit: str | None = None,
        cache_hit: bool = False,
        attempt_no: int = 1,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO analysis_stage_metric (session_id, stage, attempt_no, duration_ms,"
                " processed_count, processed_unit, cache_hit, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    stage,
                    attempt_no,
                    duration_ms,
                    processed_count,
                    unit,
                    int(cache_hit),
                    _now(),
                ),
            )

    def list_stage_metrics(self, session_id: str) -> list[StageMetric]:
        cur = self.connection().execute(
            "SELECT * FROM analysis_stage_metric WHERE session_id = ? ORDER BY id", (session_id,)
        )
        return [
            StageMetric(
                stage=r["stage"],
                attempt_no=r["attempt_no"],
                duration_ms=r["duration_ms"],
                processed_count=r["processed_count"],
                processed_unit=r["processed_unit"],
                cache_hit=bool(r["cache_hit"]),
            )
            for r in cur.fetchall()
        ]

    # ----- cache index -----

    def register_cache(
        self,
        *,
        cache_key: str,
        cache_type: str,
        video_sha256: str,
        engine_name: str,
        model_name: str | None,
        config_hash: str,
        file_path: str,
    ) -> None:
        now = _now()
        with self.connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO analysis_cache (cache_key, cache_type, video_sha256,"
                " engine_name, model_name, config_hash, file_path, created_at, last_used_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (cache_key, cache_type, video_sha256, engine_name, model_name, config_hash,
                 file_path, now, now),
            )

    def find_cache(self, cache_key: str) -> str | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT file_path FROM analysis_cache WHERE cache_key = ?", (cache_key,)
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE analysis_cache SET last_used_at = ? WHERE cache_key = ?",
                (_now(), cache_key),
            )
            return str(row["file_path"])

    def delete_cache(self, cache_key: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM analysis_cache WHERE cache_key = ?", (cache_key,))

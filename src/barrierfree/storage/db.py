"""SQLite 연결과 스키마 마이그레이션 (docs/project/database-design-draft.md 기준, 보류 상태 제거).

스키마 변경은 `PRAGMA user_version` 기반의 순차 마이그레이션으로 관리한다.
앱 업데이트마다 DB를 삭제하고 다시 만드는 방식은 쓰지 않는다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

_V1 = """
BEGIN;

CREATE TABLE IF NOT EXISTS analysis_session (
    id                   TEXT PRIMARY KEY,
    video_path           TEXT NOT NULL,
    video_name           TEXT NOT NULL,
    video_sha256         TEXT NOT NULL,
    video_duration_ms    INTEGER NOT NULL CHECK (video_duration_ms >= 0),
    subtitle_source      TEXT NOT NULL DEFAULT 'UNKNOWN'
                         CHECK (subtitle_source IN ('FILE', 'OCR', 'NONE', 'UNKNOWN')),
    subtitle_path        TEXT,
    generated_srt_path   TEXT,
    subtitle_region_json TEXT,
    status               TEXT NOT NULL
                         CHECK (status IN ('PREPARING', 'ANALYZING', 'AWAITING_CONFIRMATION',
                                           'COMPLETED', 'FAILED', 'CANCELLED')),
    stt_model_name       TEXT,
    ocr_engine_name      TEXT,
    compute_device       TEXT,
    config_snapshot_json TEXT NOT NULL,
    review_duration_ms   INTEGER NOT NULL DEFAULT 0 CHECK (review_duration_ms >= 0),
    error_code           TEXT,
    created_at           TEXT NOT NULL,
    started_at           TEXT,
    finished_at          TEXT
);

CREATE TABLE IF NOT EXISTS issue (
    id                   TEXT PRIMARY KEY,
    session_id           TEXT NOT NULL REFERENCES analysis_session(id) ON DELETE CASCADE,
    source               TEXT NOT NULL
                         CHECK (source IN ('M1', 'M2', 'M2_SCREEN_DEPENDENT')),
    issue_kind           TEXT NOT NULL,
    verdict              TEXT NOT NULL
                         CHECK (verdict IN ('ERROR', 'REVIEW_REQUIRED', 'UNDETERMINED')),
    review_status        TEXT NOT NULL DEFAULT 'UNREVIEWED'
                         CHECK (review_status IN ('UNREVIEWED', 'CONFIRMED', 'DISMISSED')),
    severity             TEXT CHECK (severity IN ('HIGH', 'MEDIUM', 'LOW')),
    confidence           REAL CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    start_ms             INTEGER NOT NULL CHECK (start_ms >= 0),
    end_ms               INTEGER NOT NULL CHECK (end_ms >= start_ms),
    reason               TEXT NOT NULL,
    speech_text          TEXT,
    comparison_text      TEXT,
    evidence_json        TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_stage_metric (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id           TEXT NOT NULL REFERENCES analysis_session(id) ON DELETE CASCADE,
    stage                TEXT NOT NULL,
    attempt_no           INTEGER NOT NULL DEFAULT 1 CHECK (attempt_no >= 1),
    duration_ms          INTEGER NOT NULL CHECK (duration_ms >= 0),
    processed_count      INTEGER CHECK (processed_count IS NULL OR processed_count >= 0),
    processed_unit       TEXT,
    cache_hit            INTEGER NOT NULL DEFAULT 0 CHECK (cache_hit IN (0, 1)),
    created_at           TEXT NOT NULL,
    UNIQUE (session_id, stage, attempt_no)
);

CREATE TABLE IF NOT EXISTS analysis_cache (
    cache_key            TEXT PRIMARY KEY,
    cache_type           TEXT NOT NULL CHECK (cache_type IN ('STT', 'OCR')),
    video_sha256         TEXT NOT NULL,
    engine_name          TEXT NOT NULL,
    model_name           TEXT,
    config_hash          TEXT NOT NULL,
    file_path            TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    last_used_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_analysis_session_created_at ON analysis_session(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_issue_session_start ON issue(session_id, start_ms);
CREATE INDEX IF NOT EXISTS idx_issue_session_review_status ON issue(session_id, review_status);
CREATE INDEX IF NOT EXISTS idx_issue_session_verdict ON issue(session_id, verdict);
CREATE INDEX IF NOT EXISTS idx_metric_session_stage ON analysis_stage_metric(session_id, stage);
CREATE INDEX IF NOT EXISTS idx_cache_video_type ON analysis_cache(video_sha256, cache_type);

PRAGMA user_version = 1;
COMMIT;
"""


def connect(path: str | Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    target = str(path)
    conn = sqlite3.connect(target, timeout=5.0, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if target != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    version: int = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"데이터베이스가 더 최신 버전({version})입니다. 앱을 업데이트해 주세요."
        )
    if version < 1:
        conn.executescript(_V1)

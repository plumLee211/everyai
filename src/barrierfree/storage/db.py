"""SQLite 연결과 스키마 마이그레이션 (docs/project/database-design-draft.md 기준, 보류 상태 제거).

스키마 변경은 `PRAGMA user_version` 기반의 순차 마이그레이션으로 관리한다.
앱 업데이트마다 DB를 삭제하고 다시 만드는 방식은 쓰지 않는다.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

SCHEMA_VERSION = 1
BACKUP_DIR_NAME = "db-backup"
_FILE_SUFFIXES = ("-shm", "-wal", "")  # 지울 때를 위해 본 파일이 마지막이다

logger = logging.getLogger(__name__)

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
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        if target != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
    except BaseException:
        conn.close()  # 남은 핸들은 Windows에서 파일 삭제·교체를 막는다
        raise
    return conn


class DatabaseHealth(StrEnum):
    OK = "OK"
    REPAIRED = "REPAIRED"  # 색인을 다시 만들어 살렸다
    RECREATED = "RECREATED"  # 읽을 수 없어 치워 두었다. 다음 연결에서 새로 만든다
    UNREPAIRED = "UNREPAIRED"  # 점검·복구를 마치지 못했다. 원본은 그 자리에 있다


@dataclass(frozen=True)
class HealthReport:
    status: DatabaseHealth
    backup_dir: Path | None = None  # 손상된 원본의 사본이 끝까지 만들어진 경우에만


def integrity_problems(conn: sqlite3.Connection) -> list[str]:
    """손상 내용 목록. 정상이면 빈 목록.

    `quick_check`는 색인과 표의 내용이 어긋난 손상을 통과시키므로 `integrity_check`를 쓴다.
    """
    rows = [str(r[0]) for r in conn.execute("PRAGMA integrity_check").fetchall()]
    return [] if rows == ["ok"] else rows


def _header_user_version(path: Path) -> int:
    """파일 머리글의 user_version. 지금의 SQLite가 못 읽는 스키마여도 확인할 수 있다."""
    try:
        with path.open("rb") as f:
            header = f.read(64)
    except OSError:
        return 0
    if len(header) < 64 or not header.startswith(b"SQLite format 3\x00"):
        return 0
    return int.from_bytes(header[60:64], "big")


def _backup(path: Path) -> Path:
    """원본 사본을 만든다. 끝까지 만들지 못하면 반쪽 사본을 지우고 `OSError`를 올린다."""
    folder = path.parent / BACKUP_DIR_NAME / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    folder.mkdir(parents=True)
    try:
        for suffix in _FILE_SUFFIXES:
            source = Path(f"{path}{suffix}")
            if source.is_file():
                shutil.copy2(source, folder / source.name)
    except OSError:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return folder


def _run_reindex(conn: sqlite3.Connection) -> None:
    conn.execute("REINDEX")


def _repair(conn: sqlite3.Connection) -> bool:
    try:
        _run_reindex(conn)
    except sqlite3.IntegrityError:
        # 깨진 색인은 중복을 막지 못한다. 캐시 색인은 버려도 다시 만들어지므로 비우고 재시도한다.
        conn.rollback()
        conn.execute("DELETE FROM analysis_cache")
        _run_reindex(conn)  # 그래도 실패하면 커밋하지 않으므로 삭제도 반영되지 않는다
    conn.commit()
    return not integrity_problems(conn)


def _read_all(conn: sqlite3.Connection, sql: str) -> None:
    conn.execute(sql).fetchall()


def _history_is_readable(conn: sqlite3.Connection) -> bool:
    try:
        _read_all(conn, "SELECT * FROM analysis_session")
        _read_all(conn, "SELECT * FROM issue")
    except sqlite3.OperationalError:
        raise  # 일시적이거나 환경 탓일 수 있다. '읽을 수 없는 파일'로 단정하지 않는다
    except sqlite3.DatabaseError:
        return False
    return True


def ensure_healthy(path: str | Path) -> HealthReport:
    """앱 시작 때 DB를 점검한다. 원칙: 확신할 수 없으면 원본을 건드리지 않는다.

    - 손상이 확인되면 사본을 먼저 만들고 색인 재생성을 시도한다.
    - 분석 기록을 읽을 수 없을 때만, 사본을 만든 뒤에 원본을 치운다.
    - 잠금·디스크 부족·권한처럼 점검을 마칠 수 없는 경우에는 `UNREPAIRED`를 돌려준다.
      이 함수 때문에 앱이 시작하지 못하는 일은 없어야 한다.
    """
    target = Path(path)
    if not target.is_file() or _header_user_version(target) > SCHEMA_VERSION:
        return HealthReport(DatabaseHealth.OK)  # 더 최신 DB의 안내는 migrate()가 한다

    conn: sqlite3.Connection | None = None
    backup: Path | None = None
    try:
        try:
            conn = connect(target)
            if conn.execute("PRAGMA user_version").fetchone()[0] > SCHEMA_VERSION:
                return HealthReport(DatabaseHealth.OK)  # 새 버전 번호가 아직 -wal에만 있는 경우
            problems = integrity_problems(conn)
            if not problems:
                return HealthReport(DatabaseHealth.OK)
            logger.error("DB 손상 발견: %s", "; ".join(problems[:20]))
            backup = _backup(target)
            if _repair(conn):
                logger.warning("DB 색인을 다시 만들어 복구했습니다. 원본 사본: %s", backup)
                return HealthReport(DatabaseHealth.REPAIRED, backup)
        except sqlite3.OperationalError:
            raise
        except sqlite3.DatabaseError:
            logger.exception("DB를 읽거나 복구하는 중 오류")
        if conn is not None and _history_is_readable(conn):
            logger.error("DB를 다 고치지 못했지만 기록은 읽을 수 있어 그대로 둡니다")
            return HealthReport(DatabaseHealth.UNREPAIRED, backup)
        backup = backup or _backup(target)
    except (OSError, sqlite3.OperationalError):
        logger.exception("DB 점검·복구를 마치지 못해 원본을 그대로 둡니다")
        return HealthReport(DatabaseHealth.UNREPAIRED, backup)
    finally:
        if conn is not None:
            conn.close()

    try:
        for suffix in _FILE_SUFFIXES:
            Path(f"{target}{suffix}").unlink(missing_ok=True)
    except OSError:
        logger.exception("읽을 수 없는 DB를 치우지 못했습니다")
        return HealthReport(DatabaseHealth.UNREPAIRED, backup)
    logger.error("DB를 읽을 수 없어 새로 만듭니다. 원본 사본: %s", backup)
    return HealthReport(DatabaseHealth.RECREATED, backup)


def migrate(conn: sqlite3.Connection) -> None:
    version: int = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"데이터베이스가 더 최신 버전({version})입니다. 앱을 업데이트해 주세요."
        )
    if version < 1:
        conn.executescript(_V1)

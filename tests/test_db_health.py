"""storage/db.py의 시작 점검(ensure_healthy)과 손상된 DB에서의 최근 작업 조회."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from barrierfree.storage import db
from barrierfree.storage.db import DatabaseHealth, ensure_healthy
from barrierfree.storage.repository import Repository
from tests.corrupt import corrupt_session_pk_index
from tests.test_storage import _issue, _session


def _completed_db(path: Path) -> str:
    repo = Repository(path)
    session_id = _session(repo)
    repo.complete_session(session_id, [_issue()])
    repo.close_all()
    return session_id


def test_recent_sessions_survives_a_broken_primary_key_index(tmp_path: Path) -> None:
    """Windows에서 보고된 크래시: 방금 찾은 id를 다시 찾지 못해 KeyError로 앱이 꺼졌다."""
    path = tmp_path / "app.db"
    session_id = _completed_db(path)
    corrupt_session_pk_index(path, session_id)
    repo = Repository(path)
    try:
        with pytest.raises(KeyError):
            repo.get_session(session_id)  # 손상이 실제로 재현됐는지 확인
        assert [r.id for r in repo.recent_sessions()] == [session_id]
    finally:
        repo.close_all()


def test_recent_sessions_skips_an_unreadable_row(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "app.db")
    good, bad = _session(repo), _session(repo)
    repo.complete_session(good, [_issue()])
    repo.complete_session(bad, [_issue()])
    with repo.connection() as conn:
        conn.execute(
            "UPDATE analysis_session SET config_snapshot_json = '{' WHERE id = ?", (bad,)
        )
    assert [r.id for r in repo.recent_sessions()] == [good]
    repo.close_all()


def test_missing_database_is_ok(tmp_path: Path) -> None:
    report = ensure_healthy(tmp_path / "app.db")
    assert report.status is DatabaseHealth.OK and report.backup_dir is None
    assert not (tmp_path / "app.db").exists()


def test_healthy_database_is_left_alone(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    _completed_db(path)
    assert ensure_healthy(path).status is DatabaseHealth.OK
    assert not (tmp_path / db.BACKUP_DIR_NAME).exists()


def test_broken_index_is_backed_up_and_repaired(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    session_id = _completed_db(path)
    corrupt_session_pk_index(path, session_id)
    damaged = path.read_bytes()

    report = ensure_healthy(path)

    assert report.status is DatabaseHealth.REPAIRED
    assert report.backup_dir is not None
    assert (report.backup_dir / "app.db").read_bytes() == damaged
    repo = Repository(path)
    assert repo.get_session(session_id).id == session_id  # 기록이 그대로 살아난다
    assert len(repo.list_issues(session_id)) == 1
    repo.close_all()


def test_unreadable_file_is_moved_aside_so_a_new_database_can_be_made(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    path.write_bytes(b"this is not a database" * 200)

    report = ensure_healthy(path)

    assert report.status is DatabaseHealth.RECREATED
    assert report.backup_dir is not None
    assert (report.backup_dir / "app.db").read_bytes().startswith(b"this is not a database")
    assert not path.exists()
    Repository(path).close_all()


def test_a_busy_database_is_not_treated_as_corrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "app.db"
    _completed_db(path)
    before = path.read_bytes()

    def locked(_conn: sqlite3.Connection) -> list[str]:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db, "integrity_problems", locked)
    report = ensure_healthy(path)
    assert report.status is DatabaseHealth.UNREPAIRED and report.backup_dir is None
    assert path.read_bytes() == before
    assert not (tmp_path / db.BACKUP_DIR_NAME).exists()


# ---------- 확신할 수 없으면 원본을 건드리지 않는다 ----------


def _damaged_db(path: Path) -> bytes:
    session_id = _completed_db(path)
    corrupt_session_pk_index(path, session_id)
    return path.read_bytes()


def _fail_copy(*_args: object, **_kwargs: object) -> None:
    raise OSError(28, "No space left on device")


def test_when_the_backup_fails_nothing_is_changed_and_no_partial_copy_is_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "app.db"
    damaged = _damaged_db(path)
    monkeypatch.setattr(db.shutil, "copy2", _fail_copy)

    report = ensure_healthy(path)

    assert report.status is DatabaseHealth.UNREPAIRED and report.backup_dir is None
    assert path.read_bytes() == damaged
    assert list((tmp_path / db.BACKUP_DIR_NAME).iterdir()) == []


def test_a_file_that_cannot_be_removed_does_not_stop_the_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "app.db"
    path.write_bytes(b"this is not a database" * 200)

    def in_use(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError(13, "사용 중인 파일")

    monkeypatch.setattr(Path, "unlink", in_use)
    report = ensure_healthy(path)
    assert report.status is DatabaseHealth.UNREPAIRED
    assert path.exists()


def test_a_failed_connection_is_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows는 열린 파일을 지울 수 없으므로, 연결에 실패해도 핸들이 남으면 안 된다."""
    closed: list[bool] = []

    class Tracked(sqlite3.Connection):
        def close(self) -> None:
            closed.append(True)
            super().close()

    real_connect = sqlite3.connect
    monkeypatch.setattr(
        db.sqlite3, "connect", lambda *a, **k: real_connect(*a, factory=Tracked, **k)
    )
    path = tmp_path / "app.db"
    path.write_bytes(b"this is not a database" * 200)
    with pytest.raises(sqlite3.DatabaseError):
        db.connect(path)
    assert closed == [True]


def test_a_database_from_a_newer_app_is_never_touched(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    path.write_bytes(_damaged_newer_header())
    before = path.read_bytes()
    assert ensure_healthy(path).status is DatabaseHealth.OK  # 안내는 migrate()가 한다
    assert path.read_bytes() == before


def _damaged_newer_header() -> bytes:
    header = bytearray(b"SQLite format 3\x00" + b"\x00" * 4080)
    header[60:64] = (db.SCHEMA_VERSION + 1).to_bytes(4, "big")
    return bytes(header)


def test_duplicate_cache_rows_do_not_cost_the_analysis_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REINDEX가 중복 때문에 실패해도, 버려도 되는 캐시 색인만 비우고 기록은 살린다."""
    path = tmp_path / "app.db"
    session_id = _completed_db(path)
    corrupt_session_pk_index(path, session_id)
    real_reindex = db._run_reindex
    calls: list[int] = []

    def fail_once(target: sqlite3.Connection) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.IntegrityError("UNIQUE constraint failed: analysis_cache.cache_key")
        real_reindex(target)

    monkeypatch.setattr(db, "_run_reindex", fail_once)
    assert ensure_healthy(path).status is DatabaseHealth.REPAIRED
    repo = Repository(path)
    assert repo.get_session(session_id).id == session_id
    repo.close_all()


def test_a_readable_database_that_cannot_be_repaired_stays_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "app.db"
    session_id = _completed_db(path)
    corrupt_session_pk_index(path, session_id)

    def never(_conn: sqlite3.Connection) -> None:
        raise sqlite3.IntegrityError("UNIQUE constraint failed")

    monkeypatch.setattr(db, "_run_reindex", never)
    report = ensure_healthy(path)
    assert report.status is DatabaseHealth.UNREPAIRED and report.backup_dir is not None
    repo = Repository(path)
    assert [r.id for r in repo.recent_sessions()] == [session_id]  # 기록은 계속 보인다
    repo.close_all()


@pytest.mark.parametrize("locked_suffix", ["-shm", "-wal"])
def test_the_database_file_is_removed_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, locked_suffix: str
) -> None:
    """곁 파일을 못 지웠는데 본 파일을 이미 지웠으면 '그대로 두었다'는 안내가 거짓이 된다."""
    path = tmp_path / "app.db"
    path.write_bytes(b"this is not a database" * 200)
    real_unlink = Path.unlink

    def partly_locked(self: Path, missing_ok: bool = False) -> None:
        if self.name.endswith(locked_suffix):
            raise PermissionError(13, "사용 중인 파일")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", partly_locked)
    assert ensure_healthy(path).status is DatabaseHealth.UNREPAIRED
    assert path.exists()


def test_a_transient_error_while_checking_readability_never_removes_the_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "app.db"
    damaged = _damaged_db(path)

    def never(_conn: sqlite3.Connection) -> None:
        raise sqlite3.IntegrityError("UNIQUE constraint failed")

    def io_error(_conn: sqlite3.Connection, _sql: str) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(db, "_run_reindex", never)
    monkeypatch.setattr(db, "_read_all", io_error)
    report = ensure_healthy(path)
    assert report.status is DatabaseHealth.UNREPAIRED
    assert path.read_bytes() == damaged


def test_a_newer_version_still_only_in_the_wal_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "app.db"
    _completed_db(path)
    newer = db.connect(path)  # 연결을 열어 둔 채라 새 버전 번호는 -wal에만 있다
    try:
        newer.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
        newer.commit()
        assert db._header_user_version(path) == db.SCHEMA_VERSION

        def must_not_run(_conn: sqlite3.Connection) -> list[str]:
            raise AssertionError("더 최신 DB는 점검하지 않는다")

        monkeypatch.setattr(db, "integrity_problems", must_not_run)
        assert ensure_healthy(path).status is DatabaseHealth.OK
    finally:
        newer.close()

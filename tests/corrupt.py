"""테스트용 DB 손상 도구. PK 색인을 깨서 표에는 있는데 id로는 못 찾는 상태를 만든다."""

from __future__ import annotations

from pathlib import Path

from barrierfree.storage.db import connect


def corrupt_session_pk_index(path: Path, session_id: str) -> None:
    conn = connect(path)
    size = conn.execute("PRAGMA page_size").fetchone()[0]
    root = conn.execute(
        "SELECT rootpage FROM sqlite_master WHERE name = 'sqlite_autoindex_analysis_session_1'"
    ).fetchone()[0]
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    data = bytearray(path.read_bytes())
    start = (root - 1) * size
    at = data.index(session_id.encode(), start, start + size)
    data[at] = ord("0") if data[at] != ord("0") else ord("1")
    path.write_bytes(bytes(data))

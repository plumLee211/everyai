"""storage 패키지 테스트: 마이그레이션, 원자성, 검수 상태 영속화, 캐시, CSV."""

from __future__ import annotations

import csv
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

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
from barrierfree.storage.cache import CacheType, FileCache, hash_file, make_key
from barrierfree.storage.db import SCHEMA_VERSION, connect, migrate
from barrierfree.storage.export import export_csv
from barrierfree.storage.repository import Repository


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[Repository]:
    r = Repository(tmp_path / "data" / "app.db")
    yield r
    r.close_all()


def _video() -> VideoInfo:
    return VideoInfo(
        path=Path("/videos/안내.mp4"),
        duration_s=12.5,
        width=640,
        height=360,
        fps=10.0,
        has_video=True,
        has_audio=True,
    )


def _session(repo: Repository) -> str:
    return repo.create_session(
        video=_video(),
        video_sha256="a" * 64,
        subtitle_source=SubtitleSource.FILE,
        subtitle_path="/videos/안내.srt",
        config=AnalysisConfig(),
        stt_model_name="small",
        ocr_engine_name="easyocr",
        compute_device="CPU",
    )


def _issue(start: float = 1.0, **overrides: object) -> Issue:
    base: dict[str, object] = {
        "source": IssueSource.M1,
        "kind": IssueKind.VALUE_MISMATCH,
        "verdict": Verdict.ERROR,
        "start_s": start,
        "end_s": start + 2.0,
        "reason": "신청 마감일이 다릅니다.",
        "speech_text": "3월 15일까지",
        "comparison_text": "3월 16일까지",
        "confidence": 0.9,
        "evidence": {"speech": "3월15일", "subtitle": "3월16일"},
    }
    base.update(overrides)
    return Issue(**base)  # type: ignore[arg-type]


# ---------- db / migration ----------


def test_migrate_sets_version_and_is_idempotent(tmp_path: Path) -> None:
    conn = connect(tmp_path / "m.db")
    migrate(conn)
    migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"analysis_session", "issue", "analysis_stage_metric", "analysis_cache"} <= tables
    conn.close()


def test_connection_pragmas(tmp_path: Path) -> None:
    conn = connect(tmp_path / "p.db")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    conn.close()


def test_schema_rejects_on_hold_status(repo: Repository) -> None:
    sid = _session(repo)
    conn = repo.connection()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO issue (id, session_id, source, issue_kind, verdict, review_status,"
            " start_ms, end_ms, reason, created_at, updated_at)"
            " VALUES ('x', ?, 'M1', 'VALUE_MISSING', 'ERROR', 'ON_HOLD', 0, 1, 'r', 't', 't')",
            (sid,),
        )


# ---------- session ----------


def test_create_session_defaults(repo: Repository) -> None:
    rec = repo.get_session(_session(repo))
    assert rec.status is SessionStatus.PREPARING
    assert rec.review_duration_ms == 0
    assert rec.video_name == "안내.mp4"
    assert rec.video_duration_s == pytest.approx(12.5)
    assert rec.config_snapshot["whisper_model"] == "small"
    assert rec.created_at.endswith("Z")


def test_status_transitions_set_timestamps(repo: Repository) -> None:
    sid = _session(repo)
    repo.set_status(sid, SessionStatus.ANALYZING)
    assert repo.get_session(sid).started_at is not None
    repo.set_status(sid, SessionStatus.AWAITING_CONFIRMATION)
    assert repo.get_session(sid).status is SessionStatus.AWAITING_CONFIRMATION
    repo.set_status(sid, SessionStatus.FAILED, error_code="ERR-07")
    rec = repo.get_session(sid)
    assert rec.finished_at is not None
    assert rec.error_code == "ERR-07"


def test_set_subtitle_saves_region_decision(repo: Repository) -> None:
    sid = _session(repo)
    region = {"normalized_rect": [0, 0.75, 1, 0.25], "decision": "CONFIRMED"}
    repo.set_subtitle(sid, SubtitleSource.OCR, generated_srt_path="artifacts/a.srt", region=region)
    rec = repo.get_session(sid)
    assert rec.subtitle_source is SubtitleSource.OCR
    assert rec.subtitle_region == region


# ---------- issues ----------


def test_complete_session_saves_issues_and_marks_completed(repo: Repository) -> None:
    sid = _session(repo)
    repo.complete_session(sid, [_issue(5.0), _issue(1.0)])
    assert repo.get_session(sid).status is SessionStatus.COMPLETED
    assert [i.start_s for i in repo.list_issues(sid)] == [1.0, 5.0]  # 시간순


def test_complete_session_is_atomic(repo: Repository) -> None:
    sid = _session(repo)
    dup = _issue()
    with pytest.raises(sqlite3.IntegrityError):
        repo.complete_session(sid, [dup, dup])  # 같은 id → 실패
    assert repo.list_issues(sid) == []
    assert repo.get_session(sid).status is not SessionStatus.COMPLETED


def test_issue_roundtrip_preserves_fields(repo: Repository) -> None:
    sid = _session(repo)
    original = _issue(kind=IssueKind.SCREEN_DEPENDENT, source=IssueSource.M2_SCREEN_DEPENDENT)
    repo.complete_session(sid, [original])
    [loaded] = repo.list_issues(sid)
    assert loaded.id == original.id
    assert loaded.kind is IssueKind.SCREEN_DEPENDENT
    assert loaded.source is IssueSource.M2_SCREEN_DEPENDENT
    assert loaded.evidence == original.evidence
    assert loaded.speech_text == "3월 15일까지"
    assert loaded.start_s == pytest.approx(1.0)
    assert loaded.review_status is ReviewStatus.UNREVIEWED


def test_set_review_status_never_touches_verdict(repo: Repository) -> None:
    sid = _session(repo)
    issue = _issue(verdict=Verdict.REVIEW_REQUIRED)
    repo.complete_session(sid, [issue])
    repo.set_review_status(issue.id, ReviewStatus.DISMISSED)
    [loaded] = repo.list_issues(sid)
    assert loaded.review_status is ReviewStatus.DISMISSED
    assert loaded.verdict is Verdict.REVIEW_REQUIRED
    repo.set_review_status(issue.id, ReviewStatus.UNREVIEWED)  # 되돌리기
    assert repo.list_issues(sid)[0].review_status is ReviewStatus.UNREVIEWED


def test_set_review_status_unknown_issue_raises(repo: Repository) -> None:
    with pytest.raises(KeyError):
        repo.set_review_status("missing", ReviewStatus.CONFIRMED)


def test_review_counts_add_up(repo: Repository) -> None:
    sid = _session(repo)
    issues = [_issue(1.0), _issue(2.0), _issue(3.0, verdict=Verdict.UNDETERMINED)]
    repo.complete_session(sid, issues)
    repo.set_review_status(issues[0].id, ReviewStatus.CONFIRMED)
    repo.set_review_status(issues[1].id, ReviewStatus.DISMISSED)
    counts = repo.review_counts(sid)
    assert counts.total == 3
    assert (counts.unreviewed, counts.confirmed, counts.dismissed) == (1, 1, 1)
    assert counts.total == counts.unreviewed + counts.confirmed + counts.dismissed
    assert counts.by_verdict[Verdict.UNDETERMINED] == 1


def test_review_state_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "persist.db"
    first = Repository(path)
    sid = _session(first)
    issue = _issue()
    first.complete_session(sid, [issue])
    first.set_review_status(issue.id, ReviewStatus.CONFIRMED)
    first.add_review_time(sid, 4000)
    first.close_all()

    second = Repository(path)
    assert second.list_issues(sid)[0].review_status is ReviewStatus.CONFIRMED
    assert second.get_session(sid).review_duration_ms == 4000
    second.close_all()


def test_add_review_time_accumulates_and_rejects_negative(repo: Repository) -> None:
    sid = _session(repo)
    repo.add_review_time(sid, 1500)
    repo.add_review_time(sid, 500)
    assert repo.get_session(sid).review_duration_ms == 2000
    with pytest.raises(ValueError):
        repo.add_review_time(sid, -1)


def test_repository_is_usable_from_worker_thread(repo: Repository) -> None:
    sid = _session(repo)
    errors: list[BaseException] = []

    def work() -> None:
        try:
            repo.complete_session(sid, [_issue()])
        except BaseException as exc:  # noqa: BLE001 - 스레드 예외를 테스트로 전달
            errors.append(exc)

    t = threading.Thread(target=work)
    t.start()
    t.join()
    assert errors == []
    assert len(repo.list_issues(sid)) == 1


# ---------- stage metrics ----------


def test_stage_metric_unique_per_attempt(repo: Repository) -> None:
    sid = _session(repo)
    repo.record_stage_metric(sid, "STT", duration_ms=1200, processed_count=40, unit="SEGMENT")
    with pytest.raises(sqlite3.IntegrityError):
        repo.record_stage_metric(sid, "STT", duration_ms=1)
    repo.record_stage_metric(sid, "STT", duration_ms=900, attempt_no=2, cache_hit=True)
    metrics = repo.list_stage_metrics(sid)
    assert [(m.stage, m.attempt_no, m.cache_hit) for m in metrics] == [
        ("STT", 1, False),
        ("STT", 2, True),
    ]


# ---------- cache ----------


def test_hash_file_matches_known_sha256(tmp_path: Path) -> None:
    f = tmp_path / "abc.bin"
    f.write_bytes(b"abc")
    assert hash_file(f) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_cache_key_depends_on_engine_model_and_config() -> None:
    sha = "h" * 64
    base = make_key(sha, CacheType.STT, "faster-whisper", "small", {"vad": True})
    assert base == make_key(sha, CacheType.STT, "faster-whisper", "small", {"vad": True})
    other_model = make_key(sha, CacheType.STT, "faster-whisper", "medium", {"vad": True})
    other_config = make_key(sha, CacheType.STT, "faster-whisper", "small", {"vad": False})
    other_type = make_key(sha, CacheType.OCR, "faster-whisper", "small", {"vad": True})
    assert len({base.key, other_model.key, other_config.key, other_type.key}) == 4


def test_cache_roundtrip_with_korean_payload(repo: Repository, tmp_path: Path) -> None:
    cache = FileCache(tmp_path / "cache", repo)
    key = make_key("h" * 64, CacheType.STT, "faster-whisper", "small", {})
    payload = [{"start": 0.0, "end": 1.0, "text": "신청은 홈페이지에서 가능합니다"}]
    assert cache.get(key) is None
    cache.put(key, payload)
    assert cache.get(key) == payload
    assert [p.name for p in (tmp_path / "cache").rglob("*.tmp")] == []  # 임시 파일 잔존 없음


def test_cache_miss_when_file_deleted_or_corrupt(repo: Repository, tmp_path: Path) -> None:
    cache = FileCache(tmp_path / "cache", repo)
    key = make_key("h" * 64, CacheType.OCR, "easyocr", None, {})
    cache.put(key, {"a": 1})
    [stored] = [p for p in (tmp_path / "cache").rglob("*.json")]
    stored.write_text("{broken", encoding="utf-8")
    assert cache.get(key) is None
    cache.put(key, {"a": 2})  # 손상된 항목은 다시 만들 수 있어야 한다
    assert cache.get(key) == {"a": 2}
    stored.unlink()
    assert cache.get(key) is None


# ---------- CSV ----------


def _read_csv(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


def test_export_csv_includes_all_issues_with_states(repo: Repository, tmp_path: Path) -> None:
    sid = _session(repo)
    issues = [_issue(1.0), _issue(65.5, verdict=Verdict.REVIEW_REQUIRED)]
    repo.complete_session(sid, issues)
    repo.set_review_status(issues[1].id, ReviewStatus.DISMISSED)  # 기각도 제거하지 않는다

    out = export_csv(repo, sid, tmp_path / "결과.csv")
    rows = _read_csv(out)
    assert out.read_bytes().startswith(b"\xef\xbb\xbf")  # Excel 한글 호환 BOM
    assert rows[0][:2] == ["문제 ID", "영상 파일명"]
    assert "자동 판정" in rows[0]
    assert "심각도" not in rows[0]
    assert len(rows) == 3
    dismissed = rows[2]
    assert "기각" in dismissed
    assert "검수 필요" in dismissed
    assert "00:01:05.500" in dismissed


def test_export_csv_neutralizes_formula_injection(repo: Repository, tmp_path: Path) -> None:
    sid = _session(repo)
    injected = _issue(speech_text='=HYPERLINK("http://x")', comparison_text="+1")
    repo.complete_session(sid, [injected])
    rows = _read_csv(export_csv(repo, sid, tmp_path / "x.csv"))
    cells = rows[1]
    assert not any(c.startswith(("=", "+", "-", "@")) for c in cells)

"""분석 상태의 기록: 소스 상태 안내와 미분석 구간을 저장하고, 다시 열어도 보여 준다."""

from __future__ import annotations

import csv
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from barrierfree.dev_artifacts import DevArtifacts
from barrierfree.models import AnalysisNote, NoteCode, NoteSource, SpeechSegment
from barrierfree.pipeline import RegionAction, RegionDecision
from barrierfree.review_state import ExportError, ReviewState
from barrierfree.storage import db as db_module
from barrierfree.storage.cache import FileCache
from barrierfree.storage.db import BACKUP_DIR_NAME, SCHEMA_VERSION, connect, migrate
from barrierfree.storage.export import export_csv, export_notes_csv, notes_csv_path
from barrierfree.storage.repository import Repository
from tests import test_pipeline as fixtures
from tests.conftest import MakeVideo
from tests.test_storage import _issue, _session


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[Repository]:
    repository = Repository(tmp_path / "app.db")
    yield repository
    repository.close_all()


@pytest.fixture
def env(tmp_path: Path) -> Iterator[SimpleNamespace]:
    repository = Repository(tmp_path / "app.db")
    cache = FileCache(tmp_path / "cache", repository)
    yield SimpleNamespace(repo=repository, cache=cache, data=tmp_path / "data", tmp=tmp_path)
    repository.close_all()


def _old_database(path: Path) -> None:
    """v0.1.2가 만든 것과 같은 구조(스키마 1)의 DB에 분석 한 건을 넣는다."""
    conn = connect(path)
    conn.executescript(db_module._V1)
    conn.execute(
        "INSERT INTO analysis_session (id, video_path, video_name, video_sha256,"
        " video_duration_ms, status, config_snapshot_json, created_at, finished_at)"
        " VALUES ('old', '/v/a.mp4', 'a.mp4', ?, 1000, 'COMPLETED', '{}',"
        " '2026-01-01', '2026-01-01')",
        ("a" * 64,),
    )
    conn.commit()
    conn.close()


def _version(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


# ---------- DB 업그레이드 ----------


def test_old_database_is_upgraded_and_its_history_stays_readable(tmp_path: Path) -> None:
    path = tmp_path / "barrierfree.db"
    _old_database(path)
    repository = Repository(path)
    try:
        assert _version(path) == SCHEMA_VERSION == 2
        assert repository.get_session("old").video_name == "a.mp4"
        assert repository.list_notes("old") == []
    finally:
        repository.close_all()


def test_upgrade_keeps_a_copy_the_old_app_can_still_open(tmp_path: Path) -> None:
    path = tmp_path / "barrierfree.db"
    _old_database(path)
    Repository(path).close_all()
    [copy] = list((tmp_path / BACKUP_DIR_NAME).glob("*/barrierfree.db"))
    assert _version(copy) == 1
    conn = sqlite3.connect(copy)
    assert conn.execute("SELECT id FROM analysis_session").fetchall() == [("old",)]
    conn.close()


def test_new_or_current_database_makes_no_upgrade_copy(tmp_path: Path) -> None:
    path = tmp_path / "barrierfree.db"
    Repository(path).close_all()
    Repository(path).close_all()
    assert not (tmp_path / BACKUP_DIR_NAME).exists()


def test_database_is_left_as_it_was_when_the_copy_cannot_be_made(tmp_path: Path) -> None:
    path = tmp_path / "barrierfree.db"
    _old_database(path)
    (tmp_path / BACKUP_DIR_NAME).write_text("폴더 자리에 파일이 있다", encoding="utf-8")
    with pytest.raises(OSError):
        Repository(path)
    assert _version(path) == 1


def test_upgrade_copy_includes_rows_still_in_the_write_ahead_log(tmp_path: Path) -> None:
    path = tmp_path / "barrierfree.db"
    _old_database(path)
    writer = connect(path)
    writer.execute("PRAGMA wal_autocheckpoint = 0")
    writer.execute("UPDATE analysis_session SET video_name = 'in-wal.mp4' WHERE id = 'old'")
    writer.commit()  # 본 파일에는 아직 반영되지 않는다
    try:
        Repository(path).close_all()
        [copy] = list((tmp_path / BACKUP_DIR_NAME).glob("*/barrierfree.db"))
        conn = sqlite3.connect(copy)
        assert conn.execute("SELECT video_name FROM analysis_session").fetchall() == [
            ("in-wal.mp4",)
        ]
        conn.close()
    finally:
        writer.close()


def test_a_copy_that_fails_midway_is_removed_and_blocks_the_upgrade(tmp_path: Path) -> None:
    path = tmp_path / "barrierfree.db"
    _old_database(path)

    class _Failing:
        def __init__(self, conn: sqlite3.Connection) -> None:
            self._conn = conn

        def execute(self, sql: str) -> sqlite3.Cursor:
            return self._conn.execute(sql)

        def backup(self, target: sqlite3.Connection) -> None:
            raise sqlite3.OperationalError("disk full")

    conn = connect(path)
    with pytest.raises(OSError):
        db_module.backup_before_upgrade(_Failing(conn), path)  # type: ignore[arg-type]
    conn.close()
    assert list((tmp_path / BACKUP_DIR_NAME).iterdir()) == []
    assert _version(path) == 1


def test_a_failing_upgrade_script_leaves_the_old_structure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "barrierfree.db"
    _old_database(path)
    broken = db_module._V2.replace("PRAGMA user_version = 2;", "SELECT * FROM no_such_table;")
    monkeypatch.setattr(db_module, "_V2", broken)
    with pytest.raises(sqlite3.OperationalError):
        Repository(path)
    assert _version(path) == 1
    conn = sqlite3.connect(path)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    conn.close()
    assert "analysis_note" not in names


def test_without_the_data_lock_an_old_database_is_not_upgraded(tmp_path: Path) -> None:
    # 다른 창(특히 이전 버전)이 쓰고 있을 수 있다. 올리면 그 창이 열지 못하게 된다.
    path = tmp_path / "barrierfree.db"
    _old_database(path)
    with pytest.raises(RuntimeError, match="모두 닫고"):
        Repository(path, allow_upgrade=False)
    assert _version(path) == 1 and not (tmp_path / BACKUP_DIR_NAME).exists()


def test_without_the_data_lock_current_and_new_databases_still_open(tmp_path: Path) -> None:
    Repository(tmp_path / "a.db", allow_upgrade=False).close_all()
    Repository(tmp_path / "a.db", allow_upgrade=False).close_all()


def test_repository_reports_where_the_upgrade_copy_is(tmp_path: Path) -> None:
    path = tmp_path / "barrierfree.db"
    _old_database(path)
    upgraded = Repository(path)
    assert upgraded.upgrade_backup is not None
    assert (upgraded.upgrade_backup / "barrierfree.db").is_file()
    upgraded.close_all()
    again = Repository(path)
    assert again.upgrade_backup is None
    again.close_all()


def test_migrating_twice_is_harmless(tmp_path: Path) -> None:
    conn = connect(tmp_path / "a.db")
    migrate(conn)
    migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


# ---------- 저장 ----------

NOTES = [
    AnalysisNote(NoteCode.EMPTY_TRANSCRIPT, NoteSource.STT, "받아쓰기 없음", 0.0, 12.5),
    AnalysisNote(NoteCode.SUBTITLE_PARTIAL, NoteSource.SUBTITLE, "자막 일부를 읽지 못함"),
]


def test_notes_are_saved_with_the_issues_and_survive_reopening(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "app.db")
    session_id = _session(repository)
    repository.complete_session(session_id, [_issue()], NOTES)
    repository.close_all()
    reopened = Repository(tmp_path / "app.db")
    try:
        assert reopened.list_notes(session_id) == NOTES
    finally:
        reopened.close_all()


def test_a_failed_save_keeps_neither_issues_nor_notes(repo: Repository) -> None:
    session_id = _session(repo)
    duplicate = _issue()
    with pytest.raises(sqlite3.IntegrityError):
        repo.complete_session(session_id, [duplicate, duplicate], NOTES)
    assert repo.list_notes(session_id) == [] and repo.list_issues(session_id) == []


def test_an_interval_needs_both_ends() -> None:
    with pytest.raises(ValueError):
        AnalysisNote(NoteCode.NO_AUDIO, NoteSource.STT, "x", 1.0, None)
    with pytest.raises(ValueError):
        AnalysisNote(NoteCode.NO_AUDIO, NoteSource.STT, "x", 5.0, 1.0)


# ---------- 검수 화면의 안내 ----------


def test_review_notices_come_from_the_saved_notes_and_are_not_repeated(repo: Repository) -> None:
    session_id = _session(repo)
    repo.complete_session(session_id, [_issue()], NOTES)
    state = ReviewState(repo, session_id, extra_notices=["받아쓰기 없음"])
    notices = state.notices()
    assert sum("받아쓰기 없음" in n for n in notices) == 1
    assert any("00:00~00:12" in n and "받아쓰기 없음" in n for n in notices)
    assert any("자막 일부를 읽지 못함" in n for n in notices)


def test_no_issues_is_not_shown_as_clean_when_something_was_not_checked(
    repo: Repository,
) -> None:
    session_id = _session(repo)
    repo.complete_session(session_id, [], NOTES)
    text = " ".join(ReviewState(repo, session_id).notices())
    assert "점검하지 못한 구간" in text
    assert "탐지된 문제가 없습니다" in text and "의미하지 않" in text


def test_no_unchecked_wording_when_there_is_no_unchecked_interval(repo: Repository) -> None:
    session_id = _session(repo)
    repo.complete_session(session_id, [], NOTES[1:])
    assert "점검하지 못한 구간" not in " ".join(ReviewState(repo, session_id).notices())


# ---------- 내보내기 ----------


def _rows(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.reader(handle))


def test_export_writes_the_analysis_state_next_to_the_results(
    repo: Repository, tmp_path: Path
) -> None:
    session_id = _session(repo)
    repo.complete_session(session_id, [_issue()], NOTES)
    out = export_csv(repo, session_id, tmp_path / "결과.csv")
    assert not notes_csv_path(out).exists()  # 지적 CSV 저장은 다른 파일을 건드리지 않는다
    companion = export_notes_csv(repo, session_id, out)
    assert companion == notes_csv_path(out) and companion.name == "결과_분석상태.csv"
    header, first, second = _rows(companion)
    assert header == ["영상 파일명", "대상", "시작 시간", "종료 시간", "내용"]
    assert first[1:4] == ["음성 인식", "00:00:00.000", "00:00:12.500"]
    assert second[1:4] == ["자막", "", ""]
    assert len(_rows(out)[0]) == 11  # 지적 CSV의 형식은 바꾸지 않는다


def test_export_without_notes_says_none_were_recorded(repo: Repository, tmp_path: Path) -> None:
    session_id = _session(repo)
    repo.complete_session(session_id, [])
    rows = _rows(export_notes_csv(repo, session_id, tmp_path / "x.csv"))
    assert len(rows) == 2 and "기록된 안내가 없습니다" in rows[1][4]


def test_an_earlier_state_file_of_ours_is_replaced(repo: Repository, tmp_path: Path) -> None:
    session_id = _session(repo)
    repo.complete_session(session_id, [], NOTES)
    export_notes_csv(repo, session_id, tmp_path / "x.csv")
    assert len(_rows(export_notes_csv(repo, session_id, tmp_path / "x.csv"))) == 3


def test_a_users_own_file_with_that_name_is_not_overwritten(
    repo: Repository, tmp_path: Path
) -> None:
    session_id = _session(repo)
    repo.complete_session(session_id, [], NOTES)
    mine = tmp_path / "x_분석상태.csv"
    mine.write_text("내가 만든 표\n", encoding="utf-8")
    with pytest.raises(FileExistsError):
        export_notes_csv(repo, session_id, tmp_path / "x.csv")
    assert mine.read_text(encoding="utf-8") == "내가 만든 표\n"
    assert sorted(p.name for p in tmp_path.glob("*.csv*")) == ["x_분석상태.csv"]


def test_results_are_saved_even_when_the_state_file_cannot_be(
    repo: Repository, tmp_path: Path
) -> None:
    session_id = _session(repo)
    repo.complete_session(session_id, [_issue()], NOTES)
    (tmp_path / "x_분석상태.csv").mkdir()  # 그 이름에는 쓸 수 없다
    state = ReviewState(repo, session_id)
    saved = state.export_csv(tmp_path / "x.csv")
    assert saved.is_file()
    with pytest.raises(ExportError):
        state.export_notes(saved)
    assert saved.is_file()


# ---------- 파이프라인 ----------


def _run(
    env: SimpleNamespace,
    video: Path,
    *,
    speech: list[SpeechSegment],
    subtitle: bool = True,
    confirm: RegionAction | None = None,
) -> tuple[str, list[AnalysisNote]]:
    pipeline = fixtures.build(
        env,
        fixtures.FakeStt(speech),
        fixtures.FakeOcr(),
        fixtures.Scenes(),
        confirm=(lambda _e: RegionDecision(confirm)) if confirm else None,
    )
    path = fixtures.srt_for(env, fixtures.SENTENCES) if subtitle else None
    result = pipeline.run(video, path)
    notes = env.repo.list_notes(result.session_id)
    assert result.notices == [n.message for n in notes]
    return result.session_id, notes


def _codes(notes: list[AnalysisNote]) -> set[NoteCode]:
    return {n.code for n in notes}


def test_video_without_audio_leaves_the_whole_video_unchecked_for_speech(
    env: SimpleNamespace, make_video: MakeVideo
) -> None:
    _, notes = _run(env, make_video(seconds=10, audio=False), speech=fixtures.SPEECH)
    [note] = [n for n in notes if n.code is NoteCode.NO_AUDIO]
    assert note.source is NoteSource.STT
    assert note.start_s == 0.0 and note.end_s == pytest.approx(10.0, abs=0.2)


def test_empty_transcript_is_recorded_without_claiming_silence(
    env: SimpleNamespace, make_video: MakeVideo
) -> None:
    _, notes = _run(env, make_video(seconds=10), speech=[])
    [note] = [n for n in notes if n.code is NoteCode.EMPTY_TRANSCRIPT]
    assert "구분할 수 없습니다" in note.message and note.end_s is not None
    assert NoteCode.NO_AUDIO not in _codes(notes)


def test_no_screen_text_is_recorded_as_a_state_not_an_interval(
    env: SimpleNamespace, make_video: MakeVideo
) -> None:
    _, notes = _run(env, make_video(seconds=10), speech=fixtures.SPEECH)
    [note] = [n for n in notes if n.code is NoteCode.NO_SCREEN_TEXT]
    assert note.source is NoteSource.OCR and note.start_s is None


def test_development_messages_are_shown_once_but_not_saved(
    env: SimpleNamespace, make_video: MakeVideo, tmp_path: Path
) -> None:
    blocked = tmp_path / "파일"
    blocked.write_text("x", encoding="utf-8")
    pipeline = fixtures.build(
        env, fixtures.FakeStt(fixtures.SPEECH), fixtures.FakeOcr(), fixtures.Scenes(),
        dev=DevArtifacts(audio_dir=blocked / "sub"),
    )
    result = pipeline.run(make_video(seconds=10), fixtures.srt_for(env, fixtures.SENTENCES))
    saved = [n.message for n in env.repo.list_notes(result.session_id)]
    assert any("저장하지 못했습니다" in n for n in result.notices)
    assert not any("저장하지 못했습니다" in n for n in saved)


def test_region_without_subtitles_leaves_the_subtitle_comparison_unchecked(
    env: SimpleNamespace, make_video: MakeVideo
) -> None:
    session_id, notes = _run(
        env, make_video(seconds=10), speech=fixtures.SPEECH, subtitle=False,
        confirm=RegionAction.NONE,
    )
    [note] = [n for n in notes if n.code is NoteCode.SUBTITLE_NONE]
    assert note.source is NoteSource.SUBTITLE and note.end_s is not None
    shown = ReviewState(env.repo, session_id).notices()
    assert sum("자막이 없는" in n for n in shown) == 1

"""배포 앱의 진입점(main.py): multiprocessing 보조 프로세스가 앱을 다시 열지 않는다.

macOS의 라이브러리는 분석 중에 앱 실행 파일을 보조 작업용 인자로 다시 실행한다. PyInstaller는
freeze_support()를 불러야 그 실행을 가려 보조 작업만 하고 끝낸다. 부르지 않으면 앱 전체가
또 열린다.
"""

from __future__ import annotations

import multiprocessing
import runpy
from pathlib import Path

import pytest

import barrierfree.app

ENTRY = str(Path(__file__).resolve().parent.parent / "main.py")


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    recorded: list[str] = []

    def fake_main() -> int:
        recorded.append("app")
        return 0

    monkeypatch.setattr(barrierfree.app, "main", fake_main)
    monkeypatch.setattr("sys.path", [*__import__("sys").path])  # main.py가 경로를 더한다
    return recorded


def test_a_helper_process_ends_before_the_app_starts(
    calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def helper_run() -> None:  # PyInstaller의 freeze_support는 보조 작업을 하고 종료한다
        calls.append("helper")
        raise SystemExit(0)

    monkeypatch.setattr(multiprocessing, "freeze_support", helper_run)
    with pytest.raises(SystemExit):
        runpy.run_path(ENTRY, run_name="__main__")
    assert calls == ["helper"]


def test_a_normal_start_checks_for_helpers_first_and_then_runs_the_app(
    calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(multiprocessing, "freeze_support", lambda: calls.append("check"))
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(ENTRY, run_name="__main__")
    assert stopped.value.code == 0
    assert calls == ["check", "app"]

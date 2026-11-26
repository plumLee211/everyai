"""logging_setup.py: 로그 파일과 전역 예외 처리기."""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from barrierfree import logging_setup


@pytest.fixture(autouse=True)
def restore_logging(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    yield
    logging_setup.close_logging()


def _boom(message: str) -> BaseException:
    try:
        raise ValueError(message)
    except ValueError as exc:
        return exc


def _hook(exc: BaseException) -> None:
    sys.excepthook(type(exc), exc, exc.__traceback__)


def test_log_file_keeps_the_traceback(tmp_path: Path) -> None:
    path = logging_setup.configure_logging(tmp_path)
    assert path == tmp_path / "logs" / "app.log"
    logging.getLogger("barrierfree.test").error("실패", exc_info=_boom("원인 문구"))
    text = path.read_text(encoding="utf-8")
    assert "Traceback" in text and "ValueError: 원인 문구" in text


def test_configuring_twice_does_not_duplicate_lines(tmp_path: Path) -> None:
    logging_setup.configure_logging(tmp_path)
    path = logging_setup.configure_logging(tmp_path)
    assert path is not None
    logging.getLogger("barrierfree.test").error("한 번만")
    assert path.read_text(encoding="utf-8").count("한 번만") == 1


def test_unusable_log_folder_does_not_stop_the_app(tmp_path: Path) -> None:
    blocker = tmp_path / "파일"
    blocker.write_text("x", encoding="utf-8")
    assert logging_setup.configure_logging(blocker) is None


def test_unhandled_exception_is_logged_and_reported_once_per_cause(tmp_path: Path) -> None:
    path = logging_setup.configure_logging(tmp_path)
    shown: list[str] = []
    logging_setup.install_exception_hooks(shown.append)
    first = _boom("같은 오류")
    _hook(first)
    _hook(first)  # 타이머처럼 반복되는 오류로 창이 계속 뜨면 안 된다
    assert shown == ["ValueError: 같은 오류"]
    assert path is not None and path.read_text(encoding="utf-8").count("같은 오류") >= 2


def test_a_failing_notifier_does_not_raise_from_the_hook(tmp_path: Path) -> None:
    def broken(_summary: str) -> None:
        raise RuntimeError("창을 띄울 수 없음")

    logging_setup.install_exception_hooks(broken)
    _hook(_boom("원래 오류"))


def test_thread_exceptions_are_logged(tmp_path: Path) -> None:
    path = logging_setup.configure_logging(tmp_path)
    logging_setup.install_exception_hooks(lambda _summary: None)

    def fail() -> None:
        raise ValueError("스레드 오류")

    thread = threading.Thread(target=fail)
    thread.start()
    thread.join()
    assert path is not None and "ValueError: 스레드 오류" in path.read_text(encoding="utf-8")


def test_exceptions_off_the_main_thread_never_open_a_window(tmp_path: Path) -> None:
    """QThread의 예외도 sys.excepthook으로 온다. 그 스레드에서 위젯을 만들면 안 된다."""
    path = logging_setup.configure_logging(tmp_path)
    shown: list[str] = []
    logging_setup.install_exception_hooks(shown.append)
    thread = threading.Thread(target=lambda: _hook(_boom("작업 스레드")))
    thread.start()
    thread.join()
    assert shown == []
    assert path is not None and "작업 스레드" in path.read_text(encoding="utf-8")


def test_closing_restores_the_root_log_level(tmp_path: Path) -> None:
    before = logging.getLogger().level
    logging_setup.configure_logging(tmp_path)
    logging_setup.close_logging()
    assert logging.getLogger().level == before

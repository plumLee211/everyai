"""로그 파일과 전역 예외 처리기.

배포본은 콘솔 창 없이 실행되므로, 원인을 추적할 수 있는 곳은 `{데이터 폴더}/logs/app.log`뿐이다.
PyQt는 슬롯에서 처리되지 않은 예외가 나면 앱을 종료시키는데, `sys.excepthook`을 바꿔 두면
종료하지 않는다. 그래서 여기서 기록하고 사용자에게 알린다.
"""

from __future__ import annotations

import logging
import platform
import sys
import threading
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType

LOG_DIR_NAME = "logs"
LOG_FILE_NAME = "app.log"
_MAX_BYTES = 1_000_000
_BACKUPS = 3
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

logger = logging.getLogger(__name__)
_handler: logging.Handler | None = None
_previous_level = logging.WARNING


def log_dir(data_dir: Path) -> Path:
    return data_dir / LOG_DIR_NAME


def close_logging() -> None:
    global _handler
    if _handler is not None:
        logging.getLogger().removeHandler(_handler)
        logging.getLogger().setLevel(_previous_level)
        _handler.close()
        _handler = None


def configure_logging(data_dir: Path) -> Path | None:
    """로그 파일을 연다. 열 수 없어도 앱은 계속 실행해야 하므로 None을 돌려준다."""
    global _handler, _previous_level
    close_logging()
    path = log_dir(data_dir) / LOG_FILE_NAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
        )
    except OSError:
        return None
    handler.setFormatter(logging.Formatter(_FORMAT))
    root = logging.getLogger()
    root.addHandler(handler)
    _previous_level = root.level
    root.setLevel(logging.INFO)
    _handler = handler
    logger.info("시작: %s, Python %s", platform.platform(), platform.python_version())
    return path


def install_exception_hooks(notify: Callable[[str], None]) -> None:
    """처리되지 않은 예외를 기록한다. 화면 스레드의 예외는 `notify`로 한 번 알린다.

    같은 위치의 같은 예외는 다시 알리지 않는다(타이머에서 반복되면 창이 끝없이 뜬다).
    다른 스레드의 예외는 기록만 한다(그 스레드에서 위젯을 만들면 안 된다).
    """
    seen: set[tuple[str, str, int]] = set()
    notifying = False

    def on_exception(
        kind: type[BaseException], exc: BaseException, tb: TracebackType | None
    ) -> None:
        nonlocal notifying
        logger.critical("처리되지 않은 예외", exc_info=(kind, exc, tb))
        if threading.current_thread() is not threading.main_thread():
            return  # QThread의 예외도 여기로 온다. 화면 스레드 밖에서는 창을 만들지 않는다
        last = tb
        while last is not None and last.tb_next is not None:
            last = last.tb_next
        where = (
            kind.__name__,
            last.tb_frame.f_code.co_filename if last else "",
            last.tb_lineno if last else 0,
        )
        if where in seen or notifying:
            return
        seen.add(where)
        notifying = True
        try:
            notify(f"{kind.__name__}: {exc}")
        except Exception:  # noqa: BLE001 - 예외 처리기에서 다시 예외가 나가면 안 된다
            logger.exception("오류 안내를 표시하지 못했습니다")
        finally:
            notifying = False

    def on_thread_exception(args: threading.ExceptHookArgs) -> None:
        value = args.exc_value or args.exc_type()
        logger.critical(
            "스레드에서 처리되지 않은 예외",
            exc_info=(args.exc_type, value, args.exc_traceback),
        )

    sys.excepthook = on_exception
    threading.excepthook = on_thread_exception

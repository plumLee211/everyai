"""파일 선택과 메시지 상자를 한곳에 모은 인터페이스.

화면 코드가 `QFileDialog`/`QMessageBox`를 직접 부르면 테스트가 모달 창에서 멈춘다. 화면은
`Dialogs`만 알고, 실제 앱은 `QtDialogs`를, 테스트는 가짜를 주입한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from PyQt6.QtWidgets import QFileDialog, QMessageBox, QWidget

from barrierfree.config import AnalysisConfig


class Dialogs(Protocol):
    def ask_open_video(self) -> Path | None: ...
    def ask_open_subtitle(self) -> Path | None: ...
    def ask_save_csv(self, default_name: str) -> Path | None: ...
    def info(self, title: str, text: str) -> None: ...
    def warn(self, title: str, text: str) -> None: ...
    def confirm(self, title: str, text: str) -> bool: ...


class QtDialogs:
    def __init__(self, parent: QWidget | None = None) -> None:
        self._parent = parent

    def _pick(self, title: str, filter_text: str) -> Path | None:
        name, _ = QFileDialog.getOpenFileName(self._parent, title, "", filter_text)
        return Path(name) if name else None

    def ask_open_video(self) -> Path | None:
        patterns = " ".join(f"*{ext}" for ext in AnalysisConfig().video_extensions)
        return self._pick("영상 파일 선택", f"영상 파일 ({patterns});;모든 파일 (*)")

    def ask_open_subtitle(self) -> Path | None:
        return self._pick("자막 파일 선택", "자막 파일 (*.srt *.smi *.sami);;모든 파일 (*)")

    def ask_save_csv(self, default_name: str) -> Path | None:
        name, _ = QFileDialog.getSaveFileName(
            self._parent, "검수 결과 저장", default_name, "CSV 파일 (*.csv)"
        )
        return Path(name) if name else None

    def info(self, title: str, text: str) -> None:
        QMessageBox.information(self._parent, title, text)

    def warn(self, title: str, text: str) -> None:
        QMessageBox.warning(self._parent, title, text)

    def confirm(self, title: str, text: str) -> bool:
        answer = QMessageBox.question(
            self._parent,
            title,
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return bool(answer == QMessageBox.StandardButton.Yes)

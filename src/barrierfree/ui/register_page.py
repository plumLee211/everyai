"""영상 등록 화면 (SCR-01, docs/project/ui-spec.md 5장).

- 영상은 등록 즉시 검증한다(존재, 형식, 길이). 실패하면 등록하지 않고, 이미 정상 등록된 영상이
  있으면 그대로 유지한다.
- 새 영상을 등록하면 이전 자막을 초기화한다(다른 영상의 자막이 잘못 연결되는 것을 막는다).
- 잘못된 자막은 오류를 안내하고 분석 시작을 차단한다. 일부 구간만 못 읽은 자막은 경고만 한다.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from barrierfree.config import AnalysisConfig
from barrierfree.media.ffmpeg import VideoInfo, validate_video
from barrierfree.media.subtitles import LoadedSubtitle, load_subtitle_file
from barrierfree.pipeline import explain_error
from barrierfree.ui.dialogs import Dialogs
from barrierfree.ui.issue_model import format_time

HINT_NO_VIDEO = "분석할 영상 파일을 선택해 주세요."
NOTICE_WITH_SUBTITLE = "등록한 자막 파일을 기준으로 음성·자막 일치 여부를 점검합니다."
NOTICE_WITHOUT_SUBTITLE = (
    "자막 파일이 없습니다. 영상에 포함된 자막을 자동 추출하여 점검합니다. "
    "영상에도 자막이 없다면 자막 부재로 안내합니다."
)


class RegisterPage(QWidget):
    start_requested = pyqtSignal(object, object)  # (영상 Path, 자막 Path | None)
    settings_requested = pyqtSignal()

    def __init__(
        self,
        dialogs: Dialogs,
        video_validator: Callable[[Path], VideoInfo] | None = None,
        subtitle_loader: Callable[[Path], LoadedSubtitle] = load_subtitle_file,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._dialogs = dialogs
        config = AnalysisConfig()
        self._validate_video = video_validator or (lambda p: validate_video(p, config))
        self._load_subtitle = subtitle_loader
        self._video: Path | None = None
        self._subtitle: Path | None = None
        self._subtitle_blocked = False
        self._busy = False

        self.title_label = QLabel("영상 등록")
        self.hint_label = QLabel(HINT_NO_VIDEO)
        self.video_button = QPushButton("영상 파일 선택")
        self.video_info_label = QLabel("")
        self.video_error_label = QLabel("")
        self.video_remove_button = QPushButton("영상 삭제")
        self.subtitle_button = QPushButton("자막 파일 선택 (선택 사항)")
        self.subtitle_info_label = QLabel("")
        self.subtitle_error_label = QLabel("")
        self.subtitle_remove_button = QPushButton("자막 삭제")
        self.notice_label = QLabel(NOTICE_WITHOUT_SUBTITLE)
        self.settings_summary_label = QLabel("")
        self.settings_button = QPushButton("설정 변경")
        self.start_button = QPushButton("분석 시작")

        for label in (self.hint_label, self.notice_label, self.video_error_label,
                      self.subtitle_error_label):
            label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addWidget(self.title_label)
        layout.addWidget(self.hint_label)
        for button, info, error, remove in (
            (self.video_button, self.video_info_label, self.video_error_label,
             self.video_remove_button),
            (self.subtitle_button, self.subtitle_info_label, self.subtitle_error_label,
             self.subtitle_remove_button),
        ):
            row = QHBoxLayout()
            row.addWidget(button)
            row.addWidget(info, 1)
            row.addWidget(remove)
            layout.addLayout(row)
            layout.addWidget(error)
        layout.addWidget(self.notice_label)
        layout.addStretch(1)
        footer = QHBoxLayout()
        footer.addWidget(self.settings_summary_label, 1)
        footer.addWidget(self.settings_button)
        footer.addWidget(self.start_button)
        layout.addLayout(footer)

        self.video_button.clicked.connect(self._pick_video)
        self.subtitle_button.clicked.connect(self._pick_subtitle)
        self.video_remove_button.clicked.connect(self.clear_video)
        self.subtitle_remove_button.clicked.connect(self.clear_subtitle)
        self.settings_button.clicked.connect(self.settings_requested.emit)
        self.start_button.clicked.connect(self._start)
        self._refresh()

    # ----- 상태 -----

    @property
    def video_path(self) -> Path | None:
        return self._video

    @property
    def subtitle_path(self) -> Path | None:
        return self._subtitle

    def set_settings_summary(self, text: str) -> None:
        self.settings_summary_label.setText(text)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._refresh()

    # ----- 영상 -----

    def set_video(self, path: Path) -> None:
        try:
            info = self._validate_video(path)
        except Exception as exc:  # noqa: BLE001 - 어떤 검증 실패든 사용자 문구로 바꾼다
            error = explain_error(exc)
            self.video_error_label.setText(f"{error.user_message} ({error.code})")
            return  # 기존에 정상 등록된 영상은 그대로 둔다
        self._video = path
        self.video_error_label.setText("")
        self.video_info_label.setText(f"{path.name} · {format_time(info.duration_s)}")
        self.clear_subtitle()

    def clear_video(self) -> None:
        self._video = None
        self.video_info_label.setText("")
        self.video_error_label.setText("")
        self.clear_subtitle()

    # ----- 자막 -----

    def set_subtitle(self, path: Path) -> None:
        if self._video is None:
            self._subtitle_blocked = False
            self._subtitle = None
            self.subtitle_error_label.setText("먼저 영상을 선택해 주세요.")
            self._refresh()
            return
        try:
            loaded = self._load_subtitle(path)
        except Exception as exc:  # noqa: BLE001
            error = explain_error(exc)
            self._subtitle = None
            self._subtitle_blocked = True
            self.subtitle_info_label.setText("")
            self.subtitle_error_label.setText(f"{error.user_message} ({error.code})")
            self._refresh()
            return
        self._subtitle = path
        self._subtitle_blocked = False
        self.subtitle_error_label.setText("")
        text = path.name
        if loaded.skipped_blocks:
            text += (
                f" · {loaded.skipped_blocks}개 구간을 읽지 못했습니다"
                "(해당 구간은 점검에서 빠집니다)"
            )
        self.subtitle_info_label.setText(text)
        self._refresh()

    def clear_subtitle(self) -> None:
        self._subtitle = None
        self._subtitle_blocked = False
        self.subtitle_info_label.setText("")
        self.subtitle_error_label.setText("")
        self._refresh()

    # ----- 내부 -----

    def _pick_video(self) -> None:
        path = self._dialogs.ask_open_video()
        if path is not None:
            self.set_video(path)

    def _pick_subtitle(self) -> None:
        path = self._dialogs.ask_open_subtitle()
        if path is not None:
            self.set_subtitle(path)

    def _can_start(self) -> bool:
        return self._video is not None and not self._subtitle_blocked and not self._busy

    def _start(self) -> None:
        if self._can_start() and self._video is not None:
            self.start_requested.emit(self._video, self._subtitle)

    def _refresh(self) -> None:
        self.start_button.setEnabled(self._can_start())
        for button in (self.video_button, self.subtitle_button, self.settings_button):
            button.setEnabled(not self._busy)
        self.video_remove_button.setEnabled(not self._busy and self._video is not None)
        self.subtitle_remove_button.setEnabled(
            not self._busy and (self._subtitle is not None or self._subtitle_blocked)
        )
        self.hint_label.setText("" if self._video is not None else HINT_NO_VIDEO)
        self.notice_label.setText(
            NOTICE_WITH_SUBTITLE if self._subtitle is not None else NOTICE_WITHOUT_SUBTITLE
        )

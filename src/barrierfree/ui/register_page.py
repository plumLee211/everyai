"""영상 등록 화면 (SCR-01, docs/project/ui-spec.md 5장).

- 영상은 등록 즉시 검증한다(존재, 형식, 길이). 실패하면 등록하지 않고, 이미 정상 등록된 영상이
  있으면 그대로 유지한다.
- 새 영상을 등록하면 이전 자막을 초기화한다(다른 영상의 자막이 잘못 연결되는 것을 막는다).
- 잘못된 자막은 오류를 안내하고 분석 시작을 차단한다. 일부 구간만 못 읽은 자막은 경고만 한다.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from barrierfree.config import AnalysisConfig
from barrierfree.media.ffmpeg import VideoInfo, validate_video
from barrierfree.media.subtitles import LoadedSubtitle, load_subtitle_file
from barrierfree.pipeline import explain_error
from barrierfree.ui import theme
from barrierfree.ui.dialogs import Dialogs
from barrierfree.ui.issue_model import format_time

HINT_NO_VIDEO = "영상 파일을 선택하세요"
SUBTITLE_PLACEHOLDER = "연결할 자막 파일을 선택하세요"
SUPPORTED_NOTE = "MP4, MOV, MKV, AVI, WMV 파일을 지원합니다"
NOTICE_WITH_SUBTITLE = "등록한 자막 파일을 기준으로 음성·자막 일치 여부를 점검합니다."
NOTICE_WITHOUT_SUBTITLE = (
    "자막 파일이 없습니다. 영상에 포함된 자막을 자동 추출하여 점검합니다. "
    "영상에도 자막이 없다면 자막 부재로 안내합니다."
)
SERVICE_NOTE = (
    "영상의 음성·자막·화면 정보를 분석해 검수할 항목을 제안합니다. "
    "분석 결과는 사용자가 직접 확인하고 확정합니다."
)
SUBTITLE_MODE_WITH = "OCR + 등록 자막 비교"
SUBTITLE_MODE_WITHOUT = "영상 속 자막 자동 추출"


def _file_size_text(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    if size >= 1024**3:
        return f"{size / 1024**3:.1f} GB"
    return f"{size / 1024**2:.0f} MB"


def _add(card: QFrame, item: QWidget | QHBoxLayout) -> None:
    layout = card.layout()
    assert isinstance(layout, QVBoxLayout)
    if isinstance(item, QWidget):
        layout.addWidget(item)
    else:
        layout.addLayout(item)


def _value_tile(title: str, value: QLabel) -> QFrame:
    box = theme.tile()
    value.setStyleSheet("font-weight: 600;")
    value.setWordWrap(True)
    _add(box, theme.role(QLabel(title), "caption"))
    _add(box, value)
    return box


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

        # 화면 1(영상 없음)
        self.title_label = theme.role(QLabel("접근성 품질 점검을 시작해 보세요"), "title")
        self.intro_label = theme.role(
            QLabel("영상과 자막을 등록하면 AI가 접근성 관련 문제를 찾아드립니다."), "muted"
        )
        self.hint_label = QLabel(HINT_NO_VIDEO)
        self.hint_label.setStyleSheet("font-size: 15px; font-weight: 600;")
        self.video_button = QPushButton("영상 파일 선택")
        theme.primary(self.video_button)

        # 화면 2(영상 등록 완료)
        self.ready_title_label = theme.role(QLabel("분석할 항목을 확인하세요"), "title")
        self.video_info_label = QLabel("")  # 파일 이름
        self.video_info_label.setStyleSheet("font-weight: 600;")
        self.video_meta_label = theme.role(QLabel(""), "caption")
        self.video_remove_button = QPushButton("영상 삭제")
        theme.link(self.video_remove_button)
        self.subtitle_info_label = QLabel(SUBTITLE_PLACEHOLDER)
        self.subtitle_button = QPushButton("자막 파일 선택")
        self.subtitle_remove_button = QPushButton("자막 삭제")
        theme.link(self.subtitle_remove_button)
        self.subtitle_error_label = theme.role(QLabel(""), "error")
        self.notice_label = theme.role(QLabel(NOTICE_WITHOUT_SUBTITLE), "caption")
        self.device_value_label = QLabel("")
        self.model_value_label = QLabel("")
        self.subtitle_mode_label = QLabel(SUBTITLE_MODE_WITHOUT)
        self.footer_note_label = theme.role(
            QLabel("설정은 분석 시작 전 언제든지 변경할 수 있습니다."), "caption"
        )
        self.settings_button = QPushButton("분석 설정")
        self.start_button = QPushButton("분석 시작")
        theme.primary(self.start_button)

        self.video_error_label = theme.role(QLabel(""), "error")
        for label in (self.hint_label, self.notice_label, self.video_error_label,
                      self.subtitle_error_label, self.subtitle_info_label, self.intro_label):
            label.setWordWrap(True)
        self.video_error_label.setVisible(False)
        self.subtitle_error_label.setVisible(False)

        self._views = QStackedWidget()
        self._views.addWidget(self._build_empty_view())
        self._views.addWidget(self._build_ready_view())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 20)
        layout.addWidget(self.video_error_label)
        layout.addWidget(self._views, 1)

        self.video_button.clicked.connect(self._pick_video)
        self.subtitle_button.clicked.connect(self._pick_subtitle)
        self.video_remove_button.clicked.connect(self.clear_video)
        self.subtitle_remove_button.clicked.connect(self.clear_subtitle)
        self.settings_button.clicked.connect(self.settings_requested.emit)
        self.start_button.clicked.connect(self._start)
        self._refresh()

    # ----- 화면 구성 -----

    def _build_empty_view(self) -> QWidget:
        start = theme.card((24, 24, 24, 24), 14)
        start.setMaximumWidth(844)
        _add(start, self.title_label)
        _add(start, self.intro_label)

        drop = QFrame()
        drop.setObjectName("DropArea")
        drop.setStyleSheet(
            f"#DropArea {{ background: {theme.COLORS['tile_bg']}; border: 1px solid "
            f"{theme.COLORS['border']}; border-radius: 10px; }}"
        )
        drop_layout = QVBoxLayout(drop)
        drop_layout.setContentsMargins(24, 28, 24, 28)
        drop_layout.setSpacing(10)
        icon = QLabel("↑")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(52, 52)
        icon.setStyleSheet(
            f"background: {theme.COLORS['primary_weak']}; color: {theme.COLORS['primary']};"
            "border-radius: 26px; font-size: 24px; font-weight: 700;"
        )
        support = theme.role(QLabel(SUPPORTED_NOTE), "caption")
        center = Qt.AlignmentFlag.AlignHCenter
        drop_layout.addWidget(icon, alignment=center)
        drop_layout.addWidget(self.hint_label, alignment=center)
        drop_layout.addWidget(support, alignment=center)
        drop_layout.addWidget(self.video_button, alignment=center)
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _add(start, drop)

        info = theme.banner("info")
        heading = QLabel("서비스 안내")
        heading.setStyleSheet(f"color: {theme.COLORS['primary']}; font-weight: 600;")
        body = QLabel(SERVICE_NOTE)
        body.setWordWrap(True)
        _add(info, heading)
        _add(info, body)
        _add(start, info)
        layout = start.layout()
        assert isinstance(layout, QVBoxLayout)
        layout.addStretch(1)

        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 24, 0, 0)
        row.addStretch(1)
        row.addWidget(start, 10)
        row.addStretch(1)
        return page

    def _build_ready_view(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self.ready_title_label)

        video_card = theme.card()
        _add(video_card, theme.role(QLabel("등록된 영상"), "section"))
        row = QHBoxLayout()
        thumb = QFrame()
        thumb.setFixedSize(72, 44)
        thumb.setStyleSheet(f"background: {theme.COLORS['neutral_bg']}; border-radius: 6px;")
        names = QVBoxLayout()
        names.setSpacing(2)
        names.addWidget(self.video_info_label)
        names.addWidget(self.video_meta_label)
        row.addWidget(thumb)
        row.addLayout(names, 1)
        row.addWidget(theme.chip("등록 완료", "success"))
        row.addWidget(self.video_remove_button)
        _add(video_card, row)
        layout.addWidget(video_card)

        subtitle_card = theme.card()
        _add(subtitle_card, theme.role(QLabel("자막 파일"), "section"))
        sub_row = QHBoxLayout()
        sub_row.addWidget(self.subtitle_info_label, 1)
        sub_row.addWidget(self.subtitle_remove_button)
        sub_row.addWidget(self.subtitle_button)
        _add(subtitle_card, sub_row)
        _add(subtitle_card, self.subtitle_error_label)
        _add(subtitle_card, self.notice_label)
        layout.addWidget(subtitle_card)

        summary = theme.card()
        _add(summary, theme.role(QLabel("분석 설정 요약"), "section"))
        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        for title, value in (
            ("감지 장치", self.device_value_label),
            ("추천 모델", self.model_value_label),
            ("자막 분석", self.subtitle_mode_label),
        ):
            tiles.addWidget(_value_tile(title, value), 1)
        _add(summary, tiles)
        layout.addWidget(summary)

        footer = QHBoxLayout()
        footer.addWidget(self.footer_note_label, 1)
        footer.addWidget(self.settings_button)
        footer.addWidget(self.start_button)
        layout.addLayout(footer)
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        content.setStyleSheet("")
        scroll.setWidget(content)
        scroll.viewport().setAutoFillBackground(False)
        content.setAutoFillBackground(False)
        return scroll

    # ----- 상태 -----

    @property
    def video_path(self) -> Path | None:
        return self._video

    @property
    def subtitle_path(self) -> Path | None:
        return self._subtitle

    def set_settings_summary(self, *, device: str, model: str) -> None:
        """분석 설정 요약 타일(감지 장치, 추천 모델)을 채운다."""
        self.device_value_label.setText(device)
        self.model_value_label.setText(model)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._refresh()

    # ----- 영상 -----

    def set_video(self, path: Path) -> None:
        try:
            info = self._validate_video(path)
        except Exception as exc:  # noqa: BLE001 - 어떤 검증 실패든 사용자 문구로 바꾼다
            error = explain_error(exc)
            self._set_error(self.video_error_label, f"{error.user_message} ({error.code})")
            return  # 기존에 정상 등록된 영상은 그대로 둔다
        self._video = path
        self._set_error(self.video_error_label, "")
        self.video_info_label.setText(path.name)
        meta = [
            f"{info.width} × {info.height}",
            format_time(info.duration_s),
            _file_size_text(path),
        ]
        self.video_meta_label.setText(" · ".join(part for part in meta if part))
        self.clear_subtitle()

    def clear_video(self) -> None:
        self._video = None
        self.video_info_label.setText("")
        self.video_meta_label.setText("")
        self._set_error(self.video_error_label, "")
        self.clear_subtitle()

    # ----- 자막 -----

    def set_subtitle(self, path: Path) -> None:
        if self._video is None:
            self._subtitle_blocked = False
            self._subtitle = None
            self._set_error(self.subtitle_error_label, "먼저 영상을 선택해 주세요.")
            self._refresh()
            return
        try:
            loaded = self._load_subtitle(path)
        except Exception as exc:  # noqa: BLE001
            error = explain_error(exc)
            self._subtitle = None
            self._subtitle_blocked = True
            self.subtitle_info_label.setText("")
            self._set_error(self.subtitle_error_label, f"{error.user_message} ({error.code})")
            self._refresh()
            return
        self._subtitle = path
        self._subtitle_blocked = False
        self._set_error(self.subtitle_error_label, "")
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
        self._set_error(self.subtitle_error_label, "")
        self._refresh()

    # ----- 내부 -----

    @staticmethod
    def _set_error(label: QLabel, text: str) -> None:
        label.setText(text)
        label.setVisible(bool(text))

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
        self.subtitle_mode_label.setText(
            SUBTITLE_MODE_WITH if self._subtitle is not None else SUBTITLE_MODE_WITHOUT
        )
        if not self.subtitle_info_label.text() and not self._subtitle_blocked:
            self.subtitle_info_label.setText(SUBTITLE_PLACEHOLDER)
        self._views.setCurrentIndex(0 if self._video is None else 1)

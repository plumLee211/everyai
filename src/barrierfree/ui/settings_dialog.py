"""분석 설정 다이얼로그 (SCR-04, docs/project/ui-spec.md 8장, Figma 시안 05).

감지된 장치와 권장 모델을 먼저 보여 주고, 고급 설정(모델 선택, 모델 경로)과 OCR 자막 영역 조정을
이어서 둔다. 기술적인 모델 설정을 몰라도 기본 분석을 실행할 수 있어야 한다.

시안의 체크박스 3개(음성 인식 정확도 우선, 장면 분석 포함, 결과에 타임코드 표시)는 대응하는 분석
기능이 없어 넣지 않았다.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPaintEvent
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from barrierfree.engines.device import SUPPORTED_MODELS, DeviceInfo, ModelRecommendation
from barrierfree.settings import (
    DEFAULT_REGION_RATIO,
    MAX_REGION_RATIO,
    MIN_REGION_RATIO,
    AppSettings,
)
from barrierfree.ui import theme


class RegionPreview(QWidget):
    """영상 화면 모양의 미리보기. 하단에서 자막 인식 영역이 차지하는 비율을 보여 준다."""

    def __init__(self, ratio: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("OcrPreview")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumHeight(110)
        self._ratio = ratio

    def set_ratio(self, ratio: float) -> None:
        self._ratio = ratio
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt 이름
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        height = self.height() - 20
        width = min(self.width() - 40, height * 16 / 9)
        screen = QRectF((self.width() - width) / 2, 10, width, height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#2A2F3C"))
        painter.drawRoundedRect(screen, 6, 6)
        band_h = screen.height() * self._ratio
        band = QRectF(screen.left() + 8, screen.bottom() - band_h - 4, screen.width() - 16, band_h)
        painter.setBrush(QColor(theme.COLORS["primary_weak"]))
        painter.drawRoundedRect(band, 4, 4)
        painter.setPen(QColor(theme.COLORS["primary"]))
        painter.drawText(band, Qt.AlignmentFlag.AlignCenter, "자막 인식 영역")
        painter.setPen(QColor("#C9CEDA"))
        painter.drawText(
            QRectF(screen.left(), screen.top(), screen.width(), max(1.0, screen.height() - band_h)),
            Qt.AlignmentFlag.AlignCenter,
            "영상 화면 미리보기",
        )


def _divider() -> QFrame:
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {theme.COLORS['border']}; border: 0;")
    return line


def _setting_row(title: str, value: QLabel) -> QFrame:
    row = theme.tile()
    layout = row.layout()
    assert isinstance(layout, QVBoxLayout)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(2)
    layout.addWidget(theme.role(QLabel(title), "caption"))
    value.setStyleSheet("font-weight: 600;")
    value.setWordWrap(True)
    layout.addWidget(value)
    return row


class SettingsDialog(QDialog):
    def __init__(
        self,
        device: DeviceInfo,
        recommendation: ModelRecommendation,
        current: AppSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("분석 설정")
        self.setMinimumWidth(680)
        self._recommendation = recommendation

        gpu = "사용 가능" if device.cuda_available else "사용 불가"
        self.device_label = QLabel(
            f"CPU: {device.cpu_name} ({device.cpu_count}코어) · 메모리 {device.ram_gb:.0f}GB · "
            f"GPU(NVIDIA CUDA): {gpu}"
        )
        self.recommendation_label = QLabel(f"권장 음성 인식 모델: {recommendation.model}")
        self.current_model_label = theme.role(QLabel(), "caption")
        self.warning_label = QLabel(recommendation.warning or "")
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet(f"color: {theme.COLORS['warn_fg']};")
        self.warning_label.setHidden(not recommendation.warning)

        # 고급 설정: 시안처럼 항상 펼쳐 둔다.
        self.advanced_box = QFrame()
        self.advanced_box.setProperty("tile", True)

        self.model_combo = QComboBox()
        self.model_combo.addItem(f"권장 모델 사용 ({recommendation.model})", None)
        for model in SUPPORTED_MODELS:
            self.model_combo.addItem(model, model)
        index = self.model_combo.findData(current.whisper_model)
        self.model_combo.setCurrentIndex(max(index, 0))
        self.model_notice = theme.role(
            QLabel("모델을 바꾸면 인식 정확도와 처리 시간이 달라질 수 있습니다."), "caption"
        )
        self.model_notice.setWordWrap(True)

        self.model_dir_edit = QLineEdit(current.model_dir or "")
        self.model_dir_edit.setPlaceholderText("비워 두면 기본 위치를 사용합니다")

        self.ratio_slider = QSlider(Qt.Orientation.Horizontal)
        self.ratio_slider.setRange(round(MIN_REGION_RATIO * 100), round(MAX_REGION_RATIO * 100))
        self.ratio_slider.setValue(round(current.subtitle_region_ratio * 100))
        self.ratio_label = theme.role(QLabel(), "caption")
        self.preview = RegionPreview(current.subtitle_region_ratio)

        self.restore_button = QPushButton("권장 설정 복원")
        theme.link(self.restore_button)
        self.close_button = QPushButton("×")
        theme.link(self.close_button)
        self.close_button.setAccessibleName("닫기")
        self.cancel_button = QPushButton("취소")
        self.ok_button = QPushButton("확인")
        theme.primary(self.ok_button)
        self.ok_button.setDefault(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(12)

        title_row = QHBoxLayout()
        title = QLabel("분석 설정")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        title_row.addWidget(title, 1)
        title_row.addWidget(self.close_button)
        root.addLayout(title_row)
        intro = theme.role(QLabel("감지 환경에 맞는 분석 장치와 모델을 선택합니다."), "caption")
        root.addWidget(intro)
        root.addWidget(_divider())

        root.addWidget(theme.role(QLabel("감지된 장치 및 추천 모델"), "section"))
        root.addWidget(_setting_row("감지 장치", self.device_label))
        root.addWidget(_setting_row("추천 모델", self.recommendation_label))
        root.addWidget(self.current_model_label)
        root.addWidget(self.warning_label)
        root.addWidget(_divider())

        root.addWidget(theme.role(QLabel("고급 설정"), "section"))
        form = QVBoxLayout(self.advanced_box)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(6)
        form.addWidget(theme.role(QLabel("음성 인식 모델"), "caption"))
        form.addWidget(self.model_combo)
        form.addWidget(self.model_notice)
        form.addWidget(theme.role(QLabel("모델 저장 경로"), "caption"))
        form.addWidget(self.model_dir_edit)
        form.addWidget(self.restore_button, alignment=Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self.advanced_box)
        root.addWidget(_divider())

        root.addWidget(theme.role(QLabel("OCR 자막 영역 조정"), "section"))
        root.addWidget(self.preview)
        slider_row = QHBoxLayout()
        slider_row.addWidget(self.ratio_slider, 1)
        slider_row.addWidget(self.ratio_label)
        root.addLayout(slider_row)
        note = theme.role(QLabel("인식할 자막 영역을 하단 비율로 조정할 수 있습니다."), "caption")
        root.addWidget(note)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.ok_button)
        root.addLayout(buttons)

        self.model_combo.currentIndexChanged.connect(self._refresh)
        self.ratio_slider.valueChanged.connect(self._refresh)
        self.restore_button.clicked.connect(self._restore_recommended)
        self.ok_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        self.close_button.clicked.connect(self.reject)
        self._refresh()

    def _chosen_model(self) -> str | None:
        data = self.model_combo.currentData()
        return data if isinstance(data, str) else None

    def _refresh(self, *_args: object) -> None:
        chosen = self._chosen_model()
        self.current_model_label.setText(f"현재 모델: {chosen or self._recommendation.model}")
        self.model_notice.setHidden(chosen is None)
        self.ratio_label.setText(f"영상 하단 {self.ratio_slider.value()}%")
        self.preview.set_ratio(self.ratio_slider.value() / 100)

    def _restore_recommended(self) -> None:
        """권장 모델과 기본 영역으로 되돌린다. 모델 경로는 사용자 환경 값이라 그대로 둔다."""
        self.model_combo.setCurrentIndex(0)
        self.ratio_slider.setValue(round(DEFAULT_REGION_RATIO * 100))

    def settings(self) -> AppSettings:
        return AppSettings(
            whisper_model=self._chosen_model(),
            model_dir=self.model_dir_edit.text().strip() or None,
            subtitle_region_ratio=self.ratio_slider.value() / 100,
        )

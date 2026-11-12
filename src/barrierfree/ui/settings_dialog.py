"""분석 설정 다이얼로그 (SCR-04, docs/project/ui-spec.md 8장).

기본 화면은 장치 정보와 권장 모델만 보여 주고, 고급 설정(모델 선택, 모델 경로, OCR 자막 영역
비율)은 접어 둔다. 기술적인 모델 설정을 몰라도 기본 분석을 실행할 수 있어야 한다.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QToolButton,
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
        self._recommendation = recommendation

        gpu = "사용 가능" if device.cuda_available else "사용 불가"
        self.device_label = QLabel(
            f"CPU: {device.cpu_name} ({device.cpu_count}코어) · 메모리 {device.ram_gb:.0f}GB · "
            f"GPU(NVIDIA CUDA): {gpu}"
        )
        self.recommendation_label = QLabel(f"권장 음성 인식 모델: {recommendation.model}")
        self.current_model_label = QLabel()
        self.warning_label = QLabel(recommendation.warning or "")
        self.warning_label.setWordWrap(True)
        self.warning_label.setHidden(not recommendation.warning)

        # 고급 설정 (기본 접힘)
        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText("고급 설정")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.advanced_box = QGroupBox("고급 설정")
        self.advanced_box.setHidden(True)
        self.advanced_toggle.toggled.connect(lambda on: self.advanced_box.setHidden(not on))

        self.model_combo = QComboBox()
        self.model_combo.addItem(f"권장 모델 사용 ({recommendation.model})", None)
        for model in SUPPORTED_MODELS:
            self.model_combo.addItem(model, model)
        index = self.model_combo.findData(current.whisper_model)
        self.model_combo.setCurrentIndex(max(index, 0))
        self.model_notice = QLabel("모델을 바꾸면 인식 정확도와 처리 시간이 달라질 수 있습니다.")
        self.model_notice.setWordWrap(True)

        self.model_dir_edit = QLineEdit(current.model_dir or "")
        self.model_dir_edit.setPlaceholderText("비워 두면 기본 위치를 사용합니다")

        self.ratio_slider = QSlider(Qt.Orientation.Horizontal)
        self.ratio_slider.setRange(round(MIN_REGION_RATIO * 100), round(MAX_REGION_RATIO * 100))
        self.ratio_slider.setValue(round(current.subtitle_region_ratio * 100))
        self.ratio_label = QLabel()

        self.restore_button = QPushButton("권장 설정 복원")

        form = QFormLayout()
        form.addRow("음성 인식 모델", self.model_combo)
        form.addRow("", self.model_notice)
        form.addRow("모델 저장 경로", self.model_dir_edit)
        form.addRow("OCR 자막 영역(하단)", self.ratio_slider)
        form.addRow("", self.ratio_label)
        advanced_layout = QVBoxLayout(self.advanced_box)
        advanced_layout.addLayout(form)
        advanced_layout.addWidget(self.restore_button, alignment=Qt.AlignmentFlag.AlignLeft)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("적용")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.device_label)
        layout.addWidget(self.recommendation_label)
        layout.addWidget(self.current_model_label)
        layout.addWidget(self.warning_label)
        layout.addWidget(self.advanced_toggle)
        layout.addWidget(self.advanced_box)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(buttons)
        layout.addLayout(row)

        self.model_combo.currentIndexChanged.connect(self._refresh)
        self.ratio_slider.valueChanged.connect(self._refresh)
        self.restore_button.clicked.connect(self._restore_recommended)
        self._refresh()

    def _chosen_model(self) -> str | None:
        data = self.model_combo.currentData()
        return data if isinstance(data, str) else None

    def _refresh(self, *_args: object) -> None:
        chosen = self._chosen_model()
        self.current_model_label.setText(f"현재 모델: {chosen or self._recommendation.model}")
        self.model_notice.setHidden(chosen is None)
        self.ratio_label.setText(f"영상 하단 {self.ratio_slider.value()}%")

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

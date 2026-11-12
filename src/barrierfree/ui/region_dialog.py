"""자막 영역 확인 다이얼로그 (docs/project/implementation-plan.md 결정 2, 결정 Q3).

자막 파일이 없을 때 분석이 하단 영역을 추정한 뒤 일시정지하고, 편집자가 다음 중 하나를 고른다.
맞음 / 영역 조정 / 영상에 자막 없음. 이름·기관명 같은 하단 글자를 자막으로 오인하지 않도록
추정을 사람이 확인하는 단계다. 영역에서 읽은 글자 예시를 보여 판단을 돕는다.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QRadioButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from barrierfree.analysis.subtitle_region import RegionEstimate, region_rect
from barrierfree.pipeline import RegionAction, RegionDecision
from barrierfree.settings import MAX_REGION_RATIO, MIN_REGION_RATIO


class RegionDialog(QDialog):
    def __init__(self, estimate: RegionEstimate, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("자막 영역 확인")
        self._estimate = estimate

        verdict = (
            "자막으로 추정되었습니다"
            if estimate.is_subtitle
            else "자막이 아닌 것으로 추정되었습니다"
        )
        self.summary_label = QLabel(
            f"자막 파일이 없어 영상 하단을 읽었습니다. 이 영역은 {verdict}(추정). "
            f"영역에서 {estimate.text_count}건의 글자가 읽혔습니다."
        )
        self.summary_label.setWordWrap(True)
        if estimate.samples:
            shown = "\n".join(f"· {text}" for text in estimate.samples)
            self.samples_label = QLabel(f"영역에서 읽은 글자 예시:\n{shown}")
        else:
            self.samples_label = QLabel("영역에서 읽힌 글자가 없습니다.")
        self.samples_label.setWordWrap(True)

        self.confirm_radio = QRadioButton("맞음 — 이 영역을 자막으로 점검합니다")
        self.adjust_radio = QRadioButton("영역 조정 — 아래에서 자막 영역의 크기를 바꿉니다")
        self.none_radio = QRadioButton("영상에 자막 없음 — 자막 점검을 하지 않습니다")
        (self.confirm_radio if estimate.is_subtitle else self.none_radio).setChecked(True)

        self.ratio_slider = QSlider(Qt.Orientation.Horizontal)
        self.ratio_slider.setRange(round(MIN_REGION_RATIO * 100), round(MAX_REGION_RATIO * 100))
        self.ratio_slider.setValue(round(estimate.rect[3] * 100))
        self.ratio_label = QLabel()
        self.ratio_slider.valueChanged.connect(self._refresh)
        for radio in (self.confirm_radio, self.adjust_radio, self.none_radio):
            radio.toggled.connect(self._refresh)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("확인")
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        for widget in (
            self.summary_label,
            self.samples_label,
            self.confirm_radio,
            self.adjust_radio,
            self.ratio_slider,
            self.ratio_label,
            self.none_radio,
            buttons,
        ):
            layout.addWidget(widget)
        self._refresh()

    def _refresh(self, *_args: object) -> None:
        self.ratio_slider.setEnabled(self.adjust_radio.isChecked())
        self.ratio_label.setText(f"영상 하단 {self.ratio_slider.value()}%")

    def decision(self) -> RegionDecision:
        if self.none_radio.isChecked():
            return RegionDecision(RegionAction.NONE)
        if self.adjust_radio.isChecked():
            rect = region_rect(self.ratio_slider.value() / 100)
            return RegionDecision(RegionAction.ADJUSTED, rect)
        return RegionDecision(RegionAction.CONFIRMED, self._estimate.rect)

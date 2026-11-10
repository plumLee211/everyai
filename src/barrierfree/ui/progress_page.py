"""분석 진행 화면 (SCR-02, 세부구현.md 6장).

- 단계 상태는 색이나 기호에만 의존하지 않고 글자("대기/진행 중/완료/실패")로 쓴다.
- 진행량을 측정할 수 없으면 임의의 전체 퍼센트를 지어내지 않고 '처리 중' 표시를 쓴다.
- 취소는 한 번만 요청되고, 즉시 중단을 보장하지 않으므로 '취소 중'을 표시한다.
- 모델 준비 단계는 필요한 경우에만 맨 앞에 나타난다.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from barrierfree.pipeline import PipelineError, Stage, StageEvent

STAGE_LABELS: dict[Stage, str] = {
    Stage.MODEL_PREPARE: "모델 준비",
    Stage.MEDIA_PREPARE: "영상 준비",
    Stage.STT: "음성 인식",
    Stage.OCR: "화면 문자 인식",
    Stage.SUBTITLE_CHECK: "자막 비교",
    Stage.SCREEN_CHECK: "화면 정보 비교",
    Stage.RESULT_SAVE: "결과 정리",
}
CORE_STAGES = [s for s in STAGE_LABELS if s is not Stage.MODEL_PREPARE]
_STATE_WORDS = {"WAITING": "대기", "RUNNING": "진행 중", "DONE": "완료", "FAILED": "실패"}


class ProgressPage(QWidget):
    cancel_requested = pyqtSignal()
    retry_requested = pyqtSignal()
    back_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._states: dict[Stage, str] = {}
        self._stages: list[Stage] = []
        self._cancel_sent = False

        self.title_label = QLabel("분석 진행")
        self.video_label = QLabel("")
        self.stage_list = QListWidget()
        self.status_label = QLabel("")
        self.progress_bar = QProgressBar()
        self.cancel_button = QPushButton("분석 취소")
        self.error_panel = QWidget()
        self.error_label = QLabel("")
        self.retry_button = QPushButton("다시 시도")
        self.back_button = QPushButton("처음으로")

        self.error_label.setWordWrap(True)
        error_layout = QVBoxLayout(self.error_panel)
        error_layout.addWidget(self.error_label)
        buttons = QHBoxLayout()
        buttons.addWidget(self.retry_button)
        buttons.addWidget(self.back_button)
        error_layout.addLayout(buttons)

        layout = QVBoxLayout(self)
        for widget in (self.title_label, self.video_label, self.stage_list, self.status_label,
                       self.progress_bar, self.error_panel, self.cancel_button):
            layout.addWidget(widget)

        self.cancel_button.clicked.connect(self._cancel)
        self.retry_button.clicked.connect(self.retry_requested.emit)
        self.back_button.clicked.connect(self.back_requested.emit)
        self.start("")

    # ----- 조회 (테스트·접근성) -----

    def visible_stages(self) -> list[Stage]:
        return list(self._stages)

    def stage_state(self, stage: Stage) -> str:
        return _STATE_WORDS[self._states[stage]]

    def stage_rows(self) -> list[tuple[Stage, str]]:
        return [(s, self._row_text(s)) for s in self._stages]

    # ----- 동작 -----

    def start(self, video_name: str) -> None:
        self._cancel_sent = False
        self._stages = list(CORE_STAGES)
        self._states = {s: "WAITING" for s in CORE_STAGES}
        self.video_label.setText(f"분석 영상: {video_name}" if video_name else "")
        self.status_label.setText("분석을 준비하는 중입니다.")
        self.progress_bar.setRange(0, 0)
        self.error_panel.setHidden(True)
        self.cancel_button.setHidden(False)
        self.cancel_button.setEnabled(True)
        self._render()

    def on_event(self, event: StageEvent) -> None:
        if event.stage not in self._states:
            self._states[event.stage] = "WAITING"
            if event.stage is Stage.MODEL_PREPARE:  # 필요한 경우에만, 맨 앞에
                self._stages.insert(0, event.stage)
            else:
                self._stages.append(event.stage)
        self._states[event.stage] = event.state if event.state in _STATE_WORDS else "RUNNING"
        if event.message and not self._cancel_sent:
            self.status_label.setText(event.message)
        if event.state == "RUNNING":
            if event.fraction is None:
                self.progress_bar.setRange(0, 0)  # 측정 불가: 처리 중 표시
            else:
                self.progress_bar.setRange(0, 100)
                self.progress_bar.setValue(round(min(1.0, max(0.0, event.fraction)) * 100))
        self._render()

    def show_error(self, error: PipelineError) -> None:
        if error.stage in self._states:
            self._states[error.stage] = "FAILED"
        stage = f"{STAGE_LABELS[error.stage]} 단계에서 " if error.stage in STAGE_LABELS else ""
        self.error_label.setText(f"{stage}분석에 실패했습니다. {error.user_message} ({error.code})")
        self.error_panel.setHidden(False)
        self.cancel_button.setHidden(True)
        self.progress_bar.setRange(0, 100)
        self._render()

    def show_cancelling(self) -> None:
        self._cancel_sent = True
        self.cancel_button.setEnabled(False)
        self.status_label.setText("취소 중입니다. 현재 작업이 끝나면 중단됩니다.")

    # ----- 내부 -----

    def _cancel(self) -> None:
        if self._cancel_sent:
            return
        self.show_cancelling()
        self.cancel_requested.emit()

    def _row_text(self, stage: Stage) -> str:
        return f"{STAGE_LABELS[stage]} — {_STATE_WORDS[self._states[stage]]}"

    def _render(self) -> None:
        self.stage_list.clear()
        for stage in self._stages:
            self.stage_list.addItem(QListWidgetItem(self._row_text(stage)))

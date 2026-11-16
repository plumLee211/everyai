"""분석 진행 화면 (SCR-02, docs/project/ui-spec.md 6장).

- 단계 상태는 색이나 기호에만 의존하지 않고 글자("대기/진행 중/완료/실패")로 쓴다.
- 진행량을 측정할 수 없으면 임의의 전체 퍼센트를 지어내지 않고 '처리 중' 표시를 쓴다.
- 취소는 한 번만 요청되고, 즉시 중단을 보장하지 않으므로 '취소 중'을 표시한다.
- 모델 준비 단계는 필요한 경우에만 맨 앞에 나타난다.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from barrierfree.pipeline import PipelineError, Stage, StageEvent
from barrierfree.ui import theme

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
_STATE_TONES = {"WAITING": "neutral", "RUNNING": "info", "DONE": "success", "FAILED": "danger"}
_WAIT_NOTES: dict[Stage, str] = {
    Stage.MODEL_PREPARE: "분석에 쓸 모델 준비 대기",
    Stage.MEDIA_PREPARE: "영상과 오디오 스트림 확인 대기",
    Stage.STT: "음성 인식 대기",
    Stage.OCR: "화면 속 글자 인식 대기",
    Stage.SUBTITLE_CHECK: "음성과 자막 비교 대기",
    Stage.SCREEN_CHECK: "화면 정보와 음성 비교 대기",
    Stage.RESULT_SAVE: "검수 항목 생성 대기",
}
CAUTION = "분석 중에는 컴퓨터를 종료하지 마세요."


class ProgressPage(QWidget):
    cancel_requested = pyqtSignal()
    retry_requested = pyqtSignal()
    back_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._states: dict[Stage, str] = {}
        self._stages: list[Stage] = []
        self._notes: dict[Stage, str] = {}
        self._cancel_sent = False

        self.title_label = theme.role(QLabel("영상 접근성을 분석하고 있습니다"), "title")
        self.intro_label = theme.role(
            QLabel("분석이 끝나면 검수 화면에서 탐지 결과를 확인할 수 있습니다."), "muted"
        )
        self.video_label = theme.role(QLabel(""), "caption")
        self.status_label = QLabel("")
        self.percent_label = QLabel("")
        self.percent_label.setStyleSheet(
            f"color: {theme.COLORS['primary']}; font-weight: 600;"
        )
        self.progress_bar = QProgressBar()
        self.cancel_button = QPushButton("분석 취소")
        self.error_panel = theme.banner("warn")
        self.error_label = QLabel("")
        self.retry_button = QPushButton("다시 시도")
        self.back_button = QPushButton("처음으로")
        self.stage_container = QVBoxLayout()
        self.stage_container.setSpacing(4)

        self.error_label.setWordWrap(True)
        self.error_panel.setStyleSheet(
            f"QFrame[banner='warn'] {{ background: {theme.COLORS['danger_bg']}; }} "
            f"QLabel {{ color: {theme.COLORS['danger_fg']}; }}"
        )
        error_layout = self.error_panel.layout()
        assert isinstance(error_layout, QVBoxLayout)
        error_layout.addWidget(self.error_label)
        buttons = QHBoxLayout()
        buttons.addWidget(self.retry_button)
        buttons.addWidget(self.back_button)
        buttons.addStretch(1)
        error_layout.addLayout(buttons)

        status_box = QFrame()
        status_box.setProperty("tile", True)
        status_layout = QVBoxLayout(status_box)
        status_layout.setContentsMargins(18, 16, 18, 16)
        status_layout.setSpacing(8)
        status_layout.addWidget(theme.role(QLabel("분석 진행률"), "caption"))
        status_layout.addWidget(self.progress_bar)
        status_row = QHBoxLayout()
        status_row.addWidget(self.percent_label)
        status_row.addWidget(self.status_label, 1)
        status_layout.addLayout(status_row)

        caution = theme.banner("warn")
        caution_layout = caution.layout()
        assert isinstance(caution_layout, QVBoxLayout)
        caution_layout.addWidget(QLabel(CAUTION))
        self.caution_banner = caution

        card = theme.card((24, 24, 24, 24), 14)
        card.setMaximumWidth(804)
        card_layout = card.layout()
        assert isinstance(card_layout, QVBoxLayout)
        card_layout.addWidget(self.title_label)
        card_layout.addWidget(self.intro_label)
        card_layout.addWidget(self.video_label)
        card_layout.addWidget(status_box)
        card_layout.addLayout(self.stage_container)
        card_layout.addWidget(self.error_panel)
        card_layout.addWidget(self.caution_banner)
        card_layout.addWidget(self.cancel_button, alignment=Qt.AlignmentFlag.AlignLeft)
        card_layout.addStretch(1)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 24)
        layout.addStretch(1)
        layout.addWidget(card, 10)
        layout.addStretch(1)

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
        self._notes = {}
        self._stages = list(CORE_STAGES)
        self._states = {s: "WAITING" for s in CORE_STAGES}
        self.video_label.setText(f"분석 영상: {video_name}" if video_name else "")
        self.status_label.setText("분석을 준비하는 중입니다.")
        self.percent_label.setText("")
        self.progress_bar.setRange(0, 0)
        self.error_panel.setHidden(True)
        self.caution_banner.setHidden(False)
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
                self.percent_label.setText("")
            else:
                self.progress_bar.setRange(0, 100)
                percent = round(min(1.0, max(0.0, event.fraction)) * 100)
                self.progress_bar.setValue(percent)
                self.percent_label.setText(f"현재 단계 {percent}% ·")
            self._notes[event.stage] = event.message
        self._render()

    def show_error(self, error: PipelineError) -> None:
        if error.stage in self._states:
            self._states[error.stage] = "FAILED"
        stage = f"{STAGE_LABELS[error.stage]} 단계에서 " if error.stage in STAGE_LABELS else ""
        self.error_label.setText(f"{stage}분석에 실패했습니다. {error.user_message} ({error.code})")
        self.error_panel.setHidden(False)
        self.cancel_button.setHidden(True)
        self.progress_bar.setRange(0, 100)
        self.caution_banner.setHidden(True)
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
        while self.stage_container.count():
            item = self.stage_container.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.hide()  # deleteLater만으로는 지워지기 전까지 옛 행이 겹쳐 보인다
                widget.setParent(None)
                widget.deleteLater()
        for number, stage in enumerate(self._stages, start=1):
            self.stage_container.addWidget(self._stage_row(number, stage))

    def _stage_row(self, number: int, stage: Stage) -> QWidget:
        state = self._states[stage]
        row = QFrame()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(4, 6, 4, 6)
        layout.setSpacing(12)
        badge = QLabel(str(number))
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(28, 28)
        fg, bg = {
            "DONE": (theme.COLORS["success_fg"], theme.COLORS["success_bg"]),
            "RUNNING": (theme.COLORS["primary"], theme.COLORS["primary_weak"]),
            "FAILED": (theme.COLORS["danger_fg"], theme.COLORS["danger_bg"]),
        }.get(state, (theme.COLORS["neutral_fg"], theme.COLORS["neutral_bg"]))
        badge.setStyleSheet(
            f"background: {bg}; color: {fg}; border-radius: 14px; font-weight: 700;"
        )
        name = QLabel(STAGE_LABELS[stage])
        name.setStyleSheet("font-weight: 600;")
        name.setMinimumWidth(150)
        if state == "WAITING":
            note = _WAIT_NOTES[stage]
        else:
            note = self._notes.get(stage, "")
        note_label = theme.role(QLabel(note), "caption")
        layout.addWidget(badge)
        layout.addWidget(name)
        layout.addWidget(note_label, 1)
        layout.addWidget(theme.chip(_STATE_WORDS[state], _STATE_TONES[state]))
        return row

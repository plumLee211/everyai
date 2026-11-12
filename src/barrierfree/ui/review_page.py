"""검수 화면 (SCR-03, docs/project/ui-spec.md 7장).

원칙
- 문제를 선택하면 해당 시점으로 이동하되 자동 재생하지 않는다.
- 검수 후에는 건수와 목록만 갱신하고, 자동으로 다음 항목으로 넘어가지 않는다.
- 필터는 표시만 바꾼다. 선택 항목이 필터에서 빠지면 상세 선택을 해제한다.
- 재생 실패나 저장 실패는 알리되, 가능한 범위에서 검수를 계속할 수 있게 한다.
- AI 신뢰도는 '참고값'이며 오류 확률이나 정확도로 단정하지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from PyQt6.QtCore import QItemSelection, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from barrierfree.models import (
    ISSUE_KIND_LABELS,
    REVIEW_STATUS_LABELS,
    VERDICT_LABELS,
    Issue,
    IssueSource,
    ReviewStatus,
    Verdict,
)
from barrierfree.review_state import (
    GROUP_LABELS,
    ExportError,
    IssueFilter,
    KindGroup,
    ReviewState,
    SaveError,
)
from barrierfree.ui.dialogs import Dialogs
from barrierfree.ui.issue_model import ISSUE_ID_ROLE, IssueTableModel, format_time

NO_SELECTION = "문제를 선택하면 상세 내용이 표시됩니다."
NO_MATCH = "조건에 해당하는 항목이 없습니다."
_UNCERTAIN_BELOW = 0.5  # 이 값 미만이면 'AI 인식이 불확실'을 함께 알린다(D-07 미확정, 임시값)


class PlayerLike(Protocol):
    playback_failed: pyqtSignal
    activity: pyqtSignal  # 재생/탐색 같은 조작이 있었다(검수 시간 측정용)

    def load(self, path: Path) -> None: ...
    def seek(self, seconds: float) -> None: ...
    def pause(self) -> None: ...
    def is_playing(self) -> bool: ...


def confidence_text(issue: Issue) -> str:
    """신뢰도를 오류 확률처럼 보이지 않게, 참고값임을 밝히고 불확실하면 그 사실을 알린다."""
    shown = "—" if issue.confidence is None else f"{round(issue.confidence * 100)}%"
    text = f"신뢰도(참고값): {shown} — 오류 확률이 아닙니다."
    uncertain = issue.verdict is Verdict.UNDETERMINED or (
        issue.confidence is not None and issue.confidence < _UNCERTAIN_BELOW
    )
    return text + (" AI 인식이 불확실합니다." if uncertain else "")


class ReviewPage(QWidget):
    new_analysis_requested = pyqtSignal()
    activity = pyqtSignal()  # 사용자 상호작용이 있었다(검수 시간 측정용)

    def __init__(
        self,
        dialogs: Dialogs,
        player_factory: Callable[[], PlayerLike],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._dialogs = dialogs
        self._state: ReviewState | None = None
        self._model: IssueTableModel | None = None
        self._video_path: Path | None = None
        self._selected: str | None = None
        self._loading = False

        # 상단 요약 (REV-01~07)
        self.video_name_label = QLabel("")
        self.total_label = QLabel("")
        self.unreviewed_label = QLabel("")
        self.confirmed_label = QLabel("")
        self.dismissed_label = QLabel("")
        self.verdict_summary_label = QLabel("")
        self.new_analysis_button = QPushButton("새 영상 분석")
        self.export_button = QPushButton("CSV 내보내기")
        self.notices_label = QLabel("")
        self.notices_label.setWordWrap(True)

        # 영상 (좌)
        self.player = player_factory()
        self.player_error_label = QLabel("")
        self.player_error_label.setWordWrap(True)

        # 문제 목록 (우)
        self.group_filter = QComboBox()
        for group, label in GROUP_LABELS.items():
            self.group_filter.addItem(label, group)
        self.status_filter = QComboBox()
        self.status_filter.addItem("전체 상태", None)
        for status, label in REVIEW_STATUS_LABELS.items():
            self.status_filter.addItem(label, status)
        self.verdict_filter = QComboBox()
        self.verdict_filter.addItem("전체 판정", None)
        for verdict, label in VERDICT_LABELS.items():
            self.verdict_filter.addItem(label, verdict)
        self.table_view = QTableView()
        self.table_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.empty_label = QLabel("")

        # 하단 상세 (DET-01~10)
        self.detail_hint_label = QLabel(NO_SELECTION)
        self.kind_label = QLabel("")
        self.span_label = QLabel("")
        self.reason_label = QLabel("")
        self.speech_title_label = QLabel("음성 인식 내용")
        self.speech_label = QLabel("")
        self.compare_title_label = QLabel("자막")
        self.compare_label = QLabel("")
        self.verdict_label = QLabel("")
        self.confidence_label = QLabel("")
        self.status_label = QLabel("")
        self.confirm_button = QPushButton("확정")
        self.dismiss_button = QPushButton("기각")
        self.reset_button = QPushButton("미검토로 되돌리기")
        for label in (self.reason_label, self.speech_label, self.compare_label,
                      self.confidence_label, self.notices_label):
            label.setWordWrap(True)

        self._build_layout()
        self.new_analysis_button.clicked.connect(self.new_analysis_requested.emit)
        self.export_button.clicked.connect(self._export)
        self.confirm_button.clicked.connect(lambda: self._review(ReviewStatus.CONFIRMED))
        self.dismiss_button.clicked.connect(lambda: self._review(ReviewStatus.DISMISSED))
        self.reset_button.clicked.connect(lambda: self._review(ReviewStatus.UNREVIEWED))
        for combo in (self.group_filter, self.status_filter, self.verdict_filter):
            combo.currentIndexChanged.connect(self._apply_filter)
        self.player.playback_failed.connect(self._on_playback_failed)
        self.player.activity.connect(self.activity.emit)  # 플레이어 조작도 검수 활동이다
        self._show_detail(None)

    def _build_layout(self) -> None:
        top = QHBoxLayout()
        for widget in (self.video_name_label, self.total_label, self.unreviewed_label,
                       self.confirmed_label, self.dismissed_label):
            top.addWidget(widget)
        top.addStretch(1)
        top.addWidget(self.new_analysis_button)
        top.addWidget(self.export_button)

        filters = QHBoxLayout()
        for combo in (self.group_filter, self.status_filter, self.verdict_filter):
            filters.addWidget(combo)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addLayout(filters)
        right_layout.addWidget(self.table_view, 1)
        right_layout.addWidget(self.empty_label)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(self.player, 1)
        left_layout.addWidget(self.player_error_label)

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([60, 40])  # 좌 약 60%, 우 약 40%

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        for widget in (self.detail_hint_label, self.kind_label, self.span_label,
                       self.reason_label, self.speech_title_label, self.speech_label,
                       self.compare_title_label, self.compare_label, self.verdict_label,
                       self.confidence_label, self.status_label):
            detail_layout.addWidget(widget)
        buttons = QHBoxLayout()
        for button in (self.dismiss_button, self.confirm_button, self.reset_button):
            buttons.addWidget(button)
        detail_layout.addLayout(buttons)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.verdict_summary_label)
        layout.addWidget(self.notices_label)
        layout.addWidget(splitter, 1)
        layout.addWidget(detail)

    # ----- 불러오기 -----

    @property
    def selected_issue_id(self) -> str | None:
        return self._selected

    def pause_playback(self) -> None:
        """검수 화면을 떠날 때 재생을 멈춘다."""
        self.player.pause()

    def is_playing(self) -> bool:
        """영상을 재생 중인가. 보고 있는 시간도 검수 시간으로 세기 위해 쓴다."""
        return self.player.is_playing()

    def load(self, state: ReviewState, video_path: Path | None) -> None:
        self._loading = True
        self._state = state
        self._video_path = video_path
        self._selected = None
        for combo in (self.group_filter, self.status_filter, self.verdict_filter):
            combo.setCurrentIndex(0)
        self._model = IssueTableModel(state)
        self.table_view.setModel(self._model)
        self.table_view.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self.table_view.resizeColumnsToContents()
        self._loading = False

        self.video_name_label.setText(f"영상: {state.session.video_name}")
        self.notices_label.setText("\n".join(state.notices()))
        self.player_error_label.setText("")
        if video_path is not None and video_path.exists():
            self.player.load(video_path)
        elif video_path is not None:
            self._on_playback_failed("영상 파일을 찾을 수 없습니다.")
        self._refresh_summary()
        self._show_detail(None)
        self._refresh_empty_label()

    # ----- 선택·상세 -----

    def _current_filter(self) -> IssueFilter:
        group = self.group_filter.currentData()
        status = self.status_filter.currentData()
        verdict = self.verdict_filter.currentData()
        return IssueFilter(
            group=group if isinstance(group, KindGroup) else KindGroup.ALL,
            status=status if isinstance(status, ReviewStatus) else None,
            verdict=verdict if isinstance(verdict, Verdict) else None,
        )

    def _apply_filter(self, *_args: object) -> None:
        if self._loading or self._model is None or self._state is None:
            return
        self.activity.emit()
        flt = self._current_filter()
        self._model.set_filter(flt)
        self._selected = self._state.keep_selection(self._selected, flt)
        row = self._model.row_of(self._selected) if self._selected else None
        if row is not None:
            self.table_view.selectRow(row)
        self._show_detail(self._state.get(self._selected) if self._selected else None)
        self._refresh_empty_label()

    def _on_selection_changed(self, selected: QItemSelection, _deselected: QItemSelection) -> None:
        if self._loading or self._state is None:
            return
        indexes = self.table_view.selectionModel().selectedRows()
        if not indexes:
            return  # 선택 해제(필터 변경 등)는 호출한 쪽이 상세를 정리한다
        self.activity.emit()
        issue_id = indexes[0].data(ISSUE_ID_ROLE)
        self._selected = str(issue_id)
        issue = self._state.get(self._selected)
        self.player.seek(issue.start_s)  # 이동만 하고 재생하지 않는다
        self._show_detail(issue)

    def _show_detail(self, issue: Issue | None) -> None:
        has = issue is not None
        for button in (self.confirm_button, self.dismiss_button, self.reset_button):
            button.setEnabled(has)
        self.detail_hint_label.setText("" if has else NO_SELECTION)
        if issue is None:
            for label in (self.kind_label, self.span_label, self.reason_label, self.speech_label,
                          self.compare_label, self.verdict_label, self.confidence_label,
                          self.status_label):
                label.setText("")
            return
        screen = issue.source is not IssueSource.M1
        self.kind_label.setText(f"문제 유형: {ISSUE_KIND_LABELS[issue.kind]}")
        self.span_label.setText(
            f"발생 구간: {format_time(issue.start_s)} ~ {format_time(issue.end_s)}"
        )
        self.reason_label.setText(f"탐지 사유: {issue.reason}")
        self.speech_label.setText(issue.speech_text or "—")
        self.compare_title_label.setText("화면 문자" if screen else "자막")
        self.compare_label.setText(issue.comparison_text or "—")
        self.verdict_label.setText(f"AI 판정: {VERDICT_LABELS[issue.verdict]}")
        self.confidence_label.setText(confidence_text(issue))
        self.status_label.setText(f"검수 상태: {REVIEW_STATUS_LABELS[issue.review_status]}")

    # ----- 검수 -----

    def _review(self, status: ReviewStatus) -> None:
        if self._model is None or self._state is None or self._selected is None:
            return
        self.activity.emit()
        issue_id = self._selected
        try:
            self._model.set_status(issue_id, status)
        except SaveError as error:
            self._dialogs.warn("저장 실패", str(error))
            return
        self._refresh_summary()
        self._refresh_empty_label()
        if self._model.row_of(issue_id) is None:  # 현재 필터에서 빠졌다
            self._selected = None
            self.table_view.clearSelection()
            self._show_detail(None)
        else:
            self._show_detail(self._state.get(issue_id))  # 같은 항목에 머문다(자동 이동 없음)

    def _refresh_summary(self) -> None:
        if self._state is None:
            return
        s = self._state.summary()
        self.total_label.setText(f"총 탐지 {s.total}")
        self.unreviewed_label.setText(f"미검토 {s.unreviewed}")
        self.confirmed_label.setText(f"확정 {s.confirmed}")
        self.dismissed_label.setText(f"기각 {s.dismissed}")
        parts = [f"{VERDICT_LABELS[v]} {s.by_verdict.get(v, 0)}" for v in VERDICT_LABELS]
        self.verdict_summary_label.setText(" · ".join(parts))

    def _refresh_empty_label(self) -> None:
        shown = self._model.rowCount() if self._model is not None else 0
        has_data = bool(self._state and self._state.issues)
        self.empty_label.setText(NO_MATCH if has_data and shown == 0 else "")

    # ----- 내보내기·재생 오류 -----

    def _export(self) -> None:
        if self._state is None:
            return
        self.activity.emit()
        try:
            name = self._state.suggested_csv_name()
            path = self._dialogs.ask_save_csv(name)
            if path is None:
                return
            saved = self._state.export_csv(path)
        except ExportError as error:
            self._dialogs.warn("내보내기 실패", str(error))
            return
        self._dialogs.info("저장 완료", f"검수 결과를 저장했습니다: {saved}")

    def _on_playback_failed(self, message: str) -> None:
        self.player_error_label.setText(
            f"영상을 재생할 수 없습니다. 영상 파일을 확인해 주세요. (ERR-10) "
            f"분석 결과는 그대로 확인할 수 있습니다. [{message}]"
        )

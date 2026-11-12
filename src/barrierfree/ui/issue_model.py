"""문제 목록 테이블 모델 (docs/project/ui-spec.md 7.5).

읽기 전용이며 검수 상태 변경은 `set_status`로만 한다.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt
from PyQt6.QtGui import QBrush, QColor

from barrierfree.models import (
    ISSUE_KIND_LABELS,
    REVIEW_STATUS_LABELS,
    VERDICT_LABELS,
    Issue,
    ReviewStatus,
)
from barrierfree.review_state import GROUP_BY_SOURCE, GROUP_LABELS, IssueFilter, ReviewState
from barrierfree.ui import theme

_STATUS_COLORS = {
    ReviewStatus.UNREVIEWED: theme.COLORS["warn_fg"],
    ReviewStatus.CONFIRMED: theme.COLORS["success_fg"],
    ReviewStatus.DISMISSED: theme.COLORS["danger_fg"],
}

COLUMNS = ("시간", "유형", "내용", "AI 판정", "상태")
ISSUE_ID_ROLE = Qt.ItemDataRole.UserRole


def format_time(seconds: float) -> str:
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


class IssueTableModel(QAbstractTableModel):
    def __init__(self, state: ReviewState, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._state = state
        self._filter = IssueFilter()
        self._rows: list[Issue] = state.visible(self._filter)

    # ----- Qt 모델 -----

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if orientation is Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        issue = self._rows[index.row()]
        if role == ISSUE_ID_ROLE:
            return issue.id
        if role == Qt.ItemDataRole.ToolTipRole and index.column() == 2:
            return issue.reason
        if role == Qt.ItemDataRole.ForegroundRole:
            if index.column() == 0:
                return QBrush(QColor(theme.COLORS["primary"]))
            if index.column() == 4:
                return QBrush(QColor(_STATUS_COLORS[issue.review_status]))
            return None
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        column = index.column()
        if column == 0:
            return format_time(issue.start_s)
        if column == 1:
            return GROUP_LABELS[GROUP_BY_SOURCE[issue.source]]
        if column == 2:
            return issue.reason
        if column == 3:
            return VERDICT_LABELS[issue.verdict]
        return REVIEW_STATUS_LABELS[issue.review_status]

    # ----- 조회·변경 -----

    def issue_at(self, row: int) -> Issue:
        if not 0 <= row < len(self._rows):
            raise IndexError(row)
        return self._rows[row]

    def row_of(self, issue_id: str) -> int | None:
        return next((r for r, i in enumerate(self._rows) if i.id == issue_id), None)

    def kind_label(self, issue: Issue) -> str:
        return ISSUE_KIND_LABELS[issue.kind]

    def set_filter(self, flt: IssueFilter) -> None:
        """표시할 행만 바꾼다. 분석 결과와 검수 상태는 그대로다."""
        self.beginResetModel()
        self._filter = flt
        self._rows = self._state.visible(flt)
        self.endResetModel()

    def set_status(self, issue_id: str, status: ReviewStatus) -> bool:
        """검수 상태를 바꾸고 해당 행만 갱신한다. 바뀌지 않았으면 False."""
        if not self._state.set_status(issue_id, status):
            return False
        row = self.row_of(issue_id)
        if row is None:
            return True
        if self._state.matches(self._rows[row], self._filter):
            self.dataChanged.emit(self.index(row, 0), self.index(row, len(COLUMNS) - 1))
        else:  # 현재 필터에서 빠지는 항목(예: 미검토만 보기에서 기각)은 목록에서 사라진다
            self.beginRemoveRows(QModelIndex(), row, row)
            del self._rows[row]
            self.endRemoveRows()
        return True

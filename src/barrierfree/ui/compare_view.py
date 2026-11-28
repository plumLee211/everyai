"""대조 보기: 음성 구간마다 같은 시간의 화면 글자와 자막을 위아래로 보여 준다.

일치나 정상을 판단하지 않으므로 그런 표시를 두지 않는다. 지적이 있는 행에만 개수를 표시한다.
접어 둔 글자도 개수는 항상 보인다. 영상에서 읽은 글자는 HTML로 해석되지 않게 escape한다.
"""

from __future__ import annotations

from html import escape

from PyQt6.QtCore import QUrl, pyqtSignal
from PyQt6.QtWidgets import QLabel, QTextBrowser, QVBoxLayout, QWidget

from barrierfree.analysis.compare_rows import CompareData, Row, build_rows, row_for
from barrierfree.models import AnalysisNote, Issue, IssueSource, NoteCode

_NO_DATA = (
    "이 분석에는 대조 자료가 없습니다. 이전 버전에서 분석했거나 자료를 저장하지 못한 경우입니다. "
    "같은 영상을 다시 분석하면 볼 수 있습니다."
)
_CAUTION = "같은 시간에 있던 것을 모은 보기입니다. 일치나 정상을 뜻하지 않습니다."
_FOLDS = {"uncertain": "불확실", "carried": "계속 표시"}
_MUTED = "color:#666666"


def _clock(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes:02d}:{secs:02d}"


def _lines(texts: list[str], style: str = "") -> str:
    return "".join(f'<div style="{style}">{escape(t)}</div>' for t in texts)


def _fold(index: int, key: str, texts: list[str], is_open: bool) -> str:
    if not texts:
        return ""
    action = "접기" if is_open else "보기"
    head = f'<div><a href="{key}:{index}">{_FOLDS[key]} {len(texts)}건 [{action}]</a></div>'
    return head + (_lines(texts, _MUTED) if is_open else "")


def _carried(row: Row) -> list[str]:
    """계속 보이는 글자. 불확실한 것은 펼쳐도 확실한 글자와 구분되게 표시한다."""
    return row.carried + [f"(불확실) {text}" for text in row.carried_uncertain]


def _section(label: str, body: str) -> str:
    if not body:
        return ""
    return (
        f'<tr><td width="44" style="{_MUTED}">{label}</td>'
        f"<td>{body}</td></tr>"
    )


def _row_html(
    index: int, row: Row, issue_count: int, suspected: bool, opened: set[tuple[str, int]]
) -> str:
    head = [f'<a href="seek:{index}"><b>{_clock(row.start_s)}~{_clock(row.end_s)}</b></a>']
    if row.speech is None:
        head.append(f'<span style="{_MUTED}">(말 없음)</span>')
    if suspected:
        head.append("<b>음성 누락 의심</b>")
    if issue_count:
        head.append(f'<a href="issue:{index}">지적 {issue_count}</a>')
    carried = _carried(row)
    screen = (
        _lines(row.screen)
        + _fold(index, "uncertain", row.uncertain, ("uncertain", index) in opened)
        + _fold(index, "carried", carried, ("carried", index) in opened)
    )
    speech = "" if row.speech is None else escape(row.speech)
    return (
        f'<p>{" &nbsp; ".join(head)}</p><table width="100%" cellpadding="2">'
        + _section("음성", speech)
        + _section("화면", screen)
        + _section("자막", _lines(row.cues))
        + "</table><hr>"
    )


class CompareView(QWidget):
    seek_requested = pyqtSignal(float)
    issue_requested = pyqtSignal(str)
    activity = pyqtSignal()  # 접기·이동 같은 조작(검수 시간 측정)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[Row] = []
        self._row_issues: list[list[Issue]] = []
        self._suspected: list[tuple[float, float]] = []
        self._opened: set[tuple[str, int]] = set()
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        self.browser = QTextBrowser()
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.anchorClicked.connect(self._on_anchor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.browser, 1)

    def set_content(
        self, data: CompareData | None, issues: list[Issue], notes: list[AnalysisNote]
    ) -> None:
        self._opened = set()
        self._suspected = [
            (n.start_s, n.end_s)
            for n in notes
            if n.code is NoteCode.SPEECH_MAYBE_MISSED
            and n.start_s is not None
            and n.end_s is not None
        ]
        self._rows = build_rows(data, self._suspected) if data is not None else []
        self._row_issues = [[] for _ in self._rows]
        for issue in sorted(issues, key=lambda i: (i.start_s, i.end_s)):
            if not self._rows:
                break
            # 지적은 한 행에만 센다. 화면 지적은 그 글자가 처음 보이는 행이다.
            shown = issue.comparison_text if issue.source is IssueSource.M2 else None
            index = row_for(self._rows, issue.start_s, issue.end_s, shown)
            self._row_issues[index].append(issue)
        if data is None:
            self.summary_label.setText("")
            self.browser.setPlainText(_NO_DATA)
            return
        self.summary_label.setText(
            f"행 {len(self._rows)} · 화면 글자 {len(data.screen)}건 · "
            f"불확실 {len(data.uncertain)}건 · 자막 {len(data.cues)}건\n{_CAUTION}"
        )
        self._render()

    def _render(self) -> None:
        position = self.browser.verticalScrollBar().value()
        self.browser.setHtml(
            "".join(
                _row_html(
                    index,
                    row,
                    len(self._row_issues[index]),
                    any(row.start_s < end and row.end_s > start for start, end in self._suspected),
                    self._opened,
                )
                for index, row in enumerate(self._rows)
            )
        )
        self.browser.verticalScrollBar().setValue(position)

    def handle_link(self, link: str) -> None:
        """`동작:행번호` 링크를 처리한다. 알 수 없는 링크는 무시한다."""
        action, _, number = link.partition(":")
        if not number.isdigit() or int(number) >= len(self._rows):
            return
        index = int(number)
        if action == "seek":
            self.activity.emit()
            self.seek_requested.emit(float(self._rows[index].start_s))
        elif action == "issue" and self._row_issues[index]:
            self.activity.emit()
            self.issue_requested.emit(self._row_issues[index][0].id)
        elif action in _FOLDS:
            self.activity.emit()
            self._opened ^= {(action, index)}
            self._render()

    def _on_anchor(self, url: QUrl) -> None:
        self.handle_link(url.toString())

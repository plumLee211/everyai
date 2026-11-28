"""검수 화면의 데이터 규칙 (docs/project/ui-spec.md 7.3~7.9). Qt에 의존하지 않아 단위 테스트가 쉽다.

- 총 탐지 = 미검토 + 확정 + 기각 (검수 상태가 바뀌면 즉시 갱신)
- 필터는 표시 결과만 바꾼다. 분석 결과와 검수 상태는 바꾸지 않는다.
- 검수 버튼은 사람의 검수 상태(review_status)만 바꾸고 AI 판정(verdict)은 바꾸지 않는다.
- 저장에 실패하면 화면 상태도 바꾸지 않는다(화면과 저장소가 어긋나지 않게).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from barrierfree.analysis.compare_rows import CompareData
from barrierfree.models import (
    NOTE_SOURCE_LABELS,
    AnalysisNote,
    Issue,
    IssueSource,
    NoteCode,
    ReviewStatus,
    SessionStatus,
    SubtitleSource,
    Verdict,
)
from barrierfree.storage.compare_data import load_compare_data
from barrierfree.storage.export import export_csv, export_notes_csv, notes_csv_path
from barrierfree.storage.repository import Repository, SessionRecord


class SaveError(Exception):
    """검수 상태를 저장하지 못했다(ERR-09)."""


_EXPORT_FAILED = "CSV를 저장하지 못했습니다. 저장 위치를 바꿔 다시 시도해 주세요."


class ExportError(Exception):
    """CSV를 저장하지 못했다(ERR-11)."""


class KindGroup(StrEnum):
    ALL = "ALL"
    SPEECH_SUBTITLE = "SPEECH_SUBTITLE"  # 음성·자막(M1)
    SCREEN_TEXT = "SCREEN_TEXT"  # 화면 문자(M2)
    SCREEN_DEPENDENT = "SCREEN_DEPENDENT"  # 화면 의존 표현(M2-2)


GROUP_BY_SOURCE: dict[IssueSource, KindGroup] = {
    IssueSource.M1: KindGroup.SPEECH_SUBTITLE,
    IssueSource.M2: KindGroup.SCREEN_TEXT,
    IssueSource.M2_SCREEN_DEPENDENT: KindGroup.SCREEN_DEPENDENT,
}

GROUP_LABELS: dict[KindGroup, str] = {
    KindGroup.ALL: "전체",
    KindGroup.SPEECH_SUBTITLE: "음성·자막",
    KindGroup.SCREEN_TEXT: "화면 문자",
    KindGroup.SCREEN_DEPENDENT: "화면 의존 표현",
}


@dataclass(frozen=True)
class IssueFilter:
    group: KindGroup = KindGroup.ALL
    status: ReviewStatus | None = None
    verdict: Verdict | None = None


@dataclass(frozen=True)
class Summary:
    total: int
    unreviewed: int
    confirmed: int
    dismissed: int
    by_verdict: dict[Verdict, int] = field(default_factory=dict)


def matches(issue: Issue, flt: IssueFilter) -> bool:
    return (
        (flt.group is KindGroup.ALL or GROUP_BY_SOURCE[issue.source] is flt.group)
        and (flt.status is None or issue.review_status is flt.status)
        and (flt.verdict is None or issue.verdict is flt.verdict)
    )


_UNCHECKED = "점검하지 못한 구간이 있습니다. 지적이 없다고 해서 확인된 것은 아닙니다."
_MAYBE_UNCHECKED = (
    "음성을 인식하지 못했을 수 있는 구간이 있습니다. 지적이 없다고 해서 확인된 것은 아닙니다."
)


def _clock(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes:02d}:{secs:02d}"


def _range(note: AnalysisNote) -> str:
    assert note.start_s is not None and note.end_s is not None
    return f"{_clock(note.start_s)}~{_clock(note.end_s)}"


def _note_lines(notes: list[AnalysisNote]) -> list[str]:
    """안내 한 건에 한 줄. 누락 의심 구간은 여러 개여도 한 줄에 모은다(문제 목록을 가리지 않게)."""
    suspected = [n for n in notes if n.code is NoteCode.SPEECH_MAYBE_MISSED]
    lines: list[str] = []
    for note in notes:
        if note.code is not NoteCode.SPEECH_MAYBE_MISSED:
            lines.append(_note_line(note))
        elif note is suspected[0]:
            ranges = ", ".join(_range(n) for n in suspected)
            lines.append(f"[음성 인식 누락 의심 {ranges}] {note.message}")
    return lines


def _note_line(note: AnalysisNote) -> str:
    if note.start_s is None or note.end_s is None:
        return note.message
    source = NOTE_SOURCE_LABELS[note.source]
    return f"[{source} 미점검 {_range(note)}] {note.message}"


class ReviewState:
    def __init__(
        self,
        repo: Repository,
        session_id: str,
        *,
        extra_notices: list[str] | None = None,
        data_dir: str | Path | None = None,
    ) -> None:
        self._repo = repo
        self.session_id = session_id
        self._data_dir = data_dir
        self._extra = list(extra_notices or [])
        self._issues = sorted(
            repo.list_issues(session_id), key=lambda i: (i.start_s, i.end_s, i.id)
        )
        self._by_id = {i.id: i for i in self._issues}

    # ----- 조회 -----

    @property
    def session(self) -> SessionRecord:
        return self._repo.get_session(self.session_id)

    @property
    def issues(self) -> list[Issue]:
        return list(self._issues)

    @property
    def is_complete(self) -> bool:
        return self.session.status is SessionStatus.COMPLETED

    def get(self, issue_id: str) -> Issue:
        return self._by_id[issue_id]

    def visible(self, flt: IssueFilter) -> list[Issue]:
        return [i for i in self._issues if matches(i, flt)]

    def matches(self, issue: Issue, flt: IssueFilter) -> bool:
        return matches(issue, flt)

    def keep_selection(self, issue_id: str | None, flt: IssueFilter) -> str | None:
        """현재 선택 항목이 필터에서 제외되면 선택을 해제한다(7.5)."""
        if issue_id is None or issue_id not in self._by_id:
            return None
        return issue_id if matches(self._by_id[issue_id], flt) else None

    def summary(self) -> Summary:
        by_status = {s: 0 for s in ReviewStatus}
        by_verdict: dict[Verdict, int] = {}
        for issue in self._issues:
            by_status[issue.review_status] += 1
            by_verdict[issue.verdict] = by_verdict.get(issue.verdict, 0) + 1
        return Summary(
            total=len(self._issues),
            unreviewed=by_status[ReviewStatus.UNREVIEWED],
            confirmed=by_status[ReviewStatus.CONFIRMED],
            dismissed=by_status[ReviewStatus.DISMISSED],
            by_verdict=by_verdict,
        )

    def notes(self) -> list[AnalysisNote]:
        return self._repo.list_notes(self.session_id)

    def compare_data(self) -> CompareData | None:
        """대조 보기 자료. 없거나 읽을 수 없으면 None."""
        if self._data_dir is None:
            return None
        return load_compare_data(self._data_dir, self.session_id)

    # ----- 검수 -----

    def set_status(self, issue_id: str, status: ReviewStatus) -> bool:
        """검수 상태를 바꾼다. 이미 같은 상태면 아무것도 하지 않고 False.

        저장에 실패하면 SaveError를 올리고 화면 상태는 그대로 둔다.
        """
        issue = self._by_id[issue_id]
        if issue.review_status is status:
            return False
        try:
            self._repo.set_review_status(issue_id, status)
        except sqlite3.Error as exc:
            raise SaveError("검수 상태를 저장하지 못했습니다. 다시 시도해 주세요.") from exc
        issue.review_status = status
        return True

    # ----- 안내 -----

    def notices(self) -> list[str]:
        saved = self._repo.list_notes(self.session_id)
        recorded = {n.message for n in saved}
        notices = _note_lines(saved)
        notices += [text for text in self._extra if text not in recorded]
        suspected = [n for n in saved if n.code is NoteCode.SPEECH_MAYBE_MISSED]
        if any(n.start_s is not None and n not in suspected for n in saved):
            notices.insert(0, _UNCHECKED)
        elif suspected:
            notices.insert(0, _MAYBE_UNCHECKED)
        joined = " ".join(notices)
        source = self.session.subtitle_source
        if source is SubtitleSource.NONE and "자막이 없는" not in joined:
            notices.append(
                "자막이 없는 영상입니다. 음성별 자막 누락은 개별 문제로 표시하지 않습니다."
            )
        elif source is SubtitleSource.OCR and "OCR" not in joined:
            notices.append(
                "영상에서 OCR로 추출한 자막 기준의 결과입니다. 인식 오류가 섞일 수 있어 "
                "음성·자막 항목은 모두 '검수 필요'로 표시됩니다."
            )
        if not self._issues:
            notices.append(
                "탐지된 문제가 없습니다. 이 결과가 접근성 기준을 모두 충족했음을 의미하지 않습니다."
            )
        return notices

    # ----- 내보내기 -----

    def suggested_csv_name(self) -> str:
        """저장 대화상자에 보여 줄 기본 파일명. 저장소를 읽지 못하면 ExportError."""
        try:
            video_name = self.session.video_name
        except sqlite3.Error as exc:
            raise ExportError(_EXPORT_FAILED) from exc
        return f"{Path(video_name).stem}_검수결과.csv"

    def export_csv(self, path: str | Path) -> Path:
        try:
            return export_csv(self._repo, self.session_id, path)
        except (OSError, sqlite3.Error) as exc:
            raise ExportError(_EXPORT_FAILED) from exc

    def export_notes(self, results_path: str | Path) -> Path:
        """지적 CSV를 저장한 뒤 분석 상태 파일을 저장한다."""
        name = notes_csv_path(results_path).name
        try:
            return export_notes_csv(self._repo, self.session_id, results_path)
        except FileExistsError as exc:
            raise ExportError(
                f"'{name}'이라는 다른 파일이 있어 덮어쓰지 않았습니다. "
                "다른 이름으로 다시 내보내 주세요."
            ) from exc
        except (OSError, sqlite3.Error) as exc:
            raise ExportError(
                f"분석 상태 파일 '{name}'을 저장하지 못했습니다. "
                "파일이 열려 있으면 닫고 다시 시도해 주세요."
            ) from exc

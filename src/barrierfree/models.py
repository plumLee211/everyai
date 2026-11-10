"""도메인 모델. Enum 문자열 값은 DB 스키마(CHECK 제약)와 1:1로 대응한다."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import uuid4


class Verdict(StrEnum):
    """AI 자동 판정. 사람의 검수 상태(ReviewStatus)와 별개 축이다."""

    ERROR = "ERROR"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    UNDETERMINED = "UNDETERMINED"


class ReviewStatus(StrEnum):
    """사람의 처리 상태. 보류는 두지 않는다(결정 Q1)."""

    UNREVIEWED = "UNREVIEWED"
    CONFIRMED = "CONFIRMED"
    DISMISSED = "DISMISSED"


class IssueSource(StrEnum):
    M1 = "M1"
    M2 = "M2"
    M2_SCREEN_DEPENDENT = "M2_SCREEN_DEPENDENT"


class IssueKind(StrEnum):
    # M1: 음성 <-> 자막
    VALUE_MISSING = "VALUE_MISSING"
    VALUE_MISMATCH = "VALUE_MISMATCH"
    SUBTITLE_MISSING = "SUBTITLE_MISSING"
    EXPRESSION_DIFFERENCE = "EXPRESSION_DIFFERENCE"
    STT_UNCERTAIN = "STT_UNCERTAIN"
    # M2: 화면 글자 <-> 음성
    SCREEN_ONLY_INFO = "SCREEN_ONLY_INFO"
    SCREEN_VALUE_MISMATCH = "SCREEN_VALUE_MISMATCH"
    MENTIONED_ELSEWHERE = "MENTIONED_ELSEWHERE"
    ALWAYS_ON_SCREEN = "ALWAYS_ON_SCREEN"
    OCR_UNCERTAIN = "OCR_UNCERTAIN"
    # M2-2
    SCREEN_DEPENDENT = "SCREEN_DEPENDENT"


class SubtitleSource(StrEnum):
    FILE = "FILE"
    OCR = "OCR"
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"


class SessionStatus(StrEnum):
    PREPARING = "PREPARING"
    ANALYZING = "ANALYZING"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"  # 자막 영역 확인 대기(결정 Q3)
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# 화면 표기: '오류 확정'이 아니라 '검토 필요' 톤을 유지한다(결정 Q2).
VERDICT_LABELS: dict[Verdict, str] = {
    Verdict.ERROR: "문제 의심",
    Verdict.REVIEW_REQUIRED: "검수 필요",
    Verdict.UNDETERMINED: "판단 불가",
}

ISSUE_KIND_LABELS: dict[IssueKind, str] = {
    IssueKind.VALUE_MISSING: "주요 정보 누락 의심",
    IssueKind.VALUE_MISMATCH: "주요 정보 불일치 의심",
    IssueKind.SUBTITLE_MISSING: "자막 누락 의심",
    IssueKind.EXPRESSION_DIFFERENCE: "표현 차이",
    IssueKind.STT_UNCERTAIN: "음성 확인 필요",
    IssueKind.SCREEN_ONLY_INFO: "화면 정보 누락 의심",
    IssueKind.SCREEN_VALUE_MISMATCH: "화면 정보 불일치 의심",
    IssueKind.MENTIONED_ELSEWHERE: "전달 시점 확인 필요",
    IssueKind.ALWAYS_ON_SCREEN: "영상 내내 표시되는 정보",
    IssueKind.OCR_UNCERTAIN: "화면 확인 필요",
    IssueKind.SCREEN_DEPENDENT: "화면 의존 표현",
}

REVIEW_STATUS_LABELS: dict[ReviewStatus, str] = {
    ReviewStatus.UNREVIEWED: "미검토",
    ReviewStatus.CONFIRMED: "확정",
    ReviewStatus.DISMISSED: "기각",
}


@dataclass(frozen=True)
class SpeechSegment:
    """STT 한 구간. 신뢰도 필드를 보존해 '판단 불가' 분리에 쓴다."""

    start_s: float
    end_s: float
    text: str
    avg_logprob: float | None = None
    no_speech_prob: float | None = None


@dataclass(frozen=True)
class SubtitleCue:
    start_s: float
    end_s: float
    text: str


@dataclass(frozen=True)
class ScreenText:
    """연속 프레임을 병합한 화면 글자 한 건."""

    text: str
    start_s: float
    end_s: float
    confidence: float = 1.0
    # 정규화 좌표 (x, y, w, h), 0~1. 모서리 로고 판별 등에 사용.
    box: tuple[float, float, float, float] | None = None


@dataclass(kw_only=True)
class Issue:
    """AI 지적 한 건. 검수 버튼은 review_status만 바꾸고 verdict는 바꾸지 않는다."""

    source: IssueSource
    kind: IssueKind
    verdict: Verdict
    start_s: float
    end_s: float
    reason: str
    id: str = field(default_factory=lambda: str(uuid4()))
    speech_text: str | None = None
    comparison_text: str | None = None
    confidence: float | None = None
    severity: str | None = None  # 기준 미정(D-08). UI에는 노출하지 않는다.
    evidence: dict[str, object] = field(default_factory=dict)
    review_status: ReviewStatus = ReviewStatus.UNREVIEWED

    def __post_init__(self) -> None:
        if not (math.isfinite(self.start_s) and math.isfinite(self.end_s)):
            raise ValueError(f"non-finite time range: {self.start_s}..{self.end_s}")
        if self.start_s < 0 or self.end_s < self.start_s:
            raise ValueError(f"invalid time range: {self.start_s}..{self.end_s}")

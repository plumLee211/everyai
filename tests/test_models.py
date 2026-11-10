"""models.py / config.py 계약 테스트.

Enum 문자열 값은 db설계초안.md의 DDL CHECK 제약과 일치해야 한다.
"""

from dataclasses import FrozenInstanceError

import pytest

from barrierfree.config import AnalysisConfig
from barrierfree.models import (
    ISSUE_KIND_LABELS,
    REVIEW_STATUS_LABELS,
    VERDICT_LABELS,
    Issue,
    IssueKind,
    IssueSource,
    ReviewStatus,
    SessionStatus,
    SubtitleSource,
    Verdict,
)


def test_verdict_values_match_db_check() -> None:
    assert {v.value for v in Verdict} == {"ERROR", "REVIEW_REQUIRED", "UNDETERMINED"}


def test_review_status_has_exactly_three_states() -> None:
    assert {s.value for s in ReviewStatus} == {"UNREVIEWED", "CONFIRMED", "DISMISSED"}


def test_issue_source_values_match_db_check() -> None:
    assert {s.value for s in IssueSource} == {"M1", "M2", "M2_SCREEN_DEPENDENT"}


def test_subtitle_source_values_match_db_check() -> None:
    assert {s.value for s in SubtitleSource} == {"FILE", "OCR", "NONE", "UNKNOWN"}


def test_session_status_values_match_db_check() -> None:
    assert {s.value for s in SessionStatus} == {
        "PREPARING",
        "ANALYZING",
        "AWAITING_CONFIRMATION",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
    }


def test_labels_cover_every_enum_member() -> None:
    assert set(ISSUE_KIND_LABELS) == set(IssueKind)
    assert set(VERDICT_LABELS) == set(Verdict)
    assert set(REVIEW_STATUS_LABELS) == set(ReviewStatus)


def test_verdict_labels_avoid_definitive_error_wording() -> None:
    # 화면 기획: '오류 확정'이 아닌 '검토 필요'로 표현한다.
    assert "오류" not in "".join(VERDICT_LABELS.values())


def _issue(**overrides: object) -> Issue:
    base: dict[str, object] = {
        "source": IssueSource.M1,
        "kind": IssueKind.VALUE_MISMATCH,
        "verdict": Verdict.ERROR,
        "start_s": 1.0,
        "end_s": 2.0,
        "reason": "r",
    }
    base.update(overrides)
    return Issue(**base)  # type: ignore[arg-type]


def test_new_issue_defaults_to_unreviewed_with_unique_id() -> None:
    a, b = _issue(), _issue()
    assert a.review_status is ReviewStatus.UNREVIEWED
    assert a.id != b.id


def test_issue_rejects_inverted_time_range() -> None:
    with pytest.raises(ValueError):
        _issue(start_s=5.0, end_s=2.0)


def test_issue_rejects_negative_start() -> None:
    with pytest.raises(ValueError):
        _issue(start_s=-0.1, end_s=2.0)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (float("nan"), 1.0),
        (0.0, float("nan")),
        (0.0, float("inf")),
        (float("-inf"), 1.0),
        (float("inf"), float("inf")),
    ],
)
def test_issue_rejects_non_finite_times(start: float, end: float) -> None:
    with pytest.raises(ValueError):
        _issue(start_s=start, end_s=end)


def test_config_defaults_follow_planv2() -> None:
    cfg = AnalysisConfig()
    assert cfg.max_video_seconds == 20 * 60
    assert cfg.subtitle_region_ratio == 0.25
    assert cfg.candidate_window_s == 2.0
    assert cfg.candidate_window_wide_s == 6.0
    assert cfg.screen_near_window_s == 5.0
    assert cfg.screen_merge_similarity == 0.85
    assert cfg.always_visible_ratio == 0.7
    assert cfg.ocr_confidence_min == 0.5
    assert cfg.screen_ocr_fps == 1.0
    assert cfg.whisper_model == "small"
    assert cfg.idle_threshold_s == 120.0


def test_config_is_immutable() -> None:
    cfg = AnalysisConfig()
    with pytest.raises(FrozenInstanceError):
        cfg.max_video_seconds = 1  # type: ignore[misc]


def test_screen_dependent_phrases_exclude_everyday_words() -> None:
    phrases = AnalysisConfig().screen_dependent_phrases
    assert "보시는 것처럼" in phrases
    assert "이쪽으로" in phrases
    # planv2: 일상 대화에 흔한 단독 표현은 제외한다.
    assert "여기서" not in phrases
    assert "이렇게" not in phrases


def test_config_snapshot_is_json_serializable() -> None:
    import json

    snapshot = AnalysisConfig().to_snapshot()
    assert json.loads(json.dumps(snapshot))["whisper_model"] == "small"

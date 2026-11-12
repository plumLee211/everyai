"""tests/eval/score.py 테스트: 정답 대비 탐지율·정밀도 채점.

(docs/project/implementation-plan.md Task 14-1).

탐지율·정밀도는 자동 판정이 '오류(ERROR)'인 항목만으로 계산한다. '검수 필요'와 '판단 불가'는
개수만 따로 센다. 정밀도 60%에서 검수 시간이 정말 줄어드는지 보려면 이 구분이 필요하다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from barrierfree.models import Verdict
from tests.eval.score import (
    Detection,
    GroundTruthItem,
    VideoScore,
    aggregate,
    format_report,
    load_ground_truth,
    score_video,
)


def truth(start: float, end: float, source: str = "M1") -> GroundTruthItem:
    return GroundTruthItem(start_s=start, end_s=end, source=source)


def found(
    start: float,
    end: float,
    *,
    source: str = "M1",
    verdict: Verdict = Verdict.ERROR,
    confirmed: bool | None = None,
) -> Detection:
    return Detection(start_s=start, end_s=end, source=source, verdict=verdict, confirmed=confirmed)


# ---------- 짝짓기 ----------


def test_perfect_detection() -> None:
    score = score_video("홍보", [truth(10, 12), truth(30, 33)], [found(10, 12), found(30, 33)])
    assert (score.true_positives, score.false_positives, score.false_negatives) == (2, 0, 0)
    assert score.recall == 1.0 and score.precision == 1.0


def test_missed_and_spurious_items_are_counted() -> None:
    score = score_video("홍보", [truth(10, 12), truth(30, 33)], [found(10, 12), found(60, 62)])
    assert (score.true_positives, score.false_positives, score.false_negatives) == (1, 1, 1)
    assert score.recall == 0.5 and score.precision == 0.5


def test_overlap_not_exact_match_is_enough_but_must_overlap() -> None:
    near = score_video("홍보", [truth(10, 14)], [found(12, 16)])
    far = score_video("홍보", [truth(10, 14)], [found(20, 22)])
    assert near.true_positives == 1
    assert far.true_positives == 0 and far.false_positives == 1


def test_source_must_match() -> None:
    score = score_video("홍보", [truth(10, 12, "M1")], [found(10, 12, source="M2")])
    assert score.true_positives == 0


def test_one_truth_matches_only_one_detection() -> None:
    # 같은 구간을 두 번 탐지해도 정답은 한 번만 맞힌 것으로 센다(탐지율 부풀리기 방지).
    score = score_video("홍보", [truth(10, 12)], [found(10, 12), found(10.5, 12)])
    assert score.true_positives == 1 and score.false_positives == 1


def test_best_overlap_wins_when_one_detection_touches_two_truths() -> None:
    score = score_video("홍보", [truth(10, 12), truth(11.9, 14)], [found(10, 12)])
    assert score.true_positives == 1 and score.false_negatives == 1


def test_detection_chooses_the_truth_it_overlaps_most() -> None:
    # 정답 둘 중 더 많이 겹치는 쪽과 짝지어, 다른 탐지가 남은 정답을 맞힐 수 있어야 한다.
    score = score_video(
        "홍보",
        [truth(10, 12), truth(12, 14)],
        [found(10.2, 12.2), found(12.5, 14)],
    )
    assert score.true_positives == 2


# ---------- 판정별 분리 ----------


def test_only_error_verdicts_count_for_precision_and_recall() -> None:
    detections = [
        found(10, 12, verdict=Verdict.ERROR),
        found(30, 33, verdict=Verdict.REVIEW_REQUIRED),
        found(50, 52, verdict=Verdict.UNDETERMINED),
    ]
    score = score_video("교육", [truth(10, 12), truth(30, 33)], detections)
    assert (score.true_positives, score.false_positives, score.false_negatives) == (1, 0, 1)
    assert score.review_required_count == 1
    assert score.undetermined_count == 1


def test_review_required_hits_are_reported_separately_not_as_recall() -> None:
    score = score_video("교육", [truth(30, 33)], [found(30, 33, verdict=Verdict.REVIEW_REQUIRED)])
    assert score.recall == 0.0
    assert score.review_required_hits == 1  # 검수 필요가 정답을 짚은 경우는 따로 알린다


# ---------- 0 나눗셈 ----------


def test_no_truth_and_no_detection_is_well_defined() -> None:
    score = score_video("홍보", [], [])
    assert score.recall is None and score.precision is None


def test_no_detections_gives_zero_recall_and_undefined_precision() -> None:
    score = score_video("홍보", [truth(1, 2)], [])
    assert score.recall == 0.0 and score.precision is None


# ---------- 검수 확정률 ----------


def test_confirmation_rate_uses_only_reviewed_error_items() -> None:
    detections = [
        found(1, 2, confirmed=True),
        found(5, 6, confirmed=False),
        found(9, 10, confirmed=None),  # 아직 검수하지 않음
    ]
    score = score_video("정책", [], detections)
    assert score.confirmation_rate == 0.5
    assert score.reviewed_count == 2


def test_confirmation_rate_is_undefined_without_any_review() -> None:
    assert score_video("정책", [], [found(1, 2)]).confirmation_rate is None


# ---------- 집계·보고 ----------


def test_aggregate_pools_counts_instead_of_averaging_ratios() -> None:
    a = score_video("홍보", [truth(1, 2)], [found(1, 2)])  # 재현율 1/1
    b = score_video("교육", [truth(i, i + 1) for i in range(0, 90, 10)], [])  # 9건 모두 놓침
    total = aggregate([a, b])
    assert total.true_positives == 1 and total.false_negatives == 9
    assert total.recall == pytest.approx(0.1)  # 비율의 평균(0.5)이 아니다


def test_report_groups_by_video_type_and_flags_target_misses() -> None:
    scores = [
        score_video("홍보", [truth(1, 2)], [found(1, 2)]),
        score_video("교육", [truth(1, 2), truth(5, 6)], [found(1, 2), found(20, 21)]),
    ]
    report = format_report(scores)
    assert "홍보" in report and "교육" in report and "전체" in report
    assert "탐지율" in report and "정밀도" in report
    assert "목표 미달" in report  # 교육: 재현율 50% < 80%, 정밀도 50% < 60%


def test_report_says_when_targets_are_met() -> None:
    report = format_report([score_video("홍보", [truth(1, 2)], [found(1, 2)])])
    assert "목표 미달" not in report


def test_report_shows_undefined_values_as_dashes() -> None:
    report = format_report([score_video("홍보", [], [])])
    assert "—" in report


def test_video_score_is_a_plain_value_object() -> None:
    assert VideoScore("x", 0, 0, 0, 0, 0, 0, 0, 0).video_type == "x"


# ---------- 정답 파일 ----------


def test_load_ground_truth_reads_items_and_type(tmp_path: Path) -> None:
    path = tmp_path / "video1.json"
    path.write_text(
        json.dumps(
            {
                "video": "홍보영상.mp4",
                "video_type": "홍보",
                "items": [{"start_s": 10, "end_s": 12, "source": "M1", "note": "날짜 불일치"}],
            }
        ),
        encoding="utf-8",
    )
    video_type, items = load_ground_truth(path)
    assert video_type == "홍보"
    assert items == [truth(10, 12, "M1")]


@pytest.mark.parametrize(
    "content",
    [
        "{broken",
        "[]",
        json.dumps({"video_type": "홍보"}),
        json.dumps({"video_type": "홍보", "items": [{"start_s": 5, "end_s": 1, "source": "M1"}]}),
        json.dumps({"video_type": "홍보", "items": [{"start_s": 1, "end_s": 2, "source": "XX"}]}),
        json.dumps({"video_type": "", "items": []}),
        '{"video_type": "홍보", "items": [{"start_s": 1' + "0" * 309
        + ', "end_s": 2, "source": "M1"}]}',
    ],
)
def test_invalid_ground_truth_is_rejected_with_a_clear_error(tmp_path: Path, content: str) -> None:
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        load_ground_truth(path)

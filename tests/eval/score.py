"""정답 대비 탐지율·정밀도 채점 (planv2.md Task 14-1, 성공 지표).

규칙
- 탐지율(재현율)과 정밀도는 자동 판정이 '오류(ERROR)'인 항목만으로 계산한다.
  '검수 필요'와 '판단 불가'는 개수만 따로 센다. 정밀도 60%에서 실제 검수 시간이 줄어드는지가
  핵심 질문이므로, 거짓 지적 부담을 판정별로 나눠 볼 수 있어야 한다.
- 정답과 탐지는 종류(M1/M2/M2_SCREEN_DEPENDENT)가 같고 구간이 겹칠 때 짝지으며, 한 정답은
  한 탐지에만 대응한다(같은 구간을 두 번 탐지해도 탐지율이 부풀지 않는다).
- 겹침이 큰 쌍부터 짝지어, 한 탐지가 두 정답에 걸쳐도 다른 탐지가 남은 정답을 맞힐 수 있게 한다.
- 영상 형식별(홍보, 정책 안내, 교육)로 보고하고, 전체는 비율의 평균이 아니라 건수를 합쳐 계산한다.

사용법: `python -m tests.eval.score <정답 폴더> <앱 데이터 폴더>`
정답 JSON의 `video`(영상 파일명)를 세션의 영상 파일명과 대조해 짝짓는다. 같은 영상을 여러 번
분석했으면 가장 최근에 완료된 세션을 쓴다.
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from barrierfree.models import IssueSource, ReviewStatus, Verdict
from barrierfree.storage.repository import Repository

RECALL_TARGET = 0.80
PRECISION_TARGET = 0.60
_VALID_SOURCES = {s.value for s in IssueSource}


@dataclass(frozen=True)
class GroundTruthItem:
    start_s: float
    end_s: float
    source: str


@dataclass(frozen=True)
class Detection:
    start_s: float
    end_s: float
    source: str
    verdict: Verdict
    confirmed: bool | None = None  # 편집자 검수: 확정 True, 기각 False, 미검토 None


@dataclass(frozen=True)
class VideoScore:
    video_type: str
    true_positives: int
    false_positives: int
    false_negatives: int
    review_required_count: int
    undetermined_count: int
    review_required_hits: int
    confirmed_count: int
    reviewed_count: int

    @property
    def recall(self) -> float | None:
        total = self.true_positives + self.false_negatives
        return self.true_positives / total if total else None

    @property
    def precision(self) -> float | None:
        total = self.true_positives + self.false_positives
        return self.true_positives / total if total else None

    @property
    def confirmation_rate(self) -> float | None:
        return self.confirmed_count / self.reviewed_count if self.reviewed_count else None


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def _pair(truths: list[GroundTruthItem], detections: list[Detection]) -> dict[int, int]:
    """정답 인덱스 -> 탐지 인덱스. 겹침이 큰 쌍부터 한 번씩만 짝짓는다."""
    candidates: list[tuple[float, int, int]] = []
    for ti, t in enumerate(truths):
        for di, d in enumerate(detections):
            if t.source != d.source:
                continue
            size = _overlap((t.start_s, t.end_s), (d.start_s, d.end_s))
            if size > 0:
                candidates.append((size, ti, di))
    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
    pairs: dict[int, int] = {}
    used_detections: set[int] = set()
    for _size, ti, di in candidates:
        if ti not in pairs and di not in used_detections:
            pairs[ti] = di
            used_detections.add(di)
    return pairs


def score_video(
    video_type: str, truths: list[GroundTruthItem], detections: list[Detection]
) -> VideoScore:
    errors = [d for d in detections if d.verdict is Verdict.ERROR]
    review = [d for d in detections if d.verdict is Verdict.REVIEW_REQUIRED]

    error_pairs = _pair(truths, errors)
    unmatched_truths = [t for i, t in enumerate(truths) if i not in error_pairs]
    review_hits = len(_pair(unmatched_truths, review))

    reviewed = [d for d in errors if d.confirmed is not None]
    return VideoScore(
        video_type=video_type,
        true_positives=len(error_pairs),
        false_positives=len(errors) - len(error_pairs),
        false_negatives=len(truths) - len(error_pairs),
        review_required_count=len(review),
        undetermined_count=sum(1 for d in detections if d.verdict is Verdict.UNDETERMINED),
        review_required_hits=review_hits,
        confirmed_count=sum(1 for d in reviewed if d.confirmed),
        reviewed_count=len(reviewed),
    )


def aggregate(scores: list[VideoScore], video_type: str = "전체") -> VideoScore:
    """건수를 합쳐 계산한다. 영상별 비율을 평균내면 항목이 적은 영상이 과대 반영된다."""
    return VideoScore(
        video_type=video_type,
        true_positives=sum(s.true_positives for s in scores),
        false_positives=sum(s.false_positives for s in scores),
        false_negatives=sum(s.false_negatives for s in scores),
        review_required_count=sum(s.review_required_count for s in scores),
        undetermined_count=sum(s.undetermined_count for s in scores),
        review_required_hits=sum(s.review_required_hits for s in scores),
        confirmed_count=sum(s.confirmed_count for s in scores),
        reviewed_count=sum(s.reviewed_count for s in scores),
    )


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def _row(score: VideoScore) -> str:
    misses = []
    if score.recall is not None and score.recall < RECALL_TARGET:
        misses.append("탐지율")
    if score.precision is not None and score.precision < PRECISION_TARGET:
        misses.append("정밀도")
    flag = f"  ← 목표 미달({', '.join(misses)})" if misses else ""
    counts = (
        f"정탐 {score.true_positives}, 오탐 {score.false_positives}, 미탐 {score.false_negatives}"
        f" · 검수 필요 {score.review_required_count}, 판단 불가 {score.undetermined_count}"
    )
    return (
        f"{score.video_type:<8} 탐지율 {_percent(score.recall):>5}  "
        f"정밀도 {_percent(score.precision):>5}  확정률 {_percent(score.confirmation_rate):>5}"
        f"  ({counts}){flag}"
    )


def format_report(scores: list[VideoScore]) -> str:
    by_type: dict[str, list[VideoScore]] = defaultdict(list)
    for score in scores:
        by_type[score.video_type].append(score)
    lines = [
        f"목표: 탐지율 {RECALL_TARGET:.0%} 이상, 정밀도 {PRECISION_TARGET:.0%} 이상 "
        "(자동 판정이 '오류'인 항목 기준)",
        "",
    ]
    lines += [_row(aggregate(items, kind)) for kind, items in by_type.items()]
    lines += ["", _row(aggregate(scores))]
    return "\n".join(lines)


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("시각이 올바르지 않습니다.")
    try:
        number = float(value)
    except OverflowError as exc:  # 10**309 같은 정수
        raise ValueError("시각이 올바르지 않습니다.") from exc
    if not math.isfinite(number):
        raise ValueError("시각이 올바르지 않습니다.")
    return number


def load_ground_truth(path: str | Path) -> tuple[str, list[GroundTruthItem]]:
    """정답 파일을 읽는다. 형식이 틀리면 어디가 문제인지 알려 주는 ValueError."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"정답 파일을 읽을 수 없습니다: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError("정답 파일의 최상위는 객체여야 합니다.")
    video_type = data.get("video_type")
    if not isinstance(video_type, str) or not video_type.strip():
        raise ValueError("video_type(영상 형식)이 필요합니다.")
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("items(정답 목록)가 필요합니다.")
    items: list[GroundTruthItem] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise ValueError(f"items[{index}]가 객체가 아닙니다.")
        try:
            start, end = _number(raw.get("start_s")), _number(raw.get("end_s"))
        except ValueError as exc:
            raise ValueError(f"items[{index}]: {exc}") from exc
        if start < 0 or end < start:
            raise ValueError(f"items[{index}]의 구간이 올바르지 않습니다.")
        source = raw.get("source")
        if source not in _VALID_SOURCES:
            raise ValueError(f"items[{index}]의 source가 올바르지 않습니다: {source!r}")
        items.append(GroundTruthItem(start, end, str(source)))
    return video_type.strip(), items


def detections_from_session(repo: Repository, session_id: str) -> list[Detection]:
    """저장된 세션의 지적을 채점용 탐지 목록으로 바꾼다."""
    flags = {ReviewStatus.CONFIRMED: True, ReviewStatus.DISMISSED: False}
    return [
        Detection(i.start_s, i.end_s, i.source.value, i.verdict, flags.get(i.review_status))
        for i in repo.list_issues(session_id)
    ]


def latest_session_for(repo: Repository, video_name: str) -> str | None:
    """해당 영상 파일명의 가장 최근 완료 세션 id."""
    row = repo.connection().execute(
        "SELECT id FROM analysis_session WHERE video_name = ? AND status = 'COMPLETED'"
        " ORDER BY created_at DESC LIMIT 1",
        (video_name,),
    ).fetchone()
    return str(row[0]) if row else None


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("사용법: python -m tests.eval.score <정답 폴더> <앱 데이터 폴더>", file=sys.stderr)
        return 2
    truth_dir, data_dir = Path(args[0]), Path(args[1])
    repo = Repository(data_dir / "barrierfree.db")
    scores: list[VideoScore] = []
    try:
        for path in sorted(truth_dir.glob("*.json")):
            try:
                video_type, truths = load_ground_truth(path)
                video_name = json.loads(path.read_text(encoding="utf-8")).get("video")
            except ValueError as exc:
                print(f"건너뜀 {path.name}: {exc}", file=sys.stderr)
                continue
            session_id = latest_session_for(repo, str(video_name)) if video_name else None
            if session_id is None:
                print(
                    f"건너뜀 {path.name}: 분석 결과(영상 {video_name!r})가 없습니다.",
                    file=sys.stderr,
                )
                continue
            detections = detections_from_session(repo, session_id)
            scores.append(score_video(video_type, truths, detections))
    finally:
        repo.close_all()
    if not scores:
        print("채점할 영상이 없습니다.", file=sys.stderr)
        return 1
    print(format_report(scores))
    return 0


if __name__ == "__main__":
    sys.exit(main())

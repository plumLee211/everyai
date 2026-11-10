"""ui/review_timer.py 테스트: 검수 시간 측정 (결정 Q11).

검수 화면이 활성 창일 때만 누적하고, 입력이 없는 채로 유휴 기준(기본 2분) 이상 이어진 구간은
통째로 제외한다. 자리를 비운 시간이 검수 시간으로 잡히면 실증 지표가 부풀려진다.
"""

from __future__ import annotations

import pytest

from barrierfree.ui.review_timer import ActiveTimeTracker


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make(threshold: float = 120.0) -> tuple[ActiveTimeTracker, Clock]:
    clock = Clock()
    return ActiveTimeTracker(threshold, clock), clock


def test_continuous_activity_accumulates_fully() -> None:
    tracker, clock = make()
    tracker.start()
    for _ in range(6):
        clock.advance(10)
        tracker.activity()
    assert tracker.total_ms() == 60_000


def test_gap_shorter_than_threshold_counts() -> None:
    tracker, clock = make()
    tracker.start()
    clock.advance(119)
    tracker.activity()
    assert tracker.total_ms() == 119_000


@pytest.mark.parametrize("gap", [120, 121, 600])
def test_gap_at_or_beyond_threshold_is_excluded_entirely(gap: float) -> None:
    tracker, clock = make()
    tracker.start()
    clock.advance(30)
    tracker.activity()
    clock.advance(gap)  # 자리를 비운 시간
    tracker.activity()
    assert tracker.total_ms() == 30_000


def test_time_before_start_and_after_stop_is_not_counted() -> None:
    tracker, clock = make()
    clock.advance(500)
    tracker.start()
    clock.advance(20)
    tracker.stop()
    clock.advance(1000)  # 창이 비활성인 동안
    assert tracker.total_ms() == 20_000
    tracker.activity()  # 멈춘 동안의 입력은 무시한다
    assert tracker.total_ms() == 20_000


def test_resuming_does_not_count_the_paused_period() -> None:
    tracker, clock = make()
    tracker.start()
    clock.advance(20)
    tracker.stop()
    clock.advance(300)
    tracker.start()
    clock.advance(15)
    tracker.stop()
    assert tracker.total_ms() == 35_000


def test_stop_counts_the_final_short_stretch() -> None:
    tracker, clock = make()
    tracker.start()
    clock.advance(45)
    tracker.stop()
    assert tracker.total_ms() == 45_000


def test_stop_after_a_long_idle_stretch_excludes_it() -> None:
    tracker, clock = make()
    tracker.start()
    clock.advance(200)
    tracker.stop()
    assert tracker.total_ms() == 0


def test_live_total_shows_the_open_stretch_while_it_is_still_short() -> None:
    tracker, clock = make()
    tracker.start()
    clock.advance(50)
    assert tracker.total_ms() == 50_000  # 화면 표시용: 진행 중인 구간 포함
    clock.advance(100)  # 합쳐 150초 -> 이제 유휴로 판정되어 빠진다
    assert tracker.total_ms() == 0


def test_drain_only_reports_committed_time_and_never_goes_negative() -> None:
    tracker, clock = make()
    tracker.start()
    clock.advance(30)
    tracker.activity()
    assert tracker.drain_ms() == 30_000
    clock.advance(100)  # 진행 중인 공백: 아직 확정되지 않았다
    assert tracker.drain_ms() == 0
    clock.advance(100)  # 200초 공백 -> 통째로 제외되어도 이미 보낸 값이 줄어들지 않는다
    tracker.activity()
    assert tracker.drain_ms() == 0
    assert tracker.total_ms() == 30_000


def test_drain_deltas_sum_to_the_committed_total() -> None:
    tracker, clock = make()
    tracker.start()
    drained = 0
    for _ in range(5):
        clock.advance(20)
        tracker.activity()
        drained += tracker.drain_ms()
    clock.advance(7)
    tracker.stop()
    drained += tracker.drain_ms()
    assert drained == tracker.total_ms() == 107_000


def test_start_twice_is_harmless() -> None:
    tracker, clock = make()
    tracker.start()
    clock.advance(10)
    tracker.start()  # 이미 측정 중이면 기준 시각을 다시 잡지 않는다
    clock.advance(10)
    tracker.stop()
    assert tracker.total_ms() == 20_000


def test_custom_threshold_is_respected() -> None:
    tracker, clock = make(threshold=30)
    tracker.start()
    clock.advance(40)
    tracker.activity()
    assert tracker.total_ms() == 0
    assert tracker.is_running

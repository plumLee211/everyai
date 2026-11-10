"""검수 시간 측정 (결정 Q11).

검수 화면이 활성 창일 때만 누적하고, 입력이 없는 채로 유휴 기준(기본 2분) 이상 이어진 구간은
통째로 제외한다. 자리를 비운 시간이 검수 시간으로 잡히면 실증 지표(검수 시간 단축)가 부풀려진다.

시계를 주입받는 순수 클래스라 Qt 없이 결정적으로 테스트할 수 있다.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class ActiveTimeTracker:
    def __init__(
        self, idle_threshold_s: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._threshold = idle_threshold_s
        self._clock = clock
        self._running = False
        self._last_activity = 0.0
        self._committed_s = 0.0  # 공백이 확정된 시간. 줄어들지 않는다.
        self._drained_s = 0.0

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        """검수 화면이 활성 창이 되었다. 이미 측정 중이면 기준 시각을 다시 잡지 않는다."""
        if not self._running:
            self._running = True
            self._last_activity = self._clock()

    def activity(self) -> None:
        """사용자 입력이나 재생이 있었다. 직전 입력 이후의 공백이 짧으면 검수 시간에 더한다."""
        if not self._running:
            return
        now = self._clock()
        self._commit(now)
        self._last_activity = now

    def stop(self) -> None:
        """창이 비활성이 되거나 검수 화면을 떠났다."""
        if not self._running:
            return
        self._commit(self._clock())
        self._running = False

    def _commit(self, now: float) -> None:
        gap = now - self._last_activity
        if gap < self._threshold:
            self._committed_s += gap
        # 유휴 기준 이상 이어진 공백은 통째로 버린다.

    def total_ms(self) -> int:
        """화면 표시용 합계. 진행 중인 공백이 아직 짧으면 포함한다."""
        total = self._committed_s
        if self._running:
            open_gap = self._clock() - self._last_activity
            if open_gap < self._threshold:
                total += open_gap
        return round(total * 1000)

    def drain_ms(self) -> int:
        """아직 저장하지 않은 확정 시간(ms)을 돌려주고 비운다.

        진행 중인 공백은 포함하지 않는다. 나중에 유휴로 판정되어 빠질 수 있는 값을 미리
        저장하면 합계가 줄어드는 음수 증분이 생기기 때문이다.
        """
        delta = round((self._committed_s - self._drained_s) * 1000)
        self._drained_s += delta / 1000
        return max(0, delta)

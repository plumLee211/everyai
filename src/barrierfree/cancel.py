"""협조적 취소. 분석 단계(STT, OCR, 판정)가 같은 토큰을 받아 작업 단위 사이에서 확인한다.

즉시 중단을 보장하지 않는다(docs/project/ui-spec.md 6.6). 엔진이 한 단위를 끝낼 때마다 확인한다.
"""

from __future__ import annotations

import threading


class CancelledError(Exception):
    """사용자가 분석을 취소했다."""


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float) -> bool:
        """취소될 때까지 최대 `timeout`초 기다린다. 취소되었으면 True."""
        return self._event.wait(timeout)

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise CancelledError("분석이 취소되었습니다.")

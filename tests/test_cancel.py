"""cancel.py 테스트: 분석 단계들이 공유하는 협조적 취소 토큰."""

from __future__ import annotations

import threading

import pytest

from barrierfree.cancel import CancelledError, CancelToken


def test_token_starts_not_cancelled() -> None:
    token = CancelToken()
    assert token.cancelled is False
    token.raise_if_cancelled()  # 예외 없음


def test_cancel_sets_flag_and_raise_if_cancelled_raises() -> None:
    token = CancelToken()
    token.cancel()
    assert token.cancelled is True
    with pytest.raises(CancelledError):
        token.raise_if_cancelled()


def test_wait_times_out_until_cancelled_then_returns_immediately() -> None:
    token = CancelToken()
    assert token.wait(0.01) is False
    token.cancel()
    assert token.wait(0.01) is True


def test_cancel_is_idempotent_and_visible_across_threads() -> None:
    token = CancelToken()
    t = threading.Thread(target=token.cancel)
    t.start()
    t.join()
    token.cancel()
    assert token.cancelled is True

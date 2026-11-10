"""validation.py 테스트: 외부(캐시·설정 파일) 숫자 값 검증은 ValueError 하나로만 실패해야 한다.

호출한 쪽(캐시 복원, 설정 읽기)은 ValueError만 잡아 '손상된 값은 버리고 다시 만든다'로 처리한다.
거대한 정수 같은 값이 다른 예외로 새면 그 복구 경로를 건너뛰어 분석이나 앱 시작이 실패한다.
"""

from __future__ import annotations

import pytest

from barrierfree.validation import finite_number, optional_finite_number

HUGE = 10**309  # JSON으로는 유효한 정수지만 float로는 변환되지 않는다


def test_normal_numbers_pass() -> None:
    assert finite_number(3) == 3.0
    assert finite_number(1.5) == 1.5
    assert finite_number(1e308) == 1e308


@pytest.mark.parametrize("bad", [HUGE, -HUGE, float("nan"), float("inf"), True, "1", None, [1]])
def test_every_bad_value_fails_with_value_error_only(bad: object) -> None:
    with pytest.raises(ValueError):
        finite_number(bad)


def test_optional_accepts_none_but_still_rejects_huge_integers() -> None:
    assert optional_finite_number(None) is None
    with pytest.raises(ValueError):
        optional_finite_number(HUGE)

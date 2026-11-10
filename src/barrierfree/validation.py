"""외부(캐시 파일 등)에서 읽은 값을 검증하는 공용 도우미. 위반하면 ValueError."""

from __future__ import annotations

import math


def finite_number(value: object) -> float:
    """bool·문자열·NaN·무한대를 거르고 유한한 숫자만 받는다."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("숫자가 아닌 값입니다.")
    try:
        number = float(value)
    except OverflowError as exc:  # 10**309 같은 정수는 float으로 바꿀 수 없다
        raise ValueError("표현할 수 없을 만큼 큰 값입니다.") from exc
    if not math.isfinite(number):
        raise ValueError("유한하지 않은 값입니다.")
    return number


def optional_finite_number(value: object) -> float | None:
    return None if value is None else finite_number(value)

"""analysis/normalize.py 테스트: 한국어 수사 양방향 정규화와 시각 통일.

음성(STT)·자막·화면 글자가 같은 규칙으로 같은 형태가 되어야 값 비교가 성립한다.
"""

from __future__ import annotations

import pytest

from barrierfree.analysis.normalize import normalize_text, sino_to_int


@pytest.mark.parametrize(
    ("spoken", "value"),
    [
        ("일", 1),
        ("이", 2),
        ("십", 10),
        ("십오", 15),
        ("이십", 20),
        ("이십삼", 23),
        ("삼십일", 31),
        ("백", 100),
        ("백이십", 120),
        ("이백오십", 250),
        ("천", 1000),
        ("삼천", 3000),
        ("천이백삼십사", 1234),
        ("일억", 100_000_000),
        ("억", 100_000_000),
        ("이억 오천만", 250_000_000),
        ("만", 10000),
        ("삼만 오천", 35000),
        ("이십만", 200000),
        ("영", 0),
    ],
)
def test_sino_korean_numbers(spoken: str, value: int) -> None:
    assert sino_to_int(spoken) == value


@pytest.mark.parametrize("text", ["", "안녕", "삼사", "일영", "십오a"])
def test_sino_to_int_returns_none_for_non_numbers(text: str) -> None:
    assert sino_to_int(text) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 날짜
        ("삼월 십오일", "3월15일"),
        ("3월 15일", "3월15일"),
        ("3월15일까지", "3월15일까지"),
        ("십이월 삼십일", "12월30일"),
        ("2025년 3월 1일", "2025년3월1일"),
        # 시각 - 하나의 형태(HH:MM)로 통일
        ("오후 세 시 삼십 분", "15:30"),
        ("15:30", "15:30"),
        ("오후 3시 30분", "15:30"),
        ("오전 아홉 시", "09:00"),
        ("오전 9시", "09:00"),
        ("오후 세 시 반", "15:30"),
        ("열두 시", "12:00"),
        ("오후 열두 시", "12:00"),
        ("오전 열두 시", "00:00"),
        ("저녁 여섯 시", "18:00"),
        ("14시 30분", "14:30"),
        # 고유어 수사 + 단위
        ("스무 명", "20명"),
        ("서른 명", "30명"),
        ("다섯 시", "05:00"),
        # 금액·인원·수치
        ("삼천 원", "3000원"),
        ("3,000원", "3000원"),
        ("만 오천 원", "15000원"),
        ("이십 퍼센트", "20%"),
        ("20%", "20%"),
        ("60퍼센트", "60%"),
        ("두 배", "2배"),
        ("백 명", "100명"),
        ("세 시간", "3시간"),
        ("3.5%", "3.5%"),
        # 전화번호: 공백·하이픈 차이를 통일
        ("02-1234-5678", "0212345678"),
        ("02 1234 5678", "0212345678"),
        ("010-1234-5678", "01012345678"),
    ],
)
def test_normalize_text_canonical_forms(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


def test_same_value_different_surface_forms_match() -> None:
    a = normalize_text("오후 세 시 삼십 분까지")
    b = normalize_text("15:30까지")
    c = normalize_text("오후 3시 30분까지")
    assert a == b == c


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 일반 문장의 '일', '이', '하나'는 숫자로 바꾸지 않는다.
        ("일이 많습니다", "일이많습니다"),
        ("이것은 하나의 예시입니다", "이것은하나의예시입니다"),
        # 흔한 낱말이 거짓 값(시각·금액·횟수)을 만들면 안 된다.
        ("오시는 길", "오시는길"),
        ("일시: 3월 15일", "일시3월15일"),
        ("확인해 주십시오", "확인해주십시오"),
        ("이번 달 사원 모집", "이번달사원모집"),
        ("이 시간에는 일명 이분이", "이시간에는일명이분이"),
        ("세 시작됩니다", "세시작됩니다"),
    ],
)
def test_normalization_does_not_touch_ordinary_words(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("백분율을 확인하세요", "백분율을확인하세요"),
        ("사분면을 확인하세요", "사분면을확인하세요"),
        ("삼분의 일", "삼분의일"),
        ("삼십 분이면 도착", "30분이면도착"),
        ("삼천 원짜리 쿠폰", "3000원짜리쿠폰"),
        ("삼월말 마감", "3월말마감"),
        ("일억 오천만 원", "150000000원"),
        ("억울한 일이 없도록", "억울한일이없도록"),
    ],
)
def test_unit_followed_by_hangul_only_when_it_is_a_particle(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


def test_normalize_is_idempotent() -> None:
    once = normalize_text("삼월 십오일 오후 세 시 삼십 분 삼천 원")
    assert normalize_text(once) == once


def test_normalize_strips_punctuation_spaces_and_case_and_width() -> None:
    assert normalize_text("  ＡＢＣ,  Def! ") == "abcdef"
    assert normalize_text("신청 기간은\n3월 15일까지.") == "신청기간은3월15일까지"


def test_different_values_stay_different() -> None:
    assert normalize_text("3월 15일") != normalize_text("3월 16일")
    assert normalize_text("오후 3시") != normalize_text("오후 4시")
    assert normalize_text("30%") != normalize_text("60%")

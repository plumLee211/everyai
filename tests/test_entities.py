"""analysis/entities.py 테스트: 중요 정보 추출과 값 비교(규칙 기반, LLM 미사용)."""

from __future__ import annotations

import time

import pytest

from barrierfree.analysis.entities import (
    ENTITY_KIND_LABELS,
    Entity,
    EntityKind,
    compare_entities,
    entities_equal,
    extract_entities,
)

K = EntityKind


def values(text: str, kind: EntityKind) -> list[str]:
    return [e.value for e in extract_entities(text) if e.kind is kind]


def first(text: str, kind: EntityKind) -> Entity:
    return next(e for e in extract_entities(text) if e.kind is kind)


def test_every_kind_has_a_korean_label() -> None:
    assert set(ENTITY_KIND_LABELS) == set(EntityKind)


# ---------- 날짜 ----------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("3월 15일부터 신청하세요", ["xxxx-03-15"]),
        ("삼월 십오일까지", ["xxxx-03-15"]),
        ("2025.03.15 오픈", ["2025-03-15"]),
        ("2025-3-5에 시작", ["2025-03-05"]),
        ("2025년 3월에 시작", ["2025-03-xx"]),
        ("매월 15일까지", ["xxxx-xx-15"]),
        ("2025년 3월 15일", ["2025-03-15"]),
    ],
)
def test_extract_dates(text: str, expected: list[str]) -> None:
    assert values(text, K.DATE) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("신청: 3.15 ~ 3.20", ["xxxx-03-15", "xxxx-03-20"]),
        ("3/15까지", ["xxxx-03-15"]),
        ("3.15.(토) 오픈", ["xxxx-03-15"]),
    ],
)
def test_lenient_dates_read_dot_and_slash_forms(text: str, expected: list[str]) -> None:
    got = [e.value for e in extract_entities(text, lenient_dates=True) if e.kind is K.DATE]
    assert got == expected


@pytest.mark.parametrize("text", ["평점 4.5점", "3.15 ~ 3.20", "3/15", "1/2 이상"])
def test_dot_and_slash_forms_are_not_dates_by_default(text: str) -> None:
    assert values(text, K.DATE) == []


@pytest.mark.parametrize("text", ["증가율 30.5%", "버전 1.2.3", "2.5배"])
def test_lenient_dates_ignore_decimals_and_versions(text: str) -> None:
    got = [e for e in extract_entities(text, lenient_dates=True) if e.kind is K.DATE]
    assert got == []


def test_duration_in_days_is_not_a_date() -> None:
    assert values("3일간 진행합니다", K.DATE) == []
    assert values("3일간 진행합니다", K.QUANTITY) == ["3일간"]


def test_weekday() -> None:
    assert values("토요일 오전에 열립니다", K.WEEKDAY) == ["토"]
    assert values("일요일은 휴관", K.WEEKDAY) == ["일"]


# ---------- 시각 ----------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("오후 세 시 삼십 분", ["15:30"]),
        ("15:30까지", ["15:30"]),
        ("오후 3시 30분", ["15:30"]),
        ("3시에 시작", ["03:00"]),
        ("오전 아홉 시", ["09:00"]),
    ],
)
def test_extract_times(text: str, expected: list[str]) -> None:
    assert values(text, K.TIME) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("접수 마감은 오후 여섯 시입니다", ["18:00"]),
        ("행사는 오전 10시였습니다", ["10:00"]),
        ("오후 두 시부터 네 시까지", ["14:00", "04:00"]),
    ],
)
def test_time_followed_by_copula_or_particle(text: str, expected: list[str]) -> None:
    assert values(text, K.TIME) == expected


@pytest.mark.parametrize("text", ["하루 1일 3회 복용합니다", "1일 3회 식후 30분"])
def test_dosage_per_day_is_not_a_date(text: str) -> None:
    assert values(text, K.DATE) == []


def test_time_records_whether_meridiem_was_explicit() -> None:
    assert first("오후 3시", K.TIME).explicit is True
    assert first("3시", K.TIME).explicit is False
    assert first("15:00", K.TIME).explicit is True
    assert first("9:30", K.TIME).explicit is False


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("오후 3시", "3시", True),  # 한쪽에 오전/오후가 없으면 12시간 기준으로 비교
        ("오후 3시", "15:00", True),
        ("오후 세 시 삼십 분", "15:30", True),
        ("오후 3시", "4시", False),
        ("오전 9시", "오후 9시", False),  # 양쪽 모두 명시되면 엄격 비교
        ("3시 30분", "3시", False),
    ],
)
def test_time_equality(a: str, b: str, same: bool) -> None:
    assert entities_equal(first(a, K.TIME), first(b, K.TIME)) is same


# ---------- 금액·인원·수치 ----------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("참가비 삼천 원", ["3000"]),
        ("3만 원입니다", ["30000"]),
        ("5만원", ["50000"]),
        ("3,000원", ["3000"]),
        ("1만 5천 원", ["15000"]),
        ("만 오천 원", ["15000"]),
    ],
)
def test_extract_money(text: str, expected: list[str]) -> None:
    assert values(text, K.MONEY) == expected


def test_extract_headcount() -> None:
    assert values("스무 명 선착순", K.HEADCOUNT) == ["20"]
    assert values("선착순 30명", K.HEADCOUNT) == ["30"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("30% 증가", ["30%"]),
        ("60퍼센트", ["60%"]),
        ("두 배", ["2배"]),
        ("65세 이상", ["65세"]),
        ("10분 소요", ["10분"]),
        ("3.5% 인상", ["3.5%"]),
    ],
)
def test_extract_quantity(text: str, expected: list[str]) -> None:
    assert values(text, K.QUANTITY) == expected


def test_graph_numbers_are_split_into_years_and_ratios() -> None:
    text = "2025년 30%, 2026년 60%"
    assert values(text, K.DATE) == ["2025-xx-xx", "2026-xx-xx"]
    assert values(text, K.QUANTITY) == ["30%", "60%"]


# ---------- 연락처 ----------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("문의 02-1234-5678", ["0212345678"]),
        ("문의 010 1234 5678", ["01012345678"]),
        ("대표번호 1588-1234", ["15881234"]),
        ("전화번호 1234-5678", []),
    ],
)
def test_extract_phone(text: str, expected: list[str]) -> None:
    assert values(text, K.PHONE) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("www.korea.kr/abc에서 확인", ["korea.kr/abc"]),
        ("https://www.example.com/ 참고", ["example.com"]),
        ("홈페이지 example.co.kr 에서", ["example.co.kr"]),
        ("report.pdf 첨부", []),
        ("증가율 3.5% 입니다", []),
    ],
)
def test_extract_url(text: str, expected: list[str]) -> None:
    assert values(text, K.URL) == expected


# ---------- 장소: 좁게 시작한다 ----------


def test_extract_address_floor_room_and_facility() -> None:
    assert values("서울시 강남구 테헤란로 123", K.ADDRESS) == ["서울시강남구테헤란로123"]
    assert values("3층 301호", K.FLOOR_ROOM) == ["3층", "301호"]
    assert values("서울시청 앞에서 만나요", K.FACILITY) == ["서울시청"]
    assert values("강남구청 방문", K.FACILITY) == ["강남구청"]
    assert values("강남역 3번 출구", K.FACILITY) == ["강남역"]


@pytest.mark.parametrize(
    "text",
    [
        "감동적인 영상입니다",
        "공감이 중요합니다",
        "역할을 소개합니다",
        "시청자 여러분 안녕하세요",
        "고객센터에 문의하세요",
        "수어 통역 서비스",
        "번역 서비스",
        "2호선으로 환승",
        "제1호 사업",
        "오시는 길을 안내합니다",
        "안녕하세요 반갑습니다",
    ],
)
def test_no_false_entities_from_ordinary_words(text: str) -> None:
    assert extract_entities(text) == []


def test_mixed_announcement() -> None:
    text = (
        "3월 15일 오후 2시, 서울시청 3층에서 20명 선착순, "
        "참가비 5천 원, 문의 02-1234-5678"
    )
    got = {(e.kind, e.value) for e in extract_entities(text)}
    assert got == {
        (K.DATE, "xxxx-03-15"),
        (K.TIME, "14:00"),
        (K.FACILITY, "서울시청"),
        (K.FLOOR_ROOM, "3층"),
        (K.HEADCOUNT, "20"),
        (K.MONEY, "5000"),
        (K.PHONE, "0212345678"),
    }


# ---------- 비교 ----------


def test_compare_all_matched_ignores_extra_target_entities() -> None:
    speech = extract_entities("3월 15일까지 신청")
    subtitle = extract_entities("신청 마감: 3월 15일, 문의 02-1234-5678")
    diff = compare_entities(speech, subtitle)
    assert len(diff.matched) == 1
    assert diff.missing == [] and diff.mismatched == []


def test_compare_mismatched_value_of_same_kind() -> None:
    diff = compare_entities(extract_entities("3월 15일까지"), extract_entities("3월 16일까지"))
    assert [(s.value, t.value) for s, t in diff.mismatched] == [("xxxx-03-15", "xxxx-03-16")]
    assert diff.missing == [] and diff.matched == []


def test_compare_missing_when_kind_absent_in_target() -> None:
    diff = compare_entities(extract_entities("문의 02-1234-5678"), extract_entities("문의하세요"))
    assert [e.value for e in diff.missing] == ["0212345678"]
    assert diff.mismatched == []


def test_compare_pairs_leftovers_in_order_and_reports_extra_as_missing() -> None:
    speech = extract_entities("3월 15일부터 3월 20일까지, 4월 1일 발표")
    subtitle = extract_entities("3월 15일부터 3월 21일까지")
    diff = compare_entities(speech, subtitle)
    assert len(diff.matched) == 1
    assert [(s.value, t.value) for s, t in diff.mismatched] == [("xxxx-03-20", "xxxx-03-21")]
    assert [e.value for e in diff.missing] == ["xxxx-04-01"]


def test_partial_date_matches_on_shared_fields() -> None:
    diff = compare_entities(extract_entities("3월 15일"), extract_entities("15일까지"))
    assert len(diff.matched) == 1
    this_year = extract_entities("2025년 3월 15일")
    next_year = extract_entities("2026년 3월 15일")
    assert len(compare_entities(this_year, next_year).mismatched) == 1


def test_compare_time_ambiguity() -> None:
    ok = compare_entities(extract_entities("오후 3시"), extract_entities("3시"))
    assert len(ok.matched) == 1
    bad = compare_entities(extract_entities("오후 3시"), extract_entities("4시"))
    assert len(bad.mismatched) == 1


def test_compare_with_no_source_entities_is_empty() -> None:
    diff = compare_entities([], extract_entities("3월 15일"))
    assert diff.matched == [] and diff.missing == [] and diff.mismatched == []


# ---------- Codex 리뷰 반영: 회귀 테스트 ----------


@pytest.mark.parametrize(
    "text",
    [
        "1" * 3000 + "X",  # 금액 정규식의 지수적 백트래킹
        "1 " * 2000 + "X",
        "1만 " * 1500,
        "1.1." * 1000,
        "일 " * 2000,
        "삼십 " * 1500,
        "3월 " * 1500,
        "서울시 " * 1500,
        "가" * 5000,
        "3시 " * 1500,
        "오후 " * 1500,
        "www." * 800,
    ],
)
def test_extraction_time_is_bounded_on_adversarial_input(text: str) -> None:
    start = time.perf_counter()
    extract_entities(text)
    extract_entities(text, lenient_dates=True)
    assert time.perf_counter() - start < 3.0


@pytest.mark.parametrize(
    ("text", "explicit"),
    [
        ("15시에 시작합니다", True),  # 13~24시는 오전/오후가 확정된다
        ("0시에 종료", True),
        ("24시까지", True),
        ("12시에 시작", False),
        ("3시에 시작", False),
    ],
)
def test_24_hour_clock_is_explicit(text: str, explicit: bool) -> None:
    assert first(text, K.TIME).explicit is explicit


def test_24_hour_time_is_not_equal_to_opposite_half_of_day() -> None:
    assert entities_equal(first("15시", K.TIME), first("오전 3시", K.TIME)) is False
    assert entities_equal(first("15시", K.TIME), first("오후 3시", K.TIME)) is True


@pytest.mark.parametrize(
    ("text", "value", "explicit"),
    [
        ("오후 3:00 시작", "15:00", True),
        ("오전 3:00 시작", "03:00", True),
        ("오후3:30", "15:30", True),
        ("저녁 7:00", "19:00", True),
        ("3:00 시작", "03:00", False),
    ],
)
def test_colon_time_keeps_meridiem(text: str, value: str, explicit: bool) -> None:
    entity = first(text, K.TIME)
    assert (entity.value, entity.explicit) == (value, explicit)


def test_morning_vs_afternoon_colon_time_is_a_mismatch() -> None:
    assert entities_equal(first("오전 3시", K.TIME), first("오후 3:00", K.TIME)) is False


def test_compare_finds_perfect_matching_regardless_of_order() -> None:
    source = extract_entities("3시, 오후 3시")
    target = extract_entities("오후 3시, 오전 3시")
    diff = compare_entities(source, target)
    assert len(diff.matched) == 2
    assert diff.mismatched == [] and diff.missing == []


def test_compare_reports_only_the_truly_unmatched_value() -> None:
    diff = compare_entities(extract_entities("오후 3시, 3시"), extract_entities("3시"))
    assert len(diff.matched) == 1
    assert len(diff.missing) == 1 and diff.mismatched == []


@pytest.mark.parametrize(
    "text",
    [
        "백분율을 확인하세요",
        "사분면을 확인하세요",
        "5원칙을 지킵니다",
        "삼분기 실적을 봅니다",
        "이분법으로 나눕니다",
        "3개월 과정입니다",
        "삼분의 일입니다",
    ],
)
def test_compound_nouns_are_not_quantities(text: str) -> None:
    assert [e for e in extract_entities(text) if e.kind in (K.QUANTITY, K.MONEY, K.HEADCOUNT)] == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("지원금 1억 5천만원", ["150000000"]),
        ("5천만원", ["50000000"]),
        ("1억5천만 원", ["150000000"]),
        ("2억원", ["200000000"]),
        ("3만 5천 원", ["35000"]),
        ("일억 오천만 원", ["150000000"]),
        ("삼천만 원", ["30000000"]),
        ("백만 원", ["1000000"]),
        ("1.5억 원", ["150000000"]),
    ],
)
def test_compound_money_units(text: str, expected: list[str]) -> None:
    assert values(text, K.MONEY) == expected


def test_compound_money_difference_is_a_mismatch() -> None:
    diff = compare_entities(extract_entities("1억 5천만원"), extract_entities("2억원"))
    assert [(s.value, t.value) for s, t in diff.mismatched] == [("150000000", "200000000")]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://example.com/ABC", ["example.com/ABC"]),  # 경로의 대소문자는 보존
        ("HTTPS://WWW.Example.COM/abc", ["example.com/abc"]),  # 호스트·스킴만 정규화
    ],
)
def test_url_path_case_is_preserved_but_host_is_normalized(text: str, expected: list[str]) -> None:
    assert values(text, K.URL) == expected


def test_urls_differing_only_in_path_case_are_different() -> None:
    a, b = first("https://example.com/ABC", K.URL), first("https://example.com/abc", K.URL)
    assert entities_equal(a, b) is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("증가율은 3.0%입니다", ["3%"]),
        ("증가율은 03%입니다", ["3%"]),
        ("3.50배 늘었습니다", ["3.5배"]),
        ("0.5% 인하", ["0.5%"]),
        ("100% 환급", ["100%"]),
    ],
)
def test_quantity_numbers_are_canonical(text: str, expected: list[str]) -> None:
    assert values(text, K.QUANTITY) == expected


@pytest.mark.parametrize("text", ["30일 동안 신청", "30일동안 신청"])
def test_duration_spacing_variants_are_not_dates(text: str) -> None:
    assert values(text, K.DATE) == []
    assert values(text, K.QUANTITY) == ["30일간"]


@pytest.mark.parametrize(
    ("text", "kind", "expected"),
    [
        ("30분이면 도착합니다", K.QUANTITY, ["30분"]),
        ("20명씩 입장합니다", K.HEADCOUNT, ["20"]),
        ("5천 원짜리 쿠폰", K.MONEY, ["5000"]),
        ("10분만에 끝납니다", K.QUANTITY, ["10분"]),
        ("백 명이 참여합니다", K.HEADCOUNT, ["100"]),
    ],
)
def test_units_followed_by_particles_still_extract(
    text: str, kind: EntityKind, expected: list[str]
) -> None:
    assert values(text, kind) == expected

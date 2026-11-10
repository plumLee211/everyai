"""중요 정보(날짜·시각·연락처·금액·인원·수치·장소) 추출과 값 비교. 규칙 기반이며 LLM은 쓰지 않는다.

추출은 두 단계다.
1. 원문 단계: 정규화가 지워 버리는 정보(URL의 `/`, 구분자 날짜, 오전/오후)를 먼저 뽑는다.
2. 정규화 단계: `normalize_text(keep_spaces=True)` 결과에서 나머지를 뽑는다.
뽑힌 구간은 공백으로 지워 같은 글자가 두 종류로 중복 집계되지 않게 한다.

장소는 오탐이 많아 좁게 시작한다. 행정구역 주소, 층·호, 명시적 시설명만 뽑는다.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from functools import partial

from barrierfree.analysis.normalize import (
    MERIDIEM_PATTERN,
    TIME_RE,
    UNIT_END,
    apply_meridiem,
    normalize_text,
    parse_time_match,
)


class EntityKind(StrEnum):
    DATE = "DATE"
    WEEKDAY = "WEEKDAY"
    TIME = "TIME"
    MONEY = "MONEY"
    HEADCOUNT = "HEADCOUNT"
    QUANTITY = "QUANTITY"  # 수치·비율·기간(30%, 2배, 65세, 10분)
    PHONE = "PHONE"
    URL = "URL"
    ADDRESS = "ADDRESS"
    FLOOR_ROOM = "FLOOR_ROOM"
    FACILITY = "FACILITY"


ENTITY_KIND_LABELS: dict[EntityKind, str] = {
    EntityKind.DATE: "날짜",
    EntityKind.WEEKDAY: "요일",
    EntityKind.TIME: "시각",
    EntityKind.MONEY: "금액",
    EntityKind.HEADCOUNT: "인원",
    EntityKind.QUANTITY: "수치",
    EntityKind.PHONE: "전화번호",
    EntityKind.URL: "누리집 주소",
    EntityKind.ADDRESS: "주소",
    EntityKind.FLOOR_ROOM: "층·호수",
    EntityKind.FACILITY: "시설",
}


@dataclass(frozen=True)
class Entity:
    kind: EntityKind
    value: str  # 비교용 canonical 값
    raw: str  # 추출 구간의 글자
    explicit: bool = True  # TIME 전용: 오전/오후(또는 24시간제)가 명시되었는가


@dataclass(frozen=True)
class EntityDiff:
    matched: list[tuple[Entity, Entity]] = field(default_factory=list)
    mismatched: list[tuple[Entity, Entity]] = field(default_factory=list)
    missing: list[Entity] = field(default_factory=list)


Maker = Callable[[re.Match[str]], Entity | None]


def _consume(text: str, pattern: re.Pattern[str], make: Maker, out: list[Entity]) -> str:
    chars = list(text)
    for m in pattern.finditer(text):
        entity = make(m)
        if entity is None:
            continue
        out.append(entity)
        for i in range(m.start(), m.end()):
            chars[i] = " "
    return "".join(chars)


# ---------- 원문 단계 ----------

# URL은 소문자로 바꾸기 전에 뽑는다. 경로·쿼리의 대소문자는 주소를 구분할 수 있다.
_URL_RE = re.compile(
    r"(?<![a-z0-9@.-])(?:https?://)?(?:www\.)?(?:[a-z0-9-]+\.)+"
    r"(?:com|net|org|kr|info|edu|gov|biz|tv|io|me|co|go|or|ac)(?![a-z0-9-])"
    r"(?:/[a-z0-9._~%/?#&=+-]*)?",
    re.IGNORECASE,
)


def _make_url(m: re.Match[str]) -> Entity | None:
    raw = m.group(0)
    value = re.sub(r"^https?://", "", raw, flags=re.IGNORECASE)
    value = re.sub(r"^www\.", "", value, flags=re.IGNORECASE)
    host, slash, path = value.partition("/")
    return Entity(EntityKind.URL, (host.lower() + slash + path).rstrip("/."), raw)


_DOTTED_DATE_RE = re.compile(r"(?<!\d)(\d{4})[.\-/]\s?(\d{1,2})[.\-/]\s?(\d{1,2})(?!\d)")


def _rewrite_dotted_date(m: re.Match[str]) -> str:
    year, month, day = m.group(1), int(m.group(2)), int(m.group(3))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return m.group(0)
    return f" {year}년 {month}월 {day}일 "


# 1588-1234 같은 대표번호. 금액·인원 앞의 숫자 덩어리는 제외한다.
_SHORT_PHONE_RE = re.compile(r"(?<![\d-])(1[568]\d{2})[\s-]?(\d{4})(?!\d|\s*[원%명])")


def _make_short_phone(m: re.Match[str]) -> Entity | None:
    return Entity(EntityKind.PHONE, m.group(1) + m.group(2), m.group(0))


def _make_time(m: re.Match[str]) -> Entity | None:
    parsed = parse_time_match(m)
    if parsed is None:
        return None
    hour, minute, explicit = parsed
    return Entity(EntityKind.TIME, f"{hour:02d}:{minute:02d}", m.group(0), explicit)


_COLON_TIME_RE = re.compile(
    rf"(?:(?<![가-힣])({MERIDIEM_PATTERN})\s*)?(?<![\d:.])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])"
)


def _make_colon_time(m: re.Match[str]) -> Entity | None:
    prefix, hour, minute = m.group(1), int(m.group(2)), int(m.group(3))
    explicit = prefix is not None or hour >= 13 or hour == 0
    value = f"{apply_meridiem(hour, prefix):02d}:{minute:02d}"
    return Entity(EntityKind.TIME, value, m.group(0), explicit)


# ---------- 정규화 단계 ----------

_PHONE_RE = re.compile(r"(?<!\d)(0\d{8,10})(?!\d)")

_DATE_RE = re.compile(
    # '3일간', '1일 3회 복용'의 '일'은 날짜가 아니다.
    r"(?<![\d.])(?:(\d{4})\s*년\s*)?(?:(\d{1,2})\s*월\s*)?"
    r"(?:(\d{1,2})\s*일(?!\s*(?:간|동안)|\s*\d+\s*(?:회|번|알|정|포)))?"
)


def _make_date(m: re.Match[str]) -> Entity | None:
    year, month, day = m.groups()
    if not m.group(0).strip():
        return None
    if (month and not 1 <= int(month) <= 12) or (day and not 1 <= int(day) <= 31):
        return None
    value = f"{year or 'xxxx'}-{int(month):02d}" if month else f"{year or 'xxxx'}-xx"
    value += f"-{int(day):02d}" if day else "-xx"
    return Entity(EntityKind.DATE, value, m.group(0).strip())


_WEEKDAY_RE = re.compile(r"([월화수목금토일])요일")


def _make_weekday(m: re.Match[str]) -> Entity | None:
    return Entity(EntityKind.WEEKDAY, m.group(1), m.group(0))


# 금액은 '숫자+단위들' 조각의 나열이다("1억 5천만원" = 1억 + 5천만). 앞쪽 조각은 반드시 단위가
# 붙어야 한다. 단위가 선택이면 같은 숫자열을 나누는 방법이 지수적으로 늘어 정규식이 멈춘다.
_MONEY_RE = re.compile(
    r"(?<![\d.])((?:\d+(?:\.\d+)?\s*[억만천백]{1,3}\s*)*\d+(?:\.\d+)?\s*[억만천백]{0,3})\s*원"
    + UNIT_END
)
_MONEY_UNITS = {"억": 10**8, "만": 10**4, "천": 10**3, "백": 10**2}


def _make_money(m: re.Match[str]) -> Entity | None:
    total = Decimal(0)
    for piece in re.finditer(r"(\d+(?:\.\d+)?)\s*([억만천백]*)", m.group(1)):
        amount = Decimal(piece.group(1))
        for unit in piece.group(2):  # '5천만' = 5 x 천 x 만
            amount *= _MONEY_UNITS[unit]
        total += amount
    return Entity(EntityKind.MONEY, str(int(total)), m.group(0).strip())


_HEADCOUNT_RE = re.compile(r"(?<![\d.])(\d+)\s*명" + UNIT_END)


def _make_headcount(m: re.Match[str]) -> Entity | None:
    return Entity(EntityKind.HEADCOUNT, str(int(m.group(1))), m.group(0))


_ADDRESS_RE = re.compile(
    r"(?<![가-힣])([가-힣]{2,6}(?:특별시|광역시|특별자치시|특별자치도|시|도))\s*"
    r"([가-힣]{1,6}(?:구|군|시))(?:\s*([가-힣0-9]{1,10}(?:로|길)(?:\s*\d{1,4})?))?"
)


def _make_address(m: re.Match[str]) -> Entity | None:
    return Entity(EntityKind.ADDRESS, re.sub(r"\s+", "", m.group(0)), m.group(0))


_FACILITY_END = r"(?=$|[^가-힣]|에|으로|까지|부터|은|는|이|가|을|를|과|와|의|도|앞)"
_FACILITY_RE = re.compile(
    r"(?<![가-힣])([가-힣]{2,8}?)"
    r"(복지관|도서관|문화회관|주민센터|복지센터|체육관|박물관|미술관|시청|구청|군청)"
    + _FACILITY_END
)
# '역'은 번역·통역 같은 낱말이 많아, 승하차 안내 문맥에서만 시설로 본다.
_STATION_RE = re.compile(
    r"(?<![가-힣])([가-힣]{2,6})역(?=\s*(?:\d{1,2}\s*번\s*출구|앞|에서|하차|인근|근처|도보))"
)


def _make_facility(m: re.Match[str]) -> Entity | None:
    return Entity(EntityKind.FACILITY, m.group(1) + m.group(2), m.group(0))


def _make_station(m: re.Match[str]) -> Entity | None:
    return Entity(EntityKind.FACILITY, m.group(1) + "역", m.group(0))


_FLOOR_RE = re.compile(r"(?<![제\d.])(지하\s*)?(\d{1,3})\s*층")
_ROOM_RE = re.compile(r"(?<![제\d.])(\d{1,4})\s*호(?!선)")


def _make_floor(m: re.Match[str]) -> Entity | None:
    prefix = "지하" if m.group(1) else ""
    return Entity(EntityKind.FLOOR_ROOM, f"{prefix}{int(m.group(2))}층", m.group(0))


def _make_room(m: re.Match[str]) -> Entity | None:
    return Entity(EntityKind.FLOOR_ROOM, f"{int(m.group(1))}호", m.group(0))


_QUANTITY_RE = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)\s*"
    r"(%|일간|일(?=\s*동안)|시간|배|건|개|회|번|세|대|분(?!의)|초|주)" + UNIT_END
)


def _canonical_number(text: str) -> str:
    """3.0 -> 3, 03 -> 3, 3.50 -> 3.5. 표기만 다른 같은 수가 불일치로 판정되지 않게 한다."""
    number = Decimal(text)
    if number == number.to_integral_value():
        return str(int(number))
    return format(number.normalize(), "f")


def _make_quantity(m: re.Match[str]) -> Entity | None:
    unit = "일간" if m.group(2) == "일" else m.group(2)  # '30일 동안' = '30일간'
    return Entity(EntityKind.QUANTITY, _canonical_number(m.group(1)) + unit, m.group(0))


_NORMALIZED_STAGE: list[tuple[re.Pattern[str], Maker]] = [
    (_PHONE_RE, lambda m: Entity(EntityKind.PHONE, m.group(1), m.group(0))),
    (_DATE_RE, _make_date),
    (_WEEKDAY_RE, _make_weekday),
    (_MONEY_RE, _make_money),
    (_HEADCOUNT_RE, _make_headcount),
    (_ADDRESS_RE, _make_address),
    (_FACILITY_RE, _make_facility),
    (_STATION_RE, _make_station),
    (_FLOOR_RE, _make_floor),
    (_ROOM_RE, _make_room),
    (_QUANTITY_RE, _make_quantity),
]


# '3.15', '3/15' 같은 월.일 표기. 소수(4.5)·분수(1/2)와 구분되지 않으므로 기본으로는 읽지 않는다.
_SHORT_DATE_RE = re.compile(
    r"(?<![\d./])(1[0-2]|0?[1-9])\s?[./]\s?(3[01]|[12]\d|0?[1-9])"
    # 뒤에 단위가 오면 소수다(2.5배, 3.5%, 4.5점).
    r"(?![\d/]|\.\d|\s?(?:%|퍼센트|배|원|명|점|개|회|세|시간|분|초|kg|km|cm|mm))"
)


def _rewrite_short_date(m: re.Match[str]) -> str:
    return f" {int(m.group(1))}월 {int(m.group(2))}일 "


def extract_entities(text: str, *, lenient_dates: bool = False) -> list[Entity]:
    """텍스트에서 중요 정보를 뽑는다.

    `lenient_dates=True`이면 '3.15', '3/15'도 날짜로 읽는다. 비교 대상(자막·음성 전체)처럼
    추가로 읽힌 값이 무시되는 쪽에만 쓴다. 기준이 되는 쪽(source)에 쓰면 소수·분수가
    날짜로 오인되어 거짓 지적이 생긴다.
    """
    found: list[Entity] = []
    s = unicodedata.normalize("NFKC", text)
    s = _consume(s, _URL_RE, _make_url, found).lower()
    s = _DOTTED_DATE_RE.sub(_rewrite_dotted_date, s)
    if lenient_dates:
        s = _SHORT_DATE_RE.sub(_rewrite_short_date, s)
    s = _consume(s, _SHORT_PHONE_RE, _make_short_phone, found)
    s = _consume(s, TIME_RE, _make_time, found)
    s = _consume(s, _COLON_TIME_RE, _make_colon_time, found)
    t = normalize_text(s, keep_spaces=True)
    for pattern, make in _NORMALIZED_STAGE:
        t = _consume(t, pattern, make, found)
    return found


# ---------- 비교 ----------


def _date_parts(value: str) -> tuple[str, str, str]:
    year, month, day = value.split("-")
    return year, month, day


def entities_equal(a: Entity, b: Entity) -> bool:
    if a.kind is not b.kind:
        return False
    if a.kind is EntityKind.TIME:
        if a.value[3:] != b.value[3:]:
            return False
        hour_a, hour_b = int(a.value[:2]), int(b.value[:2])
        if hour_a == hour_b:
            return True
        # 한쪽에 오전/오후가 없으면 12시간 기준으로 같은 시각으로 본다.
        return (not a.explicit or not b.explicit) and hour_a % 12 == hour_b % 12
    if a.kind is EntityKind.DATE:
        shared = [
            (x, y)
            for x, y in zip(_date_parts(a.value), _date_parts(b.value), strict=True)
            if not x.startswith("x") and not y.startswith("x")
        ]
        return bool(shared) and all(x == y for x, y in shared)
    return a.value == b.value


def _looseness(source: Entity, target: list[Entity], index: int) -> tuple[bool, int]:
    """표기가 같은 짝을 앞에 둔다(False < True). 같으면 입력 순서."""
    other = target[index]
    return (other.value != source.value or other.explicit != source.explicit, index)


def compare_entities(source: list[Entity], target: list[Entity]) -> EntityDiff:
    """source(예: 음성)의 값이 target(예: 자막)에 있는지 본다. target에만 있는 값은 무시한다.

    같은 값이 있으면 일치, 같은 종류에 다른 값만 남으면 불일치, 같은 종류가 없으면 누락이다.
    """
    # 모호한 값(오전/오후 없는 시각, 부분 날짜)이 있으면 탐욕적 매칭은 입력 순서에 따라 짝을
    # 놓친다. 이분 매칭(최대 매칭)으로 짝이 가능한 값이 불일치로 남지 않게 한다.
    adjacency: list[list[int]] = []
    for s in source:
        options = [j for j, t in enumerate(target) if entities_equal(s, t)]
        # 같은 표기(값·명시 여부)를 먼저 시도해 결과를 사람이 보기에 자연스럽게 한다.
        options.sort(key=partial(_looseness, s, target))
        adjacency.append(options)

    owner: dict[int, int] = {}  # target 인덱스 -> source 인덱스

    def augment(i: int, seen: set[int]) -> bool:
        for j in adjacency[i]:
            if j in seen:
                continue
            seen.add(j)
            if j not in owner or augment(owner[j], seen):
                owner[j] = i
                return True
        return False

    for i in range(len(source)):
        augment(i, set())

    pairs = sorted((i, j) for j, i in owner.items())
    matched = [(source[i], target[j]) for i, j in pairs]
    matched_sources = {i for i, _ in pairs}
    leftover = [s for i, s in enumerate(source) if i not in matched_sources]
    unused = [t for j, t in enumerate(target) if j not in owner]

    mismatched: list[tuple[Entity, Entity]] = []
    missing: list[Entity] = []
    for entity in leftover:
        partner = next((t for t in unused if t.kind is entity.kind), None)
        if partner is None:
            missing.append(entity)
        else:
            unused.remove(partner)
            mismatched.append((entity, partner))
    return EntityDiff(matched=matched, mismatched=mismatched, missing=missing)

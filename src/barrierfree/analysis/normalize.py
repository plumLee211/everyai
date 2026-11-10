"""텍스트 정규화: 음성·자막·화면 글자를 같은 규칙으로 같은 형태로 만든다.

비교용 canonical 문자열만 만든다(표시용 원문은 호출자가 따로 보존한다).
한국어 수사는 '값을 만드는 문맥'에서만 숫자로 바꾼다. 일반 문장의 '일', '이', '하나',
'오시는 길', '일시', '이번' 같은 낱말이 거짓 시각·금액을 만들면 값 비교가 오탐을 낸다.

주의: 오전/오후 표현이 없는 시각("3시")은 `03:00`으로 정규화된다. 오후 3시(`15:00`)와
같은 값인지는 이 모듈이 알 수 없으므로, 값 비교(entities.py)에서 시(hour)를 12시간 기준으로
비교해야 한다.
"""

from __future__ import annotations

import re
import unicodedata

_DIGIT_VALUES = {"영": 0, "공": 0, "일": 1, "이": 2, "삼": 3, "사": 4, "오": 5,
                 "육": 6, "칠": 7, "팔": 8, "구": 9}
_PLACE_VALUES = {"십": 10, "백": 100, "천": 1000}
_SINO_CHARS = "영공일이삼사오육칠팔구십백천만억"
_PLACE_CHARS = "십백천만억"

_TENS = {"열": 10, "스물": 20, "스무": 20, "서른": 30, "마흔": 40, "쉰": 50,
         "예순": 60, "일흔": 70, "여든": 80, "아흔": 90}
_ONES = {"다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "하나": 1, "한": 1,
         "두": 2, "둘": 2, "세": 3, "셋": 3, "네": 4, "넷": 4}


def sino_to_int(text: str) -> int | None:
    """한자어 수사("이십삼", "삼만 오천")를 정수로. 수사가 아니거나 문법이 틀리면 None."""
    s = re.sub(r"\s+", "", text)
    if not s or any(c not in _SINO_CHARS for c in s):
        return None
    if s in ("영", "공"):
        return 0
    if "영" in s or "공" in s:
        return None
    total = section = num = 0
    pending_digit = False
    for ch in s:
        if ch in _DIGIT_VALUES:
            if pending_digit:  # '삼사'처럼 자리 없이 숫자가 이어지면 수가 아니다
                return None
            num = _DIGIT_VALUES[ch]
            pending_digit = True
        elif ch in _PLACE_VALUES:
            section += (num or 1) * _PLACE_VALUES[ch]
            num = 0
            pending_digit = False
        else:  # 만, 억
            scale = 10000 if ch == "만" else 100_000_000
            total += ((section + num) or 1) * scale
            section = num = 0
            pending_digit = False
    return total + section + num


def native_to_int(text: str) -> int | None:
    """고유어 수사("열두", "스물한", "세")를 정수로."""
    s = text.strip()
    for tens, value in sorted(_TENS.items(), key=lambda kv: -len(kv[0])):
        if s.startswith(tens):
            rest = s[len(tens):]
            if not rest:
                return value
            return value + _ONES[rest] if rest in _ONES else None
    return _ONES.get(s)


def _alt(words: dict[str, int]) -> str:
    return "|".join(sorted(words, key=len, reverse=True))


_NATIVE = rf"(?:(?:{_alt(_TENS)})(?:{_alt(_ONES)})?|(?:{_alt(_ONES)}))"

# ---------- 시각 ----------

_MERIDIEM = "오전|오후|아침|낮|저녁|밤|새벽"
MERIDIEM_PATTERN = _MERIDIEM

# 수치+단위 뒤에 한글이 이어질 때는 조사·어미일 때만 단위로 읽는다. 백분율·사분면·5원칙·삼분기처럼
# 단위 글자로 시작하는 낱말이 '100분', '4분', '5원'으로 오인되는 것을 막는다.
UNIT_END = (
    r"(?=$|[^가-힣]|까지|부터|동안|정도|쯤|간|전|후|뒤|안|이내|이상|이하|이며|이고|마다|짜리|씩"
    r"|에|은|는|이|가|을|를|도|만|으로|로|입|였|와|과|의|께|내|가량|남짓|넘|걸|중|째|라|며"
    r"|말|초|경)"
)
# '세 시작', '시간'처럼 다른 낱말의 일부인 '시'를 시각으로 읽지 않도록,
# '시' 뒤에 올 수 있는 말(조사 등)을 제한한다.
_TIME_END = (
    r"(?=$|[^가-힣]|까지|부터|에|쯤|경|정각|이후|이전|마다|정도"
    r"|가|는|은|를|을|도|만|로|이|부|입|였|며|인|와|과|라)"
)
_TIME_RE = re.compile(
    rf"(?<![가-힣\d])(?:({_MERIDIEM})\s*)?(\d{{1,2}}|{_NATIVE})\s*시"
    rf"(?:\s*(반)|\s*(\d{{1,2}}|[일이삼사오육칠팔구십]+)\s*분)?{_TIME_END}"
)


def _apply_meridiem(hour: int, prefix: str | None) -> int:
    if prefix in ("오후", "저녁"):
        return hour + 12 if hour < 12 else hour % 24
    if prefix == "밤":
        if 6 <= hour < 12:
            return hour + 12
        return 0 if hour in (12, 24) else hour
    if prefix == "낮":
        return hour + 12 if 1 <= hour <= 6 else hour
    return 0 if hour in (12, 24) and prefix in ("오전", "아침", "새벽") else hour % 24


TIME_RE = _TIME_RE


def parse_time_match(m: re.Match[str]) -> tuple[int, int, bool] | None:
    """`TIME_RE` 매치를 (시, 분, 오전/오후 명시 여부)로. 값이 범위를 벗어나면 None."""
    prefix, hour_s, half, minute_s = m.group(1), m.group(2), m.group(3), m.group(4)
    hour = int(hour_s) if hour_s.isdigit() else native_to_int(hour_s)
    if hour is None or hour > 24:
        return None
    minute = 30 if half else 0
    if minute_s:
        parsed = int(minute_s) if minute_s.isdigit() else sino_to_int(minute_s)
        if parsed is None or parsed > 59:
            return None
        minute = parsed
    # 13~24시와 0시는 오전/오후가 확정된다. 1~12시는 접두어가 없으면 모호하다.
    explicit = prefix is not None or hour >= 13 or hour == 0
    return _apply_meridiem(hour, prefix), minute, explicit


apply_meridiem = _apply_meridiem


def _time_sub(m: re.Match[str]) -> str:
    parsed = parse_time_match(m)
    if parsed is None:
        return m.group(0)
    hour, minute, _ = parsed
    return f"{hour:02d}:{minute:02d}"


# ---------- 단위가 붙는 수사 ----------

_NATIVE_COUNT_RE = re.compile(
    rf"(?<![가-힣\d])({_NATIVE})\s*(시간|명|개|번|살|배){UNIT_END}"
)


def _native_sub(m: re.Match[str]) -> str:
    value = native_to_int(m.group(1))
    return f"{value}{m.group(2)}" if value else m.group(0)


_UNITS = "퍼센트|시간|월|일|년|원|분(?!의)|초|명|배|층|호|개|회|번|건|주|세|대|%"
_SINO_RE = re.compile(
    rf"(?<![가-힣\d])([{_SINO_CHARS}]+(?:\s[{_SINO_CHARS}]+)*)(\s*)({_UNITS}){UNIT_END}"
)
# 십·백·천·만이 없는 한 글자 수사(일·이·삼…)는 흔한 낱말과 겹친다(일명, 이분, 사원, 이번…).
# 그래서 아래 단위가 공백 없이 붙었을 때만 숫자로 읽는다.
_WEAK_ADJACENT_UNITS = {"월", "일", "년", "분", "초", "층", "호"}
_ANY_SPACING_UNITS = {"퍼센트", "%"}
_STRONG_UNITS = _WEAK_ADJACENT_UNITS | _ANY_SPACING_UNITS | {
    "원", "명", "배", "개", "회", "번", "건", "주", "세", "대", "시간"
}


def _sino_sub(m: re.Match[str]) -> str:
    num, space, unit = m.groups()
    tokens = num.split()
    for i in range(len(tokens)):
        candidate = "".join(tokens[i:])
        value = sino_to_int(candidate)
        if value is None:
            continue
        strong = any(c in _PLACE_CHARS for c in candidate)
        if unit in _ANY_SPACING_UNITS:
            allowed = True
        elif strong:
            allowed = unit in _STRONG_UNITS
        else:
            allowed = (
                unit in _WEAK_ADJACENT_UNITS
                and space == ""
                and len(tokens) - i == 1
                and not (candidate == "이" and unit == "분")  # '이분'은 대개 '이 사람'
            )
        if not allowed:
            return m.group(0)
        head = " ".join(tokens[:i])
        shown_unit = "%" if unit in _ANY_SPACING_UNITS else unit
        return f"{head + ' ' if head else ''}{value}{shown_unit}"
    return m.group(0)


_PHONE_RE = re.compile(r"(?<![\d-])(0\d{1,2})[\s\-.)]*(\d{3,4})[\s\-.]*(\d{4})(?!\d)")


def normalize_text(text: str, *, keep_spaces: bool = False) -> str:
    """비교용 canonical 문자열. 멱등이다(normalize(normalize(x)) == normalize(x)).

    `keep_spaces=True`이면 공백을 한 칸으로 남겨 단어 경계를 보존한다(값 추출용).
    """
    s = unicodedata.normalize("NFKC", text).lower()
    s = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", s)  # 3,000 -> 3000
    s = _PHONE_RE.sub(lambda m: "".join(m.groups()), s)
    s = _TIME_RE.sub(_time_sub, s)
    s = _NATIVE_COUNT_RE.sub(_native_sub, s)
    s = _SINO_RE.sub(_sino_sub, s)
    s = re.sub(r"(\d)\s*(?:퍼센트|%)", r"\1%", s)
    s = re.sub(r"(?<![\d:])(\d):(\d{2})(?!\d)", r"0\1:\2", s)  # 9:30 -> 09:30
    s = re.sub(r"[^\w.:%\s]|_" if keep_spaces else r"[^\w.:%]|_", "", s)
    s = re.sub(r"(?<!\d)\.|\.(?!\d)", "", s)  # 숫자 사이가 아닌 마침표 제거
    s = re.sub(r"(?<!\d):|:(?!\d)", "", s)  # 시각이 아닌 콜론 제거
    return re.sub(r"\s+", " ", s).strip() if keep_spaces else s

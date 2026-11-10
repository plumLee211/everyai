"""자막 파일 파서(SRT, SMI)와 SRT 작성기.

인코딩은 BOM → UTF-8(엄격) → CP949 순으로 판정한다. 짧은 한글 자막은 통계적 추측이
틀리기 쉽고, CP949는 EUC-KR의 상위 집합이라 공공기관 자막을 모두 덮는다.
"""

from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass
from pathlib import Path

from barrierfree.models import SubtitleCue

MAX_SUBTITLE_BYTES = 5 * 1024 * 1024
_SMI_LAST_CUE_SECONDS = 2.0


class SubtitleError(Exception):
    pass


class SubtitleParseError(SubtitleError):
    pass


class UnsupportedSubtitleError(SubtitleError):
    pass


@dataclass(frozen=True)
class LoadedSubtitle:
    cues: list[SubtitleCue]
    encoding: str
    format: str  # "SRT" | "SMI"
    # 읽지 못해 건너뛴 블록 수. 0이 아니면 화면에서 "N개 구간을 읽지 못했습니다"로 알린다.
    skipped_blocks: int = 0


# ---------- 공통 ----------


def decode_bytes(data: bytes) -> tuple[str, str]:
    """(텍스트, 인코딩). 판정할 수 없으면 SubtitleParseError."""
    try:
        if data.startswith(b"\xef\xbb\xbf"):
            return data.decode("utf-8-sig"), "utf-8-sig"
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            return data.decode("utf-16"), "utf-16"
    except UnicodeDecodeError as exc:
        raise SubtitleParseError("자막 파일의 인코딩을 확인할 수 없습니다.") from exc
    for encoding in ("utf-8", "cp949"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise SubtitleParseError("자막 파일의 인코딩을 확인할 수 없습니다.")


def _clean(text: str) -> str:
    text = re.sub(r"\{\\[^}]*\}", "", text)  # {\an8} 등 ASS 태그
    text = re.sub(r"<[^>]*>", "", text)
    text = html.unescape(text).replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip()


def _make_cue(start: float, end: float, text: str) -> SubtitleCue | None:
    cleaned = _clean(text)
    if not cleaned or not (math.isfinite(start) and math.isfinite(end)):
        return None
    if start < 0 or end < start:
        return None
    return SubtitleCue(start, end, cleaned)


# ---------- SRT ----------

_TIME = r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
_TIMING = re.compile(rf"{_TIME}\s*-->\s*{_TIME}")


def _to_seconds(h: str, m: str, s: str, ms: str) -> float | None:
    """시:분:초,밀리초 -> 초. 분·초가 0~59를 벗어나면 None."""
    if int(m) > 59 or int(s) > 59:
        return None
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def _parse_srt(text: str) -> tuple[list[SubtitleCue], int]:
    """(자막 목록, 건너뛴 블록 수). 내용이 빈 자막은 건너뛴 것으로 세지 않는다."""
    normalized = text.replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[SubtitleCue] = []
    skipped = 0
    for block in re.split(r"\n\s*\n", normalized):
        if not block.strip():
            continue
        lines = block.strip().split("\n")
        timing_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        match = _TIMING.search(lines[timing_index]) if timing_index is not None else None
        if timing_index is None or match is None:
            skipped += 1
            continue
        g = match.groups()
        start, end = _to_seconds(*g[0:4]), _to_seconds(*g[4:8])
        body = " ".join(lines[timing_index + 1 :])
        if start is None or end is None:
            skipped += 1
            continue
        cue = _make_cue(start, end, body)
        if cue is not None:
            cues.append(cue)
        elif _clean(body):  # 내용은 있는데 시각이 잘못되었다
            skipped += 1
    return sorted(cues, key=lambda c: (c.start_s, c.end_s)), skipped


def parse_srt(text: str) -> list[SubtitleCue]:
    cues, _ = _parse_srt(text)
    if not cues:
        raise SubtitleParseError("자막 파일에서 유효한 자막을 찾을 수 없습니다.")
    return cues


# ---------- SMI ----------

_SYNC = re.compile(r"<SYNC\s+Start\s*=\s*[\"']?(\d+)[\"']?[^>]*>", re.IGNORECASE)
_SMI_P = re.compile(
    r"<P(?:\s+Class\s*=\s*[\"']?(\w+)[\"']?)?[^>]*>(.*?)(?=<P[\s>]|\Z)",
    re.IGNORECASE | re.DOTALL,
)


def parse_smi(text: str) -> list[SubtitleCue]:
    """SAMI. 텍스트가 처음 나타난 언어 클래스 하나만 쓴다."""
    parts = _SYNC.split(text.replace("﻿", ""))
    # parts = [머리말, start1, body1, start2, body2, ...]
    syncs: list[tuple[float, dict[str, str]]] = []
    for i in range(1, len(parts) - 1, 2):
        body = re.sub(r"<br\s*/?>", " ", parts[i + 1], flags=re.IGNORECASE)
        by_class: dict[str, str] = {}
        paragraphs = _SMI_P.findall(body)
        if not paragraphs:
            paragraphs = [("", body)]
        for klass, content in paragraphs:
            by_class.setdefault(klass, _clean(content))
        syncs.append((int(parts[i]) / 1000, by_class))

    chosen: str | None = None
    for _, by_class in syncs:
        chosen = next((k for k, v in by_class.items() if v), None)
        if chosen is not None:
            break
    if chosen is None:
        raise SubtitleParseError("자막 파일에서 유효한 자막을 찾을 수 없습니다.")

    cues: list[SubtitleCue] = []
    for index, (start, by_class) in enumerate(syncs):
        content = by_class.get(chosen, "")
        if not content:
            continue
        end = syncs[index + 1][0] if index + 1 < len(syncs) else start + _SMI_LAST_CUE_SECONDS
        cue = _make_cue(start, end, content)
        if cue is not None:
            cues.append(cue)
    if not cues:
        raise SubtitleParseError("자막 파일에서 유효한 자막을 찾을 수 없습니다.")
    return sorted(cues, key=lambda c: (c.start_s, c.end_s))


# ---------- 파일 ----------


def load_subtitle_file(path: str | Path) -> LoadedSubtitle:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix not in (".srt", ".smi", ".sami"):
        raise UnsupportedSubtitleError(f"지원하지 않는 자막 형식입니다: {suffix or '(없음)'}")
    try:
        if p.stat().st_size > MAX_SUBTITLE_BYTES:
            raise SubtitleParseError("자막 파일이 너무 큽니다.")
        data = p.read_bytes()
    except OSError as exc:
        raise SubtitleParseError(f"자막 파일을 읽을 수 없습니다: {p.name}") from exc
    text, encoding = decode_bytes(data)
    if suffix == ".srt":
        cues, skipped = _parse_srt(text)
        if not cues:
            raise SubtitleParseError("자막 파일에서 유효한 자막을 찾을 수 없습니다.")
        return LoadedSubtitle(cues, encoding, "SRT", skipped)
    return LoadedSubtitle(parse_smi(text), encoding, "SMI")


# ---------- SRT 작성 ----------


def _srt_time(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def format_srt(cues: list[SubtitleCue]) -> str:
    blocks = [
        f"{i}\n{_srt_time(c.start_s)} --> {_srt_time(c.end_s)}\n{c.text}"
        for i, c in enumerate(cues, start=1)
    ]
    return "\n\n".join(blocks) + "\n"


def write_srt(cues: list[SubtitleCue], out_path: str | Path) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(format_srt(cues), encoding="utf-8")
    return out

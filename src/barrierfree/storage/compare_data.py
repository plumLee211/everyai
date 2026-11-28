"""대조 보기 자료의 저장: 분석마다 JSON 파일 하나(`artifacts/compare/<분석 id>.json`).

음성 인식 결과와 화면 글자가 들어 있으므로 데이터 폴더 밖에 두지 않는다. DB 구조는 바꾸지 않는다.
읽을 수 없거나 형식이 틀리면 없는 것으로 본다(검수 화면은 '자료 없음'을 보여 준다).
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from barrierfree.analysis.compare_rows import CompareData
from barrierfree.models import ScreenText, SpeechSegment, SubtitleCue
from barrierfree.validation import finite_number

_VERSION = 1
MAX_BYTES = 20_000_000  # 20분 영상의 자료는 1MB 안팎이다. 훨씬 크면 읽지 않는다
_SESSION_ID = re.compile(r"[A-Za-z0-9-]+")
logger = logging.getLogger(__name__)


def compare_data_path(data_dir: str | Path, session_id: str) -> Path:
    if not _SESSION_ID.fullmatch(session_id):
        raise ValueError("분석 id 형식이 올바르지 않습니다.")
    return Path(data_dir) / "artifacts" / "compare" / f"{session_id}.json"


def save_compare_data(data_dir: str | Path, session_id: str, data: CompareData) -> Path:
    """다 쓴 뒤에 바꿔 넣어 반쪽 파일을 남기지 않는다."""

    def text(item: ScreenText) -> dict[str, object]:
        return {"s": item.start_s, "e": item.end_s, "t": item.text, "c": item.confidence}

    payload = {
        "version": _VERSION,
        "speech": [{"s": s.start_s, "e": s.end_s, "t": s.text} for s in data.speech],
        "screen": [text(t) for t in data.screen],
        "uncertain": [text(t) for t in data.uncertain],
        "cues": [{"s": c.start_s, "e": c.end_s, "t": c.text} for c in data.cues],
    }
    target = compare_data_path(data_dir, session_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(temp, target)
    finally:
        Path(temp).unlink(missing_ok=True)
    return target


def _span(item: Any) -> tuple[float, float, str]:
    start, end = finite_number(item["s"]), finite_number(item["e"])
    if start < 0 or end < start or not isinstance(item["t"], str):
        raise ValueError("대조 자료의 항목이 올바르지 않습니다.")
    return start, end, item["t"]


def _screen_text(item: Any) -> ScreenText:
    start, end, text = _span(item)
    return ScreenText(
        text=text, start_s=start, end_s=end, confidence=finite_number(item["c"]), box=None
    )


def load_compare_data(data_dir: str | Path, session_id: str) -> CompareData | None:
    path = compare_data_path(data_dir, session_id)
    try:
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("대조 자료 파일이 너무 큽니다.")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != _VERSION:
            raise ValueError("대조 자료의 형식이 다릅니다.")
        speech = []
        for item in payload["speech"]:
            start, end, text = _span(item)
            speech.append(SpeechSegment(start, end, text, None, None))
        return CompareData(
            speech=speech,
            screen=[_screen_text(i) for i in payload["screen"]],
            uncertain=[_screen_text(i) for i in payload["uncertain"]],
            cues=[SubtitleCue(*_span(i)) for i in payload["cues"]],
        )
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        logger.exception("대조 자료를 읽지 못했습니다: %s", path.name)
        return None

"""화면 글자 인식(OCR): 엔진 래퍼, 프레임 건너뛰기, 연속 프레임 병합, 캐시 직렬화.

엔진은 `OcrEngine` 프로토콜 뒤에 둔다(EasyOCR 먼저, 필요하면 PaddleOCR로 교체).
OCR은 영상당 한 번만 실행하고, 영역별 분리(자막/일반 화면 글자)는 호출자가 결과로 나눈다.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Protocol

import cv2
import numpy as np
from numpy.typing import NDArray

from barrierfree.cancel import CancelToken
from barrierfree.config import AnalysisConfig
from barrierfree.models import ScreenText
from barrierfree.validation import finite_number

Frame = NDArray[np.uint8]
Rect = tuple[float, float, float, float]  # 정규화 좌표 (x, y, w, h), 0~1

_DIFF_SIZE = (64, 36)
_SAME_POSITION_DISTANCE = 0.25  # 같은 글자를 같은 자리로 볼 중심 거리(프레임 비율)


class OcrError(Exception):
    """화면 글자 인식 실패(ERR-08)."""


class OcrModelError(OcrError):
    """OCR 모델 준비·로드 실패."""


@dataclass(frozen=True)
class OcrBox:
    text: str
    confidence: float
    box: Rect


@dataclass(frozen=True)
class OcrObservation:
    """한 시각에 읽힌 글자들. 건너뛴 프레임은 직전 처리 결과를 그대로 가진다."""

    t: float
    boxes: tuple[OcrBox, ...]


@dataclass(frozen=True)
class OcrResult:
    texts: list[ScreenText] = field(default_factory=list)
    uncertain: list[ScreenText] = field(default_factory=list)  # 신뢰도 낮음 → 화면 확인 필요


class OcrEngine(Protocol):
    def read(self, frame: Frame) -> list[OcrBox]: ...


# ---------- EasyOCR ----------


def _default_reader_factory(languages: list[str], **kwargs: Any) -> Any:
    import easyocr

    return easyocr.Reader(languages, verbose=False, **kwargs)


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


class EasyOcrEngine:
    engine_name = "easyocr"
    model_name = "default"  # EasyOCR은 언어 조합이 모델을 정한다

    def __init__(
        self,
        *,
        gpu: bool,
        model_dir: str | None = None,
        languages: tuple[str, ...] = ("ko", "en"),
        reader_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._gpu = gpu
        self._model_dir = model_dir
        self._languages = languages
        self._factory = reader_factory or _default_reader_factory
        self._reader: Any = None

    def _load(self) -> Any:
        if self._reader is None:
            try:
                self._reader = self._factory(
                    list(self._languages), gpu=self._gpu, model_storage_directory=self._model_dir
                )
            except Exception as exc:  # noqa: BLE001 - 다운로드·로드 실패를 도메인 예외로 변환
                raise OcrModelError(f"화면 글자 인식 모델을 불러올 수 없습니다: {exc}") from exc
        return self._reader

    def prepare(self) -> None:
        """모델을 미리 불러온다(없으면 내려받는다). '모델 준비' 단계를 따로 보여 주려고 분리."""
        self._load()

    def read(self, frame: Frame) -> list[OcrBox]:
        reader = self._load()
        height, width = frame.shape[:2]
        try:
            raw = reader.readtext(frame)
        except Exception as exc:  # noqa: BLE001
            raise OcrError(f"화면 글자 인식에 실패했습니다: {exc}") from exc
        boxes: list[OcrBox] = []
        for polygon, text, confidence in raw:
            cleaned = str(text).strip()
            if not cleaned:
                continue
            xs = [float(p[0]) for p in polygon]
            ys = [float(p[1]) for p in polygon]
            x0, x1 = _clamp(min(xs) / width), _clamp(max(xs) / width)
            y0, y1 = _clamp(min(ys) / height), _clamp(max(ys) / height)
            boxes.append(OcrBox(cleaned, _clamp(float(confidence)), (x0, y0, x1 - x0, y1 - y0)))
        return boxes


# ---------- 프레임 처리 ----------


def _thumbnail(frame: Frame) -> NDArray[np.int16]:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, _DIFF_SIZE, interpolation=cv2.INTER_AREA).astype(np.int16)


def frame_difference(a: Frame, b: Frame) -> float:
    """두 프레임의 평균 밝기 차이(0~1). 크기가 달라도 같은 축소본으로 비교한다."""
    return float(np.mean(np.abs(_thumbnail(a) - _thumbnail(b)))) / 255.0


def run_ocr(
    frames: Iterable[tuple[float, Frame]],
    engine: OcrEngine,
    config: AnalysisConfig,
    *,
    cancel: CancelToken | None = None,
) -> list[OcrObservation]:
    """프레임마다 글자를 읽는다. 직전에 *처리한* 프레임과 거의 같으면 엔진을 부르지 않고 재사용한다.

    직전 프레임이 아니라 마지막 처리 프레임과 비교한다. 직전 프레임과 비교하면 천천히 변하는
    화면의 변화가 누적되어도 계속 무시된다.
    """
    observations: list[OcrObservation] = []
    last_frame: Frame | None = None
    last_boxes: tuple[OcrBox, ...] = ()
    for t, frame in frames:
        if cancel is not None:
            cancel.raise_if_cancelled()
        if (
            last_frame is None
            or frame_difference(last_frame, frame) >= config.frame_diff_skip_threshold
        ):
            last_boxes = tuple(engine.read(frame))
            last_frame = frame
        observations.append(OcrObservation(t, last_boxes))
    return observations


# ---------- 연속 프레임 병합 ----------


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _center(box: Rect) -> tuple[float, float]:
    return box[0] + box[2] / 2, box[1] + box[3] / 2


def _value_changed(a: str, b: str) -> bool:
    """두 글자의 차이에 '숫자가 다른 숫자로 바뀐' 부분이 있는가.

    전화번호·날짜·수치의 숫자가 바뀐 것은 OCR 잡음이 아니라 화면의 실제 변화일 수 있다. 이를 같은
    구간으로 합치면 두 번째 값이 사라져 음성 전달 여부를 점검할 수 없다. 숫자가 글자로 잘못
    읽히거나(5678→567S) 빠진 경우는 잡음으로 보고 계속 병합한다.
    """
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "replace" and a[i1:i2].isdigit() and b[j1:j2].isdigit():
            return True
    return False


@dataclass
class _Track:
    norm: str
    text: str
    box: Rect
    start: float
    last_t: float
    last_index: int
    best_conf: float
    confs: list[float]


def _finish(track: _Track, interval_s: float) -> ScreenText:
    return ScreenText(
        text=track.text,
        start_s=track.start,
        end_s=track.last_t + interval_s,
        confidence=sum(track.confs) / len(track.confs),
        box=track.box,
    )


def merge_observations(
    observations: list[OcrObservation], config: AnalysisConfig, interval_s: float
) -> OcrResult:
    """연속 프레임의 같은 글자(유사도·위치)를 하나의 `ScreenText` 구간으로 합친다."""
    open_tracks: list[_Track] = []
    finished: list[ScreenText] = []

    for index, observation in enumerate(sorted(observations, key=lambda o: o.t)):
        used: set[int] = set()
        for ob in observation.boxes:
            norm = _norm(ob.text)
            if len(norm) < config.ocr_min_text_chars or not re.search(r"\w", norm):
                continue
            best: tuple[float, int] | None = None
            cx, cy = _center(ob.box)
            for i, track in enumerate(open_tracks):
                if i in used:
                    continue
                # 허용 공백을 넘긴 트랙은 같은 글자가 다시 나타나도 잇지 않는다(만료 전에 검사).
                if index - track.last_index - 1 > config.ocr_max_gap_samples:
                    continue
                tx, ty = _center(track.box)
                if abs(cx - tx) > _SAME_POSITION_DISTANCE or abs(cy - ty) > _SAME_POSITION_DISTANCE:
                    continue
                if _value_changed(norm, track.norm):
                    continue
                ratio = SequenceMatcher(None, norm, track.norm, autojunk=False).ratio()
                if ratio >= config.screen_merge_similarity and (best is None or ratio > best[0]):
                    best = (ratio, i)
            if best is None:
                open_tracks.append(
                    _Track(norm, ob.text, ob.box, observation.t, observation.t, index,
                           ob.confidence, [ob.confidence])
                )
                used.add(len(open_tracks) - 1)
                continue
            track = open_tracks[best[1]]
            used.add(best[1])
            track.last_t, track.last_index = observation.t, index
            track.confs.append(ob.confidence)
            if ob.confidence > track.best_conf:  # 가장 확실하게 읽힌 표기를 대표로 쓴다
                track.best_conf, track.text, track.norm, track.box = (
                    ob.confidence, ob.text, norm, ob.box,
                )

        still_open: list[_Track] = []
        for track in open_tracks:
            if index - track.last_index - 1 > config.ocr_max_gap_samples:
                finished.append(_finish(track, interval_s))
            else:
                still_open.append(track)
        open_tracks = still_open

    finished.extend(_finish(track, interval_s) for track in open_tracks)
    finished.sort(key=lambda s: (s.start_s, s.box[1] if s.box else 0.0, s.box[0] if s.box else 0.0))
    return OcrResult(
        texts=[s for s in finished if s.confidence >= config.ocr_confidence_min],
        uncertain=[s for s in finished if s.confidence < config.ocr_confidence_min],
    )


# ---------- 캐시 직렬화 ----------


def observations_to_payload(observations: list[OcrObservation]) -> list[dict[str, object]]:
    return [
        {
            "t": o.t,
            "boxes": [
                {"text": b.text, "confidence": b.confidence, "box": list(b.box)} for b in o.boxes
            ],
        }
        for o in observations
    ]


def observations_from_payload(payload: object) -> list[OcrObservation]:
    """캐시 파일에서 읽은 값을 검증하며 복원한다. 손상되었으면 ValueError."""
    if not isinstance(payload, list):
        raise ValueError("OCR 캐시 형식이 올바르지 않습니다.")
    result: list[OcrObservation] = []
    for item in payload:
        if not isinstance(item, dict) or not isinstance(item.get("boxes"), list):
            raise ValueError("OCR 캐시 항목이 올바르지 않습니다.")
        t = finite_number(item.get("t"))
        if t < 0:
            raise ValueError("OCR 캐시의 시각이 올바르지 않습니다.")
        boxes: list[OcrBox] = []
        for raw in item["boxes"]:
            if not isinstance(raw, dict) or not isinstance(raw.get("text"), str):
                raise ValueError("OCR 캐시 글자 항목이 올바르지 않습니다.")
            confidence = finite_number(raw.get("confidence"))
            rect = raw.get("box")
            if not 0.0 <= confidence <= 1.0 or not isinstance(rect, list) or len(rect) != 4:
                raise ValueError("OCR 캐시 글자 항목이 올바르지 않습니다.")
            x, y, w, h = (finite_number(v) for v in rect)
            # 정규화 좌표는 프레임 안이어야 한다. 손상된 좌표가 영역 판정으로 넘어가지 않게 한다.
            if min(x, y, w, h) < 0 or max(x, y, w, h) > 1 or x + w > 1 + 1e-6 or y + h > 1 + 1e-6:
                raise ValueError("OCR 캐시의 글자 위치가 프레임 밖입니다.")
            boxes.append(OcrBox(raw["text"], confidence, (x, y, w, h)))
        result.append(OcrObservation(t, tuple(boxes)))
    return result

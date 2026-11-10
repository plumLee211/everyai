"""engines/ocr.py 테스트: 프레임 건너뛰기, 구간 병합, EasyOCR 래퍼(주입), 캐시 직렬화."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from barrierfree.cancel import CancelledError, CancelToken
from barrierfree.config import AnalysisConfig
from barrierfree.engines.ocr import (
    EasyOcrEngine,
    OcrBox,
    OcrError,
    OcrModelError,
    OcrObservation,
    frame_difference,
    merge_observations,
    observations_from_payload,
    observations_to_payload,
    run_ocr,
)

CFG = AnalysisConfig()


def frame(value: int, size: tuple[int, int] = (36, 64)) -> np.ndarray:
    return np.full((*size, 3), value, dtype=np.uint8)


def box(text: str, conf: float = 0.9, x: float = 0.1, y: float = 0.1) -> OcrBox:
    return OcrBox(text=text, confidence=conf, box=(x, y, 0.3, 0.05))


def obs(t: float, *boxes: OcrBox) -> OcrObservation:
    return OcrObservation(t, tuple(boxes))


class FakeEngine:
    def __init__(self, script: dict[int, list[OcrBox]] | None = None) -> None:
        self.calls = 0
        self._script = script or {}

    def read(self, f: np.ndarray) -> list[OcrBox]:
        self.calls += 1
        return list(self._script.get(int(f[0, 0, 0]), [box("기본 문구")]))


# ---------- 프레임 차이 ----------


def test_frame_difference_bounds() -> None:
    assert frame_difference(frame(10), frame(10)) == 0.0
    assert frame_difference(frame(0), frame(255)) == pytest.approx(1.0)


def test_frame_difference_handles_different_sizes() -> None:
    assert frame_difference(frame(10, (36, 64)), frame(10, (180, 320))) == pytest.approx(0.0)


# ---------- run_ocr ----------


def test_run_ocr_skips_nearly_identical_frames_and_reuses_boxes() -> None:
    engine = FakeEngine({0: [box("첫 화면")], 200: [box("둘째 화면")]})
    frames = [(0.0, frame(0)), (1.0, frame(1)), (2.0, frame(200)), (3.0, frame(201))]
    result = run_ocr(frames, engine, CFG)
    assert engine.calls == 2
    assert [o.t for o in result] == [0.0, 1.0, 2.0, 3.0]
    assert result[1].boxes == result[0].boxes
    assert result[3].boxes == result[2].boxes
    assert result[2].boxes[0].text == "둘째 화면"


def test_run_ocr_compares_against_last_processed_frame_not_previous_frame() -> None:
    # 프레임마다 1씩 천천히 밝아져도 누적 변화는 결국 감지되어야 한다.
    engine = FakeEngine()
    run_ocr([(float(i), frame(i)) for i in range(11)], engine, CFG)
    assert engine.calls == 2  # 0번과, 누적 차이가 임계값을 넘는 6번


def test_run_ocr_first_frame_is_always_processed() -> None:
    engine = FakeEngine()
    run_ocr([(0.0, frame(0))], engine, CFG)
    assert engine.calls == 1


def test_run_ocr_respects_cancellation() -> None:
    token = CancelToken()

    class CancellingEngine(FakeEngine):
        def read(self, f: np.ndarray) -> list[OcrBox]:
            token.cancel()
            return super().read(f)

    engine = CancellingEngine()
    with pytest.raises(CancelledError):
        run_ocr([(float(i), frame(i * 50)) for i in range(5)], engine, CFG, cancel=token)
    assert engine.calls == 1


# ---------- merge_observations ----------


def test_persistent_text_becomes_one_screen_text() -> None:
    observations = [obs(float(t), box("문의 02-1234-5678")) for t in range(5)]
    result = merge_observations(observations, CFG, interval_s=1.0)
    [item] = result.texts
    assert (item.start_s, item.end_s) == (0.0, 5.0)
    assert item.text == "문의 02-1234-5678"


def test_end_time_uses_sample_interval() -> None:
    result = merge_observations([obs(0.0, box("안내 문구")), obs(0.5, box("안내 문구"))], CFG, 0.5)
    assert result.texts[0].end_s == pytest.approx(1.0)


def test_ocr_jitter_is_merged_and_best_confidence_text_is_kept() -> None:
    observations = [
        obs(0.0, box("문의 02-1234-5678", 0.80)),
        obs(1.0, box("문의 02-1234-567B", 0.95)),
        obs(2.0, box("문의 02-1234-5678", 0.85)),
    ]
    [item] = merge_observations(observations, CFG, 1.0).texts
    assert item.text == "문의 02-1234-567B"
    assert item.confidence == pytest.approx(0.8667, abs=0.001)


def test_different_texts_stay_separate() -> None:
    observations = [obs(0.0, box("신청 안내")), obs(1.0, box("접수 방법 안내"))]
    assert len(merge_observations(observations, CFG, 1.0).texts) == 2


def test_single_missed_sample_is_bridged_but_longer_gap_splits() -> None:
    bridged = [obs(0.0, box("안내 문구")), obs(1.0), obs(2.0, box("안내 문구"))]
    assert len(merge_observations(bridged, CFG, 1.0).texts) == 1
    split = [obs(0.0, box("안내 문구")), obs(1.0), obs(2.0), obs(3.0), obs(4.0, box("안내 문구"))]
    assert len(merge_observations(split, CFG, 1.0).texts) == 2


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("문의 02-1234-5678", "문의 02-1234-5679"),  # 전화번호 마지막 자리
        ("접수 3월 15일", "접수 3월 16일"),  # 날짜
        ("증가율 30%", "증가율 60%"),  # 수치
    ],
)
def test_changed_digits_are_a_different_value_and_split_the_track(before: str, after: str) -> None:
    observations = [obs(0.0, box(before)), obs(1.0, box(after))]
    texts = merge_observations(observations, CFG, 1.0).texts
    assert [t.text for t in texts] == [before, after]  # 바뀐 값을 잡음으로 지우지 않는다
    assert [t.start_s for t in texts] == [0.0, 1.0]


@pytest.mark.parametrize("misread", ["문의 02-1234-567S", "문의 02-1234-567B", "문의 02-1234-567"])
def test_digit_misread_as_a_letter_or_dropped_is_still_jitter(misread: str) -> None:
    observations = [obs(0.0, box("문의 02-1234-5678", 0.9)), obs(1.0, box(misread, 0.8))]
    assert len(merge_observations(observations, CFG, 1.0).texts) == 1


def test_a_gap_longer_than_tolerated_is_not_bridged_when_the_text_returns() -> None:
    # 허용 공백은 1샘플. 글자-빈-빈-글자(2샘플 공백)는 같은 구간으로 잇지 않는다.
    observations = [obs(0.0, box("안내 문구")), obs(1.0), obs(2.0), obs(3.0, box("안내 문구"))]
    texts = merge_observations(observations, CFG, 1.0).texts
    assert [(t.start_s, t.end_s) for t in texts] == [(0.0, 1.0), (3.0, 4.0)]


def test_a_gap_exactly_at_the_tolerance_is_still_bridged() -> None:
    observations = [obs(0.0, box("안내 문구")), obs(1.0), obs(2.0, box("안내 문구"))]
    assert [(t.start_s, t.end_s) for t in merge_observations(observations, CFG, 1.0).texts] == [
        (0.0, 3.0)
    ]


def test_low_confidence_text_goes_to_uncertain_list() -> None:
    observations = [obs(0.0, box("흐릿한 글자", 0.3)), obs(1.0, box("흐릿한 글자", 0.4))]
    result = merge_observations(observations, CFG, 1.0)
    assert result.texts == []
    assert [u.text for u in result.uncertain] == ["흐릿한 글자"]


@pytest.mark.parametrize("noise", ["-", "|", " ", "ㅡ", "."])
def test_noise_marks_are_dropped(noise: str) -> None:
    result = merge_observations([obs(0.0, box(noise))], CFG, 1.0)
    assert result.texts == [] and result.uncertain == []


def test_same_text_at_two_positions_is_two_screen_texts() -> None:
    observations = [
        obs(0.0, box("주의 사항", x=0.1, y=0.1), box("주의 사항", x=0.6, y=0.7)),
        obs(1.0, box("주의 사항", x=0.1, y=0.1), box("주의 사항", x=0.6, y=0.7)),
    ]
    assert len(merge_observations(observations, CFG, 1.0).texts) == 2


def test_whitespace_differences_do_not_split_a_track() -> None:
    observations = [obs(0.0, box("문의  안내 센터")), obs(1.0, box("문의 안내센터"))]
    assert len(merge_observations(observations, CFG, 1.0).texts) == 1


def test_results_are_sorted_by_start_then_position() -> None:
    observations = [
        obs(0.0, box("아래쪽 문구", y=0.8), box("위쪽 문구", y=0.1)),
        obs(1.0, box("아래쪽 문구", y=0.8), box("위쪽 문구", y=0.1), box("나중 문구", y=0.4)),
    ]
    assert [t.text for t in merge_observations(observations, CFG, 1.0).texts] == [
        "위쪽 문구",
        "아래쪽 문구",
        "나중 문구",
    ]


def test_empty_observations() -> None:
    result = merge_observations([], CFG, 1.0)
    assert result.texts == [] and result.uncertain == []


# ---------- EasyOCR 래퍼 ----------


class FakeReader:
    def __init__(self, results: list[Any] | None = None, *, fail: bool = False) -> None:
        self.results = results or []
        self.fail = fail

    def readtext(self, image: np.ndarray) -> list[Any]:
        if self.fail:
            raise RuntimeError("cuda out of memory")
        return self.results


def _engine(reader: FakeReader) -> tuple[EasyOcrEngine, list[dict[str, Any]]]:
    created: list[dict[str, Any]] = []

    def factory(languages: list[str], **kwargs: Any) -> FakeReader:
        created.append({"languages": languages, **kwargs})
        return reader

    return EasyOcrEngine(gpu=False, model_dir="D:/models", reader_factory=factory), created


def test_easyocr_normalizes_polygons_to_frame_coordinates() -> None:
    reader = FakeReader([([[10, 20], [110, 20], [110, 40], [10, 40]], " 안녕 ", 0.8)])
    engine, _ = _engine(reader)
    [result] = engine.read(frame(0, (100, 200)))
    assert result.text == "안녕"
    assert result.box == pytest.approx((0.05, 0.2, 0.5, 0.2))
    assert result.confidence == pytest.approx(0.8)


def test_easyocr_drops_empty_text_and_clamps_values() -> None:
    reader = FakeReader(
        [
            ([[0, 0], [10, 0], [10, 10], [0, 10]], "   ", 0.9),
            ([[-20, -5], [400, -5], [400, 300], [-20, 300]], "크게 벗어남", 1.7),
        ]
    )
    engine, _ = _engine(reader)
    [result] = engine.read(frame(0, (100, 200)))
    assert result.box == (0.0, 0.0, 1.0, 1.0)
    assert result.confidence == 1.0


def test_easyocr_loads_lazily_once_with_languages_and_model_dir() -> None:
    engine, created = _engine(FakeReader())
    assert created == []
    engine.read(frame(0))
    engine.read(frame(0))
    assert len(created) == 1
    assert created[0]["languages"] == ["ko", "en"]
    assert created[0]["gpu"] is False
    assert created[0]["model_storage_directory"] == "D:/models"


def test_easyocr_model_load_failure_is_model_error() -> None:
    def factory(languages: list[str], **kwargs: Any) -> FakeReader:
        raise OSError("download failed")

    engine = EasyOcrEngine(gpu=False, reader_factory=factory)
    with pytest.raises(OcrModelError):
        engine.read(frame(0))


def test_easyocr_read_failure_is_ocr_error() -> None:
    engine, _ = _engine(FakeReader(fail=True))
    with pytest.raises(OcrError):
        engine.read(frame(0))


# ---------- 캐시 직렬화 ----------


def test_observations_payload_roundtrip() -> None:
    original = [
        obs(0.0, box("안내 문구", 0.9), box("다른 문구", 0.5, x=0.5, y=0.5)),
        obs(1.0),
    ]
    assert observations_from_payload(observations_to_payload(original)) == original


def _item(**overrides: Any) -> dict[str, Any]:
    base = {"t": 1.0, "boxes": [{"text": "글", "confidence": 0.9, "box": [0.1, 0.1, 0.2, 0.2]}]}
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "bad",
    [
        {"t": 10**309},
        {"boxes": [{"text": "글", "confidence": 10**309, "box": [0, 0, 1, 1]}]},
        {"boxes": [{"text": "글", "confidence": 0.9, "box": [0, 0, 10**309, 1]}]},
        {"t": -1.0},
        {"t": float("nan")},
        {"t": "1"},
        {"boxes": "x"},
        {"boxes": [{"text": None, "confidence": 0.9, "box": [0, 0, 1, 1]}]},
        {"boxes": [{"text": "글", "confidence": 1.5, "box": [0, 0, 1, 1]}]},
        {"boxes": [{"text": "글", "confidence": 0.9, "box": [0, 0, 1]}]},
        {"boxes": [{"text": "글", "confidence": 0.9, "box": [0, 0, float("nan"), 1]}]},
    ],
)
def test_corrupt_observation_payload_is_rejected(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        observations_from_payload([_item(**bad)])


@pytest.mark.parametrize(
    "rect",
    [
        [2, 2, -1, -1],  # 범위 밖 + 음수 크기
        [-0.1, 0.1, 0.2, 0.2],
        [0.1, 0.1, -0.2, 0.2],
        [0.1, 0.1, 0.2, 1.5],
        [0.9, 0.1, 0.5, 0.2],  # 오른쪽 끝을 넘는다
        [0.1, 0.9, 0.2, 0.5],  # 아래쪽 끝을 넘는다
    ],
)
def test_box_coordinates_outside_the_frame_are_rejected(rect: list[float]) -> None:
    bad = {"t": 1.0, "boxes": [{"text": "글", "confidence": 0.9, "box": rect}]}
    with pytest.raises(ValueError):
        observations_from_payload([bad])


def test_box_touching_the_frame_edge_is_valid() -> None:
    edge = {"t": 1.0, "boxes": [{"text": "글", "confidence": 0.9, "box": [0.0, 0.5, 1.0, 0.5]}]}
    assert observations_from_payload([edge])[0].boxes[0].box == (0.0, 0.5, 1.0, 0.5)


@pytest.mark.parametrize("payload", [None, {}, "x", [1]])
def test_non_list_payload_is_rejected(payload: object) -> None:
    with pytest.raises(ValueError):
        observations_from_payload(payload)

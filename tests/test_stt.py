"""engines/stt.py 테스트: faster-whisper 래퍼. 모델은 주입하는 가짜를 쓴다."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from barrierfree.cancel import CancelledError, CancelToken
from barrierfree.engines.stt import (
    FasterWhisperEngine,
    SttError,
    SttModelError,
    segments_from_payload,
    segments_to_payload,
)
from barrierfree.models import SpeechSegment


@dataclass
class FakeSegment:
    start: float
    end: float
    text: str
    avg_logprob: float = -0.2
    no_speech_prob: float = 0.01


class FakeModel:
    def __init__(self, segments: list[FakeSegment], *, fail: bool = False) -> None:
        self._segments = segments
        self._fail = fail
        self.calls: list[dict[str, Any]] = []

    def transcribe(self, audio: str, **kwargs: Any) -> tuple[Iterator[FakeSegment], object]:
        self.calls.append({"audio": audio, **kwargs})
        if self._fail:
            raise RuntimeError("decode failed")
        return iter(self._segments), object()


def _audio(tmp_path: Path) -> Path:
    f = tmp_path / "a.wav"
    f.write_bytes(b"RIFF")
    return f


def _engine(model: FakeModel, **kwargs: Any) -> tuple[FasterWhisperEngine, list[dict[str, Any]]]:
    created: list[dict[str, Any]] = []

    def factory(name: str, **fkwargs: Any) -> FakeModel:
        created.append({"name": name, **fkwargs})
        return model

    engine = FasterWhisperEngine(
        "small", device="cpu", compute_type="int8", model_factory=factory, **kwargs
    )
    return engine, created


def test_transcribe_converts_segments_and_keeps_confidence(tmp_path: Path) -> None:
    model = FakeModel(
        [
            FakeSegment(0.0, 1.5, "  신청은 3월 15일부터입니다.  ", -0.35, 0.02),
            FakeSegment(2.0, 3.0, "문의는 홈페이지로", -1.4, 0.7),
        ]
    )
    engine, _ = _engine(model)
    assert engine.transcribe(_audio(tmp_path)) == [
        SpeechSegment(0.0, 1.5, "신청은 3월 15일부터입니다.", -0.35, 0.02),
        SpeechSegment(2.0, 3.0, "문의는 홈페이지로", -1.4, 0.7),
    ]


def test_transcribe_uses_korean_vad_and_no_previous_text_conditioning(tmp_path: Path) -> None:
    model = FakeModel([FakeSegment(0, 1, "안녕")])
    engine, _ = _engine(model)
    engine.transcribe(_audio(tmp_path))
    call = model.calls[0]
    assert call["language"] == "ko"
    assert call["vad_filter"] is True
    # 직전 문장을 이어받으면 환각이 반복·증폭되므로 끈다.
    assert call["condition_on_previous_text"] is False


def test_empty_text_segments_are_dropped(tmp_path: Path) -> None:
    model = FakeModel([FakeSegment(0, 1, "   "), FakeSegment(1, 2, "안녕")])
    engine, _ = _engine(model)
    assert [s.text for s in engine.transcribe(_audio(tmp_path))] == ["안녕"]


def test_model_is_loaded_lazily_once_with_requested_settings(tmp_path: Path) -> None:
    model = FakeModel([FakeSegment(0, 1, "안녕")])
    engine, created = _engine(model, model_dir="D:/models")
    assert created == []  # 생성만으로는 로드하지 않는다
    engine.transcribe(_audio(tmp_path))
    engine.transcribe(_audio(tmp_path))
    assert len(created) == 1
    assert created[0] == {
        "name": "small",
        "device": "cpu",
        "compute_type": "int8",
        "download_root": "D:/models",
    }


def test_model_load_failure_is_reported_as_model_error(tmp_path: Path) -> None:
    def factory(name: str, **kwargs: Any) -> FakeModel:
        raise OSError("download failed")

    engine = FasterWhisperEngine(
        "small", device="cpu", compute_type="int8", model_factory=factory
    )
    with pytest.raises(SttModelError):
        engine.transcribe(_audio(tmp_path))


def test_transcribe_failure_is_reported_as_stt_error(tmp_path: Path) -> None:
    engine, _ = _engine(FakeModel([], fail=True))
    with pytest.raises(SttError):
        engine.transcribe(_audio(tmp_path))


def test_missing_audio_file_is_stt_error(tmp_path: Path) -> None:
    engine, _ = _engine(FakeModel([]))
    with pytest.raises(SttError):
        engine.transcribe(tmp_path / "missing.wav")


def test_cancel_between_segments_stops_and_raises(tmp_path: Path) -> None:
    token = CancelToken()
    seen: list[str] = []

    class CancellingModel(FakeModel):
        def transcribe(self, audio: str, **kwargs: Any) -> tuple[Iterator[FakeSegment], object]:
            def gen() -> Iterator[FakeSegment]:
                for i in range(5):
                    seen.append(f"yielded-{i}")
                    if i == 1:
                        token.cancel()
                    yield FakeSegment(i, i + 1, f"문장{i}")

            return gen(), object()

    engine, _ = _engine(CancellingModel([]))
    with pytest.raises(CancelledError):
        engine.transcribe(_audio(tmp_path), cancel=token)
    assert seen == ["yielded-0", "yielded-1"]  # 취소 뒤에는 더 읽지 않는다


def _cuda_engine(
    gpu_error: Exception, *, fail_on: str, cpu_model: FakeModel
) -> tuple[FasterWhisperEngine, list[dict[str, Any]]]:
    """GPU 모델은 fail_on("load"|"transcribe") 시점에 gpu_error로 실패하고 CPU 모델은 정상이다."""
    created: list[dict[str, Any]] = []

    class GpuModel(FakeModel):
        def transcribe(self, audio: str, **kwargs: Any) -> tuple[Iterator[FakeSegment], object]:
            raise gpu_error

    def factory(name: str, **fkwargs: Any) -> FakeModel:
        created.append({"name": name, **fkwargs})
        if fkwargs["device"] == "cuda":
            if fail_on == "load":
                raise gpu_error
            return GpuModel([])
        return cpu_model

    engine = FasterWhisperEngine(
        "medium", device="cuda", compute_type="float16", model_factory=factory
    )
    return engine, created


def test_cuda_library_missing_at_inference_falls_back_to_cpu(tmp_path: Path) -> None:
    cpu = FakeModel([FakeSegment(0, 1, "안녕")])
    err = RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
    engine, created = _cuda_engine(err, fail_on="transcribe", cpu_model=cpu)
    assert [s.text for s in engine.transcribe(_audio(tmp_path))] == ["안녕"]
    assert engine.device == "cpu"
    assert engine.compute_type == "int8"
    assert engine.fell_back_to_cpu is True
    assert [c["device"] for c in created] == ["cuda", "cpu"]
    assert all(c["name"] == "medium" for c in created)  # 모델은 바꾸지 않는다


def test_cuda_error_at_model_load_falls_back_to_cpu(tmp_path: Path) -> None:
    cpu = FakeModel([FakeSegment(0, 1, "안녕")])
    err = RuntimeError("CUDA driver version is insufficient for CUDA runtime version")
    engine, created = _cuda_engine(err, fail_on="load", cpu_model=cpu)
    assert [s.text for s in engine.transcribe(_audio(tmp_path))] == ["안녕"]
    assert engine.fell_back_to_cpu is True
    assert [c["device"] for c in created] == ["cuda", "cpu"]


def test_fallback_happens_once_and_cpu_is_reused(tmp_path: Path) -> None:
    cpu = FakeModel([FakeSegment(0, 1, "안녕")])
    engine, created = _cuda_engine(
        RuntimeError("cublas not found"), fail_on="transcribe", cpu_model=cpu
    )
    engine.transcribe(_audio(tmp_path))
    engine.transcribe(_audio(tmp_path))
    assert [c["device"] for c in created] == ["cuda", "cpu"]


def test_non_cuda_error_on_gpu_is_not_swallowed(tmp_path: Path) -> None:
    cpu = FakeModel([FakeSegment(0, 1, "안녕")])
    engine, created = _cuda_engine(
        RuntimeError("decode failed"), fail_on="transcribe", cpu_model=cpu
    )
    with pytest.raises(SttError):
        engine.transcribe(_audio(tmp_path))
    assert engine.device == "cuda"
    assert engine.fell_back_to_cpu is False
    assert [c["device"] for c in created] == ["cuda"]


def test_cpu_engine_never_falls_back_on_cuda_like_error(tmp_path: Path) -> None:
    engine, created = _engine(FakeModel([], fail=True))
    with pytest.raises(SttError):
        engine.transcribe(_audio(tmp_path))
    assert engine.fell_back_to_cpu is False
    assert len(created) == 1


def test_cpu_failure_after_fallback_is_reported(tmp_path: Path) -> None:
    engine, _ = _cuda_engine(
        RuntimeError("cublas not found"),
        fail_on="transcribe",
        cpu_model=FakeModel([], fail=True),
    )
    with pytest.raises(SttError):
        engine.transcribe(_audio(tmp_path))


def test_payload_roundtrip_preserves_values() -> None:
    segments = [
        SpeechSegment(0.0, 1.5, "안녕하세요", -0.3, 0.05),
        SpeechSegment(2.0, 3.0, "신호 없음", None, None),
    ]
    assert segments_from_payload(segments_to_payload(segments)) == segments


@pytest.mark.parametrize(
    "payload",
    [None, {}, [{"start": 0}], [{"start": "a", "end": 1, "text": "x"}]],
)
def test_invalid_payload_raises_value_error(payload: object) -> None:
    with pytest.raises(ValueError):
        segments_from_payload(payload)


def _item(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "start": 0.0,
        "end": 1.0,
        "text": "안녕",
        "avg_logprob": -0.3,
        "no_speech_prob": 0.1,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "bad",
    [
        {"start": -1.0},
        {"end": float("nan")},
        {"end": float("inf")},
        {"start": 5.0, "end": 1.0},
        {"text": None},
        {"text": 123},
        {"avg_logprob": "bad"},
        {"avg_logprob": float("nan")},
        {"avg_logprob": True},
        {"no_speech_prob": 1.5},
        {"no_speech_prob": -0.1},
        {"start": True},
        {"start": "1.0"},
        {"start": 10**309},
        {"end": 10**309},
        {"avg_logprob": 10**309},
        {"no_speech_prob": 10**309},
    ],
)
def test_corrupt_cache_items_are_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        segments_from_payload([_item(**bad)])


def test_optional_confidence_fields_may_be_null_or_missing() -> None:
    item = _item(avg_logprob=None, no_speech_prob=None)
    assert segments_from_payload([item])[0].avg_logprob is None
    del item["avg_logprob"]
    assert segments_from_payload([item])[0].avg_logprob is None

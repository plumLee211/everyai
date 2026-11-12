"""engines/stt.py 테스트: openai-whisper 래퍼. 모델은 주입하는 가짜를 쓴다."""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from barrierfree.cancel import CancelledError, CancelToken
from barrierfree.engines.stt import (
    SttError,
    SttModelError,
    WhisperEngine,
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "avg_logprob": self.avg_logprob,
            "no_speech_prob": self.no_speech_prob,
        }


class FakeModel:
    """whisper.model.Whisper 흉내. transcribe는 30초 창마다 decode를 부른다."""

    def __init__(
        self, segments: list[FakeSegment], *, fail: bool = False, windows: int = 1
    ) -> None:
        self._segments = segments
        self._fail = fail
        self._windows = windows
        self.calls: list[dict[str, Any]] = []
        self.decoded = 0

    def decode(self, *_args: Any, **_kwargs: Any) -> None:
        self.decoded += 1

    def transcribe(self, audio: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"audio": audio, **kwargs})
        if self._fail:
            raise RuntimeError("decode failed")
        for _ in range(self._windows):
            self.decode()
        return {"text": "", "segments": [seg.as_dict() for seg in self._segments]}


def _audio(tmp_path: Path, seconds: float = 0.5) -> Path:
    f = tmp_path / "a.wav"
    with wave.open(str(f), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x40" * int(16000 * seconds))  # 0x4000 = 0.5
    return f


def _engine(model: FakeModel, **kwargs: Any) -> tuple[WhisperEngine, list[dict[str, Any]]]:
    created: list[dict[str, Any]] = []

    def factory(name: str, **fkwargs: Any) -> FakeModel:
        created.append({"name": name, **fkwargs})
        return model

    engine = WhisperEngine("small", device="cpu", model_factory=factory, **kwargs)
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


def test_transcribe_uses_korean_and_no_previous_text_conditioning(tmp_path: Path) -> None:
    model = FakeModel([FakeSegment(0, 1, "안녕")])
    engine, _ = _engine(model)
    engine.transcribe(_audio(tmp_path))
    call = model.calls[0]
    assert call["language"] == "ko"
    # 직전 문장을 이어받으면 환각이 반복·증폭되므로 끈다.
    assert call["condition_on_previous_text"] is False
    assert call["fp16"] is False  # CPU에서는 fp16을 쓰지 않는다


def test_wav_is_passed_as_float_samples_not_a_path(tmp_path: Path) -> None:
    # 경로를 넘기면 whisper가 PATH의 ffmpeg를 부르므로 이미 추출한 WAV를 배열로 넘긴다.
    model = FakeModel([])
    engine, _ = _engine(model)
    engine.transcribe(_audio(tmp_path, seconds=0.5))
    audio = model.calls[0]["audio"]
    assert isinstance(audio, np.ndarray) and audio.dtype == np.float32
    assert audio.shape == (8000,)
    assert audio[0] == pytest.approx(0.5)


def test_wav_in_a_different_format_is_stt_error(tmp_path: Path) -> None:
    f = tmp_path / "stereo.wav"
    with wave.open(str(f), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(44100)
        wav.writeframes(b"\x00" * 400)
    engine, _ = _engine(FakeModel([]))
    with pytest.raises(SttError):
        engine.transcribe(f)


def test_unreadable_audio_file_is_stt_error(tmp_path: Path) -> None:
    f = tmp_path / "broken.wav"
    f.write_bytes(b"RIFF")
    engine, _ = _engine(FakeModel([]))
    with pytest.raises(SttError):
        engine.transcribe(f)


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
    assert created == [{"name": "small", "device": "cpu", "download_root": "D:/models"}]


def test_model_load_failure_is_reported_as_model_error(tmp_path: Path) -> None:
    def factory(name: str, **kwargs: Any) -> FakeModel:
        raise OSError("download failed")

    engine = WhisperEngine("small", device="cpu", model_factory=factory)
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


def test_cancel_between_windows_stops_and_raises(tmp_path: Path) -> None:
    token = CancelToken()
    model = FakeModel([], windows=5)
    original = FakeModel.decode

    def decode_then_cancel(self: FakeModel, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        if self.decoded == 2:
            token.cancel()

    engine, _ = _engine(model)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(FakeModel, "decode", decode_then_cancel)
        with pytest.raises(CancelledError):
            engine.transcribe(_audio(tmp_path), cancel=token)
    assert model.decoded == 2  # 취소 뒤의 창은 디코딩하지 않는다
    assert "decode" not in vars(model)  # 감쌌던 decode는 원래대로 돌려놓는다


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

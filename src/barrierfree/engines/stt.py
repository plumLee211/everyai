"""openai-whisper 래퍼. 구간별 신뢰도(avg_logprob, no_speech_prob)를 보존한다.

모델은 `model_factory`로 주입할 수 있어 테스트는 다운로드 없이 가짜 모델을 쓴다.
원시 결과를 캐시할 수 있도록 JSON 직렬화 도우미를 함께 둔다.
"""

from __future__ import annotations

import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

from barrierfree.cancel import CancelToken
from barrierfree.models import SpeechSegment
from barrierfree.validation import finite_number, optional_finite_number

_SAMPLE_RATE = 16000  # whisper 입력 규격. extract_audio가 이 형식으로 추출한다.


class SttError(Exception):
    """음성 인식 실패(ERR-07)."""


class SttModelError(SttError):
    """모델 준비·로드 실패(ERR-05/06)."""


class SttEngine(Protocol):
    def transcribe(
        self, audio_path: str | Path, *, cancel: CancelToken | None = None
    ) -> list[SpeechSegment]: ...


def _default_factory(model_name: str, **kwargs: Any) -> Any:
    import whisper

    return whisper.load_model(model_name, **kwargs)


def _load_wav(path: Path) -> npt.NDArray[np.float32]:
    """16kHz mono 16bit WAV를 whisper가 받는 float32 배열로 읽는다.

    경로를 그대로 넘기면 whisper가 PATH의 ffmpeg를 다시 호출해 동봉 ffmpeg를 못 쓰므로,
    이미 추출해 둔 WAV를 직접 읽는다.
    """
    try:
        with wave.open(str(path), "rb") as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, _SAMPLE_RATE):
                raise SttError("오디오 형식이 16kHz mono 16bit WAV가 아닙니다.")
            raw = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError, OSError) as exc:
        raise SttError(f"오디오 파일을 읽을 수 없습니다: {exc}") from exc
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


class WhisperEngine:
    engine_name = "openai-whisper"

    def __init__(
        self,
        model_name: str,
        *,
        device: str,
        model_dir: str | None = None,
        model_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self._model_dir = model_dir
        self._factory = model_factory or _default_factory
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            try:
                self._model = self._factory(
                    self.model_name, device=self.device, download_root=self._model_dir
                )
            except Exception as exc:  # noqa: BLE001 - 다운로드·로드 실패를 도메인 예외로 변환
                raise SttModelError(f"음성 인식 모델을 불러올 수 없습니다: {exc}") from exc
        return self._model

    def prepare(self) -> None:
        """모델을 미리 불러온다(없으면 내려받는다). '모델 준비' 단계를 따로 보여 주려고 분리."""
        self._load()

    def transcribe(
        self, audio_path: str | Path, *, cancel: CancelToken | None = None
    ) -> list[SpeechSegment]:
        path = Path(audio_path)
        if not path.is_file():
            raise SttError(f"오디오 파일이 없습니다: {path.name}")
        audio = _load_wav(path)
        model = self._load()
        # whisper의 transcribe는 도중에 멈출 수 없다. 30초 창마다 부르는 decode를 잠시 감싸
        # 창 사이에서 취소를 확인한다.
        if cancel is not None:
            original_decode = model.decode

            def decode(*args: Any, **kwargs: Any) -> Any:
                cancel.raise_if_cancelled()
                return original_decode(*args, **kwargs)

            model.decode = decode
        try:
            result = model.transcribe(
                audio,
                language="ko",
                # 직전 문장을 이어받으면 무음·음악 구간의 환각이 반복·증폭된다.
                condition_on_previous_text=False,
                fp16=self.device == "cuda",
                verbose=None,  # 콘솔 출력·진행 막대 없음
            )
        except Exception as exc:
            if cancel is not None and cancel.cancelled:
                raise
            raise SttError(f"음성 인식에 실패했습니다: {exc}") from exc
        finally:
            if cancel is not None:
                del model.decode
        results: list[SpeechSegment] = []
        for seg in result["segments"]:
            text = str(seg["text"]).strip()
            if text:
                results.append(
                    SpeechSegment(
                        start_s=float(seg["start"]),
                        end_s=float(seg["end"]),
                        text=text,
                        avg_logprob=seg.get("avg_logprob"),
                        no_speech_prob=seg.get("no_speech_prob"),
                    )
                )
        return results


def write_whisper_srt(segments: list[SpeechSegment], out_path: str | Path) -> Path:
    """인식 결과를 whisper의 SRT 작성기(whisper.utils.WriteSRT)로 저장한다.

    캐시에서 복원한 결과도 쓸 수 있도록 whisper 원시 결과 대신 SpeechSegment에서 다시 만든다.
    """
    from whisper.utils import WriteSRT

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "segments": [{"start": s.start_s, "end": s.end_s, "text": s.text} for s in segments]
    }
    try:
        with out.open("w", encoding="utf-8") as file:
            WriteSRT(str(out.parent)).write_result(result, file=file)
    except BaseException:
        out.unlink(missing_ok=True)  # 반쯤 쓴 자막 파일은 남기지 않는다
        raise
    return out


def segments_to_payload(segments: list[SpeechSegment]) -> list[dict[str, object]]:
    return [
        {
            "start": s.start_s,
            "end": s.end_s,
            "text": s.text,
            "avg_logprob": s.avg_logprob,
            "no_speech_prob": s.no_speech_prob,
        }
        for s in segments
    ]


def segments_from_payload(payload: object) -> list[SpeechSegment]:
    """캐시 파일에서 읽은 값을 검증하며 복원한다. 손상되었으면 ValueError.

    캐시는 외부에서 바뀌거나 손상될 수 있으므로, 시각·텍스트·신뢰도를 모두 검사해
    분석 단계로 잘못된 값이 넘어가지 않게 한다.
    """
    if not isinstance(payload, list):
        raise ValueError("STT 캐시 형식이 올바르지 않습니다.")
    segments: list[SpeechSegment] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("STT 캐시 항목이 올바르지 않습니다.")
        try:
            start = finite_number(item.get("start"))
            end = finite_number(item.get("end"))
            text = item.get("text")
            logprob = optional_finite_number(item.get("avg_logprob"))
            no_speech = optional_finite_number(item.get("no_speech_prob"))
            if start < 0 or end < start:
                raise ValueError("시각이 올바르지 않습니다.")
            if not isinstance(text, str):
                raise ValueError("텍스트가 문자열이 아닙니다.")
            if no_speech is not None and not 0.0 <= no_speech <= 1.0:
                raise ValueError("무음 확률이 범위를 벗어났습니다.")
        except ValueError as exc:
            raise ValueError(f"STT 캐시 항목이 올바르지 않습니다: {exc}") from exc
        segments.append(SpeechSegment(start, end, text, logprob, no_speech))
    return segments

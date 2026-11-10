"""faster-whisper 래퍼. 구간별 신뢰도(avg_logprob, no_speech_prob)를 보존한다.

모델은 `model_factory`로 주입할 수 있어 테스트는 다운로드 없이 가짜 모델을 쓴다.
원시 결과를 캐시할 수 있도록 JSON 직렬화 도우미를 함께 둔다.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from barrierfree.cancel import CancelToken
from barrierfree.models import SpeechSegment
from barrierfree.validation import finite_number, optional_finite_number


class SttError(Exception):
    """음성 인식 실패(ERR-07)."""


class SttModelError(SttError):
    """모델 준비·로드 실패(ERR-05/06)."""


class SttEngine(Protocol):
    def transcribe(
        self, audio_path: str | Path, *, cancel: CancelToken | None = None
    ) -> list[SpeechSegment]: ...


def _default_factory(model_name: str, **kwargs: Any) -> Any:
    from faster_whisper import WhisperModel

    return WhisperModel(model_name, **kwargs)


class FasterWhisperEngine:
    engine_name = "faster-whisper"

    def __init__(
        self,
        model_name: str,
        *,
        device: str,
        compute_type: str,
        model_dir: str | None = None,
        model_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self._model_dir = model_dir
        self._factory = model_factory or _default_factory
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            try:
                self._model = self._factory(
                    self.model_name,
                    device=self.device,
                    compute_type=self.compute_type,
                    download_root=self._model_dir,
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
        model = self._load()
        results: list[SpeechSegment] = []
        try:
            segments, _info = model.transcribe(
                str(path),
                language="ko",
                vad_filter=True,
                # 직전 문장을 이어받으면 무음·음악 구간의 환각이 반복·증폭된다.
                condition_on_previous_text=False,
            )
            for seg in segments:
                text = seg.text.strip()
                if text:
                    results.append(
                        SpeechSegment(
                            start_s=float(seg.start),
                            end_s=float(seg.end),
                            text=text,
                            avg_logprob=seg.avg_logprob,
                            no_speech_prob=seg.no_speech_prob,
                        )
                    )
                if cancel is not None:
                    cancel.raise_if_cancelled()
        except SttError:
            raise
        except Exception as exc:
            if cancel is not None and cancel.cancelled:
                raise
            raise SttError(f"음성 인식에 실패했습니다: {exc}") from exc
        return results


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

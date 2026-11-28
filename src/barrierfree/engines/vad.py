"""말소리 탐지(VAD). faster-whisper에 들어 있는 Silero 모델을 받아쓰기와 따로 부른다.

받아쓰기 설정과 결과는 바꾸지 않는다.
"""

from __future__ import annotations

from pathlib import Path

_SAMPLE_RATE = 16000


def detect_speech(audio_path: str | Path) -> list[tuple[float, float]]:
    """말소리가 있다고 본 구간(초). 받아쓰기에 쓰는 VAD의 기본값과 같다."""
    from faster_whisper.audio import decode_audio
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    audio = decode_audio(str(audio_path), sampling_rate=_SAMPLE_RATE)
    return [
        (found["start"] / _SAMPLE_RATE, found["end"] / _SAMPLE_RATE)
        for found in get_speech_timestamps(audio, VadOptions())
    ]

"""개발용 산출물 저장: 분석 중 얻은 음성·화면 결과를 눈과 귀로 확인할 수 있도록 파일로 남긴다.

환경변수로 폴더를 지정한 항목만 저장한다. 아무것도 지정하지 않으면 이 모듈은 아무 일도 하지 않으며,
분석 흐름과 결과는 그대로다. 저장 실패는 분석을 멈추지 않고 안내 메시지(notice)만 남긴다.

- `BARRIERFREE_AUDIO_DIR`: 영상의 음성(MP3)과, 같은 이름의 음성 인식 결과(SRT)
- `BARRIERFREE_IMAGE_DIR`: 1초 간격 화면 캡처(JPG), 영상별 하위 폴더
- `BARRIERFREE_OCR_DIR`: 분석에서 읽은 화면 글자를 자막처럼 묶은 SRT

화면 글자 SRT는 분석에 쓰인 OCR 결과를 그대로 쓴다. 캡처 이미지를 다시 인식하지 않는다.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from barrierfree.analysis.subtitle_extract import extract_cues
from barrierfree.cancel import CancelToken
from barrierfree.media.ffmpeg import FfmpegError, save_audio_mp3, save_frames_jpg
from barrierfree.media.subtitles import write_srt
from barrierfree.models import ScreenText, SpeechSegment, SubtitleCue

ENV_AUDIO_DIR = "BARRIERFREE_AUDIO_DIR"
ENV_IMAGE_DIR = "BARRIERFREE_IMAGE_DIR"
ENV_OCR_DIR = "BARRIERFREE_OCR_DIR"

CAPTURE_INTERVAL_S = 1.0


def speech_to_cues(segments: list[SpeechSegment]) -> list[SubtitleCue]:
    """음성 인식 구간을 자막 큐로 바꾼다. 시간순이고 빈 글자는 뺀다."""
    return [
        SubtitleCue(s.start_s, s.end_s, s.text)
        for s in sorted(segments, key=lambda s: (s.start_s, s.end_s))
        if s.text.strip()
    ]


def _dir_from(environ: Mapping[str, str], name: str) -> Path | None:
    value = environ.get(name, "").strip()
    return Path(value) if value else None


@dataclass(frozen=True)
class DevArtifacts:
    audio_dir: Path | None = None
    image_dir: Path | None = None
    ocr_dir: Path | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> DevArtifacts | None:
        """환경변수에서 읽는다. 지정된 폴더가 하나도 없으면 None(기능 꺼짐)."""
        env = os.environ if environ is None else environ
        found = cls(
            audio_dir=_dir_from(env, ENV_AUDIO_DIR),
            image_dir=_dir_from(env, ENV_IMAGE_DIR),
            ocr_dir=_dir_from(env, ENV_OCR_DIR),
        )
        if found.audio_dir is None and found.image_dir is None and found.ocr_dir is None:
            return None
        return found

    @staticmethod
    def _stem(video: Path, session_id: str) -> str:
        return f"{video.stem}_{session_id}"

    def save_audio(
        self, video: Path, session_id: str, cancel: CancelToken, notices: list[str]
    ) -> Path | None:
        """음성을 MP3로 저장하고 그 경로를 돌려준다. 꺼져 있거나 실패하면 None."""
        if self.audio_dir is None:
            return None
        try:
            return save_audio_mp3(
                video, self.audio_dir / f"{self._stem(video, session_id)}.mp3", cancel=cancel
            )
        except (FfmpegError, OSError):
            notices.append("음성 파일(MP3)을 저장하지 못했습니다.")
            return None

    def save_speech_srt(
        self, mp3: Path | None, segments: list[SpeechSegment], notices: list[str]
    ) -> None:
        """MP3 옆에 같은 이름의 SRT로 음성 인식 결과를 남긴다. MP3가 없으면 하지 않는다."""
        cues = speech_to_cues(segments)
        if mp3 is None or not cues:
            return
        try:
            write_srt(cues, mp3.with_suffix(".srt"))
        except OSError:
            notices.append("음성 인식 자막 파일(SRT)을 저장하지 못했습니다.")

    def save_screen(
        self,
        video: Path,
        session_id: str,
        texts: list[ScreenText],
        cancel: CancelToken,
        notices: list[str],
    ) -> None:
        """1초 간격 화면 캡처(JPG)와, 분석이 읽은 화면 글자 SRT를 남긴다."""
        stem = self._stem(video, session_id)
        if self.image_dir is not None:
            try:
                save_frames_jpg(
                    video, self.image_dir / stem, interval_s=CAPTURE_INTERVAL_S, cancel=cancel
                )
            except (FfmpegError, OSError):
                notices.append("화면 캡처 이미지를 저장하지 못했습니다.")
        if self.ocr_dir is not None:
            cues = extract_cues(texts)
            if not cues:
                return
            try:
                write_srt(cues, self.ocr_dir / f"{stem}.srt")
            except OSError:
                notices.append("화면 글자 자막 파일(SRT)을 저장하지 못했습니다.")

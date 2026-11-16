"""dev_artifacts.py 테스트: 환경변수로 켤 때만 산출물을 남기고, 실패해도 분석을 막지 않는다."""

from __future__ import annotations

from pathlib import Path

import pytest

from barrierfree.cancel import CancelToken
from barrierfree.dev_artifacts import (
    ENV_AUDIO_DIR,
    ENV_IMAGE_DIR,
    ENV_OCR_DIR,
    DevArtifacts,
    speech_to_cues,
)
from barrierfree.models import ScreenText, SpeechSegment
from tests.conftest import MakeVideo


def test_from_env_is_off_when_nothing_is_set() -> None:
    assert DevArtifacts.from_env({}) is None


def test_from_env_treats_blank_values_as_off() -> None:
    env = {ENV_AUDIO_DIR: "", ENV_IMAGE_DIR: "   ", ENV_OCR_DIR: ""}
    assert DevArtifacts.from_env(env) is None


def test_from_env_enables_only_the_given_folders(tmp_path: Path) -> None:
    found = DevArtifacts.from_env({ENV_AUDIO_DIR: str(tmp_path / "a")})
    assert found == DevArtifacts(audio_dir=tmp_path / "a", image_dir=None, ocr_dir=None)


def test_from_env_reads_the_process_environment_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in (ENV_AUDIO_DIR, ENV_IMAGE_DIR, ENV_OCR_DIR):
        monkeypatch.delenv(name, raising=False)
    assert DevArtifacts.from_env() is None
    monkeypatch.setenv(ENV_OCR_DIR, str(tmp_path))
    assert DevArtifacts.from_env() == DevArtifacts(ocr_dir=tmp_path)


def test_speech_to_cues_sorts_by_time_and_drops_blank_text() -> None:
    segments = [
        SpeechSegment(4.0, 6.0, "둘째", None, None),
        SpeechSegment(0.0, 2.0, "첫째", None, None),
        SpeechSegment(2.0, 3.0, "   ", None, None),
    ]
    cues = speech_to_cues(segments)
    assert [(c.start_s, c.end_s, c.text) for c in cues] == [
        (0.0, 2.0, "첫째"),
        (4.0, 6.0, "둘째"),
    ]


def test_save_audio_writes_mp3_named_after_video_and_session(
    tmp_path: Path, make_video: MakeVideo
) -> None:
    video = make_video("영상.mp4", seconds=2)
    notices: list[str] = []
    mp3 = DevArtifacts(audio_dir=tmp_path / "out").save_audio(
        video, "s1", CancelToken(), notices
    )
    assert mp3 == tmp_path / "out" / "영상_s1.mp3"
    assert mp3.is_file() and mp3.stat().st_size > 0
    assert notices == []


def test_save_audio_does_nothing_when_the_folder_is_not_set(
    tmp_path: Path, make_video: MakeVideo
) -> None:
    notices: list[str] = []
    result = DevArtifacts(image_dir=tmp_path / "img").save_audio(
        make_video(), "s1", CancelToken(), notices
    )
    assert result is None and notices == []


def test_save_audio_failure_becomes_a_notice_not_an_error(
    tmp_path: Path, make_video: MakeVideo
) -> None:
    silent = make_video("무음.mp4", audio=False)
    notices: list[str] = []
    result = DevArtifacts(audio_dir=tmp_path / "out").save_audio(
        silent, "s1", CancelToken(), notices
    )
    assert result is None
    assert notices == ["음성 파일(MP3)을 저장하지 못했습니다."]


def test_save_speech_srt_goes_next_to_the_mp3(tmp_path: Path) -> None:
    mp3 = tmp_path / "x_s1.mp3"
    notices: list[str] = []
    DevArtifacts(audio_dir=tmp_path).save_speech_srt(
        mp3, [SpeechSegment(0.0, 1.5, "안녕하세요", None, None)], notices
    )
    srt = tmp_path / "x_s1.srt"
    assert "00:00:00,000 --> 00:00:01,500" in srt.read_text(encoding="utf-8")
    assert notices == []


def test_save_speech_srt_skips_without_an_mp3_or_without_speech(tmp_path: Path) -> None:
    dev = DevArtifacts(audio_dir=tmp_path)
    segs = [SpeechSegment(0.0, 1.0, "말", None, None)]
    dev.save_speech_srt(None, segs, [])
    dev.save_speech_srt(tmp_path / "y.mp3", [], [])
    assert list(tmp_path.iterdir()) == []


def test_save_speech_srt_failure_becomes_a_notice(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("폴더가 아니다", encoding="utf-8")
    notices: list[str] = []
    DevArtifacts(audio_dir=blocker).save_speech_srt(
        blocker / "z.mp3", [SpeechSegment(0.0, 1.0, "말", None, None)], notices
    )
    assert notices == ["음성 인식 자막 파일(SRT)을 저장하지 못했습니다."]


def _screen_text(text: str, start: float, end: float) -> ScreenText:
    return ScreenText(text, start, end, 0.9, (0.1, 0.1, 0.5, 0.1))


def test_save_screen_writes_jpg_folder_and_ocr_srt(
    tmp_path: Path, make_video: MakeVideo
) -> None:
    video = make_video("영상.mp4", seconds=3)
    notices: list[str] = []
    DevArtifacts(image_dir=tmp_path / "img", ocr_dir=tmp_path / "ocr").save_screen(
        video, "s1", [_screen_text("상도3동 15명", 0.0, 2.0)], CancelToken(), notices
    )
    frames = sorted((tmp_path / "img" / "영상_s1").glob("frame_*.jpg"))
    assert len(frames) >= 2
    srt = (tmp_path / "ocr" / "영상_s1.srt").read_text(encoding="utf-8")
    assert "상도3동 15명" in srt
    assert notices == []


def test_save_screen_only_does_what_was_asked(tmp_path: Path, make_video: MakeVideo) -> None:
    video = make_video(seconds=2)
    DevArtifacts(ocr_dir=tmp_path / "ocr").save_screen(
        video, "s1", [_screen_text("글자", 0.0, 1.0)], CancelToken(), []
    )
    assert not (tmp_path / "img").exists()
    assert (tmp_path / "ocr" / "sample_s1.srt").is_file()


def test_save_screen_skips_the_ocr_srt_when_nothing_was_read(
    tmp_path: Path, make_video: MakeVideo
) -> None:
    DevArtifacts(ocr_dir=tmp_path / "ocr").save_screen(
        make_video(seconds=2), "s1", [], CancelToken(), []
    )
    assert not (tmp_path / "ocr").exists()


def test_save_screen_failures_become_notices(tmp_path: Path, make_video: MakeVideo) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("폴더가 아니다", encoding="utf-8")
    notices: list[str] = []
    DevArtifacts(image_dir=blocker, ocr_dir=blocker).save_screen(
        make_video(seconds=2), "s1", [_screen_text("글자", 0.0, 1.0)], CancelToken(), notices
    )
    assert notices == [
        "화면 캡처 이미지를 저장하지 못했습니다.",
        "화면 글자 자막 파일(SRT)을 저장하지 못했습니다.",
    ]

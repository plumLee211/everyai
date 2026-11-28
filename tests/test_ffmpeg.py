"""media/ffmpeg.py 테스트 (합성 영상 사용)."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import pytest

from barrierfree import paths
from barrierfree.cancel import CancelledError, CancelToken
from barrierfree.config import AnalysisConfig
from barrierfree.media import ffmpeg as ff
from tests.conftest import MakeVideo


def test_find_binary_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ff.shutil, "which", lambda _name: None)
    monkeypatch.delenv("BARRIERFREE_FFMPEG_DIR", raising=False)
    with pytest.raises(ff.FfmpegNotFoundError):
        ff.find_binary("ffmpeg")


def test_find_binary_prefers_the_ffmpeg_folder_shipped_next_to_a_frozen_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # PyInstaller onedir 배포본: 실행 파일 옆의 ffmpeg 폴더를 PATH보다 먼저 쓴다.
    shipped = tmp_path / "ffmpeg"
    shipped.mkdir()
    (shipped / "ffmpeg.exe").write_bytes(b"")
    monkeypatch.delenv("BARRIERFREE_FFMPEG_DIR", raising=False)
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "executable", str(tmp_path / "barrierfree.exe"))
    monkeypatch.setattr(ff.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    assert ff.find_binary("ffmpeg") == str(shipped / "ffmpeg.exe")


def test_find_binary_finds_ffmpeg_in_the_pyinstaller_internal_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # PyInstaller 6 onedir는 datas를 실행 파일 옆 `_internal`(sys._MEIPASS)에 둔다.
    internal = tmp_path / "_internal"
    (internal / "ffmpeg").mkdir(parents=True)
    (internal / "ffmpeg" / "ffmpeg.exe").write_bytes(b"")
    monkeypatch.delenv("BARRIERFREE_FFMPEG_DIR", raising=False)
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "_MEIPASS", str(internal), raising=False)
    monkeypatch.setattr(paths.sys, "executable", str(tmp_path / "barrierfree.exe"))
    monkeypatch.setattr(ff.shutil, "which", lambda _name: None)
    assert ff.find_binary("ffmpeg") == str(internal / "ffmpeg" / "ffmpeg.exe")


def test_find_binary_ignores_the_shipped_folder_when_not_frozen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "ffmpeg").mkdir()
    (tmp_path / "ffmpeg" / "ffmpeg").write_bytes(b"")
    monkeypatch.delenv("BARRIERFREE_FFMPEG_DIR", raising=False)
    monkeypatch.delattr(paths.sys, "frozen", raising=False)
    monkeypatch.setattr(paths.sys, "executable", str(tmp_path / "python"))
    monkeypatch.setattr(ff.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    assert ff.find_binary("ffmpeg") == "/usr/bin/ffmpeg"


def test_every_ffmpeg_process_is_started_without_a_console_window(
    monkeypatch: pytest.MonkeyPatch, make_video: MakeVideo, tmp_path: Path
) -> None:
    video = make_video()
    flags: list[object] = []
    real_popen = subprocess.Popen

    class RecordingPopen(real_popen):  # type: ignore[type-arg]
        def __init__(self, *args: object, **kwargs: object) -> None:
            flags.append(kwargs.get("creationflags"))
            super().__init__(*args, **kwargs)  # type: ignore[call-overload]

    # subprocess.run도 내부에서 Popen을 쓰므로 세 실행 경로가 모두 여기를 지난다.
    monkeypatch.setattr(ff.subprocess, "Popen", RecordingPopen)

    info = ff.probe(video)  # run
    ff.extract_audio(video, tmp_path / "a.wav", cancel=CancelToken())  # 취소 가능한 Popen
    list(ff.iter_frames(video, fps=1.0, max_width=320, info=info))  # 프레임 스트림

    assert len(flags) >= 3
    assert all(value == getattr(subprocess, "CREATE_NO_WINDOW", 0) for value in flags)


def test_probe_returns_stream_info(make_video: MakeVideo) -> None:
    info = ff.probe(make_video())
    assert info.duration_s == pytest.approx(2.0, abs=0.3)
    assert (info.width, info.height) == (640, 360)
    assert info.fps == pytest.approx(10.0)
    assert info.has_audio is True


def test_probe_detects_missing_audio(make_video: MakeVideo) -> None:
    assert ff.probe(make_video("mute.mp4", audio=False)).has_audio is False


def test_probe_rejects_unsupported_extension(tmp_path: Path) -> None:
    f = tmp_path / "notes.txt"
    f.write_text("x")
    with pytest.raises(ff.UnsupportedVideoError):
        ff.probe(f)


def test_probe_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ff.VideoFileNotFoundError):
        ff.probe(tmp_path / "nope.mp4")


def test_probe_rejects_corrupt_file(tmp_path: Path) -> None:
    f = tmp_path / "broken.mp4"
    f.write_bytes(b"not a video at all" * 50)
    with pytest.raises(ff.VideoReadError):
        ff.probe(f)


def test_validate_video_rejects_over_limit(make_video: MakeVideo) -> None:
    cfg = AnalysisConfig(max_video_seconds=1.0)
    with pytest.raises(ff.VideoTooLongError) as exc:
        ff.validate_video(make_video(seconds=2.0), cfg)
    assert exc.value.limit_s == 1.0
    assert exc.value.duration_s > 1.0


def test_validate_video_accepts_within_limit(make_video: MakeVideo) -> None:
    assert ff.validate_video(make_video(), AnalysisConfig()).duration_s > 0


def test_korean_path_is_supported(make_video: MakeVideo, tmp_path: Path) -> None:
    video = make_video("안내 영상.mp4", folder="한글 폴더")
    info = ff.probe(video)
    assert info.has_video
    out = ff.extract_audio(video, tmp_path / "출력 폴더" / "음성.wav")
    assert out.exists()
    frames = list(ff.iter_frames(video, fps=1.0, max_width=320))
    assert len(frames) >= 1


def test_extract_audio_is_16khz_mono_wav(make_video: MakeVideo, tmp_path: Path) -> None:
    out = ff.extract_audio(make_video(), tmp_path / "a.wav")
    with wave.open(str(out)) as w:
        assert w.getframerate() == 16000
        assert w.getnchannels() == 1


def test_extract_audio_without_audio_stream_raises(make_video: MakeVideo, tmp_path: Path) -> None:
    video = make_video("mute.mp4", audio=False)
    with pytest.raises(ff.NoAudioStreamError):
        ff.extract_audio(video, tmp_path / "a.wav")


def test_iter_frames_count_timestamps_and_shape(make_video: MakeVideo) -> None:
    frames = list(ff.iter_frames(make_video(seconds=2.0), fps=2.0, max_width=320))
    assert 3 <= len(frames) <= 5
    times = [t for t, _ in frames]
    assert times == sorted(times)
    assert times[1] - times[0] == pytest.approx(0.5)
    assert frames[0][1].shape == (180, 320, 3)


def test_iter_frames_does_not_upscale(make_video: MakeVideo) -> None:
    _, frame = next(iter(ff.iter_frames(make_video(), fps=1.0, max_width=1280)))
    assert frame.shape[1] == 640


def test_iter_frames_bottom_crop(make_video: MakeVideo) -> None:
    _, frame = next(iter(ff.iter_frames(make_video(), fps=1.0, max_width=640, bottom_ratio=0.25)))
    assert frame.shape == (90, 640, 3)


_FAKE_FFMPEG = """#!{python}
import os, sys
mode = os.environ["FAKE_MODE"]
out = sys.stdout.buffer
frame = bytes(12)  # 2x2 BGR
if mode == "partial_then_fail":
    out.write(frame); out.flush(); sys.exit(1)
if mode == "truncated_frame":
    out.write(frame + bytes(7)); out.flush(); sys.exit(0)
if mode == "stderr_flood":
    sys.stderr.write("x" * (1 << 20)); sys.stderr.flush()
    out.write(frame); out.flush(); sys.exit(0)
"""

_TINY = ff.VideoInfo(
    path=Path("tiny.mp4"),
    duration_s=1.0,
    width=2,
    height=2,
    fps=1.0,
    has_video=True,
    has_audio=False,
)


@pytest.fixture
def fake_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import stat

    script = tmp_path / "fake_ffmpeg.py"
    script.write_text(_FAKE_FFMPEG.format(python=sys.executable), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(ff, "find_binary", lambda _name: str(script))


@pytest.mark.skipif(sys.platform == "win32", reason="shebang 기반 가짜 실행 파일")
@pytest.mark.parametrize("mode", ["partial_then_fail", "truncated_frame"])
def test_iter_frames_reports_failure_after_partial_output(
    fake_ffmpeg: None, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setenv("FAKE_MODE", mode)
    with pytest.raises(ff.VideoReadError):
        list(ff.iter_frames("tiny.mp4", fps=1.0, max_width=2, info=_TINY))


@pytest.mark.skipif(sys.platform == "win32", reason="shebang 기반 가짜 실행 파일")
def test_iter_frames_survives_stderr_flood(
    fake_ffmpeg: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_MODE", "stderr_flood")
    result: list[int] = []

    def run() -> None:
        result.append(len(list(ff.iter_frames("tiny.mp4", fps=1.0, max_width=2, info=_TINY))))

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=15)
    assert not t.is_alive(), "stderr 버퍼 포화로 교착"
    assert result == [1]


def test_rotated_video_uses_display_size(
    make_video: MakeVideo, ffmpeg_exe: str, tmp_path: Path
) -> None:
    source = make_video("src.mp4", size="640x360")
    rotated = tmp_path / "rotated.mp4"
    subprocess.run(
        [ffmpeg_exe, "-v", "error", "-y", "-display_rotation:v:0", "90", "-i", str(source),
         "-c", "copy", str(rotated)],
        check=True,
        capture_output=True,
    )
    info = ff.probe(rotated)
    assert (info.width, info.height) == (360, 640)  # 회전을 반영한 표시 크기
    _, frame = next(iter(ff.iter_frames(rotated, fps=1.0, max_width=320)))
    assert frame.shape[1] == 320
    assert frame.shape[0] > frame.shape[1]  # 세로 영상 비율 유지


_FAKE_PROBE = """#!{python}
import sys
print('{{"streams":[{{"codec_type":"video","width":2,"height":2,"r_frame_rate":"1/1"}}],'
      '"format":{{"duration":"%s"}}}}' % __import__("os").environ["FAKE_DURATION"])
"""


@pytest.mark.skipif(sys.platform == "win32", reason="shebang 기반 가짜 실행 파일")
@pytest.mark.parametrize("duration", ["nan", "inf", "-1"])
def test_probe_rejects_non_finite_or_negative_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, duration: str
) -> None:
    import stat

    script = tmp_path / "fake_ffprobe.py"
    script.write_text(_FAKE_PROBE.format(python=sys.executable), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(ff, "find_binary", lambda _name: str(script))
    monkeypatch.setenv("FAKE_DURATION", duration)
    with pytest.raises(ff.VideoReadError):
        ff.validate_video(video)


_HANG_FFMPEG = """#!{python}
import time
time.sleep(60)
"""


@pytest.fixture
def hanging_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ffprobe는 진짜, ffmpeg만 출력 없이 멈추는 가짜로 바꾼다."""
    import stat

    script = tmp_path / "hang_ffmpeg.py"
    script.write_text(_HANG_FFMPEG.format(python=sys.executable), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    real = ff.find_binary
    monkeypatch.setattr(
        ff, "find_binary", lambda name: str(script) if name == "ffmpeg" else real(name)
    )


def _cancel_soon(token: CancelToken, delay: float = 0.3) -> threading.Thread:
    t = threading.Timer(delay, token.cancel)
    t.start()
    return t


@pytest.mark.skipif(sys.platform == "win32", reason="shebang 기반 가짜 실행 파일")
def test_cancel_unblocks_a_hung_frame_stream(make_video: MakeVideo, hanging_ffmpeg: None) -> None:
    video = make_video()
    token = CancelToken()
    timer = _cancel_soon(token)
    start = time.perf_counter()
    with pytest.raises(CancelledError):
        list(ff.iter_frames(video, fps=1.0, max_width=320, cancel=token))
    timer.join()
    assert time.perf_counter() - start < 10  # 60초 대기가 아니라 곧바로 풀려야 한다


@pytest.mark.skipif(sys.platform == "win32", reason="shebang 기반 가짜 실행 파일")
def test_cancel_stops_a_hung_audio_extraction(
    make_video: MakeVideo, hanging_ffmpeg: None, tmp_path: Path
) -> None:
    video = make_video()
    token = CancelToken()
    timer = _cancel_soon(token)
    start = time.perf_counter()
    with pytest.raises(CancelledError):
        ff.extract_audio(video, tmp_path / "a.wav", cancel=token)
    timer.join()
    assert time.perf_counter() - start < 10


def test_cancel_token_is_ignored_when_not_cancelled(make_video: MakeVideo, tmp_path: Path) -> None:
    video = make_video()
    token = CancelToken()
    assert len(list(ff.iter_frames(video, fps=1.0, max_width=320, cancel=token))) >= 1
    assert ff.extract_audio(video, tmp_path / "a.wav", cancel=token).exists()


def test_iter_frames_can_be_closed_early(make_video: MakeVideo) -> None:
    gen = ff.iter_frames(make_video(seconds=2.0), fps=10.0, max_width=320)
    next(gen)
    gen.close()  # 취소 시 ffmpeg 프로세스가 정리되어야 한다(예외 없이 종료)


# ---------- 개발용 산출물: MP3·JPG 저장 ----------


def test_save_audio_mp3_writes_a_playable_file(make_video: MakeVideo, tmp_path: Path) -> None:
    out = ff.save_audio_mp3(make_video(seconds=2.0), tmp_path / "새 폴더" / "음성.mp3")
    assert out.is_file() and out.stat().st_size > 0
    assert out.read_bytes()[:3] in (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")  # MP3 머리


def test_save_audio_mp3_rejects_a_video_without_audio(
    make_video: MakeVideo, tmp_path: Path
) -> None:
    with pytest.raises(ff.NoAudioStreamError):
        ff.save_audio_mp3(make_video(audio=False), tmp_path / "a.mp3")
    assert not (tmp_path / "a.mp3").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="shebang 기반 가짜 실행 파일")
def test_save_audio_mp3_cancel_leaves_no_half_written_file(
    make_video: MakeVideo, hanging_ffmpeg: None, tmp_path: Path
) -> None:
    video = make_video()
    token = CancelToken()
    timer = _cancel_soon(token)
    with pytest.raises(CancelledError):
        ff.save_audio_mp3(video, tmp_path / "a.mp3", cancel=token)
    timer.join()
    assert not (tmp_path / "a.mp3").exists()


def test_save_frames_jpg_captures_one_image_per_second(
    make_video: MakeVideo, tmp_path: Path
) -> None:
    frames = ff.save_frames_jpg(make_video(seconds=3.0), tmp_path / "캡처")
    assert 2 <= len(frames) <= 4
    assert all(f.suffix == ".jpg" and f.stat().st_size > 0 for f in frames)
    assert frames == sorted(frames)


def test_save_frames_jpg_rejects_a_non_positive_interval(
    make_video: MakeVideo, tmp_path: Path
) -> None:
    with pytest.raises(ValueError):
        ff.save_frames_jpg(make_video(), tmp_path / "x", interval_s=0)
    assert not (tmp_path / "x").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="shebang 기반 가짜 실행 파일")
def test_save_frames_jpg_cancel_removes_the_folder(
    make_video: MakeVideo, hanging_ffmpeg: None, tmp_path: Path
) -> None:
    video = make_video()
    token = CancelToken()
    timer = _cancel_soon(token)
    with pytest.raises(CancelledError):
        ff.save_frames_jpg(video, tmp_path / "캡처", cancel=token)
    timer.join()
    assert not (tmp_path / "캡처").exists()

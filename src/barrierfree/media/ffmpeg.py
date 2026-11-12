"""ffmpeg/ffprobe 래퍼: 영상 검증, 오디오 추출, 프레임 스트리밍.

프레임은 임시 이미지 파일 없이 stdout 파이프로 읽는다. 한글 경로에서
`cv2.imread` 계열이 실패하는 문제(planv2 리스크)를 피하기 위해서다.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import numpy as np
from numpy.typing import NDArray

from barrierfree.cancel import CancelledError, CancelToken
from barrierfree.config import AnalysisConfig

Frame = NDArray[np.uint8]

_PROBE_TIMEOUT_S = 30
_AUDIO_TIMEOUT_S = 60 * 30


class FfmpegError(Exception):
    """ffmpeg 처리 실패의 최상위 예외."""


class FfmpegNotFoundError(FfmpegError):
    pass


class VideoReadError(FfmpegError):
    pass


class VideoFileNotFoundError(VideoReadError):
    pass


class UnsupportedVideoError(VideoReadError):
    pass


class NoAudioStreamError(FfmpegError):
    pass


class VideoTooLongError(FfmpegError):
    def __init__(self, duration_s: float, limit_s: float) -> None:
        super().__init__(f"video is {duration_s:.1f}s, limit is {limit_s:.1f}s")
        self.duration_s = duration_s
        self.limit_s = limit_s


@dataclass(frozen=True)
class VideoInfo:
    path: Path
    duration_s: float
    width: int  # 표시 크기(회전 메타데이터 반영)
    height: int
    fps: float
    has_video: bool
    has_audio: bool


def _bundled_dirs() -> list[Path]:
    """동봉된 ffmpeg 폴더 후보: 환경변수 → (PyInstaller 배포본이면) 실행 파일 옆 → 번들 데이터 폴더.

    PyInstaller 6 onedir은 datas를 `_internal`(sys._MEIPASS) 아래에 두므로 그곳도 찾는다.
    """
    dirs: list[Path] = []
    override = os.environ.get("BARRIERFREE_FFMPEG_DIR")
    if override:
        dirs.append(Path(override))
    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).parent / "ffmpeg")
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle:
            dirs.append(Path(bundle) / "ffmpeg")
    return dirs


def find_binary(name: str) -> str:
    """ffmpeg/ffprobe 실행 파일 경로. 동봉 폴더 → PATH 순."""
    for folder in _bundled_dirs():
        for candidate in (folder / name, folder / f"{name}.exe"):
            if candidate.is_file():
                return str(candidate)
    found = shutil.which(name)
    if not found:
        raise FfmpegNotFoundError(f"{name} 실행 파일을 찾을 수 없습니다.")
    return found


_POLL_S = 0.2


def _run(
    cmd: list[str], timeout: float, cancel: CancelToken | None = None
) -> subprocess.CompletedProcess[bytes]:
    """명령을 실행한다. `cancel`이 있으면 취소되는 즉시 프로세스를 종료하고 CancelledError."""
    if cancel is None:
        try:
            return subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise FfmpegError(f"{Path(cmd[0]).name} 실행 시간이 초과되었습니다.") from exc

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.monotonic() + timeout
    while True:
        try:
            out, err = proc.communicate(timeout=_POLL_S)
            return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
        except subprocess.TimeoutExpired:
            if cancel.cancelled:
                proc.kill()
                proc.communicate()
                raise CancelledError("분석이 취소되었습니다.") from None
            if time.monotonic() > deadline:
                proc.kill()
                proc.communicate()
                raise FfmpegError(f"{Path(cmd[0]).name} 실행 시간이 초과되었습니다.") from None


def _stderr_tail(data: bytes, limit: int = 400) -> str:
    return data.decode("utf-8", errors="replace").strip()[-limit:]


def _parse_rate(value: str | None) -> float:
    if not value or "/" not in value:
        return 0.0
    num, _, den = value.partition("/")
    try:
        return float(num) / float(den) if float(den) else 0.0
    except ValueError:
        return 0.0


def _rotation_degrees(stream: dict[str, object]) -> int:
    """스트림의 표시 회전(도). display matrix 또는 구형 `rotate` 태그를 읽는다."""
    side_data = stream.get("side_data_list")
    if isinstance(side_data, list):
        for item in side_data:
            if isinstance(item, dict) and "rotation" in item:
                try:
                    return abs(round(float(item["rotation"])))
                except (TypeError, ValueError):
                    return 0
    tags = stream.get("tags")
    if isinstance(tags, dict) and "rotate" in tags:
        try:
            return abs(round(float(tags["rotate"])))
        except (TypeError, ValueError):
            return 0
    return 0


def probe(path: str | Path, config: AnalysisConfig | None = None) -> VideoInfo:
    """파일을 검사해 VideoInfo를 돌려준다. 길이 제한은 `validate_video`에서 본다."""
    cfg = config or AnalysisConfig()
    p = Path(path)
    if not p.is_file():
        raise VideoFileNotFoundError(f"파일이 없습니다: {p.name}")
    if p.suffix.lower() not in cfg.video_extensions:
        raise UnsupportedVideoError(f"지원하지 않는 파일 형식입니다: {p.suffix or '(없음)'}")

    proc = _run(
        [
            find_binary("ffprobe"),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-i",
            str(p),
        ],
        _PROBE_TIMEOUT_S,
    )
    if proc.returncode != 0:
        raise VideoReadError(f"영상 파일을 읽을 수 없습니다: {_stderr_tail(proc.stderr)}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise VideoReadError("영상 정보를 해석할 수 없습니다.") from exc

    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    if video is None:
        raise VideoReadError("영상 스트림이 없는 파일입니다.")
    try:
        duration = float(data["format"]["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise VideoReadError("영상 길이를 확인할 수 없습니다.") from exc
    if not math.isfinite(duration) or duration < 0:
        raise VideoReadError("영상 길이를 확인할 수 없습니다.")

    width, height = int(video.get("width", 0)), int(video.get("height", 0))
    if _rotation_degrees(video) % 180 == 90:
        width, height = height, width  # ffmpeg는 회전을 적용한 뒤 필터를 실행한다

    return VideoInfo(
        path=p,
        duration_s=duration,
        width=width,
        height=height,
        fps=_parse_rate(video.get("r_frame_rate")),
        has_video=True,
        has_audio=has_audio,
    )


def validate_video(path: str | Path, config: AnalysisConfig | None = None) -> VideoInfo:
    cfg = config or AnalysisConfig()
    info = probe(path, cfg)
    if info.duration_s > cfg.max_video_seconds:
        raise VideoTooLongError(info.duration_s, cfg.max_video_seconds)
    return info


def extract_audio(
    path: str | Path, out_path: str | Path, *, cancel: CancelToken | None = None
) -> Path:
    """음성을 16kHz mono WAV로 추출한다. 오디오 스트림이 없으면 NoAudioStreamError."""
    info = probe(path)
    if not info.has_audio:
        raise NoAudioStreamError("영상에 오디오 트랙이 없습니다.")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = _run(
        [
            find_binary("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-i",
            str(info.path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "wav",
            str(out),
        ],
        _AUDIO_TIMEOUT_S,
        cancel,
    )
    if proc.returncode != 0 or not out.is_file():
        raise FfmpegError(f"오디오 추출에 실패했습니다: {_stderr_tail(proc.stderr)}")
    return out


def save_audio_mp3(
    path: str | Path, out_path: str | Path, *, cancel: CancelToken | None = None
) -> Path:
    """영상의 음성만 원본 음질에 가깝게(VBR 약 190kbps) MP3로 저장한다.

    인식용 WAV(16kHz mono)를 다시 인코딩하지 않고 원본 영상에서 뽑는다.
    """
    info = probe(path)
    if not info.has_audio:
        raise NoAudioStreamError("영상에 오디오 트랙이 없습니다.")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = _run(
            [
                find_binary("ffmpeg"),
                "-v",
                "error",
                "-y",
                "-i",
                str(info.path),
                "-vn",
                "-c:a",
                "libmp3lame",
                "-q:a",
                "2",
                str(out),
            ],
            _AUDIO_TIMEOUT_S,
            cancel,
        )
        if proc.returncode != 0 or not out.is_file():
            raise FfmpegError(f"MP3 저장에 실패했습니다: {_stderr_tail(proc.stderr)}")
    except BaseException:
        out.unlink(missing_ok=True)  # 실패·취소로 남은 반쪽 파일은 지운다
        raise
    return out


def save_frames_jpg(
    path: str | Path,
    out_dir: str | Path,
    *,
    interval_s: float = 1.0,
    cancel: CancelToken | None = None,
) -> list[Path]:
    """영상을 `interval_s`초마다 원본 크기 JPG로 캡처한다.

    파일 이름은 캡처 시각(초)이다(frame_00012.jpg = 12초). fps 필터의 타임스탬프를 그대로
    쓰므로 1초 간격일 때 번호가 곧 초가 된다. 실패·취소하면 만든 폴더를 지운다.
    """
    info = probe(path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    try:
        proc = _run(
            [
                find_binary("ffmpeg"),
                "-v",
                "error",
                "-y",
                "-i",
                str(info.path),
                "-an",
                "-vf",
                f"fps=1/{interval_s:g}",
                "-q:v",
                "2",
                "-frame_pts",
                "1",
                str(out / "frame_%05d.jpg"),
            ],
            _AUDIO_TIMEOUT_S,
            cancel,
        )
        frames = sorted(out.glob("frame_*.jpg"))
        if proc.returncode != 0 or not frames:
            raise FfmpegError(f"프레임 캡처에 실패했습니다: {_stderr_tail(proc.stderr)}")
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise
    return frames


def _even(value: int) -> int:
    return max(2, value - value % 2)


def scaled_size(width: int, height: int, max_width: int) -> tuple[int, int]:
    """가로 max_width 이하로 줄인 (w, h). 확대하지 않고 짝수로 맞춘다."""
    if width <= max_width:
        return _even(width), _even(height)
    return _even(max_width), _even(round(height * max_width / width))


def _read_exact(stream: IO[bytes], size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _kill_on_cancel(
    proc: subprocess.Popen[bytes], cancel: CancelToken, done: threading.Event
) -> None:
    """취소 또는 정상 종료(done)를 기다리다가, 취소되면 프로세스를 종료한다."""
    while not done.is_set():
        if cancel.wait(_POLL_S):
            if proc.poll() is None:
                proc.kill()
            return


def iter_frames(
    path: str | Path,
    *,
    fps: float,
    max_width: int,
    bottom_ratio: float | None = None,
    info: VideoInfo | None = None,
    cancel: CancelToken | None = None,
) -> Iterator[tuple[float, Frame]]:
    """(시각 초, BGR 프레임)을 순서대로 낸다.

    `bottom_ratio`를 주면 축소한 프레임의 하단 영역만 잘라서 낸다(자막 영역 OCR용).
    제너레이터를 닫으면 ffmpeg 프로세스를 정리한다. `cancel`이 있으면 다른 스레드에서
    취소하는 즉시 프로세스를 종료해, 출력 없이 멈춘 ffmpeg에서도 읽기가 풀리고
    CancelledError가 난다.
    """
    meta = info or probe(path)
    width, height = scaled_size(meta.width, meta.height, max_width)
    filters = [f"fps={fps}", f"scale={width}:{height}"]
    out_height = height
    if bottom_ratio is not None:
        out_height = _even(int(height * bottom_ratio))
        filters.append(f"crop={width}:{out_height}:0:{height - out_height}")

    cmd = [
        find_binary("ffmpeg"),
        "-v",
        "error",
        "-i",
        str(meta.path),
        "-an",
        "-vf",
        ",".join(filters),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-",
    ]
    frame_bytes = width * out_height * 3
    emitted = 0
    # stderr는 파이프 대신 임시 파일로 받는다. 파이프는 읽는 쪽이 없으면 버퍼가 차서
    # ffmpeg가 멈추고(stdout 읽기와 교착), 취소도 막힌다.
    with tempfile.TemporaryFile() as err_file:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err_file)
        done = threading.Event()
        if cancel is not None:
            threading.Thread(
                target=_kill_on_cancel, args=(proc, cancel, done), daemon=True
            ).start()
        try:
            assert proc.stdout is not None
            truncated = False
            while True:
                buf = _read_exact(proc.stdout, frame_bytes)
                if len(buf) == frame_bytes:
                    frame = np.frombuffer(buf, dtype=np.uint8).reshape(out_height, width, 3)
                    yield emitted / fps, frame
                    emitted += 1
                    continue
                truncated = len(buf) > 0  # 잘린 프레임은 정상 종료가 아니다
                break
            if cancel is not None:
                cancel.raise_if_cancelled()  # 감시 스레드가 프로세스를 죽였으면 취소로 끝난다
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired as exc:
                raise FfmpegError("ffmpeg가 종료되지 않습니다.") from exc
            if truncated or proc.returncode != 0:
                err_file.seek(0)
                raise VideoReadError(
                    f"프레임 읽기가 중간에 실패했습니다({emitted}장 이후): "
                    f"{_stderr_tail(err_file.read())}"
                )
        finally:
            done.set()
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            if proc.stdout:
                proc.stdout.close()

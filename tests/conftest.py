"""공용 픽스처. 합성 영상은 ffmpeg lavfi로 만든다(저장소에 바이너리를 두지 않는다)."""

from __future__ import annotations

import os

# GUI 테스트가 실제 창을 열지 않도록, Qt를 불러오기 전에 헤드리스 플랫폼을 기본으로 정한다.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

MakeVideo = Callable[..., Path]


@pytest.fixture(scope="session")
def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    assert exe, "ffmpeg가 설치되어 있어야 한다 (brew install ffmpeg)"
    return exe


@pytest.fixture
def make_video(tmp_path: Path, ffmpeg_exe: str) -> MakeVideo:
    def _make(
        name: str = "sample.mp4",
        *,
        seconds: float = 2.0,
        size: str = "640x360",
        fps: int = 10,
        audio: bool = True,
        folder: str | None = None,
    ) -> Path:
        directory = tmp_path / folder if folder else tmp_path
        directory.mkdir(parents=True, exist_ok=True)
        out = directory / name
        cmd = [
            ffmpeg_exe,
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={size}:rate={fps}:duration={seconds}",
        ]
        if audio:
            cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
        if audio:
            cmd += ["-c:a", "aac", "-shortest"]
        cmd.append(str(out))
        subprocess.run(cmd, check=True, capture_output=True)
        return out

    return _make

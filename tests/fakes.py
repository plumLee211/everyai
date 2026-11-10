"""GUI 테스트용 가짜 대화상자. 실제 모달 창은 테스트를 멈추게 하므로 주입해서 쓴다."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget

from barrierfree.config import AnalysisConfig
from barrierfree.media.ffmpeg import VideoInfo
from barrierfree.models import Issue, SubtitleSource
from barrierfree.review_state import ReviewState
from barrierfree.storage.repository import Repository


class FakePlayer(QWidget):
    """영상 플레이어 자리 표시자. 어디로 이동했고 재생 중인지만 기록한다."""

    playback_failed = pyqtSignal(str)
    activity = pyqtSignal()  # 재생/탐색 같은 플레이어 조작

    def __init__(self) -> None:
        super().__init__()
        self.loaded: Path | None = None
        self.seeks: list[float] = []
        self.playing = False

    def is_playing(self) -> bool:
        return self.playing

    def load(self, path: Path) -> None:
        self.loaded = path

    def seek(self, seconds: float) -> None:
        self.seeks.append(seconds)
        self.playing = False  # 문제를 선택해도 자동으로 재생하지 않는다

    def pause(self) -> None:
        self.playing = False

    def current_seconds(self) -> float:
        return self.seeks[-1] if self.seeks else 0.0


def make_state(
    repo: Repository,
    issues: list[Issue],
    *,
    source: SubtitleSource = SubtitleSource.FILE,
    video_name: str = "안내 영상.mp4",
    extra_notices: list[str] | None = None,
) -> ReviewState:
    session_id = repo.create_session(
        video=VideoInfo(Path("/videos") / video_name, 600.0, 640, 360, 10.0, True, True),
        video_sha256="a" * 64,
        subtitle_source=source,
        subtitle_path=None,
        config=AnalysisConfig(),
        stt_model_name="small",
        ocr_engine_name="easyocr",
        compute_device="cpu",
    )
    repo.complete_session(session_id, issues)
    return ReviewState(repo, session_id, extra_notices=extra_notices)


class FakeDialogs:
    def __init__(self) -> None:
        self.video: Path | None = None
        self.subtitle: Path | None = None
        self.csv: Path | None = None
        self.confirm_answer = True
        self.infos: list[tuple[str, str]] = []
        self.warnings: list[tuple[str, str]] = []
        self.confirms: list[tuple[str, str]] = []
        self.csv_default_names: list[str] = []

    def ask_open_video(self) -> Path | None:
        return self.video

    def ask_open_subtitle(self) -> Path | None:
        return self.subtitle

    def ask_save_csv(self, default_name: str) -> Path | None:
        self.csv_default_names.append(default_name)
        return self.csv

    def info(self, title: str, text: str) -> None:
        self.infos.append((title, text))

    def warn(self, title: str, text: str) -> None:
        self.warnings.append((title, text))

    def confirm(self, title: str, text: str) -> bool:
        self.confirms.append((title, text))
        return self.confirm_answer

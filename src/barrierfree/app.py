"""앱 조립: 저장소·캐시·장치 감지·엔진·화면을 연결하고 실행한다.

화면과 분석 코드는 서로를 직접 만들지 않는다. 필요한 것은 여기서 `AppServices`로 한꺼번에 채워
`MainWindow`에 넘긴다. 테스트는 같은 함수를 가짜 장치·대화상자로 호출한다.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QStandardPaths
from PyQt6.QtWidgets import QApplication

from barrierfree.analysis.subtitle_region import RegionEstimate
from barrierfree.config import AnalysisConfig
from barrierfree.engines.device import (
    DeviceInfo,
    ModelRecommendation,
    detect_device,
    recommend_model,
)
from barrierfree.engines.ocr import EasyOcrEngine
from barrierfree.engines.stt import FasterWhisperEngine
from barrierfree.media.ffmpeg import FfmpegNotFoundError, find_binary
from barrierfree.paths import bundled_model_dir
from barrierfree.pipeline import AnalysisPipeline, RegionDecision, StageEvent
from barrierfree.settings import AppSettings, apply_settings, effective_model
from barrierfree.storage.cache import FileCache
from barrierfree.storage.repository import Repository
from barrierfree.ui.dialogs import Dialogs, QtDialogs
from barrierfree.ui.main_window import AppServices, MainWindow
from barrierfree.ui.player import VideoPlayerPanel
from barrierfree.ui.region_dialog import RegionDialog
from barrierfree.ui.settings_dialog import SettingsDialog

APP_NAME = "barrierfree"
DB_NAME = "barrierfree.db"


def default_data_dir() -> Path:
    """사용자별 데이터 위치. 환경변수 BARRIERFREE_DATA_DIR로 바꿀 수 있다."""
    override = os.environ.get("BARRIERFREE_DATA_DIR")
    if override:
        return Path(override)
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    return Path(base) / APP_NAME if base else Path.home() / f".{APP_NAME}"


def ensure_ffmpeg(dialogs: Dialogs) -> bool:
    """ffmpeg/ffprobe를 찾을 수 있는지 시작 전에 확인한다. 없으면 원인을 안내한다."""
    try:
        find_binary("ffmpeg")
        find_binary("ffprobe")
    except FfmpegNotFoundError as exc:
        dialogs.warn(
            "실행 환경 확인",
            f"영상 처리 도구(ffmpeg)를 찾을 수 없습니다. ffmpeg를 설치하거나 설치 위치를 PATH에 "
            f"추가한 뒤 다시 실행해 주세요. (ERR-04: {exc})",
        )
        return False
    return True


class _EngineCache:
    """모델 로딩은 비싸므로 같은 설정이면 분석마다 엔진을 새로 만들지 않고 재사용한다."""

    def __init__(self, recommendation: ModelRecommendation) -> None:
        self._rec = recommendation
        self._bundled_dir = bundled_model_dir()
        self._stt: dict[tuple[str, str | None], FasterWhisperEngine] = {}
        self._ocr: dict[str | None, EasyOcrEngine] = {}

    def stt(self, settings: AppSettings) -> FasterWhisperEngine:
        model = effective_model(settings, self._rec.model)
        model_dir = self._model_dir(settings)
        key = (model, model_dir)
        if key not in self._stt:
            self._stt[key] = FasterWhisperEngine(
                model,
                device=self._rec.device,
                compute_type=self._rec.compute_type,
                model_dir=model_dir,
            )
        return self._stt[key]

    def ocr(self, settings: AppSettings) -> EasyOcrEngine:
        model_dir = self._model_dir(settings)
        if model_dir not in self._ocr:
            self._ocr[model_dir] = EasyOcrEngine(
                gpu=self._rec.device == "cuda", model_dir=model_dir
            )
        return self._ocr[model_dir]

    def _model_dir(self, settings: AppSettings) -> str | None:
        """사용자가 지정한 경로가 우선이고, 없으면 배포본에 동봉된 모델 폴더를 쓴다."""
        return settings.model_dir or self._bundled_dir


def build_services(
    data_dir: Path,
    dialogs: Dialogs,
    *,
    device: DeviceInfo | None = None,
    parent_window: Callable[[], MainWindow | None] | None = None,
) -> AppServices:
    data_dir.mkdir(parents=True, exist_ok=True)
    base_config = AnalysisConfig()
    detected = device or detect_device()
    recommendation = recommend_model(detected, base_config)

    repo = Repository(data_dir / DB_NAME)
    cache = FileCache(data_dir / "cache", repo)
    engines = _EngineCache(recommendation)

    def pipeline_factory(
        settings: AppSettings,
        confirm: Callable[[RegionEstimate], RegionDecision],
        progress: Callable[[StageEvent], None],
    ) -> AnalysisPipeline:
        return AnalysisPipeline(
            repo=repo,
            cache=cache,
            stt=engines.stt(settings),
            ocr=engines.ocr(settings),
            config=apply_settings(base_config, settings),
            data_dir=data_dir,
            confirm_region=confirm,
            on_progress=progress,
        )

    def region_prompt(estimate: RegionEstimate) -> RegionDecision:
        dialog = RegionDialog(estimate, parent_window() if parent_window else None)
        dialog.exec()
        return dialog.decision()

    def edit_settings(current: AppSettings) -> AppSettings | None:
        dialog = SettingsDialog(
            detected, recommendation, current, parent_window() if parent_window else None
        )
        return dialog.settings() if dialog.exec() else None

    return AppServices(
        repo=repo,
        data_dir=data_dir,
        settings_path=data_dir / "settings.json",
        device=detected,
        recommendation=recommendation,
        pipeline_factory=pipeline_factory,
        dialogs=dialogs,
        player_factory=VideoPlayerPanel,
        region_prompt=region_prompt,
        edit_settings=edit_settings,
    )


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    dialogs = QtDialogs()
    if not ensure_ffmpeg(dialogs):
        return 1
    window_ref: list[MainWindow] = []
    services = build_services(
        default_data_dir(), dialogs, parent_window=lambda: window_ref[0] if window_ref else None
    )
    window = MainWindow(services)
    window_ref.append(window)
    window.show()
    code = app.exec()
    services.repo.close_all()
    return int(code)

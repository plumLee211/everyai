"""앱 조립: 저장소·캐시·장치 감지·엔진·화면을 연결하고 실행한다.

화면과 분석 코드는 서로를 직접 만들지 않는다. 필요한 것은 여기서 `AppServices`로 한꺼번에 채워
`MainWindow`에 넘긴다. 테스트는 같은 함수를 가짜 장치·대화상자로 호출한다.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QLockFile, QStandardPaths
from PyQt6.QtWidgets import QApplication

from barrierfree.analysis.subtitle_region import RegionEstimate
from barrierfree.config import AnalysisConfig
from barrierfree.dev_artifacts import DevArtifacts
from barrierfree.engines.device import (
    DeviceInfo,
    ModelRecommendation,
    detect_device,
    recommend_model,
)
from barrierfree.engines.ocr import EasyOcrEngine
from barrierfree.engines.stt import FasterWhisperEngine
from barrierfree.logging_setup import configure_logging, install_exception_hooks, log_dir
from barrierfree.media.ffmpeg import FfmpegNotFoundError, find_binary
from barrierfree.paths import bundled_model_dir
from barrierfree.pipeline import AnalysisPipeline, RegionDecision, StageEvent
from barrierfree.settings import AppSettings, apply_settings, effective_model
from barrierfree.storage.cache import FileCache
from barrierfree.storage.db import DatabaseHealth, ensure_healthy
from barrierfree.storage.repository import Repository
from barrierfree.ui import theme
from barrierfree.ui.dialogs import Dialogs, QtDialogs
from barrierfree.ui.main_window import AppServices, MainWindow
from barrierfree.ui.player import VideoPlayerPanel
from barrierfree.ui.region_dialog import RegionDialog
from barrierfree.ui.settings_dialog import SettingsDialog

APP_NAME = "barrierfree"
DB_NAME = "barrierfree.db"
LOCK_NAME = "app.lock"

logger = logging.getLogger(__name__)


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


def check_database(path: Path, dialogs: Dialogs) -> None:
    """DB를 열기 전에 손상을 점검하고, 손댄 것이 있으면 알린다."""
    report = ensure_healthy(path)
    if report.status is DatabaseHealth.OK:
        return
    text = {
        DatabaseHealth.REPAIRED: "분석 기록 파일이 손상되어 있었습니다. "
        "손상된 부분을 복구했습니다.",
        DatabaseHealth.RECREATED: "분석 기록 파일을 읽을 수 없어 새 기록으로 시작합니다. "
        "지난 분석 기록은 보이지 않습니다.",
        DatabaseHealth.UNREPAIRED: "분석 기록 파일의 점검이나 복구를 마치지 못했습니다. "
        "파일은 그대로 두었으며, 일부 기록이 보이지 않거나 저장에 실패할 수 있습니다.",
    }[report.status]
    if report.backup_dir is not None:
        text += f"\n원본 사본: {report.backup_dir}"
    dialogs.warn("분석 기록 점검", text)


def acquire_data_lock(data_dir: Path) -> QLockFile | None:
    """이 데이터 폴더를 쓰는 첫 번째 앱이면 잠금을 돌려준다. 앱이 끝날 때까지 들고 있는다."""
    lock = QLockFile(str(data_dir / LOCK_NAME))
    lock.setStaleLockTime(0)  # 오래 켜 둔 앱의 잠금을 시간만으로 빼앗지 않는다
    return lock if lock.tryLock(0) else None


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
    repair_database: bool = True,
) -> AppServices:
    data_dir.mkdir(parents=True, exist_ok=True)
    base_config = AnalysisConfig()
    detected = device or detect_device()
    recommendation = recommend_model(detected, base_config)

    if repair_database:
        check_database(data_dir / DB_NAME, dialogs)
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
            dev_artifacts=DevArtifacts.from_env(),
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
    app.setStyle("Fusion")  # 시안 스타일시트가 플랫폼 기본 모양에 가려지지 않게 한다
    app.setStyleSheet(theme.stylesheet())
    dialogs = QtDialogs()
    data_dir = default_data_dir()
    configure_logging(data_dir)
    install_exception_hooks(
        lambda summary: dialogs.warn(
            "예상하지 못한 오류",
            f"작업 중 오류가 발생했습니다. 앱은 계속 실행됩니다.\n{summary}\n\n"
            f"자세한 내용은 로그에 남았습니다: {log_dir(data_dir)}",
        )
    )
    if not ensure_ffmpeg(dialogs):
        return 1
    window_ref: list[MainWindow] = []
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        data_lock = acquire_data_lock(data_dir)
        services = build_services(
            data_dir,
            dialogs,
            parent_window=lambda: window_ref[0] if window_ref else None,
            repair_database=data_lock is not None,
        )
        window = MainWindow(services)
    except Exception as exc:  # noqa: BLE001 - 창이 뜨기 전의 실패는 원인을 알리고 끝낸다
        logger.exception("앱을 시작하지 못했습니다")
        dialogs.warn(
            "시작 실패",
            f"앱을 시작하지 못했습니다.\n{type(exc).__name__}: {exc}\n\n"
            f"자세한 내용은 로그에 남았습니다: {log_dir(data_dir)}",
        )
        return 1
    window_ref.append(window)
    window.show()
    code = app.exec()
    services.repo.close_all()
    if data_lock is not None:
        data_lock.unlock()
    return int(code)

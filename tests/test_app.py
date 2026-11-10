"""app.py 테스트: 앱 조립(서비스 구성, 엔진 재사용, ffmpeg 점검, 데이터 위치)."""

from __future__ import annotations

from pathlib import Path

import pytest

from barrierfree import app as app_module
from barrierfree.app import build_services, default_data_dir, ensure_ffmpeg
from barrierfree.engines.device import DeviceInfo
from barrierfree.engines.ocr import EasyOcrEngine
from barrierfree.engines.stt import FasterWhisperEngine
from barrierfree.media.ffmpeg import FfmpegNotFoundError
from barrierfree.pipeline import AnalysisPipeline
from barrierfree.settings import AppSettings
from tests.fakes import FakeDialogs

CPU = DeviceInfo("Intel i5", 8, 16.0, False)
GPU = DeviceInfo("Xeon", 16, 64.0, True)


def noop_confirm(estimate: object) -> object:
    raise AssertionError("확인 콜백은 이 테스트에서 호출되지 않는다")


def pipeline(services: object, settings: AppSettings) -> AnalysisPipeline:
    made = services.pipeline_factory(settings, noop_confirm, lambda event: None)  # type: ignore[attr-defined]
    assert isinstance(made, AnalysisPipeline)
    return made


# ---------- 서비스 구성 ----------


def test_build_services_creates_the_data_layout(tmp_path: Path) -> None:
    services = build_services(tmp_path / "데이터", FakeDialogs(), device=CPU)
    try:
        assert services.data_dir == tmp_path / "데이터"
        assert services.settings_path == tmp_path / "데이터" / "settings.json"
        assert (tmp_path / "데이터" / "barrierfree.db").is_file()
        assert services.device == CPU
        assert services.recommendation.model == "small"
    finally:
        services.repo.close_all()


def test_recommendation_follows_the_device(tmp_path: Path) -> None:
    services = build_services(tmp_path, FakeDialogs(), device=GPU)
    try:
        assert (services.recommendation.model, services.recommendation.device) == ("medium", "cuda")
    finally:
        services.repo.close_all()


# ---------- 파이프라인 팩토리 ----------


def test_default_settings_use_the_recommended_model(tmp_path: Path) -> None:
    services = build_services(tmp_path, FakeDialogs(), device=CPU)
    try:
        made = pipeline(services, AppSettings())
        stt = made._stt  # noqa: SLF001 - 조립 결과를 확인한다
        assert isinstance(stt, FasterWhisperEngine)
        assert (stt.model_name, stt.device, stt.compute_type) == ("small", "cpu", "int8")
        assert isinstance(made._ocr, EasyOcrEngine)  # noqa: SLF001
    finally:
        services.repo.close_all()


def test_users_model_choice_overrides_the_recommendation(tmp_path: Path) -> None:
    services = build_services(tmp_path, FakeDialogs(), device=CPU)
    try:
        stt = pipeline(services, AppSettings(whisper_model="medium"))._stt  # noqa: SLF001
        assert stt.model_name == "medium"
    finally:
        services.repo.close_all()


def test_engines_are_reused_for_the_same_settings_and_rebuilt_for_different_ones(
    tmp_path: Path,
) -> None:
    services = build_services(tmp_path, FakeDialogs(), device=CPU)
    try:
        first = pipeline(services, AppSettings())
        second = pipeline(services, AppSettings())
        assert first._stt is second._stt  # noqa: SLF001 - 모델 로딩은 비싸므로 재사용한다
        assert first._ocr is second._ocr  # noqa: SLF001
        other = pipeline(services, AppSettings(whisper_model="base"))
        assert other._stt is not first._stt  # noqa: SLF001
        assert other._stt.model_name == "base"  # noqa: SLF001
    finally:
        services.repo.close_all()


def test_region_ratio_setting_reaches_the_analysis_config(tmp_path: Path) -> None:
    services = build_services(tmp_path, FakeDialogs(), device=CPU)
    try:
        made = pipeline(services, AppSettings(subtitle_region_ratio=0.4))
        assert made._config.subtitle_region_ratio == 0.4  # noqa: SLF001
    finally:
        services.repo.close_all()


def test_pipeline_shares_the_services_repository(tmp_path: Path) -> None:
    services = build_services(tmp_path, FakeDialogs(), device=CPU)
    try:
        assert pipeline(services, AppSettings())._repo is services.repo  # noqa: SLF001
    finally:
        services.repo.close_all()


# ---------- ffmpeg 점검 ----------


def test_missing_ffmpeg_is_explained_and_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> str:
        raise FfmpegNotFoundError(name)

    monkeypatch.setattr(app_module, "find_binary", missing)
    dialogs = FakeDialogs()
    assert ensure_ffmpeg(dialogs) is False
    assert dialogs.warnings and "ffmpeg" in dialogs.warnings[0][1]
    assert "ERR-04" in dialogs.warnings[0][1]


def test_available_ffmpeg_passes_silently() -> None:
    dialogs = FakeDialogs()
    assert ensure_ffmpeg(dialogs) is True
    assert dialogs.warnings == []


# ---------- 데이터 위치 ----------


def test_data_dir_can_be_overridden_by_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BARRIERFREE_DATA_DIR", str(tmp_path / "내 데이터"))
    assert default_data_dir() == tmp_path / "내 데이터"


def test_default_data_dir_is_a_per_user_location(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BARRIERFREE_DATA_DIR", raising=False)
    path = default_data_dir()
    assert path.is_absolute()
    assert "barrierfree" in str(path).lower()

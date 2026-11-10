"""settings.py 테스트: 앱 설정 저장·복원(손상에 안전), 분석 설정 반영."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from barrierfree.config import AnalysisConfig
from barrierfree.settings import (
    MAX_REGION_RATIO,
    MIN_REGION_RATIO,
    AppSettings,
    apply_settings,
    effective_model,
    load_settings,
    save_settings,
)


def test_defaults() -> None:
    s = AppSettings()
    assert s.whisper_model is None  # None = 권장 모델 사용
    assert s.model_dir is None
    assert s.subtitle_region_ratio == 0.25


def test_save_and_load_roundtrip_with_korean_path(tmp_path: Path) -> None:
    path = tmp_path / "설정 폴더" / "settings.json"
    original = AppSettings("medium", "D:/모델 저장소", 0.3)
    save_settings(path, original)
    assert load_settings(path) == original


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    assert load_settings(tmp_path / "none.json") == AppSettings()


@pytest.mark.parametrize("content", ["", "{broken", "[]", '"text"', "null"])
def test_corrupt_file_gives_defaults(tmp_path: Path, content: str) -> None:
    path = tmp_path / "s.json"
    path.write_text(content, encoding="utf-8")
    assert load_settings(path) == AppSettings()


def test_invalid_fields_fall_back_individually(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    path.write_text(
        json.dumps({"whisper_model": "giant-v9", "model_dir": 123, "subtitle_region_ratio": 9}),
        encoding="utf-8",
    )
    assert load_settings(path) == AppSettings()


def test_valid_fields_survive_next_to_invalid_ones(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    path.write_text(
        json.dumps({"whisper_model": "base", "subtitle_region_ratio": "x"}), encoding="utf-8"
    )
    loaded = load_settings(path)
    assert loaded.whisper_model == "base" and loaded.subtitle_region_ratio == 0.25


@pytest.mark.parametrize("ratio", [MIN_REGION_RATIO - 0.01, MAX_REGION_RATIO + 0.01, float("nan")])
def test_out_of_range_ratio_is_rejected(tmp_path: Path, ratio: float) -> None:
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"subtitle_region_ratio": ratio}), encoding="utf-8")
    assert load_settings(path).subtitle_region_ratio == 0.25


def test_huge_integer_ratio_falls_back_instead_of_breaking_startup(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    # 유효한 JSON 정수지만 float로 바꿀 수 없다. 이 한 값 때문에 앱이 시작되지 않으면 안 된다.
    path.write_text('{"whisper_model": "base", "subtitle_region_ratio": 1' + "0" * 309 + "}",
                    encoding="utf-8")
    loaded = load_settings(path)
    assert loaded.subtitle_region_ratio == 0.25
    assert loaded.whisper_model == "base"  # 나머지 필드는 살린다


def test_save_leaves_no_temp_files(tmp_path: Path) -> None:
    save_settings(tmp_path / "s.json", AppSettings())
    assert [p.name for p in tmp_path.iterdir()] == ["s.json"]


def test_apply_settings_overrides_region_ratio_and_model_dir() -> None:
    cfg = apply_settings(AnalysisConfig(), AppSettings(None, "D:/m", 0.35))
    assert cfg.subtitle_region_ratio == 0.35
    assert cfg.model_dir == "D:/m"


def test_apply_settings_with_defaults_keeps_base_config() -> None:
    base = AnalysisConfig()
    assert apply_settings(base, AppSettings()) == base


def test_effective_model_prefers_the_users_choice() -> None:
    assert effective_model(AppSettings("medium"), "small") == "medium"
    assert effective_model(AppSettings(), "small") == "small"

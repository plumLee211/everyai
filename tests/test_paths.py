"""paths.py 테스트: 동봉 모델 폴더 탐색."""

from __future__ import annotations

from pathlib import Path

import pytest

from barrierfree import paths


def _frozen(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    internal = tmp_path / "_internal"
    internal.mkdir()
    monkeypatch.delenv("BARRIERFREE_MODEL_DIR", raising=False)
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "_MEIPASS", str(internal), raising=False)
    monkeypatch.setattr(paths.sys, "executable", str(tmp_path / "barrierfree.exe"))
    return internal


def test_bundled_model_dir_is_found_in_the_internal_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    internal = _frozen(monkeypatch, tmp_path)
    (internal / "models").mkdir()
    assert paths.bundled_model_dir() == str(internal / "models")


def test_bundled_model_dir_is_none_when_the_folder_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _frozen(monkeypatch, tmp_path)
    assert paths.bundled_model_dir() is None


def test_bundled_model_dir_is_none_in_development(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("BARRIERFREE_MODEL_DIR", raising=False)
    monkeypatch.delattr(paths.sys, "frozen", raising=False)
    assert paths.bundled_model_dir() is None


def test_environment_variable_overrides_the_bundled_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BARRIERFREE_MODEL_DIR", str(tmp_path))
    assert paths.bundled_model_dir() == str(tmp_path)

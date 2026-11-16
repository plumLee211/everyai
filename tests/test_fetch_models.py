"""build/fetch_models.py 테스트: 빌드 전 모델 확인과 자동 다운로드(네트워크 없이)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from barrierfree.engines.device import bundled_models

BUILD_SCRIPT = Path(__file__).resolve().parent.parent / "build" / "fetch_models.py"


@pytest.fixture
def fm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location("fetch_models_under_test", BUILD_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "MODELS", tmp_path / "models")
    (tmp_path / "models").mkdir()
    return module


def put_whisper(fm: ModuleType, name: str) -> None:
    snapshot = fm.MODELS / f"models--Systran--faster-whisper-{name}" / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "model.bin").write_bytes(b"x")


def put_easyocr(fm: ModuleType, filename: str) -> None:
    (fm.MODELS / filename).write_bytes(b"x")


def put_everything(fm: ModuleType) -> None:
    for name in bundled_models():
        put_whisper(fm, name)
    for filename in fm.EASYOCR_FILES:
        put_easyocr(fm, filename)


def test_empty_folder_reports_every_required_model_as_missing(fm: ModuleType) -> None:
    missing = fm.missing_models()
    expected = len(bundled_models()) + len(fm.EASYOCR_FILES)
    assert len(missing) == expected
    assert all(isinstance(item, str) and item for item in missing)


def test_partial_download_is_not_complete(fm: ModuleType) -> None:
    # 폴더가 비어 있지 않아도 일부만 있으면 '없음'으로 본다(예전 검사는 이 경우 통과했다).
    put_whisper(fm, bundled_models()[0])
    put_easyocr(fm, next(iter(fm.EASYOCR_FILES)))
    assert fm.missing_models() != []


def test_whisper_folder_without_weights_counts_as_missing(fm: ModuleType) -> None:
    name = bundled_models()[0]
    (fm.MODELS / f"models--Systran--faster-whisper-{name}" / "snapshots" / "abc").mkdir(
        parents=True
    )
    assert any(name in item for item in fm.missing_models())


def test_nothing_is_missing_when_every_model_is_present(fm: ModuleType) -> None:
    put_everything(fm)
    assert fm.missing_models() == []


def test_ensure_models_downloads_nothing_when_all_present(
    fm: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    put_everything(fm)
    calls: list[str] = []
    monkeypatch.setattr(fm, "fetch_whisper", lambda: calls.append("whisper"))
    monkeypatch.setattr(fm, "fetch_easyocr", lambda: calls.append("easyocr"))
    fm.ensure_models()
    assert calls == []


def test_ensure_models_downloads_when_something_is_missing(
    fm: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_whisper() -> None:
        calls.append("whisper")
        for name in bundled_models():
            put_whisper(fm, name)

    def fake_easyocr() -> None:
        calls.append("easyocr")
        for filename in fm.EASYOCR_FILES:
            put_easyocr(fm, filename)

    monkeypatch.setattr(fm, "fetch_whisper", fake_whisper)
    monkeypatch.setattr(fm, "fetch_easyocr", fake_easyocr)
    fm.ensure_models()
    assert calls == ["whisper", "easyocr"]
    assert fm.missing_models() == []


def test_ensure_models_stops_the_build_if_models_are_still_missing(
    fm: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fm, "fetch_whisper", lambda: None)  # 받았다고 하지만 실제로는 없음
    monkeypatch.setattr(fm, "fetch_easyocr", lambda: None)
    with pytest.raises(SystemExit) as raised:
        fm.ensure_models()
    assert "모델" in str(raised.value)


def test_ensure_models_creates_the_models_folder(
    fm: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fm, "MODELS", tmp_path / "fresh" / "models")
    monkeypatch.setattr(fm, "fetch_whisper", lambda: None)
    monkeypatch.setattr(fm, "fetch_easyocr", lambda: None)
    with pytest.raises(SystemExit):
        fm.ensure_models()
    assert (tmp_path / "fresh" / "models").is_dir()

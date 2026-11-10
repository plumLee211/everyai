"""engines/device.py 테스트: 장치 감지와 모델 추천 규칙(결정 Q5, 실측 전 임시 규칙)."""

from __future__ import annotations

import pytest

from barrierfree.config import AnalysisConfig
from barrierfree.engines import device as dev
from barrierfree.engines.device import DeviceInfo, detect_device, recommend_model


def _info(*, cuda: bool, ram_gb: float) -> DeviceInfo:
    return DeviceInfo(cpu_name="Test CPU", cpu_count=8, ram_gb=ram_gb, cuda_available=cuda)


def test_detect_device_reports_plausible_values() -> None:
    info = detect_device()
    assert info.cpu_count >= 1
    assert info.ram_gb > 0
    assert isinstance(info.cuda_available, bool)
    assert info.cpu_name


def test_detect_device_reflects_cuda_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dev, "_cuda_available", lambda: True)
    assert detect_device().cuda_available is True


def test_cuda_probe_failure_means_no_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> int:
        raise RuntimeError("driver missing")

    monkeypatch.setattr(dev, "_cuda_device_count", boom)
    assert dev._cuda_available() is False


def test_recommend_uses_gpu_when_cuda_available() -> None:
    rec = recommend_model(_info(cuda=True, ram_gb=16), AnalysisConfig())
    assert (rec.model, rec.device, rec.compute_type) == ("medium", "cuda", "float16")
    assert rec.warning is None


def test_recommend_cpu_with_enough_ram() -> None:
    rec = recommend_model(_info(cuda=False, ram_gb=16), AnalysisConfig())
    assert (rec.model, rec.device, rec.compute_type) == ("small", "cpu", "int8")
    assert rec.warning is None


def test_recommend_low_ram_downgrades_model_and_warns() -> None:
    rec = recommend_model(_info(cuda=False, ram_gb=4), AnalysisConfig())
    assert rec.model == "base"
    assert rec.device == "cpu"
    assert rec.warning is not None


def test_recommend_boundary_at_threshold_is_not_low() -> None:
    rec = recommend_model(_info(cuda=False, ram_gb=8.0), AnalysisConfig())
    assert rec.model == "small"


def test_recommend_follows_config_overrides() -> None:
    cfg = AnalysisConfig(recommend_cpu_model="turbo", recommend_cuda_model="large-v3")
    assert recommend_model(_info(cuda=False, ram_gb=32), cfg).model == "turbo"
    assert recommend_model(_info(cuda=True, ram_gb=32), cfg).model == "large-v3"

"""실행 장치 감지와 음성 인식 모델 추천.

추천 규칙은 결정 Q5의 임시값이다(CUDA → medium, CPU → small, 저RAM → base).
Task 0 실측 후 `AnalysisConfig`의 상수만 갱신하면 된다.
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass

import psutil

from barrierfree.config import AnalysisConfig

SUPPORTED_MODELS = ("tiny", "base", "small", "medium", "large-v3", "turbo")


@dataclass(frozen=True)
class DeviceInfo:
    cpu_name: str
    cpu_count: int
    ram_gb: float
    cuda_available: bool


@dataclass(frozen=True)
class ModelRecommendation:
    model: str
    device: str  # "cuda" | "cpu"
    warning: str | None = None


def _cuda_device_count() -> int:
    import torch

    return int(torch.cuda.device_count()) if torch.cuda.is_available() else 0


def _cuda_available() -> bool:
    """torch가 NVIDIA CUDA 장치를 볼 수 있는가. 드라이버 문제 등은 '없음'으로 본다.

    CPU용 torch가 설치되어 있으면 GPU가 있어도 '없음'이다.
    """
    try:
        return _cuda_device_count() > 0
    except Exception:  # noqa: BLE001 - 어떤 실패든 CPU로 안전하게 폴백한다
        return False


def detect_device() -> DeviceInfo:
    return DeviceInfo(
        cpu_name=platform.processor() or platform.machine() or "알 수 없는 CPU",
        cpu_count=psutil.cpu_count(logical=True) or os.cpu_count() or 1,
        ram_gb=psutil.virtual_memory().total / (1024**3),
        cuda_available=_cuda_available(),
    )


def recommend_model(info: DeviceInfo, config: AnalysisConfig) -> ModelRecommendation:
    if info.cuda_available:
        return ModelRecommendation(config.recommend_cuda_model, "cuda")
    if info.ram_gb < config.low_ram_threshold_gb:
        return ModelRecommendation(
            config.recommend_low_ram_model,
            "cpu",
            warning="메모리가 적어 작은 모델을 권장합니다. 인식 정확도가 낮을 수 있습니다.",
        )
    return ModelRecommendation(config.recommend_cpu_model, "cpu")

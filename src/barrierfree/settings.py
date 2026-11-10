"""앱 설정(분석 설정 다이얼로그에서 바꾸는 값)의 저장·복원.

설정 파일은 사용자가 건드리거나 손상될 수 있으므로, 읽을 때 필드마다 검증하고 잘못된 값만
기본값으로 되돌린다(파일 전체를 버리지 않는다).
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from barrierfree.config import AnalysisConfig
from barrierfree.engines.device import SUPPORTED_MODELS

# 자막 영역(하단) 비율의 허용 범위. 세부구현.md D-05(미확정)의 임시값이다.
MIN_REGION_RATIO = 0.10
MAX_REGION_RATIO = 0.50
DEFAULT_REGION_RATIO = 0.25


@dataclass(frozen=True)
class AppSettings:
    whisper_model: str | None = None  # None이면 장치에 맞는 권장 모델을 쓴다
    model_dir: str | None = None
    subtitle_region_ratio: float = DEFAULT_REGION_RATIO


def _valid_ratio(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return DEFAULT_REGION_RATIO
    try:
        ratio = float(value)
    except OverflowError:  # 10**309 같은 정수
        return DEFAULT_REGION_RATIO
    if not math.isfinite(ratio) or not MIN_REGION_RATIO <= ratio <= MAX_REGION_RATIO:
        return DEFAULT_REGION_RATIO
    return ratio


def load_settings(path: str | Path) -> AppSettings:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return AppSettings()
    if not isinstance(data, dict):
        return AppSettings()
    model = data.get("whisper_model")
    model_dir = data.get("model_dir")
    return AppSettings(
        whisper_model=model if isinstance(model, str) and model in SUPPORTED_MODELS else None,
        model_dir=model_dir if isinstance(model_dir, str) and model_dir else None,
        subtitle_region_ratio=_valid_ratio(data.get("subtitle_region_ratio")),
    )


def save_settings(path: str | Path, settings: AppSettings) -> None:
    """임시 파일에 쓴 뒤 교체한다. 저장 도중 종료되어도 기존 설정이 깨지지 않는다."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "whisper_model": settings.whisper_model,
            "model_dir": settings.model_dir,
            "subtitle_region_ratio": settings.subtitle_region_ratio,
        },
        ensure_ascii=False,
        indent=2,
    )
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def apply_settings(config: AnalysisConfig, settings: AppSettings) -> AnalysisConfig:
    return replace(
        config,
        subtitle_region_ratio=settings.subtitle_region_ratio,
        model_dir=settings.model_dir,
    )


def effective_model(settings: AppSettings, recommended: str) -> str:
    return settings.whisper_model or recommended

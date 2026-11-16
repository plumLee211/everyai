"""동봉 자원(ffmpeg, 모델)의 위치.

PyInstaller 6 onedir는 datas를 실행 파일 옆 `_internal`(= `sys._MEIPASS`)에 둔다.
예전 배치(실행 파일 바로 옆)도 함께 본다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def bundle_roots() -> list[Path]:
    """PyInstaller 배포본이면 동봉 자원이 있을 수 있는 폴더들. 개발 환경이면 빈 목록."""
    if not getattr(sys, "frozen", False):
        return []
    roots: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    exe_dir = Path(sys.executable).parent
    if exe_dir not in roots:
        roots.append(exe_dir)
    return roots


def bundled_model_dir() -> str | None:
    """동봉된 모델 폴더(환경변수 BARRIERFREE_MODEL_DIR → 배포본의 models 폴더). 없으면 None."""
    override = os.environ.get("BARRIERFREE_MODEL_DIR")
    if override and Path(override).is_dir():
        return override
    for root in bundle_roots():
        candidate = root / "models"
        if candidate.is_dir():
            return str(candidate)
    return None

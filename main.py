"""개발 중 `python main.py`로 앱을 실행하기 위한 진입점. 패키지는 `src/barrierfree`에 있다.

설치된 환경에서는 `python -m barrierfree`를 쓰면 된다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))

# 저장소 루트의 ffmpeg 폴더(빌드용으로 받아 둔 것)가 있으면 PATH 설정 없이도 그것을 쓴다.
if (ROOT / "ffmpeg").is_dir():
    os.environ.setdefault("BARRIERFREE_FFMPEG_DIR", str(ROOT / "ffmpeg"))

from barrierfree.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

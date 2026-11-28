"""`python main.py`와 배포 앱(build/barrierfree.spec)의 진입점. 패키지는 `src/barrierfree`에 있다.

설치된 환경에서는 `python -m barrierfree`를 쓰면 된다.
"""

from __future__ import annotations

import multiprocessing
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))

# 저장소 루트의 ffmpeg 폴더(빌드용으로 받아 둔 것)가 있으면 PATH 설정 없이도 그것을 쓴다.
if (ROOT / "ffmpeg").is_dir():
    os.environ.setdefault("BARRIERFREE_FFMPEG_DIR", str(ROOT / "ffmpeg"))

if __name__ == "__main__":
    # 맨 먼저 부른다. macOS의 라이브러리는 분석 중에 앱 실행 파일을 보조 작업용으로 다시 실행하는데,
    # 배포 앱은 여기서 그것을 가려 보조 작업만 하고 끝낸다. 없으면 앱이 하나 더 열린다.
    multiprocessing.freeze_support()

    from barrierfree.app import main

    sys.exit(main())

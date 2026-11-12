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
# 개발 실행에서는 추출한 음성(MP3)을 저장소의 docs 폴더(테스트 영상 옆)에 둔다.
os.environ.setdefault("BARRIERFREE_AUDIO_DIR", str(ROOT / "docs"))
# 1초 간격 화면 캡처는 docs/image 아래에 영상별 폴더로 둔다.
os.environ.setdefault("BARRIERFREE_IMAGE_DIR", str(ROOT / "docs" / "image"))
# 캡처 이미지의 OCR 결과(SRT)는 docs/ocr에 둔다.
os.environ.setdefault("BARRIERFREE_OCR_DIR", str(ROOT / "docs" / "ocr"))

from barrierfree.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

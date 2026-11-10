"""개발 중 `python main.py`로 앱을 실행하기 위한 진입점. 패키지는 `src/barrierfree`에 있다.

설치된 환경에서는 `python -m barrierfree`를 쓰면 된다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from barrierfree.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

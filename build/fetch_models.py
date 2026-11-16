"""배포본에 동봉할 모델을 저장소 루트의 `models/`에 내려받는다.

빌드(`build/barrierfree.spec`)가 시작될 때 `ensure_models()`를 자동 호출하므로 보통은 따로
실행할 필요가 없다. 미리 받아 두고 싶으면 직접 실행해도 된다.

    python build/fetch_models.py          (Windows: .venv\\Scripts\\python build\\fetch_models.py)

- 음성 인식(faster-whisper): 앱이 권장할 수 있는 모델 전부(config의 recommend_*).
- 화면 글자 인식(EasyOCR): 한국어·영어 인식에 필요한 검출/인식 모델.
모델이 모두 있으면 아무것도 받지 않고, 일부만 있으면 없는 것만 받는다. 인증서 문제로 내려받기가
막히면 curl로 한 번 더 시도한다.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from barrierfree.engines.device import bundled_models  # noqa: E402

MODELS = ROOT / "models"

EASYOCR_FILES = {
    "craft_mlt_25k.pth": "https://github.com/JaidedAI/EasyOCR/releases/download/pre-v1.1.6/craft_mlt_25k.zip",
    "korean_g2.pth": "https://github.com/JaidedAI/EasyOCR/releases/download/v1.3/korean_g2.zip",
}


def _download(url: str, target: Path) -> None:
    try:
        urllib.request.urlretrieve(url, target)  # noqa: S310 - 고정된 공식 릴리스 주소
    except Exception as exc:  # noqa: BLE001 - 인증서 오류 등은 curl(Windows는 시스템 인증서 사용)로 재시도
        print(f"  urllib 실패({exc}); curl로 재시도")
        curl = shutil.which("curl") or shutil.which("curl.exe")
        if not curl:
            raise
        subprocess.run([curl, "-L", "--fail", "-s", "-o", str(target), url], check=True)


def fetch_whisper() -> None:
    from faster_whisper.utils import download_model

    for name in bundled_models():
        repo_dir = MODELS / f"models--Systran--faster-whisper-{name}"
        if any(repo_dir.glob("snapshots/*/model.bin")):
            print(f"whisper {name} (이미 있음)")
            continue
        print(f"whisper {name}")
        download_model(name, cache_dir=str(MODELS))
    _materialize_cache()


def _materialize_cache() -> None:
    """HF 캐시의 심볼릭 링크를 실제 파일로 바꾸고 blobs를 지운다.

    PyInstaller가 링크를 따라가 파일을 중복 복사하거나, 링크가 깨진 채 배포되는 것을 막는다.
    snapshots의 실제 파일만으로 오프라인 로드가 된다.
    """
    for link in MODELS.glob("models--*/snapshots/**/*"):
        if link.is_symlink():
            target = link.resolve()
            link.unlink()
            shutil.copy2(target, link)
    for junk in (MODELS / "blobs", MODELS / ".locks"):
        shutil.rmtree(junk, ignore_errors=True)
    for repo in MODELS.glob("models--*"):
        shutil.rmtree(repo / "blobs", ignore_errors=True)
        shutil.rmtree(repo / ".no_exist", ignore_errors=True)


def fetch_easyocr() -> None:
    for filename, url in EASYOCR_FILES.items():
        if (MODELS / filename).is_file():
            print(f"easyocr {filename} (이미 있음)")
            continue
        print(f"easyocr {filename}")
        archive = MODELS / (filename + ".zip")
        _download(url, archive)
        with zipfile.ZipFile(archive) as z:
            z.extract(filename, MODELS)
        archive.unlink()


def _whisper_present(name: str) -> bool:
    repo_dir = MODELS / f"models--Systran--faster-whisper-{name}"
    return any(repo_dir.glob("snapshots/*/model.bin"))


def missing_models() -> list[str]:
    """동봉에 필요한 모델 중 아직 없는 것의 이름. 모두 있으면 빈 목록.

    폴더가 비어 있지 않다는 것만으로는 부족하다. 내려받다 끊겨 일부만 있는 경우도 '없음'으로 본다.
    """
    missing = [f"whisper {name}" for name in bundled_models() if not _whisper_present(name)]
    missing += [f"easyocr {name}" for name in EASYOCR_FILES if not (MODELS / name).is_file()]
    return missing


def ensure_models() -> None:
    """모델이 모두 있으면 건너뛰고, 없는 것이 있으면 내려받는다. 끝내 없으면 빌드를 멈춘다."""
    MODELS.mkdir(parents=True, exist_ok=True)
    missing = missing_models()
    if not missing:
        print("모델 확인: 필요한 모델이 모두 있어 내려받기를 건너뜁니다.")
        return
    print(f"모델 확인: {len(missing)}개가 없어 내려받습니다 -> {', '.join(missing)}")
    fetch_whisper()
    fetch_easyocr()
    still = missing_models()
    if still:
        raise SystemExit(f"모델을 모두 준비하지 못했습니다. 없는 모델: {', '.join(still)}")


def main() -> int:
    ensure_models()
    total = sum(f.stat().st_size for f in MODELS.rglob("*") if f.is_file() and not f.is_symlink())
    print(f"완료: {MODELS} ({total / 1024**3:.2f} GB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- mode: python ; coding: utf-8 -*-
# PyInstaller onedir 설정. 저장소 루트에서 실행한다(Windows는 .exe 폴더, macOS는 .app 번들이 나온다):
#   pyinstaller build/barrierfree.spec --noconfirm
# PyInstaller는 크로스 컴파일을 하지 못한다. 각 OS에서 그 OS용 산출물을 따로 빌드한다.
# 자세한 절차와 점검 목록은 build/README.md를 본다.
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # 저장소 루트

block_cipher = None
IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

datas = []
datas += collect_data_files("easyocr")           # 문자 사전 등
datas += collect_data_files("faster_whisper")     # VAD(silero) 모델 파일

# ffmpeg/ffprobe는 사전에 저장소 루트의 ffmpeg/ 폴더에 둔다(Windows: .exe, macOS: 확장자 없음).
# 별도 바이너리 폴더는 BARRIERFREE_FFMPEG_DIR로 지정할 수 있다.
_exe_suffix = ".exe" if IS_WINDOWS else ""
_ffmpeg_dir = os.environ.get("BARRIERFREE_FFMPEG_DIR", os.path.join(ROOT, "ffmpeg"))
for _name in ("ffmpeg", "ffprobe"):
    _path = os.path.join(_ffmpeg_dir, _name + _exe_suffix)
    if not os.path.isfile(_path):
        raise SystemExit(f"{_path} 가 없습니다. build/README.md의 '준비'를 먼저 따라 주세요.")
    datas += [(_path, "ffmpeg")]

# 모델은 빌드 때 자동으로 준비한다: 필요한 모델이 모두 있으면 건너뛰고, 없으면 내려받는다
# (build/fetch_models.py의 ensure_models). 끝내 없으면 빌드를 멈춘다(모델 없는 배포 방지).
# 패키징 구조만 확인하는 개발용 빌드는 BARRIERFREE_SKIP_MODELS=1로 건너뛸 수 있다(배포 금지).
_models = os.path.join(ROOT, "models")
if os.environ.get("BARRIERFREE_SKIP_MODELS") == "1":
    print("경고: 모델을 준비하지 않고 빌드합니다. 개발용이며 배포하면 안 됩니다.")
    if os.path.isdir(_models) and os.listdir(_models):
        datas += [(_models, "models")]
else:
    sys.path.insert(0, os.path.join(ROOT, "build"))
    import fetch_models

    fetch_models.ensure_models()
    datas += [(_models, "models")]

hiddenimports = collect_submodules("easyocr") + collect_submodules("faster_whisper")
# torchvision의 C++ 확장은 import가 아니라 파일 경로로 로드돼 PyInstaller 분석에 잡히지 않는다.
# Windows는 .pyd(+ 의존 .dll), macOS는 .so·.dylib이라 모두 모은다. 빠지면 실행 시
# "operator torchvision::nms does not exist"가 난다.
binaries = collect_dynamic_libs(
    "torchvision", search_patterns=["*.pyd", "*.dll", "*.so", "*.dylib"]
)
if not any(
    os.path.basename(_src).startswith("_C") and _src.endswith((".pyd", ".so"))
    for _src, _ in binaries
):
    raise SystemExit("torchvision의 C++ 확장(_C*.pyd 또는 _C*.so)을 찾지 못했습니다.")

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tests", "pytest", "mypy", "ruff"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="barrierfree",
    console=False,  # GUI 앱. 문제를 추적할 때만 True로 바꿔 콘솔을 본다.
)
coll = COLLECT(exe, a.binaries, a.datas, name="barrierfree")

if IS_MAC:
    # macOS는 폴더가 아니라 .app 번들로 배포한다. 서명·공증을 하지 않으면 다른 Mac에서
    # Gatekeeper 경고가 뜬다(README 참고).
    app = BUNDLE(
        coll,
        name="barrierfree.app",
        bundle_identifier="kr.everyai.barrierfree",
        info_plist={
            "CFBundleName": "배리어프리 영상 점검",
            "NSHighResolutionCapable": True,
        },
    )

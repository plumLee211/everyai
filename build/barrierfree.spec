# -*- mode: python ; coding: utf-8 -*-
# PyInstaller onedir 설정. Windows PC에서 저장소 루트에서 실행한다:
#   pyinstaller build/barrierfree.spec --noconfirm
# 자세한 절차와 점검 목록은 build/README.md를 본다. (Mac에서는 검증하지 못했다.)
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # 저장소 루트

block_cipher = None

datas = []
datas += collect_data_files("easyocr")           # 문자 사전 등
datas += collect_data_files("faster_whisper")     # VAD(silero) 모델 파일
datas += [(os.path.join(ROOT, "ffmpeg", "ffmpeg.exe"), "ffmpeg"), (os.path.join(ROOT, "ffmpeg", "ffprobe.exe"), "ffmpeg")]  # 사전에 내려받아 둔다

# 모델은 build/fetch_models.py로 미리 내려받아 models/에 둔다. 없으면 빌드를 멈춘다(모델 없는 배포 방지).
_models = os.path.join(ROOT, "models")
if not os.path.isdir(_models) or not os.listdir(_models):
    raise SystemExit("models/ 폴더가 비어 있습니다. 먼저 `python build/fetch_models.py`를 실행하세요.")
datas += [(_models, "models")]

hiddenimports = collect_submodules("easyocr") + collect_submodules("faster_whisper")

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[os.path.join(ROOT, "src")],
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

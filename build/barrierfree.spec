# -*- mode: python ; coding: utf-8 -*-
# PyInstaller onedir 설정. Windows PC에서 저장소 루트에서 실행한다:
#   pyinstaller build/barrierfree.spec --noconfirm
# 자세한 절차와 점검 목록은 build/README.md를 본다. (Mac에서는 검증하지 못했다.)
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# spec 안의 상대 경로는 이 파일(build/) 기준이므로 저장소 루트를 명시한다.
ROOT = os.path.dirname(SPECPATH)

datas = []
datas += collect_data_files("easyocr")           # 문자 사전 등
datas += collect_data_files("whisper")            # mel 필터, 토크나이저 어휘 파일(assets)
datas += [  # 저장소 루트의 ffmpeg 폴더에 사전에 내려받아 둔다
    (os.path.join(ROOT, "ffmpeg", "ffmpeg.exe"), "ffmpeg"),
    (os.path.join(ROOT, "ffmpeg", "ffprobe.exe"), "ffmpeg"),
]

hiddenimports = collect_submodules("easyocr") + collect_submodules("whisper")
hiddenimports += ["tiktoken_ext", "tiktoken_ext.openai_public"]  # whisper 토크나이저가 동적으로 찾는다

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

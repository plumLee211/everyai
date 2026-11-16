# 패키징 (Phase 4, Windows)

개발은 macOS에서 했고 **이 절차는 Windows에서 한 번도 실행해 보지 못했다.** 아래 "확인 목록"을 모두
통과하기 전에는 배포하지 않는다.

## 준비 (Windows 10/11, x64)

1. Python 3.11 설치 후 가상환경 생성: `py -3.11 -m venv .venv`
2. `.venv\Scripts\pip install -r requirements.txt pyinstaller`
   - GPU를 쓰려면 CUDA용 torch를 따로 설치한다(requirements.txt는 일반 빌드 기준).
3. 저장소 루트에 `ffmpeg\ffmpeg.exe`, `ffmpeg\ffprobe.exe`를 둔다(LGPL 빌드 권장, 라이선스 고지 포함).
4. 모델을 미리 내려받는다: `.venv\Scripts\python build\fetch_models.py` (저장소 루트의 `models/`, 약 2.1GB).
   음성 인식 base·small·medium(앱이 권장할 수 있는 모델 전부)과 EasyOCR 한국어·영어 모델이다.
   `models/`가 비어 있으면 빌드가 멈춘다. 배포본에는 모델이 들어 있어 첫 실행에 인터넷이 필요 없다.
   설정 화면에서 모델 경로를 지정하면 그 경로가 우선한다.
5. CUDA 라이브러리(cuBLAS·cuDNN)는 동봉하지 않는다. GPU를 쓸 수 없으면 앱이 자동으로 CPU로 폴백한다.

## 빌드

```
.venv\Scripts\pyinstaller build\barrierfree.spec --noconfirm
```

결과: `dist\barrierfree\` 폴더(onedir). 폴더째 배포한다(약 3.3GB). 앱은 `_internal\ffmpeg\`(`paths.py`)를 먼저 찾고(`media/ffmpeg.py`의 `find_binary`), 없으면 PATH를 본다.

## 확인 목록 (배포 전)

- [ ] 한글 경로의 영상(`C:\사용자\홍길동\영상\안내 영상.mp4`)으로 분석이 끝까지 된다.
- [ ] 자막 있음 / 자막 영역 확인 / 자막 없음 3경로가 모두 동작한다.
- [ ] 영상 재생이 된다(코덱 미지원이면 SCR-03에 "재생 실패" 안내가 보인다).
- [ ] 분석 취소 후 ffmpeg 프로세스가 남지 않는다(작업 관리자 확인).
- [ ] CSV를 엑셀에서 열면 한글이 깨지지 않고, `=`로 시작하는 값이 수식으로 실행되지 않는다.
- [ ] 데이터 폴더(`%APPDATA%\barrierfree`)를 지우고 다시 실행해도 정상 시작한다.
- [ ] 백신 프로그램이 `barrierfree.exe`를 오탐하지 않는다(기관 배포 시 사전 확인).
- [ ] GPU 없는 PC에서 권장 모델(CPU)로 20분 영상을 분석하는 시간을 기록한다(Task 0 벤치마크).

## 알려진 위험

- PyInstaller가 torch/easyocr의 숨은 import를 놓치면 실행 시 `ModuleNotFoundError`가 난다.
  `hiddenimports`에 추가하며 반복한다.
- 용량이 크다(torch 포함 수 GB). 필요하면 CPU 전용 torch로 별도 빌드를 만든다.

# 패키징 (Phase 4)

PyInstaller는 크로스 컴파일을 하지 못한다. Windows용(`.exe` 폴더)은 Windows에서, macOS용(`.app`)은
Mac에서 각각 빌드한다. 같은 `build/barrierfree.spec`이 OS를 보고 알맞게 동작한다.

**Windows 절차는 Windows에서 한 번도 실행해 보지 못했다.** "확인 목록"을 모두 통과하기 전에는
배포하지 않는다. macOS 절차는 이 저장소 개발 Mac(Apple Silicon)에서 빌드와 시작까지 확인했다.

## Windows

## 준비 (Windows 10/11, x64)

1. Python 3.11 설치 후 가상환경 생성: `py -3.11 -m venv .venv`
2. `.venv\Scripts\pip install -r requirements.txt pyinstaller`
   - GPU를 쓰려면 CUDA용 torch를 따로 설치한다(requirements.txt는 일반 빌드 기준).
3. 저장소 루트에 `ffmpeg\ffmpeg.exe`, `ffmpeg\ffprobe.exe`를 둔다(LGPL 빌드 권장, 라이선스 고지 포함).
4. 모델은 빌드가 시작될 때 자동으로 준비된다(저장소 루트의 `models/`, 약 2.1GB).
   음성 인식 base·small·medium(앱이 권장할 수 있는 모델 전부)과 EasyOCR 한국어·영어 모델이다.
   필요한 모델이 모두 있으면 건너뛰고, 없거나 일부만 있으면 없는 것만 내려받는다(첫 빌드는 인터넷이 필요).
   끝내 준비하지 못하면 빌드가 멈춘다. 미리 받아 두려면 `.venv\Scripts\python build\fetch_models.py`.
   배포본에는 모델이 들어 있어 첫 실행에 인터넷이 필요 없다. 설정 화면에서 모델 경로를 지정하면 그 경로가 우선한다.
5. CUDA 라이브러리(cuBLAS·cuDNN)는 동봉하지 않는다. GPU를 쓸 수 없으면 앱이 자동으로 CPU로 폴백한다.

## 빌드

```
.venv\Scripts\pyinstaller build\barrierfree.spec --noconfirm
```

결과: `dist\barrierfree\` 폴더(onedir). 폴더째 배포한다(약 3.3GB). 앱은 `_internal\ffmpeg\`(`paths.py`)를 먼저 찾고(`media/ffmpeg.py`의 `find_binary`), 없으면 PATH를 본다.

### Windows PC 없이 빌드하기 (GitHub Actions)

Mac에서는 `.exe`를 만들 수 없으므로 `.github/workflows/build-windows.yml`이 windows runner에서 같은 spec으로 빌드한다.
Actions 탭 → **Build Windows** → Run workflow(또는 `v*` 태그 푸시)로 실행하고, 끝나면 `barrierfree-windows` 아티팩트(zip, 3일 보관)를 받는다.

- ffmpeg는 BtbN의 win64 LGPL 빌드를 `latest`로 받는다(버전이 고정되지 않는다. 배포 전 버전과 라이선스 고지를 확인한다).
- 빌드 끝에 앱을 30초 띄워 종료되지 않고 `barrierfree.db`를 만드는지 확인한다(헤드리스 시작 확인뿐이다).
- 위 "확인 목록"(한글 경로 분석, 재생, 취소 등)은 CI가 대신하지 못한다. 받은 zip을 Windows PC에서 직접 확인해야 한다.
- `skip_models`를 켜면 모델 없는 개발용 빌드(약 1GB)가 나온다. 배포하지 않는다.

## macOS (`.app`)

검증 범위: Apple Silicon(arm64) Mac에서 `.app`이 빌드되고, 실행해 DB를 만드는 시작 단계까지 확인했다
(`PATH`에서 Homebrew를 뺀 상태에서도 번들 안 ffmpeg를 찾았다). 영상 분석, 재생, 모델 동봉 빌드는
이 절차로 확인하지 않았다.

### 준비

1. `brew install python@3.11 ffmpeg` 후 `python3.11 -m venv .venv`
2. `.venv/bin/pip install -r requirements.txt pyinstaller`
3. 저장소 루트에 `ffmpeg/` 폴더를 만들고 `ffmpeg`, `ffprobe` 실행 파일(확장자 없음)을 둔다.
   - 이 Mac에서만 쓰는 시험 빌드: `mkdir ffmpeg && cp "$(which ffmpeg)" "$(which ffprobe)" ffmpeg/`
   - Homebrew의 ffmpeg는 Homebrew 라이브러리(dylib)에 의존해서 **다른 Mac에서는 실행되지 않는다.**
     배포용은 정적 빌드(LGPL)를 구해 라이선스 고지와 함께 둔다.
4. 모델: Windows 절차 4번과 같다. 빌드가 자동으로 확인하고 없으면 내려받는다(약 2.1GB).
   패키징 구조만 확인하는 개발용 빌드는 `BARRIERFREE_SKIP_MODELS=1`로 모델 준비를 건너뛸 수 있다(배포 금지).

### 빌드와 실행

```
.venv/bin/pyinstaller build/barrierfree.spec --noconfirm
open dist/barrierfree.app
```

모델 없는 개발용 빌드는 약 1GB, 모델이 들어가면 약 3GB다.

### macOS 한계

- Intel Mac용과 Apple Silicon용은 각각 그 Mac에서 따로 빌드해야 한다.
- 서명·공증을 하지 않은 앱은 다른 Mac에서 "확인되지 않은 개발자" 경고로 막힌다(우클릭 → 열기로 우회).
  기관 배포용은 Apple Developer 계정으로 서명하고 공증해야 한다.
- GPU 가속이 없다. 장치 감지가 NVIDIA CUDA만 보기 때문에 Mac에서는 항상 CPU로 실행한다
  (음성 인식이 Windows+NVIDIA보다 느리다).
- `wmv`, `avi` 일부는 Mac의 코덱 제한으로 재생이 안 될 수 있다(분석 결과 확인은 가능, "재생 실패" 안내).

## 확인 목록 (배포 전, Windows 기준)

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

# EveryAI

영상의 음성, 자막, 화면 글자를 비교해 접근성 점검이 필요한 구간을 찾고 편집자가 결과를 검수할 수 있도록 돕는 데스크톱 도구입니다.

## 주요 기능

- 음성과 자막을 비교해 누락이나 불일치가 의심되는 구간을 표시합니다.
- 화면 글자와 음성 정보를 비교해 음성 안내가 필요한 구간을 표시합니다.
- 편집자가 영상 구간을 확인하고 점검 결과를 확정하거나 기각할 수 있습니다.
- 영상과 분석 결과는 로컬에서 처리합니다.

## 요구 사항

- Python 3.11
- 영상 분석을 위한 FFmpeg 및 ffprobe
- 첫 실행 시 음성 인식·OCR 모델을 내려받을 인터넷 연결 또는 별도 모델 파일

## 설치 및 실행

```bash
python3.11 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python -m barrierfree
```

Windows 패키징 절차와 미검증 항목은 [`build/README.md`](build/README.md)를 참고하세요.

## 개발

개발 도구와 커밋 hook은 아래처럼 설치합니다. Gitleaks 8.30.0도 별도로 설치해야 합니다.

```bash
python -m pip install -r requirements-dev.txt
pre-commit install
```

Gitleaks는 [공식 v8.30.0 릴리스](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.0)에서 설치하세요. 커밋 전 hook은 Ruff와 staged 변경의 비밀정보 패턴을 검사합니다.

```bash
ruff check .
mypy src
pytest
```

GitHub Actions는 `main` 브랜치 push와 pull request에서 위 세 검사를 실행합니다.

## 문서

포함 문서와 보관 기준은 [`docs/README.md`](docs/README.md)에서 확인할 수 있습니다. 제출용 PDF·DOCX와 외부에서 받은 자료는 저장소에 포함하지 않습니다.

## 라이선스

현재 비공개 저장소 준비 단계이며, 공개 전 라이선스를 결정할 예정입니다.

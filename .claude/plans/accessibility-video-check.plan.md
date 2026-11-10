# Plan: 영상의 빈틈을 찾는 AI 배리어프리 점검 도구 (Python + PyQt6, Windows)

**Source PRD**: plan.md (`.prd.md` 형식이 아니라 참고 모드로 작성. PRD 마일스톤 표는 갱신하지 않음)
**Selected Milestone**: M1~M3 전체 (자막 점검 → 화면 전용 정보 점검 → 사람 확인과 결과 정리)
**Complexity**: Large
**Status**: APPROVED (결정 4건 반영). 구현은 아직 시작하지 않음.

## Summary
공공기관 영상 편집자가 영상(최대 20분, 1건씩)과 자막 파일을 넣으면, ffmpeg로 오디오·프레임을 추출하고 faster-whisper(받아쓰기)와 easyocr(화면 글자)로 음성·화면 글자를 얻어 자막과 교차 비교한다. 누락·불일치·화면 전용 정보를 구간별 목록으로 보여 주고, 편집자가 확정/기각한다. 자막 파일이 없으면 OCR로 영상 속 자막을 추출해 자막 파일을 만든 뒤 같은 절차를 진행한다.

## 확정된 결정
| # | 결정 | 내용 |
|---|---|---|
| 1 | Python 버전 | 3.11로 가상환경 재생성 (현재 .venv는 3.9.6) |
| 2 | 자막 파일이 없을 때 | OCR로 자막을 추출해 SRT를 생성한 뒤 M1·M2 절차를 이어서 진행 |
| 3 | 오프라인/모델 동봉 | 이번 계획에서는 고려하지 않음 (후순위) |
| 4 | 계획 저장 | 이 파일로 저장 |

## 요구사항 정리
- 입력: 영상 1건(≤20분), 자막 파일(SRT 우선, 선택)
- 처리: 음성(받아쓰기) / 자막 / 화면 글자(OCR)를 추출해 교차 비교
  - M1: 음성↔자막 누락·불일치
  - M2: 화면에만 있고 음성에 없는 중요 정보
  - M3: 구간별 목록, 영상 이동, 확정/기각, 요약
- 제외: 자막 편집, 화면해설(TTS), 소리 정보 제안, 장면 분석, 다건 처리

## Patterns to Mirror
| Category | Source | Pattern |
|---|---|---|
| Naming | — | 저장소가 비어 있어 기존 패턴 없음. PEP 8, snake_case, 타입 힌트, `dataclass` 사용 |
| Errors | — | 기존 패턴 없음. 도메인 예외(`VideoTooLongError`, `FfmpegNotFoundError`)를 정의하고 UI에서 메시지로 변환 |
| Tests | — | 기존 패턴 없음. `pytest`, 엔진은 주입식(가짜 STT/OCR)으로 테스트 |

## Files to Change
| File | Action | Why |
|---|---|---|
| `src/barrierfree/models.py` | CREATE | `SpeechSegment`, `SubtitleCue`, `ScreenText`, `Issue`, `IssueKind`, `Status` |
| `src/barrierfree/config.py` | CREATE | 임계값을 모은 `AnalysisConfig` |
| `src/barrierfree/media/ffmpeg.py` | CREATE | ffmpeg 탐색, 길이 확인(20분 제한), 오디오·프레임 추출 |
| `src/barrierfree/media/subtitles.py` | CREATE | SRT 파서와 SRT 작성기 (OCR 생성 자막 저장용) |
| `src/barrierfree/engines/stt.py` | CREATE | faster-whisper 래퍼 (VAD, 한국어) |
| `src/barrierfree/engines/ocr.py` | CREATE | easyocr 래퍼, 프레임 중복 제거, 표시 구간 병합, 자막 영역/일반 영역 분리 |
| `src/barrierfree/analysis/normalize.py` | CREATE | 텍스트 정규화, 한국어 수사→숫자 변환 |
| `src/barrierfree/analysis/entities.py` | CREATE | 중요 정보(날짜·시각·장소·전화·금액) 추출 |
| `src/barrierfree/analysis/subtitle_extract.py` | CREATE | 자막 파일이 없을 때 OCR 자막 영역 결과로 `SubtitleCue` 목록 생성 |
| `src/barrierfree/analysis/subtitle_check.py` | CREATE | M1 판정 |
| `src/barrierfree/analysis/screen_check.py` | CREATE | M2 판정 |
| `src/barrierfree/pipeline.py` | CREATE | 단계 실행, 진행률 콜백, 취소 |
| `src/barrierfree/storage.py` | CREATE | 세션 JSON, CSV 내보내기, 검수 소요 시간 기록 |
| `src/barrierfree/ui/main_window.py` | CREATE | 플레이어, 지적 목록, 상세 패널, 확정/기각, 요약 |
| `src/barrierfree/ui/issue_model.py` | CREATE | `QAbstractTableModel` (정렬·필터) |
| `src/barrierfree/ui/worker.py` | CREATE | 분석을 `QThread`에서 실행, 진행률·취소 신호 |
| `src/barrierfree/__main__.py` | CREATE | 앱 진입점 |
| `main.py` | UPDATE | PyCharm 샘플 제거, 진입점 호출 |
| `requirements.txt` | CREATE | 의존성 고정 (Python 3.11) |
| `tests/**`, `tests/fixtures/**` | CREATE | 단위·통합 테스트, 합성 영상/SRT |
| `build/barrierfree.spec`, `build/README.md` | CREATE | PyInstaller 설정과 Windows 빌드 절차 |

## 판정 로직

### 공통 정규화
NFKC, 소문자화, 공백·구두점 제거. 날짜·시각·전화번호·금액 문맥에서 한국어 수사를 숫자로 변환("삼월 십오일"→"3월15일", "세 시 반"→"3시30분").

### 중요 정보 (규칙 기반, LLM 미사용)
1. 날짜·요일·시각·기간
2. 장소(`시/구/동/로/길/층/호/센터/관/청/역` 접미 패턴)
3. 전화번호·URL
4. 금액·인원

### 자막 소스 결정 (결정 2)
- 자막 파일이 있으면 그것을 사용한다.
- 없으면 프레임 하단 영역(초기값: 프레임 높이의 하단 25%)의 OCR 결과를 자막 후보로 보고, 연속 프레임의 같은 글자를 하나의 구간으로 병합해 `SubtitleCue`와 SRT 파일을 생성한다. 사용자에게 "OCR로 추출한 자막으로 점검합니다"라고 표시한다.
- 같은 OCR 결과를 M2에서도 쓰므로 OCR은 영상당 한 번만 실행한다. 하단 영역 결과는 자막 후보, 나머지 영역은 화면 글자로 분리한다.
- 주의: OCR 생성 자막은 OCR 오류가 섞여 M1 정밀도가 떨어진다. 이 모드의 결과에는 낮은 신뢰 표시를 한다.

### M1: 음성↔자막
1. 후보 자막: `[시작−2.0s, 끝+2.0s]`와 겹치는 자막. 겹침이 없으면 ±6s로 한 번 넓힌다.
2. 커버리지: `difflib.SequenceMatcher`로 일치 글자 수 / 음성 글자 수.
3. 분류: 0.3 미만 누락, 0.3~0.8 불일치, 0.8 이상 정상.
4. 중요 정보 검사: 음성의 중요 정보가 후보 자막에 없거나 값이 다르면 커버리지와 무관하게 불일치(높음).
5. 노이즈 억제: `no_speech_prob`가 높거나 3자 미만인 구간 제외.

### M2: 화면 글자↔음성
1. 1fps 샘플링, 가로 1280px로 축소, 직전 OCR 프레임과 차이가 작으면 건너뜀, 신뢰도 0.5 미만 제외.
2. 연속 프레임의 유사도 0.85 이상 글자를 `ScreenText(text, t_start, t_end)`로 병합.
3. 제외: 자막과 일치하는 글자, 영상 길이 70% 이상 계속 보이는 글자(로고·워터마크), 중요 정보가 없는 글자.
4. 음성 대조(2단계 창):
   - 가까운 창 `[t_start−5s, t_end+5s]`에 있으면 정상
   - 영상 다른 곳에서만 말했으면 낮은 심각도 "다른 시점에 언급됨"
   - 영상 전체에서 말하지 않았으면 화면 전용 정보(높음)
5. 화면 글자와 음성의 값이 정규화 후 다르면 불일치로 지적.

### 임계값
모두 `AnalysisConfig`에 둔다. 위 숫자는 시작값이며 합성 샘플과 실영상으로 조정한다.

## Tasks (TDD: 테스트를 먼저 작성)

### Phase 0. 기반
- **Task 1**: Python 3.11 가상환경 재생성, 패키지 골격, 의존성 고정
  - Validate: `python -c "import PyQt6, faster_whisper, easyocr"`
- **Task 2**: `models.py`, `config.py`
  - Validate: `pytest tests/test_models.py`
- **Task 3**: `ffmpeg.py` (20분 초과는 `VideoTooLongError`로 거부)
  - Validate: `pytest tests/test_ffmpeg.py` (합성 영상)

### Phase 1. M1 자막 점검
- **Task 4**: `subtitles.py` 파서와 작성기 (BOM, CRLF, 깨진 줄 케이스)
- **Task 5**: `normalize.py` (한국어 수사 표를 테스트로 먼저 작성)
- **Task 6**: `entities.py`
- **Task 7**: `stt.py` (모델 주입식, 테스트는 가짜 STT 사용)
- **Task 8**: `subtitle_check.py` (정상, 누락, 일부 누락, 숫자 오류, 시간 어긋남 ±3s 테스트)

### Phase 2. 화면 점검과 자막 생성
- **Task 9**: `ocr.py` (프레임 중복 제거, 구간 병합, 영역 분리를 가짜 OCR 결과로 테스트)
- **Task 10**: `subtitle_extract.py` (OCR 결과→`SubtitleCue`/SRT, 왕복 테스트)
- **Task 11**: `screen_check.py` (정상, 가까운 창, 먼 창, 화면 전용, 자막과 겹침, 워터마크)

### Phase 3. 통합과 GUI (M3)
- **Task 12**: `pipeline.py` (자막 파일 유무 분기, 진행률, 취소)
- **Task 13**: `storage.py` (지적 목록, 확정/기각 상태, 검수 소요 시간, CSV)
- **Task 14**: UI (파일 선택 → 분석 진행률/취소 → 목록 → 선택 시 영상 이동 → 확정/기각 → 요약). `pytest-qt`로 모델·상태 전환 테스트

### Phase 4. 패키징 (Windows PC에서 수행)
- **Task 15**: PyInstaller `onedir` 빌드, ffmpeg 동봉. 깨끗한 Windows에서 실행 확인. 모델 동봉은 이번 범위 밖(결정 3).

## Validation
```bash
pytest -q --cov=src/barrierfree          # 엔진 로직 커버리지 80%+
ruff check src tests && mypy src
python -m barrierfree                     # GUI 수동 확인
```
PRD 지표(탐지율 80%, 정밀도 60%) 측정에는 정답을 표시한 평가용 영상이 필요하다. 영상과 정답 준비는 팀 작업이며, 필요하면 `tests/eval/` 채점 스크립트를 추가한다.

## Risks
| Risk | Likelihood | Mitigation |
|---|---|---|
| OCR 속도: easyocr는 CPU에서 프레임당 수 초라 20분 영상이 수십 분 걸릴 수 있음 | 높음 | 프레임 차이 기반 건너뛰기, 축소, 샘플 간격 조정. 초기에 실측 후 확정 |
| 자막 없을 때 OCR 생성 자막의 오류가 M1 정밀도를 떨어뜨림 | 높음 | 낮은 신뢰 표시, 하단 영역 비율을 설정으로 조정, 사람 확인 단계 |
| 자막 영역과 일반 화면 글자 영역 분리 오류(자막 위치가 영상마다 다름) | 중 | 영역 비율을 설정으로 노출, 합성/실영상 샘플로 확인 |
| 한국어 수사·고유어 시각 변환 누락 | 높음 | 테스트 주도로 변환 표 확장, 못 맞춘 경우는 지적 쪽으로 기울여 사람이 기각 |
| whisper 환각(음악·무음에서 없는 문장) | 중 | VAD, `no_speech_prob` 필터 |
| 설치 용량(torch 포함 수 GB) | 중 | `onedir` 빌드, CPU 전용 torch |
| 개발 환경이 macOS라 Windows 동작·빌드 검증 불가, ffmpeg 미설치 | 확정 | 엔진은 Mac에서 테스트(ffmpeg 설치 필요), Phase 4와 Windows 전용 검증은 Windows PC에서 수행 |
| 거짓 지적이 많으면 편집자의 신뢰 하락 | 중 | 심각도 구분, 기각이 쉬운 UI, 임계값 조정 |
| 오프라인/방화벽 환경에서 모델 다운로드 실패 | 미정 | 결정 3에 따라 이번 범위에서 제외. 배포 환경 확인 후 재검토 |

## Acceptance
- [ ] 모든 Task 완료
- [ ] `pytest`, `ruff`, `mypy` 통과
- [ ] 자막 파일 있음/없음 두 경로 모두 합성 영상으로 끝까지 동작
- [ ] 20분 초과 영상은 명확한 메시지로 거부
- [ ] Windows PC에서 실행 확인
- [ ] 평가용 영상으로 PRD 지표(탐지율, 정밀도, 검수 시간) 측정

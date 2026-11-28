# 기능 정의서 (현재 구현 기준)

## 1. 문서 개요

| 항목 | 내용 |
|---|---|
| 목적 | `src/barrierfree`에 **현재 구현되어 있는 기능**을 기능 단위로 정의한다. 개발·검수·인수인계의 기준으로 쓴다. |
| 기준 커밋 | `c4b5a24` (master, 2026-11-16) |
| 근거 | **소스코드 구현만**이다. 테스트, CI(workflow), 빌드 파일은 근거로 쓰지 않았다. |
| 범위 | 구현된 기능만 적는다. 기획 문서(`ui-spec.md` 등)와 비교하지 않으며, 코드에 없는 것은 추측해 쓰지 않는다. |
| 기능 ID | `영역-번호`. REG 등록, ANL 분석, RUL 분석 규칙, REV 검수, SET 설정, DAT 저장, SYS 시스템 |

각 기능은 같은 서식으로 쓴다: **설명 / 입력 / 동작 규칙 / 출력 / 예외·실패 처리 / 구현 위치**. 구현 위치는 저장소 기준 상대 경로다(`src/barrierfree/` 생략).

## 2. 시스템 개요

공공기관 영상 편집자가 영상과 자막을 등록하면, 시스템이 음성·자막·화면 글자를 비교해 **검토가 필요한 구간**을 찾고, 편집자가 이를 확정·기각한 뒤 CSV로 내보낸다. 모든 처리는 로컬에서 이루어진다(영상 1건씩, 최대 20분).

```
[등록]  영상 + 자막(선택)
   │  REG-01~04
   ▼
[분석]  영상 준비 → 음성 인식 → 화면 문자 인식 → 자막 비교(M1) → 화면 정보 비교(M2, M2-2) → 결과 저장
   │  ANL-01~14 (규칙: RUL-01~03)         ※ 자막 파일이 없으면 자막 영역을 추정하고 편집자에게 확인받는다(ANL-05)
   ▼
[검수]  문제 목록 → 영상 구간 확인 → 확정 / 기각 → CSV 내보내기
      REV-01~07
```

**모듈 지도**

| 위치 | 역할 |
|---|---|
| `ui/` | 화면(등록·분석 중·검수), 설정 대화상자, 영상 플레이어, 백그라운드 실행 |
| `pipeline.py` | 분석 파이프라인(영상 준비~결과 저장 6단계와 모델 준비 이벤트) 조율, 세션 기록, 캐시, 오류 변환 |
| `analysis/` | 판정 규칙: 자막 비교, 화면 정보 비교, 화면 의존 표현, 자막 영역 추정, 정규화, 핵심 정보 추출 |
| `engines/` | 음성 인식(faster-whisper), 화면 글자 인식(EasyOCR), 장치 감지·모델 추천 |
| `media/` | ffmpeg 래퍼(검증·오디오 추출·프레임), 자막 파일 읽기·쓰기 |
| `storage/` | SQLite 저장소, 파일 캐시, CSV 내보내기 |
| `app.py`, `settings.py`, `paths.py` | 앱 조립, 사용자 설정, 번들 자원 위치 |
| `review_state.py`, `dev_artifacts.py`, `cancel.py` | 검수 상태, 개발용 산출물, 취소 토큰 |
| `config.py`, `models.py`, `validation.py` | 분석 설정 상수, 도메인 모델, 외부값 검증 |

## 3. 기능 목록

| ID | 기능 | 한 줄 설명 | 구현 위치 |
|---|---|---|---|
| REG-01 | 영상 파일 선택·검증 | 영상을 고르면 형식·길이·재생 가능 여부를 바로 검증한다 | `ui/register_page.py`, `media/ffmpeg.py` |
| REG-02 | 자막 파일 선택·검증 | SRT·SMI 자막을 불러와 인코딩과 형식을 검증한다 | `ui/register_page.py`, `media/subtitles.py` |
| REG-03 | 등록 상태 관리 | 영상·자막 삭제, 분석 시작 가능 조건을 관리한다 | `ui/register_page.py` |
| REG-04 | 분석 설정 요약 표시 | 감지 장치·사용 모델·자막 분석 방식을 보여 준다 | `ui/register_page.py` |
| ANL-01 | 분석 실행 | 영상 1건을 단계별로 분석하고 세션을 기록한다 | `pipeline.py` |
| ANL-02 | 영상 준비 | 영상 해시와 인식용 오디오(WAV)를 만든다 | `pipeline.py`, `media/ffmpeg.py` |
| ANL-03 | 음성 인식(STT) | 한국어 음성을 구간별 신뢰도와 함께 받아쓴다 | `engines/stt.py` |
| ANL-04 | 화면 글자 인식(OCR) | 화면 글자를 읽어 연속 프레임을 한 건으로 병합한다 | `engines/ocr.py` |
| ANL-05 | 자막 영역 추정과 확인 | 자막 파일이 없을 때 영상 하단에서 자막을 추정하고 편집자가 확인한다 | `pipeline.py`, `analysis/subtitle_region.py`, `ui/region_dialog.py` |
| ANL-06 | OCR 자막 추출 | 확인된 영역의 글자를 자막으로 만들어 SRT로 저장한다 | `analysis/subtitle_extract.py` |
| ANL-07 | 자막 비교(M1) | 음성과 자막의 누락·불일치·표현 차이를 찾는다 | `analysis/subtitle_check.py` |
| ANL-08 | 화면 정보 비교(M2) | 화면 글자의 정보가 음성으로 전달되는지 본다 | `analysis/screen_check.py` |
| ANL-09 | 화면 의존 표현 탐지(M2-2) | "보시다시피" 같은 화면 의존 말투를 찾는다 | `analysis/screen_dependent.py` |
| ANL-10 | 결과 저장 | 지적을 한 트랜잭션으로 저장한다 | `pipeline.py`, `storage/repository.py` |
| ANL-11 | 인식 결과 캐시 재사용 | 같은 영상·설정이면 STT·OCR을 다시 하지 않는다 | `pipeline.py`, `storage/cache.py` |
| ANL-12 | 진행 이벤트와 취소 | 단계별 진행을 알리고 협조적으로 취소한다 | `pipeline.py`, `cancel.py` |
| ANL-13 | 오류 변환 | 예외를 오류 코드와 사용자 문구로 바꾼다 | `pipeline.py` |
| ANL-14 | 단계별 측정 기록 | 단계별 소요 시간·처리 건수·캐시 적중을 기록한다 | `pipeline.py`, `storage/repository.py` |
| RUL-01 | 텍스트 정규화 | 비교를 위해 수사·시각·기호를 통일한다 | `analysis/normalize.py` |
| RUL-02 | 핵심 정보 추출 | 날짜·시각·금액 등 11종을 규칙으로 뽑는다 | `analysis/entities.py` |
| RUL-03 | 핵심 정보 비교 | 두 문장의 핵심 정보를 짝지어 일치·불일치·누락으로 나눈다 | `analysis/entities.py` |
| REV-01 | 문제 목록과 필터 | 지적을 시간순으로 보여 주고 3종 필터를 제공한다 | `ui/review_page.py`, `review_state.py` |
| REV-02 | 문제 상세 표시 | 선택한 지적의 사유·음성·비교 대상·신뢰도를 보여 준다 | `ui/review_page.py` |
| REV-03 | 영상 플레이어 연동 | 지적을 고르면 해당 시점으로 이동하고 일시정지한다 | `ui/review_page.py`, `ui/player.py` |
| REV-04 | 확정·기각 처리 | 검수 상태를 바꾸고 즉시 저장한다 | `review_state.py`, `storage/repository.py` |
| REV-05 | 검수 요약과 안내문 | 처리 현황과 결과 해석 안내를 보여 준다 | `review_state.py`, `ui/review_page.py` |
| REV-06 | 검수 시간 측정 | 활동한 시간만 세어 저장한다 | `ui/review_timer.py`, `ui/main_window.py` |
| REV-07 | CSV 내보내기 | 검수 결과를 11열 CSV로 저장한다 | `storage/export.py`, `review_state.py` |
| SET-01 | 분석 설정 변경 | 모델·모델 경로·자막 영역 비율을 바꾼다 | `ui/settings_dialog.py` |
| SET-02 | 장치 감지와 모델 추천 | CPU·메모리·CUDA를 보고 음성 모델을 추천한다 | `engines/device.py` |
| SET-03 | 설정 저장·복원 | 설정을 JSON으로 저장하고 잘못된 값은 기본값으로 되돌린다 | `settings.py` |
| DAT-01 | 로컬 DB | SQLite 스키마와 마이그레이션을 관리한다 | `storage/db.py`, `storage/repository.py` |
| DAT-02 | 최근 작업 | 완료된 분석을 최근순으로 보여 주고 다시 연다 | `storage/repository.py`, `ui/main_window.py` |
| DAT-03 | 파일 캐시 | STT·OCR 원시 결과를 파일로 저장·복구한다 | `storage/cache.py` |
| SYS-01 | 앱 시작과 조립 | 저장소·엔진·화면을 연결해 앱을 띄운다 | `app.py`, `main.py` |
| SYS-02 | ffmpeg 탐색 | 번들·환경변수·PATH에서 ffmpeg를 찾고 시작 때 확인한다 | `media/ffmpeg.py`, `app.py` |
| SYS-03 | 메인 창 틀 | 헤더·사이드바·화면 전환·도움말·종료 보호 | `ui/main_window.py` |
| SYS-04 | 백그라운드 분석 실행 | 분석을 UI와 분리된 스레드에서 실행한다 | `ui/worker.py` |
| SYS-05 | 개발용 산출물 저장 | 환경변수로 켜면 MP3·캡처·SRT를 남긴다 | `dev_artifacts.py` |
| SYS-06 | 번들 자원 위치 | 배포본에 동봉된 모델 폴더를 찾는다 | `paths.py` |

---

## 4. 기능 상세

### 4.1 등록 (REG)

등록 화면(`RegisterPage`)은 내부에 "빈 화면"과 "등록 완료 화면" 둘을 두고, 영상이 있으면 후자를 보여 준다.

#### REG-01 영상 파일 선택·검증
- **설명**: 영상 파일을 고르면 바로 검증하고, 통과하면 등록한다.
- **입력**: 파일 대화상자에서 고른 영상 경로(지원 형식 안내: MP4, MOV, MKV, AVI, WMV)
- **동작 규칙**
  - 확장자가 `.mp4 .mov .mkv .avi .wmv`가 아니면 거부한다(`AnalysisConfig.video_extensions`).
  - ffprobe로 영상 스트림과 길이를 확인한다. 영상 스트림이 없거나 길이가 없거나 비정상이면 거부한다.
  - 길이가 1200초(20분)를 넘으면 거부한다(`max_video_seconds`).
  - 영상 회전 정보(display matrix의 `rotation`, 구형 `rotate` 태그)가 90°·270°이면 가로·세로를 맞바꿔 해상도를 읽는다.
  - 새 영상이 등록되면 이전에 고른 자막은 초기화된다.
- **출력**: 파일 이름, "해상도 · 길이 · 크기", "등록 완료" 표시(`VideoInfo`: 경로, 길이, 가로, 세로, fps, 영상·오디오 유무)
- **예외·실패 처리**: 실패하면 화면 위쪽에 "{사용자 문구} ({오류 코드})"를 보여 준다. **이미 등록된 정상 영상은 그대로 둔다.** 파일 없음·형식 미지원·읽기 불가는 ERR-01, 길이 초과는 ERR-02(부록 B). 등록 화면은 `explain_error`가 만드는 코드를 그대로 보여 주므로, ffprobe 실행 실패·시간 초과는 ERR-04, 예상 밖 예외는 ERR-99로 표시될 수 있다.
- **구현 위치**: `ui/register_page.py`(`RegisterPage._pick_video`, `set_video`), `media/ffmpeg.py`(`probe`, `validate_video`), `pipeline.py`(`explain_error`)

#### REG-02 자막 파일 선택·검증
- **설명**: 자막 파일(SRT·SMI)을 고르면 바로 읽어 형식과 인코딩을 검증한다. 자막은 선택 사항이다.
- **입력**: 자막 파일 경로(`.srt`, `.smi`, `.sami`)
- **동작 규칙**
  - 영상을 먼저 등록해야 한다. 없으면 "먼저 영상을 선택해 주세요."를 안내한다.
  - 확장자는 `.srt .smi .sami`만 허용한다. 파일 크기는 5MB(`MAX_SUBTITLE_BYTES`) 이하여야 한다.
  - **인코딩 판별 순서**: UTF-8 BOM → UTF-16 BOM → UTF-8(엄격) → CP949. 모두 실패하면 거부한다.
  - 텍스트 정리: ASS 태그(`{\...}`)와 HTML 태그를 지우고, HTML 엔티티를 풀고, nbsp를 공백으로 바꾸고, 연속 공백을 하나로 줄인다.
  - **SRT**: 빈 줄로 블록을 나누고 `-->`가 있는 줄을 타이밍으로 본다(구분자 `,`·`.` 모두 허용). 타이밍 줄이 없거나 형식이 맞지 않는 블록, 분·초가 59를 넘는 블록은 내용과 관계없이 건너뛰고 개수를 센다. 시각은 정상이지만 큐를 만들 수 없는 블록은 내용이 있을 때만 개수를 센다(내용이 빈 자막은 세지 않는다). 결과는 시작 시각순으로 정렬한다.
  - **SMI**: `<SYNC Start=ms>` 단위로 나누고 `<br>`은 공백으로 바꾼다. 여러 언어 클래스(`<P Class=...>`)가 있으면 텍스트가 처음 나타난 하나만 쓴다. 각 자막의 끝은 다음 SYNC 시각이고, 마지막 자막은 시작 + 2.0초다.
  - 유효한 자막이 0개면 거부한다.
- **출력**: `LoadedSubtitle(cues, encoding, format, skipped_blocks)`. 일부만 읽지 못한 경우 화면에 "N개 구간을 읽지 못했습니다(해당 구간은 점검에서 빠집니다)" 경고를 띄운다.
- **예외·실패 처리**: 불러오기에 실패하면 "{문구} ({코드})"를 보여 주고 **분석 시작을 막는다**(ERR-03). 영상 검증(REG-01)과 달리 **이미 등록되어 있던 정상 자막도 함께 해제**된다. 실패 상태는 자막을 삭제하거나 다시 고르면 풀린다.
- **참고**: SMI는 건너뛴 블록 수를 세지 않는다(`skipped_blocks`는 항상 0). 자막을 쓰는 기능은 SRT만 있다(ANL-06, SYS-05).
- **구현 위치**: `ui/register_page.py`(`_pick_subtitle`, `set_subtitle`), `media/subtitles.py`(`load_subtitle_file`, `decode_bytes`, `parse_srt`, `parse_smi`, `write_srt`)

#### REG-03 등록 상태 관리
- **설명**: 영상·자막 삭제와 "분석 시작" 가능 여부를 관리한다.
- **입력**: [영상 삭제], [자막 삭제], [분석 시작] 조작
- **동작 규칙**
  - [영상 삭제]: 영상과 자막을 초기화하고 빈 화면으로 돌아간다. 영상이 있고 작업 중이 아닐 때만 활성이다.
  - [자막 삭제]: 자막과 차단 상태를 초기화한다. 자막이 있거나 차단 상태일 때만 활성이다.
  - 안내 문구가 자막 유무에 따라 바뀐다. 자막이 있으면 "등록한 자막 파일을 기준으로…", 없으면 "영상에 포함된 자막을 자동 추출…"
  - **[분석 시작] 활성 조건**: 영상이 있고, 자막이 차단 상태가 아니며, 작업 중이 아닐 것.
- **출력**: `start_requested(영상 경로, 자막 경로 또는 None)` 신호
- **예외·실패 처리**: 없음(조건이 안 맞으면 버튼이 비활성이다)
- **구현 위치**: `ui/register_page.py`(`clear_video`, `clear_subtitle`, `_can_start`, `_start`, `_refresh`)

#### REG-04 분석 설정 요약 표시
- **설명**: 현재 분석에 쓰일 장치·모델·자막 분석 방식을 등록 화면에 보여 준다.
- **입력**: 장치 감지 결과, 저장된 설정
- **동작 규칙**: 세 가지 타일을 표시한다. ① 감지 장치("자동 감지 · GPU" 또는 "자동 감지 · CPU") ② 추천 모델 타일(표시 값은 설정에서 고른 모델, 없으면 추천 모델) ③ 자막 분석 방식(자막 파일이 있으면 "OCR + 등록 자막 비교", 없으면 "영상 속 자막 자동 추출"). [분석 설정] 버튼으로 설정 대화상자를 연다(SET-01). 설정을 저장하면 요약이 갱신된다. 작업 중에는 버튼이 비활성이다.
- **출력**: 화면 표시, `settings_requested` 신호
- **예외·실패 처리**: 없음
- **구현 위치**: `ui/register_page.py`(`set_settings_summary`), `settings.py`(`effective_model`)

---

### 4.2 분석 (ANL)

분석은 `AnalysisPipeline`이 조율한다. 아래 단계 순서로 진행하며, 각 단계가 끝날 때마다 취소 여부를 확인한다.

| 순서 | 단계 (`Stage`) | 기능 |
|---|---|---|
| 1 | MEDIA_PREPARE | 영상 준비(ANL-02) |
| 2 | STT | 음성 인식(ANL-03) |
| 3 | OCR | 화면 글자 인식(ANL-04), 자막 파일이 없으면 자막 영역 확인 포함(ANL-05, ANL-06) |
| 4 | SUBTITLE_CHECK | 자막 비교 M1(ANL-07) |
| 5 | SCREEN_CHECK | 화면 정보 비교 M2, 화면 의존 표현 M2-2(ANL-08, ANL-09) |
| 6 | RESULT_SAVE | 결과 저장(ANL-10) |
| (보조) | MODEL_PREPARE | 엔진이 `prepare()`를 가진 경우에 한해 모델 준비 이벤트를 보낸다 |

#### ANL-01 분석 실행
- **설명**: 영상 1건을 끝까지 분석하고, 찾아낸 지적 목록을 저장한다.
- **입력**: `run(video_path, subtitle_path=None, cancel=None)`
- **동작 규칙**
  - **입력 검증(세션을 만들기 전에 거부)**: ① `validate_video` ② 자막이 있으면 `load_subtitle_file` ③ 영상 해시(SHA-256) 계산. 여기서 실패하면 세션을 만들지 않는다.
  - **세션 생성**: 상태 PREPARING으로 만든다. 자막 출처는 자막 파일이 있으면 `FILE`, 없으면 `UNKNOWN`이다. 사용할 STT 모델명, OCR 엔진명, 장치(없으면 "cpu"), 분석 설정 스냅샷을 함께 기록한다.
  - 작업 폴더 `{데이터 폴더}/temp/{세션 ID}`를 만들고, 끝나면(성공·실패·취소 모두) 삭제한다.
  - **상태 전이**: PREPARING → ANALYZING(시작 시각은 처음 한 번만 기록) → [AWAITING_CONFIRMATION → ANALYZING] → COMPLETED. 취소는 CANCELLED, 실패는 FAILED(오류 코드 기록).
  - 지적 목록은 모든 판정이 끝난 뒤 한 트랜잭션으로 저장한다(ANL-10).
- **출력**: `PipelineResult(session_id, issue_count, subtitle_source, notices)`. `notices`는 사용자에게 알릴 안내 문구 목록이다(예: 오디오 없음, 일부 자막 블록 읽기 실패, 자막 없음).
- **예외·실패 처리**: 실패하면 `PipelineError`(오류 코드, 사용자 문구, 실패 단계, 세션 ID), 취소하면 `CancelledError`가 발생한다(ANL-12, ANL-13).
- **참고(코드 기준)**: `create_session` 자체가 SQLite 오류로 실패하면 `PipelineError`로 바뀌지 않고 원래 예외가 올라간다. `KeyboardInterrupt` 같은 `BaseException`은 세션을 종료 상태로 기록하지 않는다.
- **구현 위치**: `pipeline.py`(`AnalysisPipeline.run`, `_analyze`, `_set_terminal`)

#### ANL-02 영상 준비
- **설명**: 분석에 쓸 인식용 오디오를 만들고 영상의 지문(해시)을 구한다.
- **입력**: 영상 경로
- **동작 규칙**
  - 영상 파일의 SHA-256 해시를 구한다(캐시 키에 쓰인다).
  - 오디오 트랙이 있으면 `-vn -ac 1 -ar 16000 -f wav`로 16kHz mono WAV를 `temp/{세션 ID}/audio.wav`에 추출한다(제한 시간 30분).
  - 오디오 트랙이 없으면 추출과 음성 인식을 건너뛰고 안내 문구 "영상에 오디오 트랙이 없어 음성 인식을 건너뛰었습니다."를 남긴다.
- **출력**: 오디오 WAV 경로(없으면 None), 영상 해시
- **예외·실패 처리**: 해시 계산 중 `OSError`는 `VideoReadError`로 바꿔 ERR-01을 낸다. ffmpeg 실패는 `FfmpegError`(ERR-04).
- **구현 위치**: `pipeline.py`(`_analyze`), `media/ffmpeg.py`(`extract_audio`, `probe`), `storage/cache.py`(`hash_file`)

#### ANL-03 음성 인식(STT)
- **설명**: faster-whisper로 한국어 음성을 받아쓰고, 구간마다 신뢰도를 보존한다.
- **입력**: 오디오 WAV 경로, 취소 토큰
- **동작 규칙**
  - 엔진 `FasterWhisperEngine`(`engine_name="faster-whisper"`). 모델은 처음 쓸 때 지연 로드한다(`WhisperModel(model, device, compute_type, download_root=모델 폴더)`).
  - 전사 옵션: `language="ko"`, `vad_filter=True`(무음 구간 제거), `condition_on_previous_text=False`(무음 구간 환각 반복 방지).
  - 텍스트를 다듬어 빈 구간은 버린다. 구간마다 `avg_logprob`, `no_speech_prob`를 함께 남긴다.
  - 구간 하나를 처리할 때마다 취소 여부를 확인한다.
  - **CUDA 폴백**: 오류 메시지에 `cuda`, `cublas`, `cudnn`, `cudart` 중 하나가 있으면(대소문자 무시) CUDA 오류로 본다. 모델 로드 중이면 device가 cuda일 때 CPU·`int8`로 바꿔 한 번 더 로드한다. 전사 중이면 취소 상태가 아니고 device가 cuda일 때 CPU로 바꿔 처음부터 한 번 더 전사한다. 폴백하면 `fell_back_to_cpu=True`로 표시한다.
  - 사용할 모델·장치는 SET-02, SET-01이 정한다.
  - **캐시 직렬화**: 구간 목록을 JSON 호환 dict로 저장·복원한다. 복원 시 형식 오류, 시각이 유한수가 아님, `start<0` 또는 `end<start`, text가 문자열이 아님, `no_speech_prob`가 0~1을 벗어나면 `ValueError`로 거부한다(ANL-11).
- **출력**: `list[SpeechSegment(start_s, end_s, text, avg_logprob, no_speech_prob)]`
- **예외·실패 처리**: 오디오 파일이 없으면 `SttError`, 모델 로드 실패는 `SttModelError`("음성 인식 모델을 불러올 수 없습니다"), 전사 실패는 `SttError`("음성 인식에 실패했습니다"). 취소 상태에서 난 예외는 변환하지 않고 그대로 올린다. 오류 코드는 모델 오류 ERR-06, 인식 오류 ERR-07이다.
- **구현 위치**: `engines/stt.py`(`FasterWhisperEngine.transcribe`, `prepare`, `segments_to_payload`, `segments_from_payload`), `pipeline.py`(`_analyze`)

#### ANL-04 화면 글자 인식(OCR)
- **설명**: 영상의 화면 글자를 읽고, 이어진 프레임의 같은 글자를 한 건으로 병합한다.
- **입력**: 프레임 스트림(영상을 `screen_ocr_fps=1.0`으로 샘플링하고 가로 `ocr_max_width=1280` 이하로 줄임)
- **동작 규칙**
  - **프레임 공급**: ffmpeg가 임시 이미지 파일 없이 stdout 파이프로 BGR 프레임을 넘긴다(`iter_frames`). 자막 영역 스캔에는 하단 `crop`을 함께 적용한다.
  - **엔진** `EasyOcrEngine`(`engine_name="easyocr"`, 언어 `ko`·`en`, GPU는 장치가 cuda일 때만). 읽은 결과의 빈 텍스트는 버리고 박스는 0~1로 정규화한 (x, y, w, h), 신뢰도는 0~1로 제한한다.
  - **프레임 건너뛰기**: 프레임을 회색 64×36으로 줄여 6행×8열의 칸으로 나누고, 칸마다 평균 절대 차이(0~1)를 구해 **가장 큰 값**을 프레임 차이로 쓴다(화면 전체의 평균은 자막처럼 작은 영역의 변화를 놓친다). **마지막으로 OCR을 실행한 프레임**과의 차이가 `frame_diff_skip_threshold=0.02` 이상일 때만 엔진을 호출하고, 아니면 직전 결과를 재사용한다. 첫 프레임은 항상 엔진을 호출한다.
  - **잡음 제거**: 공백을 지운 길이가 `ocr_min_text_chars=2` 미만이거나 문자(`\w`)가 없으면 버린다.
  - **연속 프레임 병합**: 열린 트랙과 다음 조건을 모두 만족하면 이어 붙인다. ① 공백 샘플 수 ≤ `ocr_max_gap_samples=1` ② 중심 거리가 x, y 모두 0.25 이하 ③ 숫자가 다른 숫자로 바뀐 경우가 아님(예: 3명 → 5명은 별개) ④ `SequenceMatcher` 유사도가 `screen_merge_similarity=0.85` 이상이면서 가장 높은 트랙. 한 프레임에서 한 트랙에는 글자 박스 하나만 붙는다. 병합된 글자의 대표 표기는 신뢰도가 가장 높게 읽힌 것이고(표기를 바꿀 때 박스도 함께 바뀐다), 구간 끝은 마지막 관측 시각 + 샘플 간격이다.
  - **분류**: 병합된 글자의 평균 신뢰도가 `ocr_confidence_min=0.5` 이상이면 확실한 글자(`texts`), 미만이면 불확실한 글자(`uncertain`)로 나눈다. 정렬 기준은 (시작, y, x)다.
  - **캐시 직렬화**: 관측값을 저장·복원한다. 복원 시 t < 0, 신뢰도가 0~1 밖, 박스 요소가 4개가 아님, 좌표가 0~1 밖이거나 `x+w`·`y+h`가 1을 넘으면 거부한다(ANL-11).
- **출력**: `OcrResult(texts, uncertain)`(각각 `ScreenText(text, start_s, end_s, confidence, box)` 목록)
- **예외·실패 처리**: 모델 로드 실패는 `OcrModelError`(ERR-06), 인식 실패는 `OcrError`(ERR-08). 프레임마다 취소 여부를 확인한다. OCR은 GPU 실패 시 CPU로 바꾸는 폴백이 없다(STT에만 있음).
- **구현 위치**: `engines/ocr.py`(`EasyOcrEngine`, `run_ocr`, `frame_difference`, `merge_observations`, `observations_to_payload`, `observations_from_payload`), `media/ffmpeg.py`(`iter_frames`, `scaled_size`), `pipeline.py`(`_ocr_pass`)

#### ANL-05 자막 영역 추정과 편집자 확인
- **설명**: 자막 파일이 없을 때, 영상 하단의 글자가 자막인지 추정하고 편집자에게 확인받는다.
- **입력**: 영상, 음성 인식 결과, 자막 영역 비율(기본 0.25)
- **동작 규칙**
  - **자막 스캔**: 하단 띠(`subtitle_region_ratio`)를 `subtitle_ocr_fps=2.0`으로 OCR하고 좌표를 전체 프레임 기준으로 되돌린다(`_remap_strip`). 병합 결과 중 신뢰도 0.5 이상인 글자(`texts`)만 영역 추정과 자막 큐 추출에 쓴다. 불확실한 글자(`uncertain`)는 점수에도 큐에도 들어가지 않는다.
  - **자막 영역 추정**(`estimate_subtitle_region`): 영역은 화면 하단 `(0, 1-ratio, 1, ratio)`다. 글자 박스의 **중심**이 영역 안이면 영역 안 글자로 본다(박스가 없는 글자는 영역 밖). 점수는 다음과 같다.
    - `overlap`: 영역 안 글자의 표시 시간 중 발화 구간과 겹친 비율
    - `agreement`: 영역 글자의 2-gram 중 음성에도 나타나는 비율
    - `frequency = min(1, 분당 변경 수 / subtitle_changes_for_full_score(10.0))`
    - `score = 0.4·overlap + 0.3·frequency + 0.3·agreement`
    - **자막 트랙 판정**(모두 만족): 글자 수 ≥ `subtitle_min_texts=3`, 분당 변경 수 ≥ `subtitle_min_changes_per_minute=4.0`, 점수 ≥ `subtitle_region_score_min=0.5`. 영역 안 글자가 없으면 판정 False, 점수 0이다. 줄 단위로 음성과 맞춰 보는 판정은 하지 않는다(순환 판정 방지).
  - **편집자 확인**: 세션 상태를 AWAITING_CONFIRMATION으로 바꾸고 `confirm_region(추정 결과)`를 부른다. 앱에서는 자막 영역 확인 대화상자가 뜬다(아래). 콜백이 없으면 추정이 자막이면 CONFIRMED, 아니면 NONE으로 처리한다. 결정 후 상태를 ANALYZING으로 되돌린다.
  - **결정에 따른 분기**

    | 결정 | 처리 |
    |---|---|
    | NONE(영상에 자막 없음) | 자막 출처 NONE으로 저장하고 "자막이 없는 영상입니다…" 안내를 남긴다. 자막 큐는 비운다. |
    | CONFIRMED·ADJUSTED | 영역은 결정에서 준 사각형(없으면 추정 영역). 영역을 넓혔다면(`1 − rect.y`가 기존 비율보다 큼) 넓은 띠로 OCR을 한 번 더 한다. |
    | 영역 안 큐가 0개 | 자막 출처 NONE으로 저장하고 "확인한 자막 영역에서 글자를 찾지 못해…" 안내를 남긴다. |
    | 큐 추출 성공 | ANL-06으로 SRT를 저장하고 자막 출처 OCR로 기록한다. |

  - **자막 영역 확인 대화상자**(`RegionDialog`): 판정 요약("자막 파일이 없어 영상 하단을 읽었습니다. 이 영역은 {자막으로 추정되었습니다 | 자막이 아닌 것으로 추정되었습니다}(추정). 영역에서 N건의 글자가 읽혔습니다.")과 읽은 글자 예시(없으면 "영역에서 읽힌 글자가 없습니다.")를 보여 준다. 라디오 버튼 선택은 **맞음** → `CONFIRMED`(추정 영역 사용), **영역 조정** → `ADJUSTED`(슬라이더 10~50%, "영상 하단 N%", 이때만 활성), **영상에 자막 없음** → `NONE`. 처음 선택은 추정 결과에 따라 정해진다. 버튼은 [확인] 하나뿐이고, 창을 닫아도 현재 선택값이 결정으로 쓰인다. 워커 스레드는 대화상자 답이 올 때까지 0.1초 간격으로 기다리며, 기다리는 중 취소되면 풀린다(SYS-04).
- **출력**: (자막 출처, 자막 큐, 자막 영역, 캐시 적중 목록). 자막 영역과 편집자의 결정은 **OCR 자막 추출에 성공했을 때만** 세션 기록(`subtitle_region_json`의 `decision`, `normalized_rect`)에 남는다. "영상에 자막 없음"을 고른 경우와 영역 안 큐가 0개인 경우에는 좌표 없이 `{"decision":"NONE"}`만 저장된다.
- **예외·실패 처리**: 스캔 중 오류는 OCR 오류 규칙(ANL-04)을 따른다. 편집자가 확인하는 동안 취소하면 `CancelledError`가 발생한다.
- **구현 위치**: `pipeline.py`(`_resolve_ocr_subtitles`, `_remap_strip`, `RegionAction`, `RegionDecision`), `analysis/subtitle_region.py`(`estimate_subtitle_region`, `region_rect`, `in_rect`, `RegionEstimate`), `ui/region_dialog.py`(`RegionDialog`)

#### ANL-06 OCR 자막 추출
- **설명**: 확인된 자막 영역의 화면 글자를 자막 큐로 만들어 SRT 파일로 저장한다. 영역 밖 글자는 화면 정보 비교(ANL-08)의 대상으로 남긴다.
- **입력**: 자막 영역 안 화면 글자 목록(`ScreenText`, 신뢰도 0.5 이상인 확실한 글자만)
- **동작 규칙**
  - `split_by_region`: 입력 순서를 유지하며 (영역 안, 영역 밖)으로 나눈다.
  - `extract_cues`: 같은 시점에 위아래로 쌓인 줄을 한 자막으로 합친다. 합치는 조건은 짧은 쪽 표시 시간의 0.6 이상이 겹치고 위아래 간격이 프레임 높이의 0~0.08일 때이며, 묶음의 어느 줄에든 붙으면 합친다. 합친 자막의 시각은 그룹의 최소 시작~최대 끝, 텍스트는 위에서 아래 순서로 공백 연결한다. 결과는 시간순이다.
  - 큐가 있으면 `{데이터 폴더}/artifacts/subtitles/{세션 ID}.srt`에 저장하고(`generated_srt_path`는 데이터 폴더 기준 상대 경로) 자막 출처를 OCR로 기록한다. "OCR로 추출한 자막으로 점검합니다…" 안내를 남긴다.
  - 자막 출처가 OCR이고 영역이 있으면, 영역 안 글자는 화면 정보 비교에서 뺀다(ANL-08).
- **출력**: `list[SubtitleCue]`, 저장된 SRT 파일
- **예외·실패 처리**: 파일 저장 실패(`OSError`)는 ERR-09로 변환된다.
- **구현 위치**: `analysis/subtitle_extract.py`(`split_by_region`, `extract_cues`), `media/subtitles.py`(`write_srt`), `pipeline.py`(`_resolve_ocr_subtitles`)

#### ANL-07 자막 비교(M1)
- **설명**: 음성으로 말한 내용이 자막에 빠졌거나 다르게 표시됐는지 찾는다.
- **입력**: 음성 구간 목록, 자막 큐 목록, 분석 설정, 자막이 OCR로 만든 것인지 여부
- **동작 규칙**
  - **제외**: 정규화한 음성 텍스트 길이가 `min_speech_chars=3` 미만이거나 `no_speech_prob > no_speech_prob_max=0.6`이면 건너뛴다.
  - **후보 자막**: 음성 구간을 ±`candidate_window_s=2.0`초 넓혀 겹치는 자막을 찾고, 없으면 ±`candidate_window_wide_s=6.0`초로 다시 찾는다. 후보가 여러 개면 공백으로 이어 붙여 한 문자열로 핵심 정보와 일치율을 비교한다. `no_speech_prob`가 None인 구간은 제외하지 않는다.
  - **판정 순서**
    1. 후보가 없다 → `SUBTITLE_MISSING` / `ERROR`
    2. 핵심 정보(RUL-02)를 비교한다. 자막 쪽은 짧은 날짜(`3.15`, `3/15`)도 날짜로 읽는다. 불일치가 있다 → `VALUE_MISMATCH` / `ERROR`. 누락만 있다 → `VALUE_MISSING` / `ERROR`.
    3. 음성에 핵심 정보가 있고 모두 일치한다 → 글자가 달라도 정상으로 본다.
    4. 음성에 핵심 정보가 없다 → 일치율(2글자 이상 일치 블록의 글자 수 ÷ 음성 글자 수)이 `expression_similarity_min=0.5` 미만이면 `EXPRESSION_DIFFERENCE` / `REVIEW_REQUIRED`
  - **강등**: 음성 `avg_logprob < stt_avg_logprob_min=-1.0`이면 `STT_UNCERTAIN` / `UNDETERMINED`로 바꾼다(원래 종류와 사유는 `evidence`의 `downgraded_from`, `original_reason`에 남긴다). 그렇지 않고 자막이 OCR로 만든 것이며 판정이 `ERROR`이면 `REVIEW_REQUIRED`로 낮추고 `evidence.ocr_generated_subtitles=True`를 기록한다.
  - **참고 신뢰도**: `confidence = clamp(exp(avg_logprob))`를 소수 셋째 자리까지 반올림한다. 오류 확률이 아니라 인식 신뢰의 참고값이다.
  - 실행 조건: 자막 출처가 FILE 또는 OCR일 때만 실행한다. 자막 출처가 NONE이면 건너뛴다.
- **출력**: `list[Issue]`(source=`M1`, 시간순)
- **예외·실패 처리**: 판정 규칙 안에서 발생시키는 예외는 없다. `Issue`는 시각이 유한수가 아니거나 `start<0`, `end<start`이면 `ValueError`를 낸다.
- **구현 위치**: `analysis/subtitle_check.py`(`check_subtitles`), `models.py`(`Issue`), `pipeline.py`(`_analyze`)

#### ANL-08 화면 정보 비교(M2)
- **설명**: 화면에 표시된 주요 문자 정보가 음성으로도 전달되는지 찾는다.
- **입력**: 확실한 화면 글자 목록(`texts`), 불확실한 화면 글자 목록(`uncertain`), 음성 구간, 자막 큐, 영상 길이, 분석 설정
- **동작 규칙**
  - **범위**: 화면에 **표시된 문자 정보**가 음성으로 전달되는지만 본다. 화면해설(행동, 몸짓, 그래프의 의미 등)은 판정하지 않는다.
  - **실행 조건**: M1(ANL-07)과 달리 자막 출처와 무관하게 실행한다(자막 출처가 NONE이어도 실행).
  - **짧은 날짜**: 기준인 화면 글자는 엄격하게 읽고, 비교 대상인 음성·자막의 핵심 정보는 짧은 날짜(`3.15`, `3/15`)도 날짜로 읽는다(`lenient_dates`).
  - **사전 처리**: 자막 출처가 OCR이고 자막 영역이 있으면, 자막 영역 안의 글자는 이미 자막으로 쓰였으므로 이 단계 대상에서 뺀다(`split_by_region`).
  - **점검 대상에서 빠지는 화면 글자**: 핵심 정보(RUL-02)가 하나도 추출되지 않은 글자. 같은 시점의 자막과 같다는 이유로는 빼지 않는다. 지적을 낼 때 자막에도 있으면(자막에 포함되거나 유사도가 `screen_merge_similarity=0.85` 이상이고 값이 모두 일치) `evidence.also_in_subtitle=True`와 안내 문장을 덧붙인다.
  - **비교할 음성이 없음**: 음성 구간이 하나도 없으면(오디오 트랙 없음, 빈 받아쓰기) 핵심 정보가 있는 확실한 화면 글자마다 `NO_SPEECH_TO_COMPARE` / `UNDETERMINED`를 올리고, 아래의 상시 표시·일반 판정은 하지 않는다.
  - **상시 표시 정보**: 표시 시간 ÷ `max(영상 길이, 1)`이 `always_visible_ratio=0.7` 이상이면, 표시 구간 ±`screen_near_window_s` 안에서 말하지 않은 값이 있을 때 한 건만 올린다. 영상 전체에서 말하지 않은 값이 있으면 `ALWAYS_ON_SCREEN`, 모두 다른 시점에서는 말했으면 `MENTIONED_ELSEWHERE`이며 둘 다 `REVIEW_REQUIRED`다.
  - **일반 판정**: 표시 구간 ±`screen_near_window_s=5.0`초 안의 음성 값과 비교해 값마다 분류한다.

    | 분류 | 조건 |
    |---|---|
    | ELSEWHERE | 영상의 다른 곳에서 같은 값을 말했다(가장 가까운 시간 간격을 기록) |
    | MISMATCH | 다른 곳에서도 말하지 않았고, 근처에 같은 종류의 다른 값이 있다 |
    | ONLY | 위 둘 다 아니다 |

    종류와 판정은 다음 우선순위로 정한다.

    | 순위 | 조건 | 종류 / 판정 |
    |---|---|---|
    | 1 | MISMATCH가 하나라도 있음 | `SCREEN_VALUE_MISMATCH` / `REVIEW_REQUIRED` |
    | 2 | ONLY가 하나라도 있음 | `SCREEN_ONLY_INFO` / `REVIEW_REQUIRED` |
    | 3 | ELSEWHERE만 있음 | `MENTIONED_ELSEWHERE` / `REVIEW_REQUIRED` |

    ELSEWHERE 항목이 하나라도 있으면 위 종류와 관계없이 `evidence.gap_s`에 가장 짧은 시간 간격을 기록한다.

  - **M2는 `ERROR`를 내지 않는다**: 값의 종류만 비교하므로 화면의 값과 근처에서 말한 값이 같은 대상인지 알 수 없다. MISMATCH의 사유는 "다릅니다"라고 단정하지 않고, 화면 값을 음성에서 확인하지 못했다는 것과 가까이서 말한 같은 종류의 값을 함께 보여 준다. 음성에 없는 화면 정보의 중요도도 판단하지 않는다(실증용 임시 규칙, `work-plan.md` 4.2). 이전에 저장한 분석의 판정과 사유는 바뀌지 않는다.
  - **강등**: 종류가 `SCREEN_VALUE_MISMATCH`나 `SCREEN_ONLY_INFO`이고 근처 음성 중 하나라도 `avg_logprob < -1.0`이면 `STT_UNCERTAIN` / `UNDETERMINED`로 바꾼다.
  - **저신뢰 OCR**: `uncertain` 목록의 글자는 핵심 정보가 있고, 신뢰도가 유한수이고, 그 값 중 하나라도 표시 구간 ±`screen_near_window_s` 안의 음성에 없을 때 `OCR_UNCERTAIN` / `UNDETERMINED`로 올린다. 값이 모두 다른 시점의 음성에는 있으면 사유에 알리고 `evidence.spoken_elsewhere_only=True`를 남긴다. 이 글자들은 자막 표시와 상시 표시 판정을 거치지 않는다.
- **출력**: `list[Issue]`(source=`M2`, 시간순)
- **예외·실패 처리**: 판정 규칙 안에서 발생시키는 예외는 없다.
- **구현 위치**: `analysis/screen_check.py`(`check_screen`), `analysis/subtitle_extract.py`(`split_by_region`), `pipeline.py`(`_analyze`)

#### ANL-09 화면 의존 표현 탐지(M2-2)
- **설명**: 음성에 "보시다시피", "이쪽으로"처럼 화면을 봐야만 이해되는 표현이 있는지 찾는다.
- **입력**: 음성 구간, 이미 만들어진 M2 지적 목록, 분석 설정
- **동작 규칙**
  - 찾는 표현(`screen_dependent_phrases`): "보시는 것처럼", "보시다시피", "보시는 바와 같이", "화면에서", "화면과 같이", "이쪽으로", "저쪽으로", "다음과 같이", "이렇게 하시면".
  - 음성 텍스트와 표현의 **공백을 모두 지운 뒤** 부분 문자열로 확인한다.
  - M1과 같은 제외 조건을 쓴다(`no_speech_prob > 0.6`이거나 정규화한 길이가 3 미만이면 제외).
  - **시간이 겹치는 M2 지적이 있으면** 새 지적을 만들지 않고 **그 지적을 제자리에서 수정한다**(부작용). `evidence["screen_dependent_phrases"]`에 표현을 추가하고, `reason` 뒤에 안내 문장을 덧붙인다. 판정은 바꾸지 않는다. 겹침은 경계를 포함한다(맞닿기만 해도 해당). 겹치는 M2 지적이 여러 개여도 **첫 번째 하나**에만 덧붙이고, 이미 같은 표현이 기록돼 있으면 `reason`을 다시 덧붙이지 않는다. source가 M2인 모든 지적(`OCR_UNCERTAIN`, `ALWAYS_ON_SCREEN`, 강등된 `STT_UNCERTAIN` 포함)이 흡수 대상이다.
  - **겹치는 M2 지적이 없으면** `SCREEN_DEPENDENT` / `REVIEW_REQUIRED`(source=`M2_SCREEN_DEPENDENT`)를 새로 만들고 `evidence.phrases`에 표현을 기록한다.
  - 장면 분석은 하지 않는다(받아쓰기 텍스트만 쓴다).
- **출력**: 새로 만든 `list[Issue]`(기존 M2 지적은 같은 목록 객체 안에서 수정됨)
- **예외·실패 처리**: 없음
- **구현 위치**: `analysis/screen_dependent.py`(`check_screen_dependent`), `pipeline.py`(`_analyze`)

#### ANL-10 결과 저장
- **설명**: 모든 판정이 끝난 뒤 지적 목록을 한 번에 저장하고 세션을 완료 처리한다.
- **입력**: M1, M2, M2-2 지적 목록
- **동작 규칙**
  - 세 목록을 합쳐 (시작, 끝) 순으로 정렬한다.
  - `complete_session`이 **한 트랜잭션**에서 지적을 모두 INSERT하고 세션 상태를 COMPLETED, 종료 시각을 기록한다.
  - 취소되거나 실패한 세션에는 지적이 남지 않는다(검수 화면에 부분 결과가 노출되지 않음).
  - 저장 뒤 단계 측정(ANL-14) 기록이 실패해도 이미 완료된 분석을 실패로 뒤집지 않는다.
- **출력**: `PipelineResult`(지적 수 포함)
- **예외·실패 처리**: 저장 중 SQLite 오류는 ERR-09로 변환된다.
- **구현 위치**: `pipeline.py`(`_analyze`), `storage/repository.py`(`Repository.complete_session`)

#### ANL-11 인식 결과 캐시 재사용
- **설명**: 같은 영상을 같은 조건으로 다시 분석할 때 STT·OCR 원시 결과를 재사용한다.
- **입력**: 영상 해시, 엔진·모델 이름, 인식 설정
- **동작 규칙**
  - **STT 키 구성**: 영상 해시, `STT`, 엔진명, 모델명, `{language:"ko", vad:true, condition_on_previous_text:false}`
  - **OCR 키 구성**: 영상 해시, `OCR`, 엔진명, 모델명, `{mode(full 또는 strip), fps, max_width, ratio, diff(frame_diff_skip_threshold), skip(생략 판단 방식 이름)}`
  - 캐시에서 값을 복원하다 `ValueError`가 나면(손상) 무시하고 다시 계산해 덮어쓴다.
  - 자막 영역 스캔은 `ratio`가 키에 들어 있다. 편집자가 영역을 **더 넓게** 조정했을 때만 넓은 띠로 다시 스캔하고(별도 캐시 항목), 같거나 좁게 확인하면 처음 스캔 결과를 그대로 쓴다(ANL-05).
  - 캐시 저장·조회 규칙은 DAT-03을 따른다.
- **출력**: 인식 결과와 캐시 적중 여부(단계 측정 `cache_hit`에 기록)
- **예외·실패 처리**: 손상된 캐시는 오류로 취급하지 않고 다시 계산한다.
- **구현 위치**: `pipeline.py`(`_cached`, `_ocr_pass`), `storage/cache.py`(`make_key`)

#### ANL-12 진행 이벤트와 취소
- **설명**: 분석 단계별 진행을 UI에 알리고, 편집자가 분석을 취소할 수 있게 한다.
- **입력**: 진행 콜백, `CancelToken`
- **동작 규칙**
  - **진행 이벤트** `StageEvent(stage, state, fraction, message)`: `state`는 `RUNNING` 또는 `DONE`이다. 측정할 수 없는 단계는 처리 중 `fraction=None`, 끝나면 1.0이다(예외: MODEL_PREPARE는 끝날 때도 None).
  - **OCR 진행률**: 자막 파일이 있으면 OCR 패스(전체 화면을 한 번 훑는 작업)를 1회, 없으면 2회로 보고 `(패스 번호 + 영상 안 위치 ÷ 길이) ÷ 전체 패스 수`로 계산하며, 값이 줄어들지 않도록 최댓값을 유지한다.
  - **협조적 취소**: `CancelToken`(`cancel()`, `cancelled`, `wait(timeout)`, `raise_if_cancelled()`)을 STT·OCR·ffmpeg가 공유한다. 단계 사이, STT 구간마다, OCR 프레임마다, ffmpeg 실행 중(0.2초마다 확인, 취소하면 프로세스를 종료)에 확인한다. 즉시 중단은 보장하지 않는다.
  - **분석 중 화면**(`ProgressPage`): 단계를 행으로 보여 준다(영상 준비, 음성 인식, 화면 문자 인식, 자막 비교, 화면 정보 비교, 결과 정리. "모델 준비"는 해당 이벤트가 올 때만 맨 앞에 추가). 상태는 "대기/진행 중/완료/실패" 칩이다. 진행률 값이 없으면 진행 막대를 무한 진행 모드로 바꾸고 퍼센트 표시를 비운다(퍼센트를 지어내지 않는다). 상태 줄에는 단계 메시지(예: "음성을 인식하는 중입니다.")가 나온다. 값이 있으면 "현재 단계 N%"이며 **전체가 아니라 현재 단계의 진행률**이다. "분석 중에는 컴퓨터를 종료하지 마세요." 안내를 분석이 진행되는 동안 보여 준다(실패하면 숨긴다).
  - **[분석 취소]**: 한 번만 요청된다. 버튼이 비활성되고 "취소 중입니다. 현재 작업이 끝나면 중단됩니다."를 표시한다. 실제로 취소되면 등록 화면으로 돌아가며 "분석이 취소되었습니다." 안내를 보여 준다.
  - **실패 시**: "{단계} 단계에서 분석에 실패했습니다. {문구} ({코드})"를 보여 주고 [다시 시도](같은 입력으로 재실행), [처음으로](등록 화면)를 제공한다. 실패한 단계가 단계 목록에 없으면(예: 작업 스레드의 ERR-99) "{단계} 단계에서" 부분은 생략된다. 이때 [분석 취소] 버튼과 "컴퓨터를 종료하지 마세요" 안내는 숨겨진다.
- **출력**: 진행 이벤트, 취소 시 세션 상태 CANCELLED
- **예외·실패 처리**: 취소는 `CancelledError`로 전파되어 세션이 CANCELLED로 기록된다(오류 코드 없음).
- **구현 위치**: `pipeline.py`(`StageEvent`, `Stage`, `_emit`), `cancel.py`(`CancelToken`), `ui/progress_page.py`(`ProgressPage`), `ui/main_window.py`(`_on_cancelled`, `_retry`)

#### ANL-13 오류 변환
- **설명**: 분석 중 발생한 예외를 오류 코드와 사용자 문구로 바꿔 화면에 보여 준다.
- **입력**: 예외, 실패 단계, 세션 ID
- **동작 규칙**: 아래 표를 **위에서부터 차례로 검사해 처음 맞는 것**을 쓴다. 오류가 나면 세션은 FAILED로 기록되고 오류 코드가 남는다.

  | 코드 | 대상 예외 | 사용자 문구 요지 |
  |---|---|---|
  | ERR-01 | `VideoFileNotFoundError`, `UnsupportedVideoError`, `VideoReadError` | 파일 없음 / 지원하지 않는 형식 / 읽을 수 없음 |
  | ERR-02 | `VideoTooLongError` | 20분을 초과하는 영상은 분석할 수 없음 |
  | ERR-03 | `SubtitleError` | 자막 파일의 형식 또는 인코딩 문제 |
  | ERR-04 | `FfmpegError` | 영상 처리 실패, 실행 환경 확인 필요 |
  | ERR-06 | `SttModelError`, `OcrModelError` | 모델을 불러올 수 없음 |
  | ERR-07 | `SttError` | 음성 인식 실패 |
  | ERR-08 | `OcrError` | 화면 글자 인식 실패 |
  | ERR-09 | `sqlite3.Error`, `OSError` | 결과 저장 실패 / 작업 폴더·파일 사용 불가 |
  | ERR-99 | 그 밖의 모든 예외 | 알 수 없는 오류 |

  `explain_error`는 같은 코드와 문구를 만들어 주는 함수이며, 등록 화면의 파일 검증이 쓴다. ERR-10(재생 실패), ERR-11(CSV 내보내기 실패)은 각각 REV-03, REV-07에서 쓴다. 전체 목록은 부록 B.
- **출력**: `PipelineError(code, user_message, stage, session_id)`
- **예외·실패 처리**: 백그라운드 스레드의 예상 밖 예외는 ERR-99("분석 중 알 수 없는 오류가 발생했습니다.")로 바꿔 알리며 앱은 종료되지 않는다(SYS-04).
- **구현 위치**: `pipeline.py`(`_ERRORS`, `_to_error`, `explain_error`, `PipelineError`)

#### ANL-14 단계별 측정 기록
- **설명**: 단계마다 걸린 시간, 처리한 건수, 캐시 적중 여부를 세션 기록에 남긴다.
- **입력**: 세션 ID, 단계, 시작 시각
- **동작 규칙**: `analysis_stage_metric`에 단계별로 기록한다.

  | 단계 | 처리 건수 / 단위 | 캐시 적중 |
  |---|---|---|
  | MEDIA_PREPARE | (소요 시간만. 해시 계산과 오디오 추출 시간 포함) | – |
  | STT | 구간 수 / `SEGMENT` | STT 캐시 적중 여부 |
  | OCR | 전체 화면 관측 수 / `FRAME` | 모든 OCR 패스가 적중일 때만 True |
  | SUBTITLE_CHECK | 지적 수 / `ISSUE` | – |
  | SCREEN_CHECK | M2 + M2-2 지적 수 / `ISSUE` | – |
  | RESULT_SAVE | 지적 수 / `ISSUE` | – |

  - `MODEL_PREPARE`는 측정 기록을 남기지 않는다.
  - 재시도 번호(`attempt_no`)는 항상 1이다.
  - 같은 (세션, 단계, 시도 번호)는 중복 저장되지 않는다.
- **출력**: 측정 행(`record_stage_metric`), 조회는 `list_stage_metrics(session_id)`
- **예외·실패 처리**: RESULT_SAVE 측정이 SQLite 오류로 실패해도 무시한다(ANL-10).
- **구현 위치**: `pipeline.py`(`_measure`), `storage/repository.py`(`record_stage_metric`, `list_stage_metrics`)

---

### 4.3 분석 규칙 (RUL)

음성·자막·화면 글자를 비교하기 전에 쓰는 규칙 기반 도구다(LLM을 쓰지 않는다). M1(ANL-07)과 M2(ANL-08)가 사용한다.

#### RUL-01 텍스트 정규화
- **설명**: 비교용 문자열로 통일한다. 같은 입력에 다시 적용해도 결과가 같다(멱등).
- **입력**: 텍스트, `keep_spaces` 여부
- **동작 규칙**: 다음 순서로 처리한다.
  1. NFKC 정규화 후 소문자로 바꾼다.
  2. 천 단위 쉼표를 지운다(`3,000` → `3000`).
  3. 전화번호의 구분자를 지운다.
  4. 시각을 `HH:MM`으로 바꾼다.
  5. 고유어 수사 + 단위(시간, 명, 개, 번, 살, 배)를 숫자로 바꾼다.
  6. 한자어 수사 + 단위를 숫자로 바꾼다. 자리수 글자(십, 백, 천, 만, 억)가 있으면 모든 단위에서 바꾸고, 자리수 글자가 없는 한 글자 수사(일·이·삼…)는 월·일·년·분·초·층·호가 공백 없이 붙을 때만 바꾼다. '이분'은 바꾸지 않는다. `%`·`퍼센트`는 띄어쓰기와 관계없이 숫자로 바꾼다. 단위 뒤에 한글이 이어지면 조사·어미일 때만 단위로 읽으므로(`UNIT_END`), '백분율', '사분면', '5원칙', '삼분기'처럼 단위 글자로 시작하는 낱말은 바꾸지 않는다. `분`은 뒤에 '의'가 오면('분의') 단위로 보지 않는다.
  7. `퍼센트`를 `%`로 바꾼다.
  8. `9:30`을 `09:30`으로 맞춘다.
  9. 기호를 지운다(숫자 사이가 아닌 마침표, 시각이 아닌 콜론 포함).
  10. `keep_spaces`이면 공백을 한 칸으로 남기고, 아니면 모두 지운다.
  - **시각 규칙**: 오전·오후·아침·낮·저녁·밤·새벽 접두어, `반`, `N분`을 인식한다. 시가 24를 넘거나 분이 59를 넘으면 무효다. 접두어가 있거나 13시 이상이거나 0시이면 오전/오후가 명시된 것으로 본다. 환산은 오후·저녁은 12시 미만이면 +12(12시 이상은 `시 % 24`이므로 오후 12시는 12시, 24시는 0시), 밤은 6~11시면 +12(12시·24시는 0), 낮은 1~6시면 +12, 오전·아침·새벽은 12시와 24시를 0시로 본다. 접두어가 없어도 24시는 `00:00`이 된다. 접두어 없는 "3시"는 `03:00`이 되며, 12시간 기준 비교는 RUL-03이 맡는다.
- **출력**: 정규화된 문자열
- **예외·실패 처리**: 문법이 틀린 수사(예: '삼사')는 숫자로 바꾸지 않는다.
- **구현 위치**: `analysis/normalize.py`(`normalize_text`, `sino_to_int`, `native_to_int`, `parse_time_match`, `apply_meridiem`, `TIME_RE`)

#### RUL-02 핵심 정보 추출
- **설명**: 문장에서 접근성 점검에 중요한 정보 11종을 규칙으로 뽑는다.
- **입력**: 텍스트, `lenient_dates` 여부(비교 **대상**인 자막·음성 쪽에서만 짧은 날짜를 허용한다. 기준이 되는 M1의 음성, M2의 화면 글자는 엄격하게 읽는다)
- **동작 규칙**: `EntityKind`는 DATE(날짜), WEEKDAY(요일), TIME(시각), MONEY(금액), HEADCOUNT(인원), QUANTITY(수치), PHONE(전화번호), URL(누리집 주소), ADDRESS(주소), FLOOR_ROOM(층·호수), FACILITY(시설)이다. 한 번 뽑힌 구간은 공백으로 지워 중복 집계를 막는다. 두 단계로 추출한다.
  - **원문 단계**(소문자로 바꾸기 전)

    | 종류 | 규칙 |
    |---|---|
    | URL | 허용 TLD: com, net, org, kr, info, edu, gov, biz, tv, io, me, co, go, or, ac. `http(s)://`와 `www.`를 지우고 host만 소문자로 바꾼다. |
    | 점 구분 날짜 | `YYYY.M.D`, `-`, `/` 형식을 `YYYY년 M월 D일`로 다시 쓴다. |
    | 짧은 날짜 | `lenient_dates`일 때만 `3.15`, `3/15`를 날짜로 읽는다. 뒤에 단위(%, 배, 원 등)가 오면 소수로 보고 제외한다. |
    | 대표번호 | `1[568]XX-XXXX` 형식. 뒤에 원·%·명이 오면 제외한다. |
    | 시각 | 한글 시각과 `HH:MM` 콜론 시각 |

  - **정규화 단계**(`keep_spaces=True`로 정규화한 결과에 적용)

    | 종류 | 규칙 |
    |---|---|
    | 전화 | `0`으로 시작하는 9~11자리 |
    | 날짜 | 'N일간', 'N일 동안', '1일 3회'는 제외. 모르는 부분은 `xxxx`, `xx`로 채운다. 월 1~12, 일 1~31만 허용 |
    | 요일 | `[월화수목금토일]요일` |
    | 금액 | 억·만·천·백 조합을 Decimal로 합산(예: 1억 5천만원 → 150000000) |
    | 인원 | `N명` |
    | 주소 | 시·도 + 구·군·시 (+ 로·길 번지) |
    | 시설 | 복지관, 도서관, 문화회관, 주민센터, 복지센터, 체육관, 박물관, 미술관, 시청, 구청, 군청. '역'은 출구·앞·하차 같은 문맥이 있을 때만 시설로 본다. |
    | 층·호 | `N층`(앞에 `지하`가 올 수 있음), `N호`. '호선'은 제외 |
    | 수치 | 단위: %, 일간, 시간, 배, 건, 개, 회, 번, 세, 대, 분, 초, 주. 숫자 표기를 통일한다(3.0 → 3) |
- **출력**: `list[Entity(kind, value, raw, explicit)]`
- **예외·실패 처리**: 없음(추출하지 못하면 빈 목록)
- **구현 위치**: `analysis/entities.py`(`extract_entities`, `Entity`, `EntityKind`)

#### RUL-03 핵심 정보 비교
- **설명**: 한쪽 문장의 핵심 정보가 다른 쪽에 있는지 짝을 지어 일치·불일치·누락으로 나눈다.
- **입력**: 기준 문장의 핵심 정보 목록, 대상 문장의 핵심 정보 목록
- **동작 규칙**
  - **같음 판정**(`entities_equal`, 같은 종류끼리만)

    | 종류 | 같다고 보는 조건 |
    |---|---|
    | TIME | 분이 같아야 한다. 한쪽이라도 오전/오후가 명시되지 않았으면 12시간 기준으로 같은 시각이면 같다. |
    | DATE | 양쪽 모두 알려진 부분이 하나 이상 있고, 그 부분이 모두 같으면 같다. |
    | 그 밖 | value가 같으면 같다. |

  - **짝짓기**(`compare_entities`): 이분 최대 매칭을 쓰며 같은 표기를 먼저 시도한다. 짝을 못 찾은 기준 값은 같은 종류의 남는 대상 값이 있으면 `mismatched`, 없으면 `missing`이다. 대상에만 있는 값은 무시한다.
- **출력**: `EntityDiff(matched, mismatched, missing)`
- **예외·실패 처리**: 없음
- **구현 위치**: `analysis/entities.py`(`compare_entities`, `entities_equal`, `EntityDiff`)

### 4.4 검수 (REV)

검수 화면(`ReviewPage`)은 영상 플레이어, 문제 목록, 문제 상세를 한 화면에 둔다. 분석이 성공하면 자동으로 이 화면으로 전환된다.

#### REV-01 문제 목록과 필터
- **설명**: 분석이 찾은 지적을 시간순 목록으로 보여 주고, 3종 필터로 좁힌다.
- **입력**: 세션의 지적 목록, 필터 선택(유형 그룹, 검수 상태, AI 판정)
- **동작 규칙**
  - 지적은 (시작, 끝, id) 순으로 고정된다. **표 정렬 기능은 없다.**
  - 표는 행 하나만 선택할 수 있고 편집할 수 없다. 보이는 열은 "시간"(파란색), "내용"(탐지 사유, 툴팁으로 전문), "상태"(상태별 색)이며, "유형"과 "AI 판정" 열은 숨긴다. 표 머리글은 숨긴다.
  - **필터 3종**(콤보 박스): ① 유형 그룹 — 전체 / 음성·자막(M1) / 화면 문자(M2) / 화면 의존 표현(M2-2) ② 검수 상태 ③ AI 판정. 세 조건은 AND로 결합된다.
  - 선택한 지적이 필터에서 빠지면 선택과 상세를 해제한다(`keep_selection`).
  - 결과가 0건이면 "조건에 해당하는 항목이 없습니다."를 표시한다.
- **출력**: 필터링된 지적 목록(`ReviewState.visible`)
- **예외·실패 처리**: 없음
- **구현 위치**: `ui/review_page.py`(`ReviewPage.load`, `_apply_filter`, `_refresh_empty_label`), `ui/issue_model.py`(`IssueTableModel`), `review_state.py`(`IssueFilter`, `matches`, `visible`, `keep_selection`)

#### REV-02 문제 상세 표시
- **설명**: 선택한 지적의 근거를 보여 준다.
- **입력**: 선택한 지적
- **동작 규칙**
  - 표시 항목: 유형, 발생 구간, 탐지 사유, 음성 인식 내용, 비교 대상, AI 판정, 신뢰도, 검수 상태(칩).
  - "비교 대상" 제목은 M1 지적이면 "자막", 그 밖에는 "화면 문자"다.
  - 신뢰도는 "신뢰도(참고값): N% — 오류 확률이 아닙니다."로 표시한다(값이 없으면 "—"). 판정이 `UNDETERMINED`이거나 신뢰도가 0.5 미만(`_UNCERTAIN_BELOW`)이면 " AI 인식이 불확실합니다."를 덧붙인다.
  - 선택이 없으면 "문제를 선택하면 상세 내용이 표시됩니다."를 표시한다.
- **출력**: 상세 패널
- **예외·실패 처리**: 없음
- **구현 위치**: `ui/review_page.py`(`_show_detail`, `_on_selection_changed`, `confidence_text`, `_UNCERTAIN_BELOW`)

#### REV-03 영상 플레이어 연동
- **설명**: 문제를 고르면 그 시점의 영상을 바로 확인할 수 있게 한다.
- **입력**: 목록에서 고른 지적, 플레이어 조작
- **동작 규칙**
  - 행을 선택하면 플레이어가 지적의 시작 시각(`start_s`)으로 이동하고 **일시정지**한다(자동 재생하지 않는다).
  - **플레이어 컨트롤**: 재생/일시정지(한 버튼, 라벨이 재생 상태에 맞춰 바뀜), 탐색 슬라이더, 현재 시간과 전체 길이 표시(mm:ss 또는 h:mm:ss). **볼륨·배속 컨트롤은 없다.**
  - 실제 재생은 `MediaBackend` 프로토콜 뒤의 `QtMediaBackend`(`QMediaPlayer` + `QAudioOutput` + `QVideoWidget`)가 맡는다.
  - **재생 실패**: 플레이어 오류가 나거나 파일이 없으면 영상 아래에 "영상을 재생할 수 없습니다… (ERR-10) 분석 결과는 그대로 확인할 수 있습니다. [{메시지}]"를 표시한다. 검수는 계속할 수 있다.
  - 재생·탐색 조작은 `activity` 신호로 검수 시간 측정(REV-06)에 전달된다.
- **출력**: 플레이어 이동·재생 상태, `playback_failed` 신호
- **예외·실패 처리**: 재생 실패는 오류로 중단하지 않고 안내만 표시한다.
- **구현 위치**: `ui/review_page.py`(`_on_selection_changed`, `_on_playback_failed`), `ui/player.py`(`VideoPlayerPanel`, `QtMediaBackend`, `seek`)

#### REV-04 확정·기각 처리
- **설명**: 편집자가 지적을 확정하거나 기각하고, 그 결과를 즉시 저장한다.
- **입력**: 선택한 지적, [확정] [기각] [미검토로 되돌리기] 조작
- **동작 규칙**
  - 상태는 `UNREVIEWED`, `CONFIRMED`, `DISMISSED` 사이에서 **어느 방향으로든** 바꿀 수 있다(전이 제한 없음). 같은 상태로 바꾸려 하면 아무것도 하지 않는다. 보류 상태는 없다.
  - **DB 저장을 먼저 하고** 성공하면 메모리 상태를 바꾼다. 바꾸는 것은 `review_status`(와 `updated_at`)뿐이며 AI 판정(`verdict`)은 건드리지 않는다.
  - 처리한 뒤 다음 항목으로 넘어가지 않고 같은 항목에 머문다. 선택이 없으면 세 버튼이 모두 비활성이다.
  - 처리한 항목이 현재 필터에서 빠지면 행을 없애고 선택을 해제한다.
- **출력**: 갱신된 상태, 요약(REV-05) 갱신
- **예외·실패 처리**: 저장 중 SQLite 오류는 `SaveError`(ERR-09)가 되며, 화면은 바꾸지 않고 "저장 실패 / 검수 상태를 저장하지 못했습니다. 다시 시도해 주세요."를 띄운다. 존재하지 않는 지적이면 `KeyError`가 난다.
- **구현 위치**: `review_state.py`(`ReviewState.set_status`, `SaveError`), `ui/issue_model.py`(`IssueTableModel.set_status`), `ui/review_page.py`(`_review`), `storage/repository.py`(`set_review_status`)

#### REV-05 검수 요약과 안내문
- **설명**: 처리 현황과, 결과를 어떻게 읽어야 하는지 안내를 보여 준다.
- **입력**: 세션의 지적 목록, 분석 때 남은 안내 문구(`notices`)
- **동작 규칙**
  - **요약**: "검수 / {영상 이름}", 상태 칩(미검토 N, 확정 N, 기각 N), "탐지 문제 · N건", 판정별 건수. 전체 = 미검토 + 확정 + 기각이며 검수할 때마다 갱신된다.
  - **안내문**: 분석 때 남은 안내 문구를 먼저 붙이고, 아래를 조건에 따라 추가한다. 자막 출처가 NONE인데 "자막이 없는" 문구가 아직 없으면 자막 없음 안내를, 출처가 OCR인데 "OCR" 문구가 아직 없으면 OCR 기준 안내를, 지적이 0건이면 "탐지된 문제가 없습니다…"를 추가한다. 0건 안내에는 접근성 기준을 모두 충족했다는 뜻이 아니라는 문구가 들어 있다.
- **출력**: 요약 칩과 안내문
- **예외·실패 처리**: 없음
- **구현 위치**: `review_state.py`(`summary`, `notices`), `ui/review_page.py`(`_refresh_summary`, `load`), `storage/repository.py`(`review_counts`)

#### REV-06 검수 시간 측정
- **설명**: 편집자가 검수에 쓴 **활동 시간**만 세어 분석 기록에 누적한다.
- **입력**: 사용자 활동 신호(지적 선택, 필터 변경, 검수 처리, 재생·탐색 조작, 재생 중 1초마다, 내보내기)
- **동작 규칙**
  - 검수 화면이 활성 창일 때만 시간이 쌓인다(창이 비활성이면 세지 않는다).
  - 활동 사이의 공백이 유휴 기준(`idle_threshold_s=120.0`초) 이상이면 그 공백은 통째로 뺀다.
  - 30초마다 DB에 저장하고(`add_review_time`, 누적), 검수 화면을 벗어나거나 창을 닫을 때도 저장한다. 창이 비활성이 되는 순간에는 측정을 멈추면서 바로 저장한다. 저장에 실패한 분량은 세션별로 보관했다가 다시 시도한다.
  - `ActiveTimeTracker`: `start`, `activity`, `stop`, `drain_ms`(확정된 증가분만 반환), `total_ms`.
- **출력**: 분석 세션의 `review_duration_ms` 누적값
- **예외·실패 처리**: 끝내 저장하지 못하면 "검수 시간 저장 실패" 경고를 띄운다. `add_review_time`에 음수를 주면 `ValueError`다.
- **참고(코드 기준)**: 측정한 시간을 화면에 표시하지는 않는다(`total_ms`는 UI에서 쓰이지 않는다).
- **구현 위치**: `ui/review_timer.py`(`ActiveTimeTracker`), `ui/main_window.py`(`_begin_review_timing`, `_end_review_timing`, `flush_review_time`, `tick_playback`, `set_window_active`), `storage/repository.py`(`add_review_time`)

#### REV-07 CSV 내보내기
- **설명**: 검수 결과를 CSV 파일로 저장한다.
- **입력**: [CSV 내보내기] 조작, 저장 경로
- **동작 규칙**
  - 저장 대화상자의 기본 이름은 영상 파일 이름에서 확장자를 뺀 것에 `_검수결과.csv`를 붙인다(예: `lecture.mp4` → `lecture_검수결과.csv`).
  - **기각된 항목을 포함해** 모든 지적을 시간순으로 내보낸다.
  - **열(11개)**: 문제 ID, 영상 파일명, 시작 시간, 종료 시간, 문제 유형, 자동 판정, 탐지 사유, 음성 인식 내용, 비교 대상 내용, 신뢰도, 검수 상태
  - 인코딩은 UTF-8 BOM(`utf-8-sig`)이다. 저장 폴더가 없으면 만든다. 시간은 `HH:MM:SS.mmm`, 신뢰도는 소수 둘째 자리(없으면 빈칸)다. 유형·판정·검수 상태는 한글 라벨로 바꾼다.
  - **수식 주입 방지**: 문자열 셀이 `=`, `+`, `-`, `@`, 탭, 캐리지 리턴으로 시작하면 앞에 `'`를 붙인다(시간·신뢰도 열은 직접 만든 값이라 적용하지 않는다).
- **출력**: CSV 파일, "저장 완료 / 검수 결과를 저장했습니다: {경로}" 안내
- **예외·실패 처리**: `OSError`·SQLite 오류는 `ExportError`(ERR-11)가 되고 "내보내기 실패"를 안내한다.
- **구현 위치**: `storage/export.py`(`export_csv`, `format_time`, `_safe`), `review_state.py`(`ExportError`, `suggested_csv_name`), `ui/review_page.py`(`_export`)

---

### 4.5 설정 (SET)

#### SET-01 분석 설정 변경
- **설명**: 사용할 음성 인식 모델, 모델 저장 경로, 자막 영역 비율을 편집자가 바꾼다.
- **입력**: 설정 대화상자(`SettingsDialog`)의 선택
- **동작 규칙**
  - **감지 장치** 표시: "CPU: {이름}({코어}코어) · 메모리 N GB · GPU(NVIDIA CUDA): 사용 가능/불가".
  - **추천 모델** 표시: "권장 음성 인식 모델: X", "현재 모델: …". 추천에 경고가 있으면 함께 표시한다.
  - **모델 선택**(콤보 박스): "권장 모델 사용 (X)"(저장값 None)과 `SUPPORTED_MODELS` 목록. 앱에 포함되지 않은 모델에는 "(내려받기 필요)"를 붙인다. 직접 고른 경우에만 "정확도·처리 시간이 달라질 수 있습니다"를 안내하고, 포함되지 않은 모델이면 "인터넷 연결 또는 모델 경로 지정이 필요합니다."도 안내한다.
  - **모델 저장 경로**: 텍스트 입력이다. 비워 두면 기본 위치를 쓴다. **찾아보기 버튼과 경로 검증은 없다.**
  - **OCR 자막 영역 비율**: 슬라이더 10~50%("영상 하단 N%"), 16:9 화면 모양 미리보기(`RegionPreview`)에 "자막 인식 영역" 띠를 그린다.
  - **[권장 설정 복원]**: 모델은 권장으로, 비율은 25%로 되돌린다. 모델 경로는 그대로 둔다.
  - **[확인]**이면 `AppSettings(whisper_model, model_dir, subtitle_region_ratio)`를 돌려주고, 취소·닫기(×)면 None이다.
  - 분석을 시작하면 [설정] 버튼이 비활성이고, 분석이 끝나면(성공·실패·취소) 다시 활성이다.
- **출력**: `AppSettings` 또는 None. 저장·적용은 SET-03을 따른다.
- **예외·실패 처리**: 저장에 실패하면 "설정 저장 실패… 기존 설정을 그대로 사용합니다."를 안내한다.
- **구현 위치**: `ui/settings_dialog.py`(`SettingsDialog`, `RegionPreview`, `settings`), `ui/main_window.py`(`_open_settings`), `app.py`(`edit_settings`)

#### SET-02 장치 감지와 모델 추천
- **설명**: 실행 환경을 보고 음성 인식 모델과 장치를 추천한다.
- **입력**: 시스템 정보, 분석 설정(`recommend_*` 상수)
- **동작 규칙**
  - **감지**(`detect_device`): `DeviceInfo(cpu_name, cpu_count, ram_gb, cuda_available)`. CPU 이름은 `platform.processor()`(비면 `platform.machine()`, 그것도 없으면 "알 수 없는 CPU"), 코어 수는 psutil의 논리 코어(없으면 `os.cpu_count`, 그것도 없으면 1), 메모리는 psutil 전체 메모리, CUDA는 `ctranslate2.get_cuda_device_count() > 0`이다. CUDA 감지 중 어떤 예외가 나도 CUDA 없음으로 본다.
  - **지원 모델**(`SUPPORTED_MODELS`): tiny, base, small, medium, large-v3, turbo
  - **추천 규칙**(위에서부터 먼저 맞는 것)

    | 순위 | 조건 | 모델 / 장치 / 연산 형식 |
    |---|---|---|
    | 1 | CUDA 사용 가능 | `recommend_cuda_model`("medium") / cuda / float16 |
    | 2 | 메모리 < `low_ram_threshold_gb`(8.0GB) | `recommend_low_ram_model`("base") / cpu / int8 + 경고 "메모리가 적어 작은 모델을 권장합니다…" |
    | 3 | 그 외 | `recommend_cpu_model`("small") / cpu / int8 |

  - `bundled_models(config)`: 추천 모델 3종을 중복 없이 모은다(기본값 base, small, medium). 설정 화면의 "(내려받기 필요)" 판단과 배포본의 모델 동봉 대상에 쓰인다.
  - 추천값은 모두 실측 전 임시값이다.
- **출력**: `ModelRecommendation(model, device, compute_type, warning)`
- **예외·실패 처리**: CUDA 감지 중 발생한 예외만 "CUDA 없음"으로 처리한다. CPU 이름·코어 수·메모리 조회가 실패하면 처리 없이 예외가 그대로 전파된다.
- **구현 위치**: `engines/device.py`(`detect_device`, `recommend_model`, `bundled_models`, `SUPPORTED_MODELS`), `config.py`

#### SET-03 설정 저장·복원
- **설명**: 사용자 설정을 JSON 파일에 저장하고, 읽을 때 값을 검증한다.
- **입력**: `AppSettings`, 설정 파일 경로(`{데이터 폴더}/settings.json`)
- **동작 규칙**

  | 항목 | 기본값 | 검증 규칙 |
  |---|---|---|
  | `whisper_model` | None(장치에 맞는 권장 모델) | 문자열이고 `SUPPORTED_MODELS`에 있어야 한다 |
  | `model_dir` | None | 비어 있지 않은 문자열이어야 한다. 폴더가 실제로 있는지는 검사하지 않는다 |
  | `subtitle_region_ratio` | 0.25 | bool이 아닌 숫자, 유한값, 0.10 이상 0.50 이하 |

  - **읽기**(`load_settings`): 필드마다 검증하고 **잘못된 필드만** 기본값으로 되돌린다. 파일을 읽을 수 없거나 JSON 오류이거나 최상위가 객체가 아니면 전부 기본값이다. 아주 큰 정수(OverflowError)도 기본값으로 처리한다.
  - **쓰기**(`save_settings`): 임시 파일에 쓴 뒤 `os.replace`로 교체한다(저장 도중 종료되어도 기존 설정이 깨지지 않는다). `fsync`는 하지 않으며 저장할 때 값 검증도 하지 않는다.
  - **적용**: `apply_settings`가 `AnalysisConfig`의 `subtitle_region_ratio`와 `model_dir`을 바꾼다. `effective_model`은 설정한 모델이 있으면 그것을, 없으면 추천 모델을 쓴다.
  - **호출 시점**: 앱이 메인 창을 만들 때 읽고(`MainWindow`), 설정 대화상자에서 [확인]할 때 저장한다. **저장에 성공해야만** 메모리 설정과 등록 화면 요약이 바뀐다. 저장된 설정은 다음 분석부터 적용된다(`pipeline_factory`).
  - 자막 영역 비율의 허용 범위는 코드에 "미확정 임시값"으로 적혀 있다.
- **출력**: `AppSettings`
- **예외·실패 처리**: 읽기 오류는 기본값으로 복구한다. 쓰기 오류는 SET-01의 안내를 띄운다.
- **구현 위치**: `settings.py`(`AppSettings`, `load_settings`, `save_settings`, `apply_settings`, `effective_model`), `ui/main_window.py`(`__init__`의 `load_settings`, `_open_settings`의 `save_settings`)

---

### 4.6 저장 (DAT)

#### DAT-01 로컬 DB
- **설명**: 분석 세션, 지적, 단계 측정, 캐시 인덱스를 SQLite에 저장한다.
- **입력**: 데이터 폴더의 `barrierfree.db`
- **동작 규칙**
  - **연결**: timeout 5초, `foreign_keys=ON`, `busy_timeout=5000`. 메모리 DB가 아니면 WAL 모드. 결과 행은 `sqlite3.Row`. **스레드마다 별도 연결**을 둔다.
  - **마이그레이션**: `PRAGMA user_version`으로 관리한다(현재 `SCHEMA_VERSION=1`). 버전이 1보다 작으면 V1 스크립트를 실행하고, 1보다 크면 `RuntimeError`("데이터베이스가 더 최신 버전…")를 낸다.
  - 시간 구간은 ms 정수, 시각은 UTC ISO 8601(밀리초, 끝에 `Z`)로 저장한다.
  - **테이블**

    | 테이블 | 주요 컬럼과 의미 |
    |---|---|
    | `analysis_session` | id(UUID), video_path/name/sha256, video_duration_ms, subtitle_source(FILE/OCR/NONE/UNKNOWN), subtitle_path(사용자 자막), generated_srt_path(OCR 자막의 상대 경로), subtitle_region_json, status, stt_model_name, ocr_engine_name, compute_device, config_snapshot_json, review_duration_ms(누적 검수 시간), error_code, created_at/started_at/finished_at |
    | `issue` | id, session_id(FK, 세션 삭제 시 함께 삭제), source(M1/M2/M2_SCREEN_DEPENDENT), issue_kind, verdict, review_status(기본 UNREVIEWED), severity(비울 수 있음), confidence(0~1, 비울 수 있음), start_ms/end_ms(end ≥ start), reason, speech_text, comparison_text, evidence_json, created_at/updated_at |
    | `analysis_stage_metric` | id, session_id(FK), stage, attempt_no(≥1), duration_ms, processed_count, processed_unit, cache_hit, created_at. (세션, 단계, 시도 번호) 조합은 중복 불가 |
    | `analysis_cache` | cache_key(PK), cache_type(STT/OCR), video_sha256, engine_name, model_name, config_hash, file_path(캐시 폴더 기준 상대 경로), created_at, last_used_at |

  - 인덱스 6개: 세션 생성 시각, 지적의 (세션, 시작)·(세션, 검수 상태)·(세션, 판정), 측정의 (세션, 단계), 캐시의 (해시, 유형).
  - **Repository 기능**

    | 메서드 | 기능 |
    |---|---|
    | `create_session` | UUID로 PREPARING 세션을 만든다(설정 스냅샷은 JSON) |
    | `get_session` | 세션 조회. 없으면 `KeyError` |
    | `recent_sessions(limit=5)` | COMPLETED 세션만 최신순(DAT-02) |
    | `set_status` | ANALYZING이면 `started_at`을 처음 한 번만 기록. 종료 상태면 `error_code`와 `finished_at` 기록 |
    | `set_subtitle` | 자막 출처, 생성 SRT 경로, 자막 영역 JSON 갱신 |
    | `add_review_time` | 검수 시간 누적(음수면 `ValueError`) |
    | `complete_session` | 지적 일괄 저장과 COMPLETED 전환을 한 트랜잭션으로(ANL-10) |
    | `list_issues` | (start_ms, created_at, id) 순 조회 |
    | `set_review_status` | 검수 상태와 `updated_at`만 변경. 대상이 없으면 `KeyError` |
    | `review_counts` | 전체·미검토·확정·기각 개수와 판정별 개수 |
    | `record_stage_metric`, `list_stage_metrics` | 단계 측정 저장·조회(ANL-14) |
    | `register_cache`, `find_cache`, `delete_cache` | 캐시 인덱스 등록·조회(조회 때 `last_used_at` 갱신)·삭제 |
    | `close_all` | 모든 스레드의 연결을 닫는다(워커 스레드 종료는 호출자 책임) |

  - **없는 기능**: 세션 삭제, 세션 목록 전체 조회, 실패·취소 세션 조회, 지적 수정.
- **출력**: 저장·조회 결과
- **예외·실패 처리**: SQLite 오류는 호출하는 쪽이 ERR-09 등으로 변환한다(ANL-13, REV-04). DB 버전이 더 최신일 때의 `RuntimeError`는 앱이 따로 처리하지 않는다.
- **구현 위치**: `storage/db.py`(`connect`, `migrate`, `SCHEMA_VERSION`), `storage/repository.py`(`Repository`, `SessionRecord`)

#### DAT-02 최근 작업
- **설명**: 완료된 분석을 사이드바에 최근순으로 보여 주고, 눌러서 다시 검수 화면으로 연다.
- **입력**: 사이드바 "최근 작업" 버튼
- **동작 규칙**
  - COMPLETED 세션만 완료 시각(없으면 생성 시각) 최신순으로 최대 5건(`RECENT_LIMIT`)을 영상 이름 버튼으로 보여 준다(툴팁에 경로).
  - 누르면 그 세션을 검수 화면으로 연다. 영상 경로는 기록된 경로를 쓰고 **자막 경로는 None으로 둔다**.
  - 분석 중에는 버튼이 비활성이다. 목록을 못 읽으면 조용히 비운다.
- **출력**: 검수 화면
- **예외·실패 처리**: 열기에 실패하면 "기록 열기 실패 / 지난 분석 기록을 열 수 없습니다."를 안내한다. 기록된 영상 파일이 없으면 재생 실패 안내가 뜬다(REV-03).
- **참고(코드 기준)**: 지난 세션을 열 때 자막 경로가 None이라, 그 상태에서 "다시 시도"를 하면 자막 없이 실행된다.
- **구현 위치**: `ui/main_window.py`(`_refresh_recent`, `_open_past`, `RECENT_LIMIT`), `storage/repository.py`(`recent_sessions`), `review_state.py`(`ReviewState`)

#### DAT-03 파일 캐시
- **설명**: STT·OCR 원시 결과를 파일로 저장해 두고 재사용한다.
- **입력**: 캐시 키, 결과 payload
- **동작 규칙**
  - **키 구성**(`make_key`): `config_hash` = 설정 딕셔너리를 키 정렬해 JSON으로 만든 뒤 SHA-256의 앞 16자. `key` = SHA-256 of `"영상sha|유형|엔진|모델(없으면 빈 문자열)|config_hash"`.
  - **파일 위치**: `{데이터 폴더}/cache/{stt|ocr}/{key}.json`
  - **저장**(`put`): 같은 폴더에 임시 파일로 쓰고 `fsync`한 뒤 `os.replace`로 원자적으로 교체하고, 그다음 인덱스(`analysis_cache`)에 등록한다. 실패하면 임시 파일을 지우고 예외를 다시 올린다.
  - **조회**(`get`): 인덱스에 없으면 None. 파일이 없거나(`OSError`) JSON이 손상됐으면(`ValueError`) **인덱스 행을 지우고** None을 반환한다. 그러면 파이프라인이 다시 계산해서 저장한다.
- **출력**: 캐시된 payload 또는 None
- **예외·실패 처리**: 손상은 복구 경로로 처리한다.
- **참고(코드 기준)**: 용량 제한이나 오래된 항목 정리 기능은 없다(`last_used_at`은 갱신만 한다). 손상된 캐시 파일 자체는 지우지 않는다.
- **구현 위치**: `storage/cache.py`(`FileCache`, `make_key`, `hash_file`), `storage/repository.py`(`register_cache`, `find_cache`, `delete_cache`)

---

### 4.7 시스템 (SYS)

#### SYS-01 앱 시작과 조립
- **설명**: 저장소·엔진·화면을 연결해 앱을 띄운다.
- **입력**: 실행(`python main.py` 또는 `python -m barrierfree`)
- **동작 규칙**
  - **진입점**: `main.py`는 `src`를 `sys.path`에 추가하고, 저장소 루트에 `ffmpeg/` 폴더가 있으면 `BARRIERFREE_FFMPEG_DIR`의 기본값으로 지정(`setdefault`)한 뒤 `app.main()`을 호출한다. `python -m barrierfree`도 `app.main()`을 호출한다.
  - **데이터 폴더**(`default_data_dir`): ① 환경변수 `BARRIERFREE_DATA_DIR` ② Qt `AppDataLocation/barrierfree` ③ 둘 다 없으면 `~/.barrierfree`
  - **서비스 구성**(`build_services`): 데이터 폴더 생성 → 기본 `AnalysisConfig` → 장치 감지 → 추천 모델 → `Repository`(`barrierfree.db`) → `FileCache`(`cache/`) → `AppServices`(설정 파일 경로, 파이프라인 생성자, 자막 영역 확인 대화상자, 설정 대화상자, 플레이어).
  - **엔진 캐시**: 모델 로딩 비용을 줄이려고 엔진을 재사용한다. STT는 (실제 사용 모델, 모델 폴더), OCR은 모델 폴더를 키로 쓴다. 모델 폴더는 설정의 `model_dir`, 없으면 배포본에 동봉된 폴더(SYS-06)다. OCR은 장치가 cuda일 때만 GPU를 쓴다.
  - **파이프라인 생성**: 분석할 때마다 `AnalysisPipeline`을 새로 만든다. 설정은 `apply_settings`로 반영하고, 개발용 산출물 설정(SYS-05)은 매번 환경변수에서 다시 읽는다.
  - **앱 실행**(`main`): Fusion 스타일과 테마 스타일시트 적용 → ffmpeg 확인(SYS-02) → 서비스 구성 → `MainWindow` 표시. 종료할 때 `repo.close_all()`을 호출한다.
- **출력**: 실행 중인 앱, 종료 코드
- **예외·실패 처리**: ffmpeg가 없으면 종료 코드 1이다(SYS-02).
- **구현 위치**: `app.py`(`main`, `build_services`, `default_data_dir`, `_EngineCache`, `AppServices`), `main.py`, `__main__.py`

#### SYS-02 ffmpeg 탐색과 시작 시 확인
- **설명**: 번들·환경변수·PATH에서 `ffmpeg`·`ffprobe`를 찾고, 앱 시작 때 있는지 확인한다.
- **입력**: 실행 파일 이름("ffmpeg", "ffprobe")
- **동작 규칙**
  - **탐색 순서**(`find_binary`): ① 환경변수 `BARRIERFREE_FFMPEG_DIR` ② PyInstaller 배포본의 번들 폴더(`bundle_roots()/ffmpeg`) ③ PATH(`shutil.which`). 각 폴더에서 `name`과 `name.exe`를 모두 확인한다.
  - **시작 확인**(`ensure_ffmpeg`): `ffmpeg`와 `ffprobe`를 모두 찾아본다. 없으면 경고 대화상자(ERR-04 문구)를 띄우고 앱은 종료 코드 1로 끝난다.
  - **실행**(`_run`): 취소 토큰이 있으면 0.2초마다 확인하고, 취소되면 프로세스를 종료한 뒤 `CancelledError`를 낸다. 제한 시간을 넘으면 `FfmpegError`다. ffprobe는 30초, 오디오 추출은 30분이다.
  - 예외 계층: `FfmpegError` ⊃ `FfmpegNotFoundError`, `VideoReadError`(⊃ `VideoFileNotFoundError`, `UnsupportedVideoError`), `NoAudioStreamError`, `VideoTooLongError`.
- **출력**: 실행 파일 경로
- **예외·실패 처리**: 못 찾으면 `FfmpegNotFoundError`(ERR-04)
- **구현 위치**: `media/ffmpeg.py`(`find_binary`, `_bundled_dirs`, `_run`), `app.py`(`ensure_ffmpeg`), `paths.py`(`bundle_roots`)

#### SYS-03 메인 창 틀
- **설명**: 하나의 창 안에서 등록 → 분석 → 검수 화면을 전환하고, 공통 틀(헤더·사이드바)을 제공한다.
- **입력**: 화면 전환 신호, 사용자 조작
- **동작 규칙**
  - **창**: 제목 "배리어프리 영상 AI 점검 도구", 기본 1280×800, 최소 1000×680. 화면 전환은 `QStackedWidget`이다.
  - **헤더**: 브랜드 "영상의 틈"과 작업 이름(기본 "새 영상 점검", 영상이 있으면 영상 파일 이름), [도움말]. 도움말은 3단계 사용법을 정보 대화상자로 보여 준다.
  - **경로 표시줄**: "작업 / 영상 점검 / {등록·분석·검수}". 검수 단계에서는 숨긴다(검수 화면이 자체 표시줄을 가진다).
  - **사이드바**: [영상 점검]/[새 영상 점검] 버튼(검수 화면에서는 새 분석 흐름, 그 밖에는 등록 화면으로 이동, 분석 중에는 반응 없음), [설정](SET-01), 최근 작업(DAT-02).
  - **화면 전환**: 분석 시작 → 분석 중 화면 / 성공 → 검수 화면 / 취소 → 등록 화면과 "분석이 취소되었습니다." 안내 / 실패 → 분석 중 화면에 머물며 오류 패널 표시. 등록 화면으로 갈 때 재생을 멈추고 검수 시간을 저장한다.
  - **새 영상 분석**(검수 화면): 미검토가 있으면 "아직 검수하지 않은 항목이 N건 있습니다. 검수한 내용은 이미 저장되어 있습니다…"로 확인받고, 예이면 영상을 지우고 등록 화면으로 간다. 미검토가 0건이면 확인 없이 이동한다.
  - **창 닫기 보호**: 분석 중에 닫으면 "분석이 진행 중입니다. 취소하고 종료할까요?"로 확인한다. 예이면 취소를 요청하고 최대 10초 기다린다. 그래도 끝나지 않으면 "종료 보류 / 분석을 정리하는 중입니다. 잠시 후 다시 종료해 주세요."를 안내한다. 종료 직전에 검수 시간을 저장한다.
  - **공통 대화상자**(`Dialogs`/`QtDialogs`): 화면 코드가 모달 창을 직접 부르지 않도록 모은 인터페이스다. 영상 열기(영상 확장자 필터), 자막 열기(`*.srt *.smi *.sami`), CSV 저장("검수 결과 저장"), 정보·경고, 확인(기본값 "아니오"). 영상·자막 열기에는 "모든 파일 (*)" 필터도 함께 있어서 확장자는 대화상자에서 강제되지 않고, 최종 판단은 REG-01·REG-02의 검증이 한다.
  - **테마**: 색 토큰(`COLORS`)과 앱 전체 스타일시트(`stylesheet()`), 칩 톤 5종(success, warn, danger, info, neutral). 글꼴 목록은 "Apple SD Gothic Neo", "Malgun Gothic", "Segoe UI", sans-serif이다.
  - **없는 것**: 단축키, 메뉴바, 영상 드래그 앤 드롭(빈 화면에 "DropArea"라는 이름의 상자만 있고 드롭을 처리하지 않는다).
- **출력**: 화면 전환, 대화상자
- **예외·실패 처리**: 분석 실패는 분석 중 화면의 오류 패널에서 [다시 시도]/[처음으로]로 처리한다(ANL-12).
- **구현 위치**: `ui/main_window.py`(`MainWindow`, `_build_header`, `_build_sidebar`, `_start`, `_on_succeeded`, `_on_failed`, `_on_cancelled`, `_new_analysis`, `closeEvent`), `ui/dialogs.py`(`Dialogs`, `QtDialogs`), `ui/theme.py`

#### SYS-04 백그라운드 분석 실행
- **설명**: 분석을 UI와 분리된 스레드에서 실행해 화면이 멈추지 않게 한다.
- **입력**: 분석 실행 함수(`runner(cancel, confirm, progress)`)
- **동작 규칙**
  - `AnalysisWorker(QThread)`는 파이프라인을 직접 만들지 않고 `runner`를 받아 실행한다.
  - **신호**: `progress(StageEvent)`, `awaiting_region(RegionEstimate)`, `succeeded(PipelineResult)`, `failed(PipelineError)`, `cancelled()`
  - **취소**: `request_cancel()`이 `CancelToken.cancel()`을 부르고, 파이프라인에서 `CancelledError`가 나면 `cancelled`를 보낸다.
  - **자막 영역 확인 대기**: 파이프라인이 확인을 요청하면 `awaiting_region`을 보내고 `threading.Event`를 0.1초 간격으로 확인하며 기다린다. 기다리는 중 취소되면 풀린다. UI는 `provide_decision`으로 답한다.
- **출력**: 위 신호
- **예외·실패 처리**: 예상 밖 예외는 로그로 남기고 `PipelineError("ERR-99", "분석 중 알 수 없는 오류가 발생했습니다.")`로 바꿔 보낸다. 앱은 종료되지 않는다.
- **구현 위치**: `ui/worker.py`(`AnalysisWorker`, `request_cancel`, `provide_decision`, `_confirm_region`)

#### SYS-05 개발용 산출물 저장
- **설명**: 분석이 무엇을 듣고 읽었는지 눈과 귀로 확인할 수 있도록, 환경변수를 지정했을 때만 중간 산출물을 파일로 남긴다. 기본은 꺼져 있고, 꺼져 있으면 분석 동작과 결과는 달라지지 않는다.
- **입력**: 환경변수 3개
- **동작 규칙**

  | 환경변수 | 저장 내용과 위치 |
  |---|---|
  | `BARRIERFREE_AUDIO_DIR` | `{영상이름}_{세션ID}.mp3`와 같은 이름의 음성 인식 결과 `.srt` |
  | `BARRIERFREE_IMAGE_DIR` | 1초 간격 JPG 캡처. 하위 폴더 `{영상이름}_{세션ID}/frame_%05d.jpg` |
  | `BARRIERFREE_OCR_DIR` | 분석에 쓴 화면 글자 OCR 결과를 자막처럼 묶은 `{영상이름}_{세션ID}.srt`(캡처 이미지를 다시 인식하지 않는다) |

  - 값의 앞뒤 공백을 제거한 뒤 비어 있으면 꺼진 것으로 본다. 세 변수가 모두 비면 기능 전체가 꺼진다(`from_env`가 None).
  - MP3는 원본에서 직접 `libmp3lame -q:a 2`(VBR 약 190kbps)로 만든다. 음성 SRT는 MP3 저장에 성공했고 큐가 있을 때만 쓴다. 화면 글자 SRT도 큐가 0개면 쓰지 않는다. 화면 글자 SRT는 신뢰도 0.5 이상인 확실한 글자(`texts`)만 쓰고 불확실한 글자는 제외하며, 자막 영역 안 글자도 포함된다.
  - 호출 시점: MP3는 음성 인식 직전, 음성 SRT는 음성 인식 직후, 캡처와 화면 글자 SRT는 OCR 단계 직후다. 오디오 트랙이 없으면 MP3와 음성 SRT는 호출되지 않는다.
- **출력**: 지정한 폴더의 파일
- **예외·실패 처리**: 저장에 실패해도(`FfmpegError`, `OSError`) 분석을 멈추지 않고 안내 문구만 남긴다. 실패·취소 시 반쪽 파일과 폴더는 지운다.
- **구현 위치**: `dev_artifacts.py`(`DevArtifacts`, `from_env`, `save_audio`, `save_speech_srt`, `save_screen`), `media/ffmpeg.py`(`save_audio_mp3`, `save_frames_jpg`), `pipeline.py`(`_analyze`)

#### SYS-06 번들 자원 위치
- **설명**: 배포본(PyInstaller)에 동봉된 모델 폴더를 찾는다.
- **입력**: 환경변수, 실행 환경
- **동작 규칙**
  - `bundle_roots()`: 배포본(`sys.frozen`)일 때만 `sys._MEIPASS`와 실행 파일 폴더를 돌려준다. 개발 환경에서는 빈 목록이다.
  - `bundled_model_dir()`: ① 환경변수 `BARRIERFREE_MODEL_DIR`(실제 폴더일 때만) ② 각 번들 루트 아래 `models/` 폴더. 없으면 None이다.
  - 사용자가 설정 화면에서 지정한 모델 경로가 동봉 폴더보다 우선한다(SYS-01).
- **출력**: 모델 폴더 경로 또는 None
- **예외·실패 처리**: 없음
- **구현 위치**: `paths.py`(`bundle_roots`, `bundled_model_dir`)

---

## 부록 A. 도메인 열거형

값은 `models.py`의 정의와 같고, 검수 화면과 CSV에 쓰이는 한글 라벨을 함께 적는다. DB의 `CHECK` 제약과 1:1로 대응한다.

| 열거형 | 값 (화면 라벨) |
|---|---|
| `Verdict` (AI 판정, 3종) | `ERROR`(문제 의심), `REVIEW_REQUIRED`(검수 필요), `UNDETERMINED`(판단 불가) |
| `ReviewStatus` (사람의 처리, 3종) | `UNREVIEWED`(미검토), `CONFIRMED`(확정), `DISMISSED`(기각) |
| `IssueSource` (3종) | `M1`(자막 비교), `M2`(화면 정보 비교), `M2_SCREEN_DEPENDENT`(화면 의존 표현) |
| `SubtitleSource` (4종) | `FILE`(자막 파일), `OCR`(영상 속 자막을 추출), `NONE`(자막 없음), `UNKNOWN`(세션 생성 직후, 아직 정해지지 않음) |
| `SessionStatus` (6종) | `PREPARING`, `ANALYZING`, `AWAITING_CONFIRMATION`(자막 영역 확인 대기), `COMPLETED`, `FAILED`, `CANCELLED` |

**`IssueKind` (12종)**

| 출처 | 값 | 화면 라벨 | 만드는 기능 |
|---|---|---|---|
| M1 | `VALUE_MISSING` | 주요 정보 누락 의심 | ANL-07 |
| M1 | `VALUE_MISMATCH` | 주요 정보 불일치 의심 | ANL-07 |
| M1 | `SUBTITLE_MISSING` | 자막 누락 의심 | ANL-07 |
| M1 | `EXPRESSION_DIFFERENCE` | 표현 차이 | ANL-07 |
| M1 | `STT_UNCERTAIN` | 음성 확인 필요 | ANL-07, ANL-08(강등) |
| M2 | `SCREEN_ONLY_INFO` | 화면 정보 누락 의심 | ANL-08 |
| M2 | `SCREEN_VALUE_MISMATCH` | 화면 값 확인 필요 | ANL-08 |
| M2 | `NO_SPEECH_TO_COMPARE` | 비교할 음성 없음 | ANL-08 |
| M2 | `MENTIONED_ELSEWHERE` | 전달 시점 확인 필요 | ANL-08 |
| M2 | `ALWAYS_ON_SCREEN` | 영상 내내 표시되는 정보 | ANL-08 |
| M2 | `OCR_UNCERTAIN` | 화면 확인 필요 | ANL-08 |
| M2-2 | `SCREEN_DEPENDENT` | 화면 의존 표현 | ANL-09 |

**`Issue` 필드**: `source`, `kind`, `verdict`, `start_s`, `end_s`, `reason`, `id`(uuid4), `speech_text`, `comparison_text`, `confidence`, `severity`, `evidence`(dict), `review_status`(기본 `UNREVIEWED`). 시각이 유한수가 아니거나 `start_s < 0`, `end_s < start_s`이면 `ValueError`다.

**기타 도메인 모델**: `SpeechSegment(start_s, end_s, text, avg_logprob, no_speech_prob)`, `SubtitleCue(start_s, end_s, text)`, `ScreenText(text, start_s, end_s, confidence, box)`(`box`는 정규화 좌표 (x, y, w, h), 0~1)

## 부록 B. 오류 코드

| 코드 | 의미 | 발생 위치 | 관련 기능 |
|---|---|---|---|
| ERR-01 | 영상 파일 없음 / 지원하지 않는 형식 / 읽을 수 없음 | `pipeline.py` | REG-01, ANL-01, ANL-02 |
| ERR-02 | 영상 길이 20분 초과 | `pipeline.py` | REG-01, ANL-01 |
| ERR-03 | 자막 파일 형식·인코딩 문제 | `pipeline.py` | REG-02, ANL-01 |
| ERR-04 | 영상 처리(ffmpeg) 실패, ffmpeg를 찾을 수 없음 | `pipeline.py`, `app.py` | ANL-02, SYS-02 |
| ERR-05 | **코드에 할당되어 있지 않다**(`engines/stt.py` 주석에만 언급) | – | – |
| ERR-06 | 음성 인식·화면 글자 인식 모델을 불러올 수 없음 | `pipeline.py` | ANL-03, ANL-04 |
| ERR-07 | 음성 인식 실패 | `pipeline.py` | ANL-03 |
| ERR-08 | 화면 글자 인식 실패 | `pipeline.py` | ANL-04 |
| ERR-09 | 결과 저장 실패 / 작업 폴더·파일 사용 불가. 검수 상태 저장 실패도 같은 코드를 내부 예외(`SaveError`)에 쓰지만, 화면 문구에는 코드가 표시되지 않는다 | `pipeline.py`, `review_state.py` | ANL-10, REV-04 |
| ERR-10 | 영상 재생 실패(분석 결과는 그대로 확인 가능) | `ui/review_page.py` | REV-03 |
| ERR-11 | CSV 내보내기 실패(코드는 내부 예외 `ExportError`에만 있고, 화면 문구에는 코드가 표시되지 않는다) | `review_state.py` | REV-07 |
| ERR-99 | 알 수 없는 오류 | `pipeline.py`, `ui/worker.py` | ANL-13, SYS-04 |

## 부록 C. 분석 설정 상수와 기본값

`AnalysisConfig`(`config.py`)의 기본값이다. 코드 주석상 모든 임계값은 **임시값(실측 전 시작값)**이다. 사용자가 바꿀 수 있는 것은 `whisper_model`(모델 선택), `model_dir`, `subtitle_region_ratio` 셋뿐이고(SET-01, SET-03), 나머지는 코드 상수다.

| 그룹 | 상수 = 기본값 | 쓰이는 기능 |
|---|---|---|
| 입력 | `max_video_seconds = 1200`, `video_extensions = (.mp4, .mov, .mkv, .avi, .wmv)` | REG-01 |
| 모델 | `whisper_model = "small"`, `whisper_compute_type_cpu = "int8"`, `model_dir = None` | ANL-03, SET-02 |
| STT 신뢰도 | `min_speech_chars = 3`, `no_speech_prob_max = 0.6`, `stt_avg_logprob_min = -1.0` | ANL-07, ANL-09 |
| 자막 비교(M1) | `candidate_window_s = 2.0`, `candidate_window_wide_s = 6.0`, `expression_similarity_min = 0.5` | ANL-07 |
| OCR | `ocr_confidence_min = 0.5`, `ocr_max_width = 1280`, `screen_ocr_fps = 1.0`, `subtitle_ocr_fps = 2.0`, `frame_diff_skip_threshold = 0.02`, `ocr_max_gap_samples = 1`, `ocr_min_text_chars = 2` | ANL-04, ANL-05 |
| 화면 정보 비교(M2) | `screen_merge_similarity = 0.85`, `screen_near_window_s = 5.0`, `always_visible_ratio = 0.7` | ANL-04, ANL-08 |
| 자막 영역 | `subtitle_region_ratio = 0.25`, `subtitle_min_texts = 3`, `subtitle_min_changes_per_minute = 4.0`, `subtitle_changes_for_full_score = 10.0`, `subtitle_region_score_min = 0.5` | ANL-05, SET-01 |
| 화면 의존 표현(M2-2) | `screen_dependent_phrases = ("보시는 것처럼", "보시다시피", "보시는 바와 같이", "화면에서", "화면과 같이", "이쪽으로", "저쪽으로", "다음과 같이", "이렇게 하시면")` | ANL-09 |
| 검수 시간 | `idle_threshold_s = 120.0` | REV-06 |
| 모델 추천 | `recommend_cuda_model = "medium"`, `recommend_cpu_model = "small"`, `recommend_low_ram_model = "base"`, `low_ram_threshold_gb = 8.0` | SET-02 |
| (쓰이지 않음) | `corner_logo_max_area_ratio = 0.05` | 부록 E 참고 |

분석 설정 전체는 세션을 만들 때 JSON 스냅샷(`to_snapshot`)으로 `analysis_session.config_snapshot_json`에 기록된다(ANL-01). 튜플은 리스트로 바뀌어 저장된다.

## 부록 D. 환경변수

| 이름 | 용도 | 읽는 곳 |
|---|---|---|
| `BARRIERFREE_DATA_DIR` | 데이터 폴더(DB, 캐시, 설정, 산출물) 위치. 없으면 Qt `AppDataLocation`, 그것도 없으면 `~/.barrierfree` | `app.py`(`default_data_dir`) |
| `BARRIERFREE_FFMPEG_DIR` | ffmpeg·ffprobe가 있는 폴더(PATH보다 우선). `main.py`가 저장소 루트에 `ffmpeg/`가 있으면 기본값으로 지정 | `media/ffmpeg.py`(`find_binary`), `main.py` |
| `BARRIERFREE_MODEL_DIR` | 동봉 모델 폴더를 직접 지정(실제 폴더일 때만) | `paths.py`(`bundled_model_dir`) |
| `BARRIERFREE_AUDIO_DIR` | 개발용: 음성 MP3와 음성 인식 SRT 저장 폴더 | `dev_artifacts.py` |
| `BARRIERFREE_IMAGE_DIR` | 개발용: 1초 간격 화면 캡처(JPG) 저장 폴더 | `dev_artifacts.py` |
| `BARRIERFREE_OCR_DIR` | 개발용: 화면 글자 OCR 결과 SRT 저장 폴더 | `dev_artifacts.py` |

## 부록 E. 코드 기준 참고 사항

기획 문서와의 비교가 아니라, **코드 안에서 정의만 되어 있거나 임시값으로 남아 있는 것**을 기록한다. 이 기능들이 "있다"고 오해하지 않도록 둔다.

| 구분 | 내용 | 위치 |
|---|---|---|
| 정의만 있고 쓰이지 않음 | `corner_logo_max_area_ratio`: 주석의 "모서리 로고 판별"은 구현되어 있지 않다 | `config.py` |
| 정의만 있고 쓰이지 않음 | `Issue.severity`: DB 컬럼(`HIGH`/`MEDIUM`/`LOW`)은 있으나 분석이 값을 채우지 않고 UI도 보여 주지 않는다(주석: 기준 미정 D-08) | `models.py`, `storage/db.py` |
| 정의만 있고 쓰이지 않음 | 호출하는 곳이 없는 메서드: `ReviewState.is_complete`, `ActiveTimeTracker.total_ms`, `IssueTableModel.issue_at`, `IssueTableModel.kind_label`, `VideoPlayerPanel.current_seconds`, `VideoPlayerPanel.source_url` | `review_state.py`, `ui/` |
| 임시값 | 모든 분석 임계값, 모델 추천 규칙(부록 C), 자막 영역 비율의 허용 범위 0.10~0.50(D-05 미확정), 검수 화면의 "AI 인식이 불확실" 기준 0.5(D-07 미확정) | `config.py`, `settings.py`, `ui/review_page.py` |
| 엔진 한계 | OCR 엔진은 EasyOCR 하나뿐이다(주석에 PaddleOCR 교체 가능성만 언급). GPU 실패 시 CPU로 바꾸는 폴백은 STT에만 있고 OCR에는 없다 | `engines/ocr.py`, `engines/stt.py` |
| 자막 형식 한계 | SMI는 읽기만 되고 쓰기는 없다. SMI는 건너뛴 블록 수를 세지 않는다 | `media/subtitles.py` |
| 설정 화면 | 시안에 있던 체크박스 3개(음성 인식 정확도 우선, 장면 분석 포함, 타임코드 표시)는 대응 기능이 없어 넣지 않았다. 모델 경로는 텍스트 입력만 있다 | `ui/settings_dialog.py` |
| UI에 없는 것 | 단축키, 메뉴바, 표 정렬, 영상 드래그 앤 드롭 | `ui/` |
| 저장소 기능 | 세션 삭제, 캐시 정리(용량 제한·오래된 항목 삭제), 지적 수정 기능이 없다 | `storage/` |

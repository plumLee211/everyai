"""분석 설정. 모든 임계값은 '시작값'이며 평가 정답 데이터(Phase -1)로만 조정한다."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class AnalysisConfig:
    # --- 입력 ---
    max_video_seconds: float = 20 * 60
    video_extensions: tuple[str, ...] = (".mp4", ".mov", ".mkv", ".avi", ".wmv")

    # --- 모델 ---
    whisper_model: str = "small"  # 임시 기본값, Task 0 실측 후 확정
    model_dir: str | None = None  # 모델 경로 설정(결정 3)

    # --- STT 신뢰도 ---
    min_speech_chars: int = 3
    no_speech_prob_max: float = 0.6
    stt_avg_logprob_min: float = -1.0  # 이보다 낮으면 '판단 불가: 음성 확인 필요'

    # --- M1 ---
    candidate_window_s: float = 2.0
    candidate_window_wide_s: float = 6.0
    expression_similarity_min: float = 0.5  # 값 없는 문장의 '표현 차이' 경계(임시)

    # --- OCR / M2 ---
    ocr_confidence_min: float = 0.5
    ocr_max_width: int = 1280
    screen_ocr_fps: float = 1.0
    subtitle_ocr_fps: float = 2.0
    frame_diff_skip_threshold: float = 0.02
    ocr_max_gap_samples: int = 1  # 글자가 이만큼 연속으로 안 읽혀도 같은 구간으로 잇는다(깜빡임)
    ocr_min_text_chars: int = 2  # 공백 제외 이보다 짧은 인식 결과는 잡음으로 버린다
    screen_merge_similarity: float = 0.85
    screen_near_window_s: float = 5.0
    always_visible_ratio: float = 0.7
    corner_logo_max_area_ratio: float = 0.05

    # --- 자막 영역 (영상 높이 기준 하단 비율, 결정 UX-D02) ---
    subtitle_region_ratio: float = 0.25
    # 자막 트랙 판단(영역 단위, 실측 전 임시값)
    subtitle_min_texts: int = 3  # 영역 안에서 이만큼은 글자가 나타나야 한다
    subtitle_min_changes_per_minute: float = 4.0  # 이름·기관명 바는 이보다 드물게 바뀐다
    subtitle_changes_for_full_score: float = 10.0
    subtitle_region_score_min: float = 0.5

    # --- M2-2: 화면 의존 표현. 좁게 시작한다. ---
    screen_dependent_phrases: tuple[str, ...] = (
        "보시는 것처럼",
        "보시다시피",
        "보시는 바와 같이",
        "화면에서",
        "화면과 같이",
        "이쪽으로",
        "저쪽으로",
        "다음과 같이",
        "이렇게 하시면",
    )

    # --- 검수 시간 측정 (결정 Q11) ---
    idle_threshold_s: float = 120.0

    # --- 모델 추천 규칙 (결정 Q5, 실측 전 임시값) ---
    recommend_cuda_model: str = "medium"
    recommend_cpu_model: str = "small"
    recommend_low_ram_model: str = "base"
    low_ram_threshold_gb: float = 8.0

    def to_snapshot(self) -> dict[str, object]:
        """세션에 저장할 JSON 직렬화 가능한 설정 스냅샷."""
        data = asdict(self)
        for key, value in data.items():
            if isinstance(value, tuple):
                data[key] = list(value)
        return data

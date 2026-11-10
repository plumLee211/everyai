"""analysis/subtitle_region.py 테스트: 하단 영역이 자막 트랙인지 '영역 전체'로 판단한다.

줄 단위로 STT와 맞는지 보고 정하면 틀린 자막이 '자막 아님'으로 빠져 점검 대상에서 사라진다.
그래서 내용 일치도는 보조 신호일 뿐이고, 변화 빈도와 발화와의 시간 겹침이 주된 신호다.
"""

from __future__ import annotations

from barrierfree.analysis.subtitle_region import (
    estimate_subtitle_region,
    in_rect,
    region_rect,
)
from barrierfree.config import AnalysisConfig
from barrierfree.models import ScreenText, SpeechSegment

CFG = AnalysisConfig()
BOTTOM = (0.2, 0.82, 0.6, 0.06)  # 하단 중앙 자막 위치
TOP = (0.1, 0.05, 0.3, 0.06)


def st(text: str, start: float, end: float, box: tuple[float, float, float, float] | None = BOTTOM
       ) -> ScreenText:
    return ScreenText(text=text, start_s=start, end_s=end, confidence=0.9, box=box)


def sp(start: float, end: float, text: str) -> SpeechSegment:
    return SpeechSegment(start, end, text, -0.2, 0.01)


SENTENCES = [
    "안녕하세요 오늘은 복지 서비스를 안내해 드리겠습니다",
    "신청은 홈페이지에서 하실 수 있습니다",
    "접수 기간은 이번 달 말까지입니다",
    "서류는 주민센터에서 받으실 수 있어요",
    "궁금한 점은 담당 부서로 문의해 주세요",
    "지원 대상은 만 육십오 세 이상입니다",
    "방문 접수도 가능합니다",
    "결과는 문자로 알려 드립니다",
    "자세한 내용은 누리집을 확인하세요",
    "감사합니다 좋은 하루 보내세요",
]


def speech_and_subtitles(
    subtitle_texts: list[str] | None = None,
) -> tuple[list[SpeechSegment], list[ScreenText]]:
    shown = subtitle_texts or SENTENCES
    speech = [sp(i * 6.0, i * 6.0 + 3.0, s) for i, s in enumerate(SENTENCES)]
    texts = [st(s, i * 6.0, i * 6.0 + 3.0) for i, s in enumerate(shown)]
    return speech, texts


# ---------- 영역 ----------


def test_region_rect_is_bottom_strip() -> None:
    assert region_rect(0.25) == (0.0, 0.75, 1.0, 0.25)


def test_in_rect_uses_box_center_and_ignores_missing_boxes() -> None:
    rect = region_rect(0.25)
    assert in_rect(st("하단", 0, 1, BOTTOM), rect) is True
    assert in_rect(st("상단", 0, 1, TOP), rect) is False
    assert in_rect(st("위치 없음", 0, 1, None), rect) is False


# ---------- 판단 ----------


def test_real_subtitle_track_is_detected() -> None:
    speech, texts = speech_and_subtitles()
    est = estimate_subtitle_region(texts, speech, 60.0, CFG)
    assert est.is_subtitle is True
    assert est.text_count == 10
    assert est.speech_overlap > 0.95
    assert est.agreement > 0.8
    assert est.rect == region_rect(CFG.subtitle_region_ratio)


def test_wrong_subtitles_are_still_a_subtitle_track() -> None:
    # 내용이 전혀 달라도(= 점검해야 할 틀린 자막) 자막 영역이어야 한다. 순환 판정 금지.
    speech, _ = speech_and_subtitles()
    wrong = [f"전혀 다른 내용의 문장 번호 {i} 입니다 가나다라마바사" for i in range(10)]
    _, texts = speech_and_subtitles(wrong)
    est = estimate_subtitle_region(texts, speech, 60.0, CFG)
    assert est.agreement < 0.3
    assert est.is_subtitle is True


def test_single_persistent_name_bar_is_not_a_subtitle() -> None:
    speech, _ = speech_and_subtitles()
    bar = [st("서울시 복지정책과 홍길동 팀장", 0.0, 60.0)]
    est = estimate_subtitle_region(bar, speech, 60.0, CFG)
    assert est.is_subtitle is False


def test_few_name_bars_during_speech_are_not_a_subtitle() -> None:
    speech, _ = speech_and_subtitles()
    # 발화 구간(0~3, 6~9, ...) 안에서 뜬다.
    bars = [st("홍길동 팀장", 6, 9), st("김철수 주무관", 24, 27), st("박영희 담당", 42, 45)]
    est = estimate_subtitle_region(bars, speech, 60.0, CFG)
    assert est.speech_overlap > 0.9  # 말하는 중에 떴지만
    assert est.is_subtitle is False  # 바뀌는 빈도가 낮다


def test_texts_outside_the_region_are_ignored() -> None:
    speech, texts = speech_and_subtitles()
    moved = [st(t.text, t.start_s, t.end_s, TOP) for t in texts]
    est = estimate_subtitle_region(moved, speech, 60.0, CFG)
    assert est.text_count == 0
    assert est.is_subtitle is False


def test_without_any_speech_there_is_not_enough_evidence() -> None:
    _, texts = speech_and_subtitles()
    est = estimate_subtitle_region(texts, [], 60.0, CFG)
    assert est.speech_overlap == 0.0
    assert est.is_subtitle is False


def test_custom_region_ratio_changes_which_texts_count() -> None:
    speech, _ = speech_and_subtitles()
    mid = (0.2, 0.55, 0.6, 0.06)
    texts = [st(s, i * 6.0, i * 6.0 + 3.0, mid) for i, s in enumerate(SENTENCES)]
    assert estimate_subtitle_region(texts, speech, 60.0, CFG).is_subtitle is False
    assert estimate_subtitle_region(texts, speech, 60.0, CFG, ratio=0.5).is_subtitle is True


def test_estimate_carries_sample_texts_in_time_order_for_the_editor() -> None:
    speech, texts = speech_and_subtitles()
    est = estimate_subtitle_region(list(reversed(texts)), speech, 60.0, CFG)
    assert est.samples == tuple(SENTENCES[:5])  # 확인 화면에 보여 줄 앞쪽 5건


def test_samples_ignore_texts_outside_the_region() -> None:
    speech, texts = speech_and_subtitles()
    outside = [st("이름 바", 1, 2, TOP)]
    est = estimate_subtitle_region(texts + outside, speech, 60.0, CFG)
    assert "이름 바" not in est.samples


def test_empty_inputs_do_not_crash() -> None:
    est = estimate_subtitle_region([], [], 0.0, CFG)
    assert est.is_subtitle is False and est.score == 0.0

"""tests/eval 도구: 정답 초안 만들기, 틀린 자막 만들기, 화면 글자 미탐률."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from barrierfree.media.subtitles import load_subtitle_file, write_srt
from barrierfree.models import SpeechSegment, SubtitleCue
from tests.eval import draft, inject, screen_recall
from tests.eval.score import load_ground_truth

CUES = [
    SubtitleCue(0.0, 2.0, "안녕하세요 복지 서비스를 안내합니다"),
    SubtitleCue(2.0, 4.0, "신청 마감은 3월 15일입니다"),
    SubtitleCue(4.0, 6.0, "참가비는 30,000원입니다"),
    SubtitleCue(6.0, 8.0, "서류는 주민센터에서 받으세요"),
    SubtitleCue(8.0, 10.0, "문의는 담당 부서로 해 주세요"),
    SubtitleCue(10.0, 12.0, "감사합니다 좋은 하루 보내세요"),
]
ONE_EACH = inject.Plan(values=2, drops=1, swaps=1)


# ---------- 틀린 자막 만들기 ----------


def test_value_change_alters_exactly_one_number_and_keeps_its_length() -> None:
    changed = inject.change_number("신청 마감은 3월 15일입니다", seed=1)
    assert changed is not None and changed != "신청 마감은 3월 15일입니다"
    before, after = re.findall(r"\d+", "3월 15일"), re.findall(r"\d+", changed)
    assert [len(n) for n in after] == [len(n) for n in before]
    assert sum(a != b for a, b in zip(before, after, strict=True)) == 1
    assert re.sub(r"\d+", "#", changed) == "신청 마감은 #월 #일입니다"


def test_a_sentence_without_numbers_has_no_value_to_change() -> None:
    assert inject.change_number("감사합니다", seed=1) is None


def test_every_injected_mistake_becomes_one_ground_truth_item() -> None:
    cues, mistakes = inject.inject(CUES, ONE_EACH, seed=7)
    kinds = sorted(m.mutation for m in mistakes)
    assert kinds == ["drop", "swap", "swap", "value", "value"]
    assert all(m.source == "M1" and m.end_s > m.start_s for m in mistakes)
    assert len(cues) == len(CUES) - 1  # 뺀 문장 하나
    # 정답에 없는 자막은 원본 그대로다
    touched = {(m.start_s, m.end_s) for m in mistakes}
    untouched = [c for c in CUES if (c.start_s, c.end_s) not in touched]
    assert all(c in cues for c in untouched)
    # 정답에 있는 자막은 실제로 달라졌다
    assert all(c not in cues for c in CUES if (c.start_s, c.end_s) in touched)


def test_one_cue_gets_at_most_one_mistake_and_the_seed_reproduces_it() -> None:
    first = inject.inject(CUES, ONE_EACH, seed=7)
    assert first == inject.inject(CUES, ONE_EACH, seed=7)
    times = [(m.start_s, m.end_s) for m in first[1]]
    assert len(times) == len(set(times))


def test_asking_for_more_mistakes_than_possible_fails_loudly() -> None:
    with pytest.raises(ValueError, match="숫자"):
        inject.inject(CUES, inject.Plan(values=3), seed=1)


def test_command_writes_subtitles_and_a_truth_file_the_scorer_reads(tmp_path: Path) -> None:
    reference = write_srt(CUES, tmp_path / "ref.srt")
    out = tmp_path / "out"
    code = inject.main(
        [str(reference), str(out), "--video", "안내.mp4", "--type", "정책 안내",
         "--values", "2", "--drops", "1", "--swaps", "1", "--seed", "3"]
    )
    assert code == 0
    video_type, items = load_ground_truth(out / "안내_seed3.json")
    assert video_type == "정책 안내" and len(items) == 5
    assert json.loads((out / "안내_seed3.json").read_text(encoding="utf-8"))["video"] == "안내.mp4"
    assert len(load_subtitle_file(out / "안내_seed3.srt").cues) == 5


# ---------- 초안 ----------


def test_draft_turns_speech_segments_into_subtitle_cues() -> None:
    segments = [
        SpeechSegment(2.0, 3.5, " 둘째 ", -0.2, 0.0),
        SpeechSegment(0.0, 1.5, "첫째", -0.2, 0.0),
        SpeechSegment(4.0, 4.0, "길이 없음", -0.2, 0.0),
        SpeechSegment(5.0, 6.0, "   ", -0.2, 0.0),
    ]
    assert draft.segments_to_cues(segments) == [
        SubtitleCue(0.0, 1.5, "첫째"),
        SubtitleCue(2.0, 3.5, "둘째"),
    ]


def test_draft_never_overwrites_a_file_a_person_may_have_corrected(tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    out = tmp_path / "v.draft.srt"
    out.write_text("사람이 고친 내용", encoding="utf-8")
    assert draft.main([str(video), str(out)]) == 1
    assert out.read_text(encoding="utf-8") == "사람이 고친 내용"


# ---------- 화면 글자 미탐률 ----------


def truth(start: float, end: float, text: str) -> screen_recall.ScreenTruth:
    return screen_recall.ScreenTruth(start, end, text)


def test_screen_text_counts_as_seen_only_with_matching_text_at_the_same_time() -> None:
    observed = [
        SubtitleCue(1.0, 3.0, "문의 02-123-4567 복지관"),  # 여러 줄이 한 큐로 묶인 형태
        SubtitleCue(10.0, 11.0, "신청 기간 3월 15일"),
    ]
    truths = [
        truth(1.5, 2.5, "02-123-4567"),  # 포함
        truth(10.2, 10.8, "신청기간 3월 15일"),  # 띄어쓰기만 다름
        truth(20.0, 20.5, "신청 기간 3월 15일"),  # 시간이 다름
        truth(1.0, 2.0, "참가비 30000원"),  # 안 읽힘
    ]
    result = screen_recall.screen_text_recall(truths, observed)
    assert (result.seen, result.total) == (2, 4)
    assert result.miss_rate == pytest.approx(0.5)
    assert [t.text for t in result.missed] == ["신청 기간 3월 15일", "참가비 30000원"]


def test_a_wrong_number_is_a_miss_even_when_the_rest_matches() -> None:
    observed = [SubtitleCue(0.0, 2.0, "신청 기간 3월 16일")]
    result = screen_recall.screen_text_recall([truth(0.0, 2.0, "신청 기간 3월 15일")], observed)
    assert result.seen == 0


def test_short_lived_misses_are_reported_separately() -> None:
    truths = [truth(0.0, 0.5, "짧게 지나간 글자"), truth(5.0, 8.0, "오래 보인 글자")]
    result = screen_recall.screen_text_recall(truths, [])
    assert (result.short_total, result.short_seen) == (1, 0)


def test_no_screen_truth_has_no_miss_rate() -> None:
    assert screen_recall.screen_text_recall([], []).miss_rate is None


@pytest.mark.parametrize(
    "item",
    [{"start_s": 1, "end_s": 0, "text": "a"}, {"start_s": 0, "end_s": 1, "text": " "}, "x"],
)
def test_invalid_screen_truth_is_explained(tmp_path: Path, item: object) -> None:
    path = tmp_path / "t.json"
    path.write_text(json.dumps({"screen_texts": [item]}), encoding="utf-8")
    with pytest.raises(ValueError, match=r"screen_texts\[0\]"):
        screen_recall.load_screen_truth(path)

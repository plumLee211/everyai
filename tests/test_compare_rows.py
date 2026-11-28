"""대조 보기의 행 만들기와 대조 자료의 저장."""

from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path

import pytest

from barrierfree.analysis.compare_rows import CompareData, build_rows, row_for
from barrierfree.models import ScreenText, SpeechSegment, SubtitleCue
from barrierfree.storage.compare_data import compare_data_path, load_compare_data, save_compare_data


def sp(start: float, end: float, text: str = "발화") -> SpeechSegment:
    return SpeechSegment(start, end, text, -0.2, 0.01)


def st(start: float, end: float, text: str, conf: float = 0.9) -> ScreenText:
    return ScreenText(text=text, start_s=start, end_s=end, confidence=conf, box=None)


def data(speech=(), screen=(), uncertain=(), cues=()) -> CompareData:
    return CompareData(list(speech), list(screen), list(uncertain), list(cues))


def test_a_row_holds_the_speech_screen_text_and_subtitle_of_the_same_time() -> None:
    [row] = build_rows(
        data([sp(0, 5, "접수합니다")], [st(1, 4, "접수 안내")], [st(1, 4, "접ㅅ", 0.2)],
             [SubtitleCue(0, 5, "접수")])
    )
    assert (row.start_s, row.end_s, row.speech) == (0, 5, "접수합니다")
    assert row.screen == ["접수 안내"] and row.uncertain == ["접ㅅ"] and row.cues == ["접수"]


def test_screen_text_without_speech_gets_its_own_row() -> None:
    rows = build_rows(data([sp(0, 4), sp(12, 16)], [st(6, 9, "말 없는 안내")]))
    assert [r.speech for r in rows] == ["발화", None, "발화"]
    assert rows[1].screen == ["말 없는 안내"]


def test_a_silent_stretch_with_nothing_new_makes_no_row() -> None:
    assert [r.speech for r in build_rows(data([sp(0, 4), sp(12, 16)]))] == ["발화", "발화"]


def test_a_subtitle_without_speech_is_still_visible() -> None:
    rows = build_rows(data([sp(0, 4)], cues=[SubtitleCue(20, 23, "말 없는 자막")]))
    assert rows[-1].speech is None and rows[-1].cues == ["말 없는 자막"]


def test_text_that_stays_on_screen_is_listed_once_and_counted_afterwards() -> None:
    rows = build_rows(data([sp(0, 4), sp(5, 9), sp(10, 14)], [st(0, 14, "기관 이름")]))
    assert rows[0].screen == ["기관 이름"] and rows[0].carried == []
    later = [r for r in rows[1:] if r.speech]
    assert all(r.screen == [] and r.carried == ["기관 이름"] for r in later)


def test_a_barely_overlapping_neighbour_does_not_take_the_text() -> None:
    # 화면 글자의 끝 시각은 표본 간격만큼 넉넉하다. 앞 행에 살짝 걸친 것은 뒤 행의 것이다.
    rows = build_rows(data([sp(0, 10, "앞"), sp(10, 20, "뒤")], [st(9.8, 14, "새 글자")]))
    assert rows[0].screen == [] and rows[1].screen == ["새 글자"]


def test_a_long_silence_is_split_into_rows_that_can_be_reached() -> None:
    texts = [st(t, t + 2, f"안내{t}") for t in (2, 14, 27)]
    rows = build_rows(data([], texts))
    assert len(rows) == 3 and all(r.speech is None for r in rows)
    assert all(r.end_s - r.start_s <= 10 for r in rows)


@pytest.mark.parametrize("with_other_content", [True, False])
def test_a_suspected_gap_in_a_silence_keeps_its_rows(with_other_content: bool) -> None:
    # 받아쓰기가 놓친 곳이라 새로 보이는 것이 없어도, 그 시간이 보기에 있어야 한다.
    screen = [st(21, 23, "뒤쪽 안내")] if with_other_content else []
    rows = build_rows(data([sp(0, 4), sp(20, 24)], screen), [(8.0, 16.0)])
    silent = [r for r in rows if r.speech is None]
    assert silent and min(r.start_s for r in silent) <= 8 and max(r.end_s for r in silent) >= 16


def test_an_item_with_a_screen_text_belongs_to_the_row_that_shows_it() -> None:
    rows = build_rows(data([sp(0, 10, "앞"), sp(10, 20, "뒤")], [st(9.8, 14, "새 글자")]))
    assert row_for(rows, 9.8, 14.0, "새 글자") == 1
    assert row_for(rows, 9.8, 14.0) == 1  # 글자를 모르면 같은 겹침 규칙을 쓴다
    assert row_for(rows, 0.0, 10.0) == 0


def test_nothing_to_show_makes_no_rows() -> None:
    assert build_rows(data()) == []


def _check_every_text_once(seed: int) -> None:
    rng = random.Random(seed)

    def span() -> tuple[float, float]:
        start = round(rng.uniform(0, 60), 1)
        return start, round(start + rng.choice([0, 0.2, 1, 3, 15, 50]), 1)

    speech = [sp(*span()) for _ in range(rng.randrange(0, 8))]
    screen = [st(*span(), f"s{i}") for i in range(rng.randrange(0, 12))]
    uncertain = [st(*span(), f"u{i}", 0.2) for i in range(rng.randrange(0, 12))]
    cues = [SubtitleCue(*span(), f"c{i}") for i in range(rng.randrange(0, 6))]
    rows = build_rows(data(speech, screen, uncertain, cues))
    assert Counter(t for r in rows for t in r.screen) == Counter(t.text for t in screen), seed
    assert Counter(t for r in rows for t in r.uncertain) == Counter(t.text for t in uncertain), seed
    assert {t for r in rows for t in r.cues} == {c.text for c in cues}, seed
    assert sum(r.speech is not None for r in rows) == len(speech), seed
    assert [r.start_s for r in rows] == sorted(r.start_s for r in rows), seed


def test_every_screen_text_appears_as_new_exactly_once_for_random_input() -> None:
    for seed in range(500):
        _check_every_text_once(seed)


# ---------- 저장 ----------

SAMPLE = data([sp(0, 2, "안녕")], [st(0, 2, "글자")], [st(1, 2, "흐림", 0.2)],
              [SubtitleCue(0, 2, "자막")])


def test_compare_data_survives_saving_and_loading(tmp_path: Path) -> None:
    save_compare_data(tmp_path, "abc", SAMPLE)
    loaded = load_compare_data(tmp_path, "abc")
    assert loaded is not None and build_rows(loaded) == build_rows(SAMPLE)
    assert not list(compare_data_path(tmp_path, "abc").parent.glob("*.tmp"))


def test_missing_data_loads_as_none(tmp_path: Path) -> None:
    assert load_compare_data(tmp_path, "not-saved") is None


@pytest.mark.parametrize(
    "content",
    ["{", "[]", '{"version": 99}', "[" * 200_000,
     '{"version": 1, "speech": [{"s": 5, "e": 1, "t": "x"}], "screen": [], "uncertain": [],'
     ' "cues": []}',
     '{"version": 1, "speech": [], "screen": [{"s": 0, "e": 1}], "uncertain": [], "cues": []}'],
)
def test_damaged_data_loads_as_none(tmp_path: Path, content: str) -> None:
    path = compare_data_path(tmp_path, "abc")
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    assert load_compare_data(tmp_path, "abc") is None


def test_an_oversized_data_file_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_compare_data(tmp_path, "abc", SAMPLE)
    monkeypatch.setattr("barrierfree.storage.compare_data.MAX_BYTES", 10)
    assert load_compare_data(tmp_path, "abc") is None


@pytest.mark.parametrize("session_id", ["../x", "a/b", ""])
def test_a_session_id_cannot_leave_the_folder(tmp_path: Path, session_id: str) -> None:
    with pytest.raises(ValueError):
        compare_data_path(tmp_path, session_id)


def test_saved_file_is_plain_json(tmp_path: Path) -> None:
    save_compare_data(tmp_path, "abc", SAMPLE)
    stored = json.loads(compare_data_path(tmp_path, "abc").read_text(encoding="utf-8"))
    assert stored["version"] == 1 and stored["speech"][0]["t"] == "안녕"

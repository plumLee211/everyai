"""받아쓰기가 말소리를 놓쳤을 수 있는 구간: 찾기, 알리기, 그 구간의 판정 낮추기."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from barrierfree.analysis.speech_gaps import downgrade_in_gaps, suspected_gaps
from barrierfree.cancel import CancelledError
from barrierfree.config import AnalysisConfig
from barrierfree.models import (
    Issue,
    IssueKind,
    IssueSource,
    NoteCode,
    NoteSource,
    SpeechSegment,
    Verdict,
)
from barrierfree.review_state import ReviewState
from barrierfree.storage.cache import FileCache
from barrierfree.storage.repository import Repository
from tests import test_pipeline as fixtures
from tests.conftest import MakeVideo

CFG = AnalysisConfig()
LONG = "이 구간에는 글자가 충분히 많아서 말한 시간에 비해 모자라지 않습니다"


def seg(start: float, end: float, text: str = LONG) -> SpeechSegment:
    return SpeechSegment(start, end, text, -0.2, 0.01)


# ---------- 찾기 ----------


def test_speech_with_no_transcript_over_it_is_suspected() -> None:
    assert suspected_gaps([(0.0, 30.0)], [seg(0, 8), seg(20, 30)], CFG) == [(8.0, 20.0)]


def test_short_uncovered_speech_is_not_suspected() -> None:
    assert suspected_gaps([(0.0, 20.0)], [seg(0, 9), seg(11.5, 20)], CFG) == []


def test_silence_without_transcript_is_not_suspected() -> None:
    assert suspected_gaps([(0.0, 8.0), (20.0, 30.0)], [seg(0, 8), seg(20, 30)], CFG) == []


def test_a_stretched_segment_with_too_few_characters_is_suspected() -> None:
    assert suspected_gaps([(0.0, 30.0)], [seg(0, 20, "짧은 한 문장"), seg(20, 30)], CFG) == [
        (0.0, 20.0)
    ]


def test_a_short_segment_is_not_judged_by_its_character_count() -> None:
    assert suspected_gaps([(0.0, 12.0)], [seg(0, 2.5, "네"), seg(2.5, 12)], CFG) == []


def test_overlapping_signals_are_reported_once() -> None:
    segments = [seg(0, 12, "짧음"), seg(24, 30)]
    assert suspected_gaps([(0.0, 30.0)], segments, CFG) == [(0.0, 24.0)]


# ---------- 그 구간의 판정 ----------


def _issue(source: IssueSource, kind: IssueKind, start: float, end: float,
           verdict: Verdict = Verdict.ERROR) -> Issue:
    return Issue(source=source, kind=kind, verdict=verdict, start_s=start, end_s=end,
                 reason="원래 사유")


@pytest.mark.parametrize(
    ("source", "kind"),
    [
        (IssueSource.M1, IssueKind.VALUE_MISMATCH),
        (IssueSource.M2, IssueKind.SCREEN_ONLY_INFO),
        (IssueSource.M2, IssueKind.SCREEN_VALUE_MISMATCH),
        (IssueSource.M2, IssueKind.MENTIONED_ELSEWHERE),
    ],
)
def test_findings_that_rely_on_the_transcript_become_undetermined(
    source: IssueSource, kind: IssueKind
) -> None:
    [issue] = downgrade_in_gaps([_issue(source, kind, 10, 12)], [(8.0, 20.0)], CFG)
    assert issue.kind is IssueKind.STT_UNCERTAIN and issue.verdict is Verdict.UNDETERMINED
    assert issue.evidence["downgraded_from"] == kind.value
    assert issue.evidence["original_reason"] == "원래 사유"
    assert issue.evidence["speech_maybe_missed"] is True
    assert "인식하지 못했을 수" in issue.reason


def test_a_screen_finding_next_to_a_gap_is_also_undetermined() -> None:
    # 화면 정보는 표시 구간 ±5초의 음성과 비교하므로 가까운 누락도 영향을 준다.
    near = _issue(IssueSource.M2, IssueKind.SCREEN_ONLY_INFO, 23, 25)
    far = _issue(IssueSource.M2, IssueKind.SCREEN_ONLY_INFO, 26, 28)
    first, second = downgrade_in_gaps([near, far], [(8.0, 20.0)], CFG)
    assert first.verdict is Verdict.UNDETERMINED and second.verdict is Verdict.ERROR


@pytest.mark.parametrize(
    ("source", "kind"),
    [
        (IssueSource.M2, IssueKind.ALWAYS_ON_SCREEN),  # 영상 내내라 어느 누락과도 겹친다
        (IssueSource.M2, IssueKind.NO_SPEECH_TO_COMPARE),
        (IssueSource.M2_SCREEN_DEPENDENT, IssueKind.SCREEN_DEPENDENT),
    ],
)
def test_other_findings_are_left_as_they_are(source: IssueSource, kind: IssueKind) -> None:
    original = _issue(source, kind, 10, 12, Verdict.REVIEW_REQUIRED)
    assert downgrade_in_gaps([original], [(8.0, 20.0)], CFG) == [original]


def test_findings_away_from_every_gap_are_untouched() -> None:
    original = _issue(IssueSource.M1, IssueKind.VALUE_MISMATCH, 30, 32)
    assert downgrade_in_gaps([original], [(8.0, 20.0)], CFG) == [original]


# ---------- 파이프라인과 화면 ----------


@pytest.fixture
def env(tmp_path: Path) -> Iterator[SimpleNamespace]:
    repo = Repository(tmp_path / "app.db")
    cache = FileCache(tmp_path / "cache", repo)
    yield SimpleNamespace(repo=repo, cache=cache, data=tmp_path / "data", tmp=tmp_path)
    repo.close_all()


def _run(env: SimpleNamespace, video: Path, speech: list[SpeechSegment], detector: object):
    pipeline = fixtures.build(
        env, fixtures.FakeStt(speech), fixtures.FakeOcr(), fixtures.Scenes(), detector=detector
    )
    return pipeline.run(video, fixtures.srt_for(env, fixtures.SHOWN))


def test_pipeline_records_each_suspected_interval(
    env: SimpleNamespace, make_video: MakeVideo
) -> None:
    speech = [fixtures.SPEECH[0], fixtures.SPEECH[4]]  # 0~2초와 8~10초만 받아썼다
    result = _run(env, make_video(seconds=10), speech, lambda _audio: [(0.0, 10.0)])
    [note] = [n for n in env.repo.list_notes(result.session_id)
              if n.code is NoteCode.SPEECH_MAYBE_MISSED]
    assert (note.source, note.start_s, note.end_s) == (NoteSource.STT, 2.0, 8.0)
    assert "인식하지 못했을 수" in note.message


def test_pipeline_downgrades_findings_inside_a_suspected_interval(
    env: SimpleNamespace, make_video: MakeVideo
) -> None:
    # 둘째 자막(2~4초)의 날짜가 틀렸다. 받아쓰기가 그 시간을 크게 늘여 놓은 모습이다.
    stretched = SpeechSegment(2.0, 10.0, "신청 마감은 3월 15일", -0.2, 0.01)
    result = _run(env, make_video(seconds=10), [fixtures.SPEECH[0], stretched],
                  lambda _audio: [(0.0, 10.0)])
    inside = [i for i in env.repo.list_issues(result.session_id) if i.source is IssueSource.M1
              and i.start_s == 2.0]
    assert inside and all(i.verdict is Verdict.UNDETERMINED for i in inside)


def test_a_failing_speech_check_does_not_fail_the_analysis(
    env: SimpleNamespace, make_video: MakeVideo
) -> None:
    def broken(_audio: Path) -> list[tuple[float, float]]:
        raise RuntimeError("vad")

    result = _run(env, make_video(seconds=10), fixtures.SPEECH, broken)
    [note] = [n for n in env.repo.list_notes(result.session_id)
              if n.code is NoteCode.SPEECH_CHECK_FAILED]
    assert note.start_s is None and "확인하지 못했습니다" in note.message


def test_cancelling_during_the_speech_check_is_still_a_cancel(
    env: SimpleNamespace, make_video: MakeVideo
) -> None:
    def cancelled(_audio: Path) -> list[tuple[float, float]]:
        raise CancelledError

    with pytest.raises(CancelledError):
        _run(env, make_video(seconds=10), fixtures.SPEECH, cancelled)


@pytest.mark.parametrize("audio", [True, False], ids=["빈-받아쓰기", "오디오-없음"])
def test_the_check_is_skipped_when_there_is_no_transcript_at_all(
    env: SimpleNamespace, make_video: MakeVideo, audio: bool
) -> None:
    calls: list[Path] = []
    _run(env, make_video(seconds=10, audio=audio), [],
         lambda path: calls.append(path) or [(0.0, 10.0)])
    assert calls == []  # 이미 영상 전체를 미점검으로 알린다


def test_suspected_intervals_are_shown_on_one_line_as_suspicion(
    env: SimpleNamespace, make_video: MakeVideo
) -> None:
    speech = [seg(0, 1), seg(5, 6), seg(9, 10)]
    result = _run(env, make_video(seconds=10), speech,
                  lambda _audio: [(0.0, 5.0), (5.0, 10.0)])
    notices = ReviewState(env.repo, result.session_id).notices()
    [line] = [n for n in notices if "누락 의심" in n and "00:0" in n]
    assert "00:01~00:05" in line and "00:06~00:09" in line
    assert "인식하지 못했을 수" in notices[0]
    assert not any("미점검" in n for n in notices)

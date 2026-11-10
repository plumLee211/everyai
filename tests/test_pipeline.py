"""pipeline.py 시나리오 테스트.

ffprobe/오디오 추출은 진짜 ffmpeg로, STT·OCR 엔진과 프레임은 주입한 가짜로 돌린다.
프레임 픽셀 값에 '지금 무엇이 보이는가'를 담고(0 = 없음), 가짜 OCR이 값으로 글자를 돌려준다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from barrierfree.analysis.subtitle_region import RegionEstimate, region_rect
from barrierfree.cancel import CancelledError, CancelToken
from barrierfree.config import AnalysisConfig
from barrierfree.engines.ocr import OcrBox
from barrierfree.engines.stt import SttError
from barrierfree.media.ffmpeg import VideoFileNotFoundError, VideoTooLongError
from barrierfree.media.subtitles import SubtitleParseError, write_srt
from barrierfree.models import (
    IssueKind,
    IssueSource,
    SessionStatus,
    SpeechSegment,
    SubtitleCue,
    SubtitleSource,
    Verdict,
)
from barrierfree.pipeline import (
    AnalysisPipeline,
    PipelineError,
    RegionAction,
    RegionDecision,
    Stage,
    StageEvent,
    explain_error,
)
from barrierfree.storage.cache import FileCache
from barrierfree.storage.repository import Repository
from tests.conftest import MakeVideo

FULL_H = 180
SENTENCES = [
    "안녕하세요 오늘은 복지 서비스를 안내해 드리겠습니다",
    "신청 마감은 3월 15일입니다",
    "서류는 주민센터에서 받으실 수 있어요",
    "궁금한 점은 담당 부서로 문의해 주세요",
    "감사합니다 좋은 하루 보내세요",
]
SHOWN = [s.replace("15일", "16일") for s in SENTENCES]  # 둘째 자막의 날짜가 틀렸다
SPEECH = [SpeechSegment(2.0 * i, 2.0 * i + 2.0, s, -0.2, 0.01) for i, s in enumerate(SENTENCES)]


class FakeStt:
    engine_name = "fake-stt"
    model_name = "tiny"
    device = "cpu"

    def __init__(
        self,
        segments: list[SpeechSegment],
        *,
        fail: Exception | None = None,
        on_call: Callable[[], None] | None = None,
    ) -> None:
        self.segments, self.fail, self.on_call, self.calls = segments, fail, on_call, 0

    def transcribe(
        self, audio_path: str | Path, *, cancel: CancelToken | None = None
    ) -> list[SpeechSegment]:
        self.calls += 1
        if self.on_call:
            self.on_call()
        if self.fail:
            raise self.fail
        assert Path(audio_path).is_file()
        return list(self.segments)


class FakeOcr:
    engine_name = "fake-ocr"
    model_name = "default"

    def __init__(
        self,
        full: dict[int, list[OcrBox]] | None = None,
        strip: dict[int, list[OcrBox]] | None = None,
    ) -> None:
        self.full, self.strip, self.calls = full or {}, strip or {}, 0

    def read(self, frame: np.ndarray) -> list[OcrBox]:
        self.calls += 1
        table = self.full if frame.shape[0] == FULL_H else self.strip
        return list(table.get(int(frame[0, 0, 0]), []))


class Scenes:
    """프레임 공급자. full/strip은 시각 -> 픽셀 값(0 = 아무것도 안 보임)."""

    def __init__(
        self,
        full: Callable[[float], int] = lambda t: 0,
        strip: Callable[[float], int] = lambda t: 0,
    ) -> None:
        self.full, self.strip = full, strip
        self.calls: list[tuple[float, float | None]] = []

    def __call__(
        self,
        path: Path,
        fps: float,
        max_width: int,
        bottom_ratio: float | None,
        info: Any,
        cancel: CancelToken | None = None,
    ) -> Iterator[tuple[float, np.ndarray]]:
        self.calls.append((fps, bottom_ratio))
        for i in range(int(info.duration_s * fps)):
            t = i / fps
            if bottom_ratio is None:
                yield t, np.full((FULL_H, 320, 3), self.full(t), np.uint8)
            else:
                height = max(2, int(FULL_H * bottom_ratio) // 2 * 2)
                yield t, np.full((height, 320, 3), self.strip(t), np.uint8)


def subtitle_scene(t: float) -> int:
    return 40 * (min(int(t // 2), 4) + 1)


def subtitle_strip_script() -> dict[int, list[OcrBox]]:
    return {40 * (i + 1): [OcrBox(SHOWN[i], 0.9, (0.2, 0.3, 0.6, 0.3))] for i in range(5)}


@pytest.fixture
def env(tmp_path: Path) -> Any:
    repo = Repository(tmp_path / "app.db")
    cache = FileCache(tmp_path / "cache", repo)
    yield SimpleNamespace(repo=repo, cache=cache, data=tmp_path / "data", tmp=tmp_path)
    repo.close_all()


def build(
    env: Any,
    stt: FakeStt,
    ocr: FakeOcr,
    scenes: Scenes,
    *,
    confirm: Callable[[RegionEstimate], RegionDecision] | None = None,
    events: list[StageEvent] | None = None,
    config: AnalysisConfig | None = None,
) -> AnalysisPipeline:
    return AnalysisPipeline(
        repo=env.repo,
        cache=env.cache,
        stt=stt,
        ocr=ocr,
        config=config or AnalysisConfig(),
        data_dir=env.data,
        confirm_region=confirm,
        on_progress=events.append if events is not None else None,
        frame_source=scenes,
    )


def srt_for(env: Any, texts: list[str]) -> Path:
    cues = [SubtitleCue(2.0 * i, 2.0 * i + 2.0, t) for i, t in enumerate(texts)]
    return write_srt(cues, env.tmp / "자막.srt")


def issues_of(env: Any, session_id: str) -> list[Any]:
    return env.repo.list_issues(session_id)


# ---------- 자막 파일 있음 ----------


def test_subtitle_file_path_runs_m1_without_asking_for_a_region(
    env: Any, make_video: MakeVideo
) -> None:
    confirm_calls: list[RegionEstimate] = []
    scenes = Scenes()
    pipeline = build(
        env, FakeStt(SPEECH), FakeOcr(), scenes,
        confirm=lambda est: confirm_calls.append(est) or RegionDecision(RegionAction.NONE),
    )
    result = pipeline.run(make_video(seconds=10), srt_for(env, SHOWN))

    session = env.repo.get_session(result.session_id)
    assert session.status is SessionStatus.COMPLETED
    assert session.subtitle_source is SubtitleSource.FILE
    assert confirm_calls == []
    assert all(ratio is None for _, ratio in scenes.calls)  # 자막 영역 스캔은 하지 않는다
    [issue] = issues_of(env, result.session_id)
    assert issue.kind is IssueKind.VALUE_MISMATCH and issue.verdict is Verdict.ERROR
    assert result.issue_count == 1


def test_stage_metrics_and_model_info_are_recorded(env: Any, make_video: MakeVideo) -> None:
    pipeline = build(env, FakeStt(SPEECH), FakeOcr(), Scenes())
    result = pipeline.run(make_video(seconds=10), srt_for(env, SHOWN))
    stages = {m.stage for m in env.repo.list_stage_metrics(result.session_id)}
    expected = {"MEDIA_PREPARE", "STT", "OCR", "SUBTITLE_CHECK", "SCREEN_CHECK", "RESULT_SAVE"}
    assert expected <= stages
    session = env.repo.get_session(result.session_id)
    assert (session.stt_model_name, session.ocr_engine_name) == ("tiny", "fake-ocr")


# ---------- 자막 없음: OCR 자막 + 편집자 확인 ----------


def ocr_subtitle_pipeline(
    env: Any,
    confirm: Callable[[RegionEstimate], RegionDecision] | None,
) -> tuple[AnalysisPipeline, Scenes]:
    scenes = Scenes(strip=subtitle_scene)
    ocr = FakeOcr(strip=subtitle_strip_script())
    return build(env, FakeStt(SPEECH), ocr, scenes, confirm=confirm), scenes


def test_missing_subtitle_file_pauses_for_region_confirmation_then_checks_ocr_subtitles(
    env: Any, make_video: MakeVideo
) -> None:
    seen: list[tuple[RegionEstimate, str]] = []

    def confirm(est: RegionEstimate) -> RegionDecision:
        status = env.repo.connection().execute("SELECT status FROM analysis_session").fetchone()[0]
        seen.append((est, status))
        return RegionDecision(RegionAction.CONFIRMED, est.rect)

    pipeline, _ = ocr_subtitle_pipeline(env, confirm)
    result = pipeline.run(make_video(seconds=10), None)

    [(estimate, status_while_waiting)] = seen
    assert estimate.is_subtitle is True
    assert status_while_waiting == "AWAITING_CONFIRMATION"

    session = env.repo.get_session(result.session_id)
    assert session.status is SessionStatus.COMPLETED
    assert session.subtitle_source is SubtitleSource.OCR
    assert session.subtitle_region == {
        "decision": "CONFIRMED",
        "normalized_rect": [0.0, 0.75, 1.0, 0.25],
    }
    assert session.generated_srt_path is not None
    assert (env.data / session.generated_srt_path).is_file()
    assert any("OCR" in n for n in result.notices)

    [issue] = issues_of(env, result.session_id)
    assert issue.kind is IssueKind.VALUE_MISMATCH
    assert issue.verdict is Verdict.REVIEW_REQUIRED  # OCR 자막이므로 오류로 단정하지 않는다
    assert issue.source is IssueSource.M1


def test_editor_can_say_the_video_has_no_subtitles(env: Any, make_video: MakeVideo) -> None:
    pipeline, _ = ocr_subtitle_pipeline(env, lambda est: RegionDecision(RegionAction.NONE))
    result = pipeline.run(make_video(seconds=10), None)
    session = env.repo.get_session(result.session_id)
    assert session.subtitle_source is SubtitleSource.NONE
    assert session.status is SessionStatus.COMPLETED
    assert issues_of(env, result.session_id) == []  # 개별 누락을 쏟아내지 않는다
    assert any("자막이 없는" in n for n in result.notices)


def test_adjusted_larger_region_rescans_the_subtitle_strip(env: Any, make_video: MakeVideo) -> None:
    pipeline, scenes = ocr_subtitle_pipeline(
        env, lambda est: RegionDecision(RegionAction.ADJUSTED, region_rect(0.4))
    )
    result = pipeline.run(make_video(seconds=10), None)
    ratios = [ratio for _, ratio in scenes.calls if ratio is not None]
    assert ratios == [0.25, 0.4]
    session = env.repo.get_session(result.session_id)
    assert session.subtitle_source is SubtitleSource.OCR
    assert session.subtitle_region is not None
    assert session.subtitle_region["decision"] == "ADJUSTED"
    assert len(issues_of(env, result.session_id)) == 1


def test_adjusted_smaller_region_reuses_the_existing_scan(env: Any, make_video: MakeVideo) -> None:
    pipeline, scenes = ocr_subtitle_pipeline(
        env, lambda est: RegionDecision(RegionAction.ADJUSTED, region_rect(0.2))
    )
    pipeline.run(make_video(seconds=10), None)
    assert [r for _, r in scenes.calls if r is not None] == [0.25]


def test_without_a_confirm_callback_the_estimate_is_accepted(
    env: Any, make_video: MakeVideo
) -> None:
    pipeline, _ = ocr_subtitle_pipeline(env, None)
    result = pipeline.run(make_video(seconds=10), None)
    assert result.subtitle_source is SubtitleSource.OCR


def test_confirmed_region_without_any_text_falls_back_to_no_subtitles(
    env: Any, make_video: MakeVideo
) -> None:
    pipeline = build(
        env, FakeStt(SPEECH), FakeOcr(), Scenes(),
        confirm=lambda est: RegionDecision(RegionAction.CONFIRMED, est.rect),
    )
    result = pipeline.run(make_video(seconds=10), None)
    assert result.subtitle_source is SubtitleSource.NONE
    assert any("찾지 못" in n for n in result.notices)


# ---------- M2와 화면 의존 표현 ----------


def test_screen_only_phone_and_screen_dependent_phrase_become_one_issue(
    env: Any, make_video: MakeVideo
) -> None:
    speech = [SpeechSegment(3.0, 5.0, "보시는 것처럼 문의 번호가 나옵니다", -0.2, 0.01)]
    phone = {40: [OcrBox("문의 02-1234-5678", 0.9, (0.1, 0.4, 0.4, 0.06))]}
    scenes = Scenes(full=lambda t: 40 if 2 <= t < 6 else 0)
    cue = SubtitleCue(3.0, 5.0, "보시는 것처럼 문의 번호가 나옵니다")
    srt = write_srt([cue], env.tmp / "a.srt")
    pipeline = build(env, FakeStt(speech), FakeOcr(full=phone), scenes)
    result = pipeline.run(make_video(seconds=10), srt)

    [issue] = issues_of(env, result.session_id)
    assert issue.source is IssueSource.M2
    assert issue.kind is IssueKind.SCREEN_ONLY_INFO
    assert issue.evidence["screen_dependent_phrases"] == ["보시는 것처럼"]


# ---------- 취소·실패 ----------


def test_cancel_marks_session_cancelled_and_saves_nothing(env: Any, make_video: MakeVideo) -> None:
    token = CancelToken()
    stt = FakeStt(SPEECH, on_call=token.cancel)
    pipeline = build(env, stt, FakeOcr(), Scenes())
    with pytest.raises(CancelledError):
        pipeline.run(make_video(seconds=10), srt_for(env, SHOWN), token)
    [row] = env.repo.connection().execute("SELECT id, status FROM analysis_session").fetchall()
    assert row["status"] == "CANCELLED"
    assert env.repo.list_issues(row["id"]) == []
    assert list((env.data / "temp").rglob("*.wav")) == []  # 임시 오디오 정리


def test_stt_failure_is_a_pipeline_error_with_code_and_failed_session(
    env: Any, make_video: MakeVideo
) -> None:
    pipeline = build(env, FakeStt(SPEECH, fail=SttError("boom")), FakeOcr(), Scenes())
    with pytest.raises(PipelineError) as exc:
        pipeline.run(make_video(seconds=10), srt_for(env, SHOWN))
    assert exc.value.code == "ERR-07"
    assert exc.value.stage is Stage.STT
    session = env.repo.get_session(exc.value.session_id or "")
    assert session.status is SessionStatus.FAILED and session.error_code == "ERR-07"


# ---------- 오류 설명 (등록 화면이 같은 문구를 쓴다) ----------


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (VideoTooLongError(1300, 1200), "ERR-02"),
        (VideoFileNotFoundError("x"), "ERR-01"),
        (SubtitleParseError("x"), "ERR-03"),
        (SttError("x"), "ERR-07"),
        (RuntimeError("x"), "ERR-99"),
    ],
)
def test_explain_error_maps_exceptions_to_codes_and_user_messages(
    exc: Exception, code: str
) -> None:
    error = explain_error(exc)
    assert error.code == code
    assert error.user_message and "Traceback" not in error.user_message


def test_explain_error_passes_pipeline_errors_through() -> None:
    original = PipelineError("ERR-05", "모델 다운로드 실패")
    assert explain_error(original) is original


# ---------- 완료 이후·작업 폴더 실패 (Codex 리뷰 반영) ----------


def test_measurement_failure_after_completion_does_not_turn_success_into_failure(
    env: Any, make_video: MakeVideo, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = env.repo.record_stage_metric

    def flaky(session_id: str, stage: str, **kwargs: Any) -> None:
        if stage == "RESULT_SAVE":
            raise sqlite3.OperationalError("database is locked")
        original(session_id, stage, **kwargs)

    monkeypatch.setattr(env.repo, "record_stage_metric", flaky)
    result = build(env, FakeStt(SPEECH), FakeOcr(), Scenes()).run(
        make_video(seconds=10), srt_for(env, SHOWN)
    )
    session = env.repo.get_session(result.session_id)
    assert session.status is SessionStatus.COMPLETED  # 이미 저장된 결과를 실패로 뒤집지 않는다
    assert len(issues_of(env, result.session_id)) == result.issue_count == 1


def test_unusable_work_folder_fails_the_session_cleanly(env: Any, make_video: MakeVideo) -> None:
    env.data.mkdir(parents=True)
    (env.data / "temp").write_text("폴더가 아니라 파일", encoding="utf-8")  # mkdir이 실패한다
    pipeline = build(env, FakeStt(SPEECH), FakeOcr(), Scenes())
    with pytest.raises(PipelineError) as exc:
        pipeline.run(make_video(seconds=10), srt_for(env, SHOWN))
    assert exc.value.code == "ERR-09"
    assert exc.value.session_id is not None
    session = env.repo.get_session(exc.value.session_id)
    assert session.status is SessionStatus.FAILED and session.error_code == "ERR-09"


def test_filesystem_errors_are_explained_as_storage_problems() -> None:
    error = explain_error(PermissionError("denied"))
    assert error.code == "ERR-09" and "저장 위치" in error.user_message


# ---------- 입력 오류: 세션을 만들기 전에 거부 ----------


def session_count(env: Any) -> int:
    return int(env.repo.connection().execute("SELECT COUNT(*) FROM analysis_session").fetchone()[0])


def test_video_over_the_limit_is_rejected_before_any_session(
    env: Any, make_video: MakeVideo
) -> None:
    pipeline = build(
        env, FakeStt(SPEECH), FakeOcr(), Scenes(), config=AnalysisConfig(max_video_seconds=1.0)
    )
    with pytest.raises(PipelineError) as exc:
        pipeline.run(make_video(seconds=3), None)
    assert exc.value.code == "ERR-02"
    assert session_count(env) == 0


def test_missing_video_file_is_err_01(env: Any, tmp_path: Path) -> None:
    pipeline = build(env, FakeStt(SPEECH), FakeOcr(), Scenes())
    with pytest.raises(PipelineError) as exc:
        pipeline.run(tmp_path / "없는 영상.mp4", None)
    assert exc.value.code == "ERR-01" and session_count(env) == 0


def test_broken_subtitle_file_is_err_03(env: Any, make_video: MakeVideo) -> None:
    bad = env.tmp / "깨진.srt"
    bad.write_text("자막이 아닌 내용", encoding="utf-8")
    pipeline = build(env, FakeStt(SPEECH), FakeOcr(), Scenes())
    with pytest.raises(PipelineError) as exc:
        pipeline.run(make_video(seconds=10), bad)
    assert exc.value.code == "ERR-03" and session_count(env) == 0


def test_unreadable_subtitle_blocks_are_reported_as_a_notice(
    env: Any, make_video: MakeVideo
) -> None:
    srt = env.tmp / "일부깨짐.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n안녕하세요 오늘은 복지 서비스를 안내해 드리겠습니다\n\n"
        "2\n깨진 시간 줄\n내용\n",
        encoding="utf-8",
    )
    result = build(env, FakeStt(SPEECH[:1]), FakeOcr(), Scenes()).run(make_video(seconds=10), srt)
    assert any("읽지 못했습니다" in n for n in result.notices)


# ---------- 캐시 ----------


def test_second_run_reuses_stt_and_ocr_results(env: Any, make_video: MakeVideo) -> None:
    video, srt = make_video(seconds=10), srt_for(env, SHOWN)
    stt, ocr = FakeStt(SPEECH), FakeOcr()
    build(env, stt, ocr, Scenes()).run(video, srt)
    ocr_calls_first = ocr.calls
    second = build(env, stt, ocr, Scenes()).run(video, srt)

    assert stt.calls == 1
    assert ocr.calls == ocr_calls_first
    metrics = {m.stage: m.cache_hit for m in env.repo.list_stage_metrics(second.session_id)}
    assert metrics["STT"] is True and metrics["OCR"] is True


def test_cache_with_an_unconvertible_integer_is_recomputed_not_fatal(
    env: Any, make_video: MakeVideo
) -> None:
    video, srt = make_video(seconds=10), srt_for(env, SHOWN)
    stt = FakeStt(SPEECH)
    build(env, stt, FakeOcr(), Scenes()).run(video, srt)
    [stored] = list((env.tmp / "cache" / "stt").glob("*.json"))
    huge = "1" + "0" * 309
    stored.write_text(f'[{{"start": {huge}, "end": 2, "text": "x"}}]', encoding="utf-8")
    result = build(env, stt, FakeOcr(), Scenes()).run(video, srt)
    assert stt.calls == 2  # 손상된 캐시를 버리고 다시 계산했다
    assert env.repo.get_session(result.session_id).status is SessionStatus.COMPLETED


def test_corrupt_stt_cache_is_recomputed(env: Any, make_video: MakeVideo) -> None:
    video, srt = make_video(seconds=10), srt_for(env, SHOWN)
    stt = FakeStt(SPEECH)
    build(env, stt, FakeOcr(), Scenes()).run(video, srt)
    [stored] = list((env.tmp / "cache" / "stt").glob("*.json"))
    stored.write_text('[{"start": -5}]', encoding="utf-8")  # 파싱은 되지만 내용이 손상됨
    result = build(env, stt, FakeOcr(), Scenes()).run(video, srt)
    assert stt.calls == 2
    assert env.repo.get_session(result.session_id).status is SessionStatus.COMPLETED


# ---------- 오디오 없음 / 진행 이벤트 ----------


def test_video_without_audio_skips_stt_and_says_so(env: Any, make_video: MakeVideo) -> None:
    stt = FakeStt(SPEECH)
    result = build(env, stt, FakeOcr(), Scenes()).run(
        make_video(seconds=10, audio=False), srt_for(env, SHOWN)
    )
    assert stt.calls == 0
    assert any("오디오" in n for n in result.notices)


def test_progress_events_cover_every_stage_in_order(env: Any, make_video: MakeVideo) -> None:
    events: list[StageEvent] = []
    build(env, FakeStt(SPEECH), FakeOcr(), Scenes(), events=events).run(
        make_video(seconds=10), srt_for(env, SHOWN)
    )
    seen: list[Stage] = []
    for e in events:
        if e.state == "RUNNING" and e.stage not in seen:
            seen.append(e.stage)
    assert seen == [
        Stage.MEDIA_PREPARE, Stage.STT, Stage.OCR, Stage.SUBTITLE_CHECK,
        Stage.SCREEN_CHECK, Stage.RESULT_SAVE,
    ]
    assert all(e.fraction is None or 0.0 <= e.fraction <= 1.0 for e in events)
    assert events[-1].stage is Stage.RESULT_SAVE and events[-1].state == "DONE"
    ocr_fractions = [e.fraction for e in events if e.stage is Stage.OCR and e.fraction is not None]
    assert ocr_fractions == sorted(ocr_fractions) and ocr_fractions

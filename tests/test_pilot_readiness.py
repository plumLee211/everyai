"""실증 준비: 모델 기본값과 동봉 목록, 버전 기록, 지표 내보내기, 점검 범위 안내."""

from __future__ import annotations

import csv
import re
import tomllib
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from pytestqt.qtbot import QtBot

import barrierfree
from barrierfree.analysis.compare_rows import CompareData
from barrierfree.config import FRAME_SKIP_METHOD, RULES_VERSION, AnalysisConfig
from barrierfree.engines.device import DeviceInfo, bundled_models, recommend_model
from barrierfree.models import (
    AnalysisNote,
    Issue,
    IssueKind,
    IssueSource,
    NoteCode,
    NoteSource,
    ReviewStatus,
    ScreenText,
    SpeechSegment,
    SubtitleCue,
    Verdict,
)
from barrierfree.review_state import ReviewState
from barrierfree.storage.cache import FileCache
from barrierfree.storage.compare_data import save_compare_data
from barrierfree.storage.repository import Repository
from barrierfree.ui.review_page import ReviewPage
from tests import test_pipeline as pipeline_fixtures
from tests.conftest import MakeVideo
from tests.fakes import FakeDialogs, FakePlayer
from tests.test_storage import _session


def info(*, cuda: bool, ram_gb: float) -> DeviceInfo:
    return DeviceInfo("cpu", 8, ram_gb, cuda)


# ---------- 모델 ----------


@pytest.mark.parametrize(
    ("cuda", "ram"), [(False, 16.0), (True, 16.0), (False, 4.0)],
    ids=["일반", "GPU", "적은-메모리"],
)
def test_every_computer_is_recommended_the_same_model(cuda: bool, ram: float) -> None:
    assert recommend_model(info(cuda=cuda, ram_gb=ram), AnalysisConfig()).model == "small"


def test_low_memory_still_warns_and_says_what_changing_the_model_costs() -> None:
    warning = recommend_model(info(cuda=False, ram_gb=4), AnalysisConfig()).warning
    assert warning is not None and "base" in warning and "빠뜨" in warning


def test_the_build_still_includes_the_models_a_user_may_switch_to() -> None:
    # 배포본은 인터넷 없이 쓴다. 권장 모델만 넣으면 설정에서 바꿀 모델이 없다.
    assert bundled_models() == ("base", "small", "medium")
    assert "turbo" in bundled_models(AnalysisConfig(recommend_cpu_model="turbo"))


# ---------- 버전 기록 ----------


def test_the_code_version_matches_the_package_version() -> None:
    project = tomllib.loads(
        (Path(__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert barrierfree.__version__ == project["project"]["version"]


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[Repository]:
    r = Repository(tmp_path / "app.db")
    yield r
    r.close_all()


def test_each_analysis_records_which_version_and_rules_made_it(repo: Repository) -> None:
    snapshot = repo.get_session(_session(repo)).config_snapshot
    assert snapshot["app_version"] == barrierfree.__version__
    assert snapshot["rules_version"] == RULES_VERSION
    assert snapshot["ocr_frame_skip"] == FRAME_SKIP_METHOD
    assert snapshot["whisper_model"]  # 기존 설정도 남는다


@pytest.mark.parametrize(
    ("falls_back", "recorded"), [(True, "cpu (cuda에서 전환)"), (False, "cuda")]
)
def test_the_device_that_actually_transcribed_is_recorded(
    tmp_path: Path, make_video: MakeVideo, falls_back: bool, recorded: str
) -> None:
    class _Stt(pipeline_fixtures.FakeStt):
        device = "cuda"
        fell_back_to_cpu = False

        def transcribe(self, audio_path, *, cancel=None):  # noqa: ANN001, ANN201
            if falls_back:  # 실제 엔진은 인식 중에 GPU 오류를 만나면 CPU로 다시 한다
                self.device, self.fell_back_to_cpu = "cpu", True
            return super().transcribe(audio_path, cancel=cancel)

    repo = Repository(tmp_path / "app.db")
    env = SimpleNamespace(
        repo=repo, cache=FileCache(tmp_path / "cache", repo), data=tmp_path / "data",
        tmp=tmp_path,
    )
    try:
        pipeline = pipeline_fixtures.build(
            env, _Stt(pipeline_fixtures.SPEECH), pipeline_fixtures.FakeOcr(),
            pipeline_fixtures.Scenes(),
        )
        video = make_video(seconds=10)
        result = pipeline.run(video, pipeline_fixtures.srt_for(env, pipeline_fixtures.SENTENCES))
        assert repo.get_session(result.session_id).compute_device == recorded
    finally:
        repo.close_all()


# ---------- 지표 내보내기 ----------

SECRETS = ["비밀발화", "비밀화면", "비밀사유", "안내.mp4", "/videos"]


def _filled(repo: Repository) -> str:
    session_id = _session(repo)  # 영상 이름 '안내.mp4', 경로 /videos
    found = Issue(source=IssueSource.M2, kind=IssueKind.SCREEN_ONLY_INFO,
                  verdict=Verdict.REVIEW_REQUIRED, start_s=1, end_s=2, reason="비밀사유",
                  speech_text="비밀발화", comparison_text="비밀화면")
    notes = [
        AnalysisNote(NoteCode.SPEECH_MAYBE_MISSED, NoteSource.STT, "비밀사유", 1.0, 4.0),
        AnalysisNote(NoteCode.SPEECH_MAYBE_MISSED, NoteSource.STT, "비밀사유", 3.0, 6.0),
        AnalysisNote(NoteCode.SUBTITLE_NONE, NoteSource.SUBTITLE, "비밀사유", 0.0, 12.5),
    ]
    repo.record_stage_metric(session_id, "STT", duration_ms=2500, processed_count=3,
                             unit="SEGMENT", cache_hit=False)
    repo.complete_session(session_id, [found], notes)
    repo.set_review_status(found.id, ReviewStatus.CONFIRMED)
    repo.add_review_time(session_id, 42_000)
    return session_id


def _metrics(path: Path) -> dict[str, str]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["항목", "값"]
    assert len({r[0] for r in rows[1:]}) == len(rows) - 1  # 항목 이름이 겹치지 않는다
    return dict(rows[1:])


def test_metrics_hold_what_the_pilot_needs(repo: Repository, tmp_path: Path) -> None:
    state = ReviewState(repo, _filled(repo))
    values = _metrics(state.export_metrics(tmp_path / "m.csv"))
    assert values["앱 버전"] == barrierfree.__version__
    assert values["규칙 버전"] == RULES_VERSION
    assert values["음성 인식 모델"] == "small"
    assert values["영상 길이(초)"] == "12.5"
    assert values["검수 시간(초)"] == "42"
    assert values["처리 시간(ms) STT"] == "2500"
    assert values["지적 수"] == "1"
    assert values["지적 M2/SCREEN_ONLY_INFO/REVIEW_REQUIRED/CONFIRMED"] == "1"
    assert values["안내 수 SPEECH_MAYBE_MISSED"] == "2"
    assert values["누락 의심 시간(초)"] == "5.0"  # 겹치는 구간은 한 번만
    assert values["미점검 시간(초) SUBTITLE"] == "12.5"
    assert "미점검 시간(초) STT" not in values  # 누락 의심은 미점검으로 세지 않는다


_ALLOWED_ITEMS = re.compile(
    r"분석 ID|영상 지문|분석 일시|영상 길이\(초\)|앱 버전|규칙 버전|OCR 생략 방식|음성 인식 모델|"
    r"화면 글자 인식|장치|자막 출처|분석 상태|검수 시간\(초\)|지적 수|누락 의심 시간\(초\)|"
    r"대조 보기 행 수|화면 글자 수|불확실 화면 글자 수|대조 자료|"
    r"(처리 시간\(ms\)|캐시 사용) [A-Z_]+|지적 [A-Z0-9_]+(/[A-Z_]+){3}|안내 수 [A-Z_]+|"
    r"미점검 시간\(초\) [A-Z]+"
)


@pytest.mark.parametrize("with_view_data", [True, False], ids=["대조자료-있음", "대조자료-없음"])
def test_metrics_carry_no_text_from_the_video_and_no_file_names(
    repo: Repository, tmp_path: Path, with_view_data: bool
) -> None:
    session_id = _filled(repo)
    if with_view_data:
        save_compare_data(
            tmp_path, session_id,
            CompareData(
                [SpeechSegment(0, 2, "비밀발화", None, None)],
                [ScreenText("비밀화면", 0, 2, 0.9, None)],
                [ScreenText("비밀화면", 0, 2, 0.2, None)],
                [SubtitleCue(0, 2, "비밀사유")],
            ),
        )
    state = ReviewState(repo, session_id, data_dir=tmp_path)
    path = state.export_metrics(tmp_path / "m.csv")
    values = _metrics(path)
    assert [k for k in values if not _ALLOWED_ITEMS.fullmatch(k)] == []
    assert ("화면 글자 수" in values) is with_view_data
    text = path.read_text(encoding="utf-8-sig")
    assert not [word for word in SECRETS if word in text]
    assert "a" * 64 not in text and "a" * 12 in text  # 영상 지문은 앞부분만


def test_the_default_metrics_file_name_does_not_contain_the_video_name(
    repo: Repository,
) -> None:
    session_id = _filled(repo)
    name = ReviewState(repo, session_id).suggested_metrics_name()
    assert name == f"실증지표_{session_id[:8]}.csv"


# ---------- 화면 ----------


@pytest.fixture
def page(qtbot: QtBot, repo: Repository):  # noqa: ANN201
    dialogs = FakeDialogs()
    widget = ReviewPage(dialogs, FakePlayer)
    qtbot.addWidget(widget)
    widget.load(ReviewState(repo, _filled(repo)), None)
    return widget, dialogs


def test_the_review_page_can_export_pilot_metrics(page, tmp_path: Path) -> None:  # noqa: ANN001
    widget, dialogs = page
    dialogs.csv = tmp_path / "지표.csv"
    widget.metrics_button.click()
    assert (tmp_path / "지표.csv").is_file()
    assert dialogs.csv_default_names[-1].startswith("실증지표_")
    [(_, message)] = dialogs.infos
    assert "발화" in message and "들어 있지 않습니다" in message


def test_cancelling_or_failing_the_metrics_export_is_handled(
    page, tmp_path: Path  # noqa: ANN001
) -> None:
    widget, dialogs = page
    dialogs.csv = None
    widget.metrics_button.click()
    assert dialogs.infos == [] and dialogs.warnings == []
    (tmp_path / "폴더").mkdir()
    dialogs.csv = tmp_path / "폴더"
    widget.metrics_button.click()
    assert dialogs.warnings


def test_the_review_page_says_what_is_not_checked(page) -> None:  # noqa: ANN001
    widget, dialogs = page
    widget.limits_button.click()
    [(title, text)] = dialogs.infos
    assert "점검" in title
    for phrase in ("문장의 뜻", "순서", "화면해설", "지적이 없", "보증하지 않습니다"):
        assert phrase in text

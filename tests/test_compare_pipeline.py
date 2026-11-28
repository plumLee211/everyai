"""파이프라인: 화면 글자를 위치 때문에 빼지 않고, 겹치는 지적을 합치고, 대조 자료를 남긴다."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from barrierfree.engines.ocr import OcrBox
from barrierfree.models import IssueKind, IssueSource, NoteCode, SubtitleSource
from barrierfree.pipeline import RegionAction, RegionDecision
from barrierfree.storage.cache import FileCache
from barrierfree.storage.compare_data import load_compare_data
from barrierfree.storage.repository import Repository
from tests import test_pipeline as fixtures
from tests.conftest import MakeVideo


@pytest.fixture
def env(tmp_path: Path) -> Iterator[SimpleNamespace]:
    repo = Repository(tmp_path / "app.db")
    cache = FileCache(tmp_path / "cache", repo)
    yield SimpleNamespace(repo=repo, cache=cache, data=tmp_path / "data", tmp=tmp_path)
    repo.close_all()


def bottom(text: str, conf: float = 0.9) -> OcrBox:
    return OcrBox(text, conf, (0.2, 0.85, 0.6, 0.08))


def top(text: str, conf: float = 0.9) -> OcrBox:
    return OcrBox(text, conf, (0.1, 0.05, 0.6, 0.08))


def _ocr_mode(
    env: SimpleNamespace, make_video: MakeVideo, full: dict[int, list[OcrBox]],
    action: RegionAction = RegionAction.CONFIRMED,
):
    """자막 파일 없이 분석한다. 하단 띠에는 틀린 날짜가 든 자막(SHOWN)이 나온다."""
    ocr = fixtures.FakeOcr(full=full, strip=fixtures.subtitle_strip_script())
    scenes = fixtures.Scenes(full=fixtures.subtitle_scene, strip=fixtures.subtitle_scene)
    pipeline = fixtures.build(
        env, fixtures.FakeStt(fixtures.SPEECH), ocr, scenes,
        confirm=lambda estimate: RegionDecision(action, estimate.rect),
    )
    return pipeline.run(make_video(seconds=10))


def _full_script() -> dict[int, list[OcrBox]]:
    """전체 화면에서도 같은 하단 자막이 읽힌다(둘째 자막의 날짜가 음성과 다르다)."""
    return {40 * (i + 1): [bottom(fixtures.SHOWN[i])] for i in range(5)}


def test_a_wrong_value_in_the_bottom_caption_is_reported_once(
    env: SimpleNamespace, make_video: MakeVideo
) -> None:
    result = _ocr_mode(env, make_video, _full_script())
    assert result.subtitle_source is SubtitleSource.OCR
    about_date = [
        i for i in env.repo.list_issues(result.session_id)
        if i.start_s < 4 and i.end_s > 2 and i.kind in (
            IssueKind.VALUE_MISMATCH, IssueKind.SCREEN_VALUE_MISMATCH, IssueKind.SCREEN_ONLY_INFO
        )
    ]
    assert [(i.source, i.kind) for i in about_date] == [(IssueSource.M1, IssueKind.VALUE_MISMATCH)]
    assert any("16일" in s["text"] for s in about_date[0].evidence["screens"])  # type: ignore[attr-defined]


def test_bottom_text_that_is_not_part_of_the_captions_is_no_longer_dropped(
    env: SimpleNamespace, make_video: MakeVideo
) -> None:
    full = _full_script()
    full[200] = [bottom(fixtures.SHOWN[4]), bottom("문의 02-1234-5678")]
    result = _ocr_mode(env, make_video, full)
    screen = [i for i in env.repo.list_issues(result.session_id) if i.source is IssueSource.M2]
    assert any("02-1234-5678" in (i.comparison_text or "") for i in screen)


@pytest.mark.parametrize(
    "action", [RegionAction.CONFIRMED, RegionAction.NONE], ids=["자막-확인", "자막-없음"]
)
def test_every_merged_screen_text_is_kept_for_the_side_by_side_view(
    env: SimpleNamespace, make_video: MakeVideo, action: RegionAction
) -> None:
    full = _full_script()
    full[40] = [bottom(fixtures.SHOWN[0]), top("상단 안내"), top("흐린 상단", 0.2)]
    result = _ocr_mode(env, make_video, full, action)
    data = load_compare_data(env.data, result.session_id)
    assert data is not None
    shown = {t.text for t in data.screen}
    assert set(fixtures.SHOWN) <= shown and "상단 안내" in shown
    assert [t.text for t in data.uncertain] == ["흐린 상단"]
    assert [s.text for s in data.speech] == fixtures.SENTENCES
    assert bool(data.cues) is (action is RegionAction.CONFIRMED)


def test_with_a_subtitle_file_the_view_gets_its_cues(
    env: SimpleNamespace, make_video: MakeVideo
) -> None:
    pipeline = fixtures.build(
        env, fixtures.FakeStt(fixtures.SPEECH), fixtures.FakeOcr(), fixtures.Scenes()
    )
    result = pipeline.run(make_video(seconds=10), fixtures.srt_for(env, fixtures.SHOWN))
    data = load_compare_data(env.data, result.session_id)
    assert data is not None and [c.text for c in data.cues] == fixtures.SHOWN


def test_failing_to_save_the_view_data_does_not_fail_the_analysis(
    env: SimpleNamespace, make_video: MakeVideo
) -> None:
    (env.data / "artifacts").mkdir(parents=True)
    (env.data / "artifacts" / "compare").write_text("폴더 자리에 파일", encoding="utf-8")
    pipeline = fixtures.build(
        env, fixtures.FakeStt(fixtures.SPEECH), fixtures.FakeOcr(), fixtures.Scenes()
    )
    result = pipeline.run(make_video(seconds=10), fixtures.srt_for(env, fixtures.SHOWN))
    codes = [n.code for n in env.repo.list_notes(result.session_id)]
    assert NoteCode.COMPARE_DATA_FAILED in codes
    assert load_compare_data(env.data, result.session_id) is None

"""분석 파이프라인: 영상 1건을 끝까지 분석해 지적 목록을 저장소에 남긴다.

단계(세부구현.md 6.4): 모델 준비 → 영상 준비 → 음성 인식 → 화면 문자 인식 → 자막 비교 →
화면 정보 비교 → 결과 정리.

설계 원칙
- 지적 목록은 모든 판정이 끝난 뒤 한 트랜잭션으로 저장한다. 취소·실패한 작업의 부분 결과는
  검수 화면에 노출되지 않는다.
- STT·OCR 원시 결과는 (영상 해시 + 엔진 + 모델 + 설정) 키로 캐시한다.
  손상된 캐시는 버리고 다시 만든다.
- 자막 파일이 없으면 하단을 스캔해 자막 영역을 추정하고, 편집자가 확인할 때까지 일시정지한다.
- 입력 오류(파일 없음, 길이 초과, 자막 파싱 실패)는 세션을 만들기 전에 거부한다.
"""

from __future__ import annotations

import shutil
import sqlite3
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol, TypeVar

from barrierfree.analysis.screen_check import check_screen
from barrierfree.analysis.screen_dependent import check_screen_dependent
from barrierfree.analysis.subtitle_check import check_subtitles
from barrierfree.analysis.subtitle_extract import extract_cues, split_by_region
from barrierfree.analysis.subtitle_region import Rect, RegionEstimate, estimate_subtitle_region
from barrierfree.cancel import CancelledError, CancelToken
from barrierfree.config import AnalysisConfig
from barrierfree.engines.ocr import (
    Frame,
    OcrBox,
    OcrError,
    OcrModelError,
    OcrObservation,
    merge_observations,
    observations_from_payload,
    observations_to_payload,
    run_ocr,
)
from barrierfree.engines.stt import (
    SttError,
    SttModelError,
    segments_from_payload,
    segments_to_payload,
)
from barrierfree.media.ffmpeg import (
    FfmpegError,
    UnsupportedVideoError,
    VideoFileNotFoundError,
    VideoInfo,
    VideoReadError,
    VideoTooLongError,
    extract_audio,
    iter_frames,
    validate_video,
)
from barrierfree.media.subtitles import LoadedSubtitle, SubtitleError, load_subtitle_file, write_srt
from barrierfree.models import Issue, SessionStatus, SpeechSegment, SubtitleCue, SubtitleSource
from barrierfree.storage.cache import CacheKey, CacheType, FileCache, hash_file, make_key
from barrierfree.storage.repository import Repository

T = TypeVar("T")


class Stage(StrEnum):
    MODEL_PREPARE = "MODEL_PREPARE"
    MEDIA_PREPARE = "MEDIA_PREPARE"
    STT = "STT"
    OCR = "OCR"
    SUBTITLE_CHECK = "SUBTITLE_CHECK"
    SCREEN_CHECK = "SCREEN_CHECK"
    RESULT_SAVE = "RESULT_SAVE"


@dataclass(frozen=True)
class StageEvent:
    stage: Stage
    state: str  # "RUNNING" | "DONE"
    fraction: float | None  # 측정할 수 없으면 None(화면은 처리 중 표시만 한다)
    message: str


class RegionAction(StrEnum):
    CONFIRMED = "CONFIRMED"
    ADJUSTED = "ADJUSTED"
    NONE = "NONE"  # 영상에 자막 없음


@dataclass(frozen=True)
class RegionDecision:
    action: RegionAction
    rect: Rect | None = None


@dataclass(frozen=True)
class PipelineResult:
    session_id: str
    issue_count: int
    subtitle_source: SubtitleSource
    notices: list[str] = field(default_factory=list)


class PipelineError(Exception):
    """사용자에게 보여 줄 수 있는 분석 실패. `code`는 세부구현.md의 ERR-xx."""

    def __init__(
        self,
        code: str,
        user_message: str,
        *,
        stage: Stage | None = None,
        session_id: str | None = None,
    ) -> None:
        super().__init__(f"{code}: {user_message}")
        self.code = code
        self.user_message = user_message
        self.stage = stage
        self.session_id = session_id


class PipelineStt(Protocol):
    engine_name: str
    model_name: str

    def transcribe(
        self, audio_path: str | Path, *, cancel: CancelToken | None = None
    ) -> list[SpeechSegment]: ...


class PipelineOcr(Protocol):
    engine_name: str
    model_name: str

    def read(self, frame: Frame) -> list[OcrBox]: ...


FrameSource = Callable[..., Iterator[tuple[float, Frame]]]
ConfirmRegion = Callable[[RegionEstimate], RegionDecision]
Progress = Callable[[StageEvent], None]


def _default_frame_source(
    path: Path,
    fps: float,
    max_width: int,
    bottom_ratio: float | None,
    info: VideoInfo,
    cancel: CancelToken | None = None,
) -> Iterator[tuple[float, Frame]]:
    return iter_frames(
        path, fps=fps, max_width=max_width, bottom_ratio=bottom_ratio, info=info, cancel=cancel
    )


_ERRORS: list[tuple[type[BaseException], str, str]] = [
    (VideoTooLongError, "ERR-02", "20분을 초과하는 영상은 분석할 수 없습니다."),
    (VideoFileNotFoundError, "ERR-01", "영상 파일을 찾을 수 없습니다. 파일 위치를 확인해 주세요."),
    (UnsupportedVideoError, "ERR-01", "지원하지 않는 영상 형식입니다."),
    (VideoReadError, "ERR-01", "영상 파일을 읽을 수 없습니다. 파일 상태를 확인해 주세요."),
    (SubtitleError, "ERR-03", "자막 파일을 읽을 수 없습니다. 형식 또는 인코딩을 확인해 주세요."),
    (FfmpegError, "ERR-04", "영상 처리에 실패했습니다. 실행 환경을 확인해 주세요."),
    (SttModelError, "ERR-06", "음성 인식 모델을 불러올 수 없습니다. 모델 설정을 확인해 주세요."),
    (SttError, "ERR-07", "음성 인식에 실패했습니다. 다시 시도해 주세요."),
    (OcrModelError, "ERR-06", "화면 글자 인식 모델을 불러올 수 없습니다. 설정을 확인해 주세요."),
    (OcrError, "ERR-08", "화면 글자 인식에 실패했습니다. 다시 시도해 주세요."),
    (sqlite3.Error, "ERR-09", "분석 결과를 저장하지 못했습니다. 저장 위치를 확인해 주세요."),
    (OSError, "ERR-09", "작업 폴더나 파일을 사용할 수 없습니다. 저장 위치를 확인해 주세요."),
]


_UNKNOWN = ("ERR-99", "분석 중 알 수 없는 오류가 발생했습니다.")


def _to_error(exc: BaseException, stage: Stage | None, session_id: str | None) -> PipelineError:
    if isinstance(exc, PipelineError):
        return exc
    code, message = next(
        ((c, m) for cls, c, m in _ERRORS if isinstance(exc, cls)),  # 구체적인 예외가 먼저다
        _UNKNOWN,
    )
    return PipelineError(code, message, stage=stage, session_id=session_id)


def explain_error(exc: BaseException) -> PipelineError:
    """예외를 사용자용 오류(코드 + 문구)로 바꾼다. 등록 화면의 파일 검증도 같은 문구를 쓴다."""
    return _to_error(exc, None, None)


def _remap_strip(observations: list[OcrObservation], ratio: float) -> list[OcrObservation]:
    """하단 띠(crop) 기준 좌표를 전체 프레임 기준 좌표로 바꾼다."""
    top = 1.0 - ratio
    return [
        OcrObservation(
            o.t,
            tuple(
                OcrBox(
                    b.text,
                    b.confidence,
                    (b.box[0], top + b.box[1] * ratio, b.box[2], b.box[3] * ratio),
                )
                for b in o.boxes
            ),
        )
        for o in observations
    ]


class AnalysisPipeline:
    def __init__(
        self,
        *,
        repo: Repository,
        cache: FileCache,
        stt: PipelineStt,
        ocr: PipelineOcr,
        config: AnalysisConfig,
        data_dir: str | Path,
        confirm_region: ConfirmRegion | None = None,
        on_progress: Progress | None = None,
        frame_source: FrameSource | None = None,
    ) -> None:
        self._repo = repo
        self._cache = cache
        self._stt = stt
        self._ocr = ocr
        self._config = config
        self._data_dir = Path(data_dir)
        self._confirm = confirm_region
        self._progress = on_progress
        self._frames = frame_source or _default_frame_source
        self._stage: Stage | None = None
        self._ocr_fraction = 0.0

    # ----- 진행 알림·측정 -----

    def _emit(self, stage: Stage, state: str, fraction: float | None, message: str) -> None:
        self._stage = stage
        if self._progress is not None:
            self._progress(StageEvent(stage, state, fraction, message))

    def _measure(
        self,
        session_id: str,
        stage: Stage,
        started: float,
        *,
        cache_hit: bool = False,
        count: int | None = None,
        unit: str | None = None,
    ) -> None:
        self._repo.record_stage_metric(
            session_id,
            stage.value,
            duration_ms=round((time.perf_counter() - started) * 1000),
            processed_count=count,
            unit=unit,
            cache_hit=cache_hit,
        )

    def _cached(
        self,
        key: CacheKey,
        compute: Callable[[], T],
        to_payload: Callable[[T], object],
        from_payload: Callable[[object], T],
    ) -> tuple[T, bool]:
        raw = self._cache.get(key)
        if raw is not None:
            try:
                return from_payload(raw), True
            except ValueError:
                pass  # 손상된 캐시는 버리고 다시 만든다
        value = compute()
        self._cache.put(key, to_payload(value))
        return value, False

    def _prepare_model(self, engine: object, message: str) -> None:
        prepare = getattr(engine, "prepare", None)
        if callable(prepare):
            self._emit(Stage.MODEL_PREPARE, "RUNNING", None, message)
            prepare()
            self._emit(Stage.MODEL_PREPARE, "DONE", None, message)

    # ----- 실행 -----

    def run(
        self,
        video_path: str | Path,
        subtitle_path: str | Path | None = None,
        cancel: CancelToken | None = None,
    ) -> PipelineResult:
        token = cancel or CancelToken()
        path = Path(video_path)
        self._stage = Stage.MEDIA_PREPARE
        try:
            info = validate_video(path, self._config)
            loaded = load_subtitle_file(subtitle_path) if subtitle_path else None
        except Exception as exc:
            raise _to_error(exc, Stage.MEDIA_PREPARE, None) from exc

        started = time.perf_counter()
        self._emit(Stage.MEDIA_PREPARE, "RUNNING", None, "영상을 준비하는 중입니다.")
        try:
            sha = hash_file(path)
        except OSError as exc:
            raise _to_error(VideoReadError(str(exc)), Stage.MEDIA_PREPARE, None) from exc

        session_id = self._repo.create_session(
            video=info,
            video_sha256=sha,
            subtitle_source=SubtitleSource.FILE if loaded else SubtitleSource.UNKNOWN,
            subtitle_path=str(subtitle_path) if subtitle_path else None,
            config=self._config,
            stt_model_name=self._stt.model_name,
            ocr_engine_name=self._ocr.engine_name,
            compute_device=str(getattr(self._stt, "device", "cpu")),
        )
        work = self._data_dir / "temp" / session_id
        try:
            # 폴더 생성도 세션 실패 처리와 정리 범위 안에 둔다(실패해도 세션이 미종료로 남지 않게).
            work.mkdir(parents=True, exist_ok=True)
            return self._analyze(session_id, path, info, sha, loaded, work, token, started)
        except CancelledError:
            self._set_terminal(session_id, SessionStatus.CANCELLED, None)
            raise
        except Exception as exc:
            error = _to_error(exc, self._stage, session_id)
            error.session_id = session_id
            self._set_terminal(session_id, SessionStatus.FAILED, error.code)
            if error is exc:
                raise
            raise error from exc
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _set_terminal(self, session_id: str, status: SessionStatus, code: str | None) -> None:
        try:
            self._repo.set_status(session_id, status, error_code=code)
        except sqlite3.Error:
            pass  # 상태 기록까지 실패했다면 더 할 수 있는 일이 없다. 원래 오류를 우선한다.

    def _analyze(
        self,
        session_id: str,
        path: Path,
        info: VideoInfo,
        sha: str,
        loaded: LoadedSubtitle | None,
        work: Path,
        cancel: CancelToken,
        media_started: float,
    ) -> PipelineResult:
        cfg, repo = self._config, self._repo
        notices: list[str] = []
        repo.set_status(session_id, SessionStatus.ANALYZING)

        # 1. 영상 준비
        audio: Path | None = None
        if info.has_audio:
            audio = extract_audio(path, work / "audio.wav", cancel=cancel)
        self._measure(session_id, Stage.MEDIA_PREPARE, media_started)
        self._emit(Stage.MEDIA_PREPARE, "DONE", 1.0, "영상 준비를 마쳤습니다.")
        cancel.raise_if_cancelled()

        # 2. 음성 인식
        started = time.perf_counter()
        self._emit(Stage.STT, "RUNNING", None, "음성을 인식하는 중입니다.")
        speech: list[SpeechSegment] = []
        stt_hit = False
        if audio is not None:
            self._prepare_model(self._stt, "음성 인식 모델을 준비하는 중입니다.")
            self._stage = Stage.STT
            key = make_key(
                sha, CacheType.STT, self._stt.engine_name, self._stt.model_name,
                {"language": "ko", "vad": True, "condition_on_previous_text": False},
            )
            speech, stt_hit = self._cached(
                key,
                lambda: self._stt.transcribe(audio, cancel=cancel),
                segments_to_payload,
                segments_from_payload,
            )
        else:
            notices.append("영상에 오디오 트랙이 없어 음성 인식을 건너뛰었습니다.")
        self._measure(session_id, Stage.STT, started, cache_hit=stt_hit, count=len(speech),
                      unit="SEGMENT")
        self._emit(Stage.STT, "DONE", 1.0, "음성 인식을 마쳤습니다.")
        cancel.raise_if_cancelled()

        # 3. 화면 문자 인식 (+ 자막 파일이 없으면 하단 자막 영역 스캔)
        started = time.perf_counter()
        self._emit(Stage.OCR, "RUNNING", 0.0, "화면 글자를 인식하는 중입니다.")
        self._prepare_model(self._ocr, "화면 글자 인식 모델을 준비하는 중입니다.")
        self._stage = Stage.OCR
        self._ocr_fraction = 0.0
        passes = 1 if loaded else 2
        hits: list[bool] = []

        full_obs, hit = self._ocr_pass(path, info, sha, "full", None, cfg.screen_ocr_fps,
                                       cancel, 0, passes)
        hits.append(hit)
        full = merge_observations(full_obs, cfg, 1.0 / cfg.screen_ocr_fps)

        cues: list[SubtitleCue] = []
        source = SubtitleSource.FILE
        rect: Rect | None = None
        if loaded is not None:
            cues = loaded.cues
            if loaded.skipped_blocks:
                notices.append(
                    f"자막 파일에서 {loaded.skipped_blocks}개 구간을 읽지 못했습니다. "
                    "해당 구간은 점검에서 빠집니다."
                )
        else:
            source, cues, rect, scan_hits = self._resolve_ocr_subtitles(
                session_id, path, info, sha, speech, cancel, notices
            )
            hits.extend(scan_hits)
        self._measure(session_id, Stage.OCR, started, cache_hit=all(hits),
                      count=len(full_obs), unit="FRAME")
        self._emit(Stage.OCR, "DONE", 1.0, "화면 글자 인식을 마쳤습니다.")
        cancel.raise_if_cancelled()

        # 4. 자막 비교 (M1)
        started = time.perf_counter()
        self._emit(Stage.SUBTITLE_CHECK, "RUNNING", None, "음성과 자막을 비교하는 중입니다.")
        m1: list[Issue] = []
        if source in (SubtitleSource.FILE, SubtitleSource.OCR):
            m1 = check_subtitles(speech, cues, cfg, ocr_generated=source is SubtitleSource.OCR)
        self._measure(session_id, Stage.SUBTITLE_CHECK, started, count=len(m1), unit="ISSUE")
        self._emit(Stage.SUBTITLE_CHECK, "DONE", 1.0, "자막 비교를 마쳤습니다.")
        cancel.raise_if_cancelled()

        # 5. 화면 정보 비교 (M2, M2-2)
        started = time.perf_counter()
        self._emit(Stage.SCREEN_CHECK, "RUNNING", None, "화면 정보와 음성을 비교하는 중입니다.")
        screen_texts, screen_uncertain = full.texts, full.uncertain
        if source is SubtitleSource.OCR and rect is not None:
            # 확인된 자막 영역의 글자는 자막으로 쓰였으므로 화면 글자 점검에서 뺀다.
            screen_texts = split_by_region(screen_texts, rect)[1]
            screen_uncertain = split_by_region(screen_uncertain, rect)[1]
        m2 = check_screen(screen_texts, screen_uncertain, speech, cues, info.duration_s, cfg)
        m2_dependent = check_screen_dependent(speech, m2, cfg)
        self._measure(session_id, Stage.SCREEN_CHECK, started,
                      count=len(m2) + len(m2_dependent), unit="ISSUE")
        self._emit(Stage.SCREEN_CHECK, "DONE", 1.0, "화면 정보 비교를 마쳤습니다.")
        cancel.raise_if_cancelled()

        # 6. 결과 정리: 한 트랜잭션으로 저장
        started = time.perf_counter()
        self._emit(Stage.RESULT_SAVE, "RUNNING", None, "결과를 정리하는 중입니다.")
        issues = sorted(m1 + m2 + m2_dependent, key=lambda i: (i.start_s, i.end_s))
        repo.complete_session(session_id, issues)
        try:
            self._measure(session_id, Stage.RESULT_SAVE, started, count=len(issues), unit="ISSUE")
        except sqlite3.Error:
            pass  # 측정은 부가 정보다. 이미 저장·완료된 분석을 실패로 뒤집지 않는다.
        self._emit(Stage.RESULT_SAVE, "DONE", 1.0, "분석을 마쳤습니다.")
        return PipelineResult(session_id, len(issues), source, notices)

    # ----- OCR -----

    def _ocr_pass(
        self,
        path: Path,
        info: VideoInfo,
        sha: str,
        mode: str,
        ratio: float | None,
        fps: float,
        cancel: CancelToken,
        pass_index: int,
        pass_total: int,
    ) -> tuple[list[OcrObservation], bool]:
        cfg = self._config
        key = make_key(
            sha, CacheType.OCR, self._ocr.engine_name, self._ocr.model_name,
            {"mode": mode, "fps": fps, "max_width": cfg.ocr_max_width, "ratio": ratio,
             "diff": cfg.frame_diff_skip_threshold},
        )

        def report(share: float) -> None:
            fraction = min(1.0, (pass_index + share) / pass_total)
            self._ocr_fraction = max(self._ocr_fraction, fraction)
            self._emit(Stage.OCR, "RUNNING", self._ocr_fraction, "화면 글자를 인식하는 중입니다.")

        def compute() -> list[OcrObservation]:
            def frames() -> Iterator[tuple[float, Frame]]:
                for t, frame in self._frames(
                    path, fps, cfg.ocr_max_width, ratio, info, cancel=cancel
                ):
                    report(min(1.0, t / max(info.duration_s, 1.0)))
                    yield t, frame

            return run_ocr(frames(), self._ocr, cfg, cancel=cancel)

        observations, hit = self._cached(
            key, compute, observations_to_payload, observations_from_payload
        )
        report(1.0)
        return observations, hit

    def _resolve_ocr_subtitles(
        self,
        session_id: str,
        path: Path,
        info: VideoInfo,
        sha: str,
        speech: list[SpeechSegment],
        cancel: CancelToken,
        notices: list[str],
    ) -> tuple[SubtitleSource, list[SubtitleCue], Rect | None, list[bool]]:
        """자막 파일이 없을 때: 하단을 스캔하고 편집자가 영역을 확인하면 OCR 자막을 만든다."""
        cfg, repo = self._config, self._repo
        ratio = cfg.subtitle_region_ratio
        fps = cfg.subtitle_ocr_fps
        raw, hit = self._ocr_pass(path, info, sha, "strip", ratio, fps, cancel, 1, 2)
        strip = merge_observations(_remap_strip(raw, ratio), cfg, 1.0 / fps)
        estimate = estimate_subtitle_region(strip.texts, speech, info.duration_s, cfg, ratio=ratio)
        hits = [hit]

        repo.set_status(session_id, SessionStatus.AWAITING_CONFIRMATION)
        if self._confirm is not None:
            decision = self._confirm(estimate)
        elif estimate.is_subtitle:
            decision = RegionDecision(RegionAction.CONFIRMED, estimate.rect)
        else:
            decision = RegionDecision(RegionAction.NONE)
        cancel.raise_if_cancelled()
        repo.set_status(session_id, SessionStatus.ANALYZING)

        if decision.action is RegionAction.NONE:
            repo.set_subtitle(session_id, SubtitleSource.NONE, region={"decision": "NONE"})
            notices.append("자막이 없는 영상입니다. 음성별 자막 누락은 개별로 표시하지 않습니다.")
            return SubtitleSource.NONE, [], None, hits

        rect = decision.rect or estimate.rect
        new_ratio = round(1.0 - rect[1], 4)
        texts = strip.texts
        if new_ratio > ratio + 1e-6:  # 더 넓게 조정했으면 넓은 띠로 다시 읽는다
            self._stage = Stage.OCR
            raw2, hit2 = self._ocr_pass(path, info, sha, "strip", new_ratio, fps, cancel, 1, 2)
            hits.append(hit2)
            texts = merge_observations(_remap_strip(raw2, new_ratio), cfg, 1.0 / fps).texts

        inside, _ = split_by_region(texts, rect)
        cues = extract_cues(inside)
        if not cues:
            repo.set_subtitle(session_id, SubtitleSource.NONE, region={"decision": "NONE"})
            notices.append(
                "확인한 자막 영역에서 글자를 찾지 못해 자막이 없는 것으로 처리했습니다."
            )
            return SubtitleSource.NONE, [], None, hits

        relative = f"artifacts/subtitles/{session_id}.srt"
        write_srt(cues, self._data_dir / relative)
        repo.set_subtitle(
            session_id,
            SubtitleSource.OCR,
            generated_srt_path=relative,
            region={"decision": decision.action.value, "normalized_rect": list(rect)},
        )
        notices.append("OCR로 추출한 자막으로 점검합니다. 이 모드의 결과는 모두 검수가 필요합니다.")
        return SubtitleSource.OCR, cues, rect, hits

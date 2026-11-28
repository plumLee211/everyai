"""정답 자막의 초안을 만든다. 초안은 정답이 아니다. 사람이 영상을 들으며 고쳐야 한다.

앱이 쓰는 모델보다 큰 모델로 받아써서 고칠 곳을 줄인다. 평가할 모델의 출력을 그대로 정답으로
쓰면 그 모델이 틀린 곳이 정답이 되므로, 교정을 건너뛰지 않는다.

사용법: python -m tests.eval.draft <영상> <출력 SRT> [--model large-v3]
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from barrierfree.engines.stt import FasterWhisperEngine, SttError
from barrierfree.media.ffmpeg import FfmpegError, extract_audio
from barrierfree.media.subtitles import write_srt
from barrierfree.models import SpeechSegment, SubtitleCue

DRAFT_MODEL = "large-v3"


def segments_to_cues(segments: list[SpeechSegment]) -> list[SubtitleCue]:
    return [
        SubtitleCue(s.start_s, s.end_s, s.text.strip())
        for s in sorted(segments, key=lambda s: (s.start_s, s.end_s))
        if s.text.strip() and s.end_s > s.start_s
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="사람이 교정할 정답 자막 초안을 만든다.")
    parser.add_argument("video", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--model", default=DRAFT_MODEL)
    args = parser.parse_args(argv)
    if args.out.exists():
        print(f"이미 있는 파일은 덮어쓰지 않습니다: {args.out}", file=sys.stderr)
        return 1
    try:
        with tempfile.TemporaryDirectory() as work:
            audio = extract_audio(args.video, Path(work) / "audio.wav")
            engine = FasterWhisperEngine(args.model, device="cpu", compute_type="int8")
            cues = segments_to_cues(engine.transcribe(audio))
    except (FfmpegError, SttError) as exc:
        print(f"실패: {exc}", file=sys.stderr)
        return 1
    write_srt(cues, args.out)
    print(f"초안 {len(cues)}개 구간: {args.out}  (모델 {args.model}, 교정 전에는 정답이 아닙니다)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

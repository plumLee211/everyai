"""화면 글자 미탐률: 사람이 적은 화면 글자 중에서 OCR이 같은 시점에 읽어 낸 비율.

지적 단위 탐지율(score.py)로는 OCR 단계에서 놓친 것과 판정 규칙이 놓친 것을 가를 수 없다.

'읽었다'의 기준: 시간이 겹치는 OCR 큐에 정답 글자가 들어 있다(띄어쓰기·기호 무시).
글자 일부가 틀려도 숫자가 모두 같고 유사도가 0.8 이상이면 인정한다. 숫자가 다르면 미탐이다.

정답 JSON: {"screen_texts": [{"start_s": 1.0, "end_s": 2.5, "text": "..."}]}
OCR 결과: 앱을 `BARRIERFREE_OCR_DIR`로 실행하면 나오는 SRT(확실한 글자만 담긴다).

사용법: python -m tests.eval.screen_recall <정답 JSON> <OCR SRT>
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from barrierfree.analysis.normalize import normalize_text
from barrierfree.media.subtitles import SubtitleError, load_subtitle_file
from barrierfree.models import SubtitleCue

SIMILARITY_MIN = 0.8
SHORT_S = 1.0  # 이보다 짧게 보인 글자는 초당 1프레임 샘플링 사이로 빠질 수 있다


@dataclass(frozen=True)
class ScreenTruth:
    start_s: float
    end_s: float
    text: str


@dataclass(frozen=True)
class RecallResult:
    total: int
    seen: int
    short_total: int
    short_seen: int
    missed: list[ScreenTruth]

    @property
    def miss_rate(self) -> float | None:
        return (self.total - self.seen) / self.total if self.total else None


def _numbers(text: str) -> Counter[str]:
    return Counter(re.findall(r"\d+", text))


def _contains(truth: str, observed: str) -> bool:
    wanted = normalize_text(truth, keep_spaces=False)
    got = normalize_text(observed, keep_spaces=False)
    if not wanted:
        return False
    if wanted in got:
        return True
    matcher = SequenceMatcher(None, wanted, got, autojunk=False)
    best = matcher.find_longest_match()
    start = max(0, best.b - best.a)
    window = got[start : start + len(wanted)]  # 여러 줄이 묶인 큐에서 해당 부분만 비교한다
    if _numbers(wanted) - _numbers(window):
        return False
    return SequenceMatcher(None, wanted, window, autojunk=False).ratio() >= SIMILARITY_MIN


def screen_text_recall(truths: list[ScreenTruth], observed: list[SubtitleCue]) -> RecallResult:
    missed: list[ScreenTruth] = []
    short_total = short_seen = 0
    for truth in truths:
        found = any(
            cue.start_s < truth.end_s
            and cue.end_s > truth.start_s
            and _contains(truth.text, cue.text)
            for cue in observed
        )
        short = truth.end_s - truth.start_s < SHORT_S
        short_total += short
        short_seen += short and found
        if not found:
            missed.append(truth)
    return RecallResult(
        len(truths), len(truths) - len(missed), short_total, short_seen, missed
    )


def load_screen_truth(path: str | Path) -> list[ScreenTruth]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"정답 파일을 읽을 수 없습니다: {path}") from exc
    items = data.get("screen_texts") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("screen_texts(화면 글자 정답 목록)가 필요합니다.")
    truths: list[ScreenTruth] = []
    for index, raw in enumerate(items):
        try:
            start, end, text = float(raw["start_s"]), float(raw["end_s"]), str(raw["text"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"screen_texts[{index}] 형식이 올바르지 않습니다.") from exc
        if not 0 <= start < end or not text.strip():
            raise ValueError(f"screen_texts[{index}]의 구간이나 글자가 올바르지 않습니다.")
        truths.append(ScreenTruth(start, end, text))
    return truths


def format_result(result: RecallResult) -> str:
    rate = "—" if result.miss_rate is None else f"{result.miss_rate * 100:.0f}%"
    lines = [
        f"화면 글자 미탐률 {rate} (읽음 {result.seen}/{result.total})",
        f"{SHORT_S:.0f}초 미만으로 보인 글자: 읽음 {result.short_seen}/{result.short_total}",
    ]
    lines += [f"  놓침 {t.start_s:.1f}-{t.end_s:.1f}s  {t.text}" for t in result.missed]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("사용법: python -m tests.eval.screen_recall <정답 JSON> <OCR SRT>", file=sys.stderr)
        return 2
    try:
        truths = load_screen_truth(args[0])
        observed = load_subtitle_file(args[1]).cues
    except (OSError, SubtitleError, ValueError) as exc:
        print(f"실패: {exc}", file=sys.stderr)
        return 1
    print(format_result(screen_text_recall(truths, observed)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

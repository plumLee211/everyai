"""교정을 마친 정답 자막에 일부러 실수를 넣고, 넣은 실수를 그대로 정답 지적 목록으로 남긴다.

실수 종류: 숫자 바꾸기(value), 문장 빼기(drop), 이웃한 두 자막의 문장 맞바꾸기(swap).
바꿀 숫자는 앱의 핵심 정보 추출기를 쓰지 않고 고른다. 채점받는 코드가 시험 문제를 고르면
그 코드가 못 읽는 값은 시험에 나오지 않는다.

정답은 '원본 자막이 음성과 맞다'는 전제 위에 있다. 사람이 교정하지 않은 초안을 넣지 않는다.

사용법:
  python -m tests.eval.inject <교정한 자막> <출력 폴더> --video <영상 파일명> --type <영상 형식>
      [--values N] [--drops N] [--swaps N] [--seed N]
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from barrierfree.media.subtitles import SubtitleError, load_subtitle_file, write_srt
from barrierfree.models import SubtitleCue

_NUMBER = re.compile(r"\d+")


@dataclass(frozen=True)
class Plan:
    values: int = 0
    drops: int = 0
    swaps: int = 0  # 한 번에 자막 두 개가 틀리게 된다


@dataclass(frozen=True)
class Mistake:
    start_s: float
    end_s: float
    source: str
    mutation: str  # value | drop | swap
    original: str
    changed: str  # 뺀 문장은 빈 문자열


def change_number(text: str, seed: int) -> str | None:
    """숫자 하나의 한 자리를 다른 숫자로 바꾼다. 자릿수는 그대로다. 숫자가 없으면 None."""
    numbers = list(_NUMBER.finditer(text))
    if not numbers:
        return None
    rng = random.Random(seed)
    target = rng.choice(numbers)
    digits = list(target.group())
    at = len(digits) - 1  # 끝자리를 바꾸면 '15일 → 16일'처럼 그럴듯한 실수가 된다
    if len(digits) > 1 and set(digits[1:]) == {"0"}:
        at = 0  # 30000처럼 0으로 끝나는 수는 앞자리를 바꾼다
    choices = [d for d in "123456789" if d != digits[at]]
    digits[at] = rng.choice(choices)
    return text[: target.start()] + "".join(digits) + text[target.end() :]


def inject(
    cues: list[SubtitleCue], plan: Plan, seed: int
) -> tuple[list[SubtitleCue], list[Mistake]]:
    """자막 하나에는 실수를 하나만 넣는다. 요청한 수를 채울 수 없으면 ValueError."""
    rng = random.Random(seed)
    changed: dict[int, str] = {}  # 인덱스 -> 새 문장(빈 문자열이면 뺀다)
    kinds: dict[int, str] = {}

    with_number = [i for i, c in enumerate(cues) if _NUMBER.search(c.text)]
    if len(with_number) < plan.values:
        raise ValueError(f"숫자가 있는 자막이 {len(with_number)}개뿐입니다(요청 {plan.values}).")
    for i in rng.sample(with_number, plan.values):
        value = change_number(cues[i].text, rng.randrange(2**31))
        assert value is not None
        changed[i], kinds[i] = value, "value"

    pairs = [(i, i + 1) for i in range(len(cues) - 1) if cues[i].text != cues[i + 1].text]
    rng.shuffle(pairs)
    swapped = 0
    for a, b in pairs:
        if swapped == plan.swaps:
            break
        if a in kinds or b in kinds:
            continue
        changed[a], changed[b] = cues[b].text, cues[a].text
        kinds[a] = kinds[b] = "swap"
        swapped += 1
    if swapped < plan.swaps:
        raise ValueError(f"맞바꿀 이웃 자막이 부족합니다(요청 {plan.swaps}, 가능 {swapped}).")

    free = [i for i in range(len(cues)) if i not in kinds]
    if len(free) < plan.drops:
        raise ValueError(f"뺄 수 있는 자막이 {len(free)}개뿐입니다(요청 {plan.drops}).")
    for i in rng.sample(free, plan.drops):
        changed[i], kinds[i] = "", "drop"

    result: list[SubtitleCue] = []
    mistakes: list[Mistake] = []
    for i, cue in enumerate(cues):
        text = changed.get(i, cue.text)
        if i in kinds:
            mistakes.append(Mistake(cue.start_s, cue.end_s, "M1", kinds[i], cue.text, text))
        if text:
            result.append(replace(cue, text=text))
    return result, mistakes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="정답 자막에 실수를 넣어 평가 자료를 만든다.")
    parser.add_argument("reference", type=Path, help="사람이 교정을 마친 자막(SRT·SMI)")
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--video", required=True, help="분석할 영상 파일명")
    parser.add_argument("--type", required=True, dest="video_type", help="영상 형식")
    parser.add_argument("--values", type=int, default=0)
    parser.add_argument("--drops", type=int, default=0)
    parser.add_argument("--swaps", type=int, default=0)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        cues = load_subtitle_file(args.reference).cues
        mutated, mistakes = inject(cues, Plan(args.values, args.drops, args.swaps), args.seed)
    except (OSError, SubtitleError, ValueError) as exc:
        print(f"실패: {exc}", file=sys.stderr)
        return 1
    stem = f"{Path(args.video).stem}_seed{args.seed}"
    write_srt(mutated, args.out_dir / f"{stem}.srt")
    truth = {
        "video": args.video,
        "video_type": args.video_type,
        "seed": args.seed,
        "items": [asdict(m) for m in mistakes],
    }
    (args.out_dir / f"{stem}.json").write_text(
        json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"실수 {len(mistakes)}건: {args.out_dir / stem}.srt, .json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

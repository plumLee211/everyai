# 평가 도구

정확도는 사람이 정한 정답과 비교해서 잰다. 영상과 정답 파일은 `eval_data/`(git 제외)에 둔다.
아래 명령은 저장소 루트에서 `.venv`를 활성화하고, `pip install -e .`가 되어 있어야 한다
(아니면 앞에 `PYTHONPATH=src`를 붙인다).

## 1. 정답 자막 만들기

```bash
python -m tests.eval.draft 영상.mp4 eval_data/이름/draft.srt   # large-v3 초안(처음엔 약 3GB 내려받음)
cp eval_data/이름/draft.srt eval_data/이름/reference.srt
```

`reference.srt`를 영상을 들으며 고친다. **고치기 전의 초안은 정답이 아니다.**

- 들리는 말을 그대로 적는다. 숫자·날짜·고유명사는 특히 확인한다.
- 화면에만 보이는 글자는 넣지 않는다.
- 알아들을 수 없는 구간은 적지 말고 따로 기록한다.

## 2. 틀린 자막과 정답 지적 목록 만들기 (M1)

```bash
python -m tests.eval.inject eval_data/이름/reference.srt eval_data/이름/m1 \
  --video 영상.mp4 --type "정책 안내" --values 2 --drops 2 --swaps 1 --seed 1
```

만들어진 `.srt`를 자막 파일로 넣어 앱에서 분석한 뒤 채점한다. 실수를 넣지 않은 `reference.srt`로도
분석해, 맞는 자막을 틀렸다고 하는 수를 함께 본다.

```bash
python -m tests.eval.score eval_data/이름/m1 <앱 데이터 폴더>
```

채점은 같은 영상의 **가장 최근** 분석 1건을 쓴다. seed마다 분석하고 바로 채점한다.

## 3. 화면 글자 미탐률

화면에 나온 글자를 사람이 `screen.json`에 적는다(보인 시각을 0.1초 단위까지).

```json
{"screen_texts": [{"start_s": 1.0, "end_s": 2.5, "text": "신청 기간 3월 15일"}]}
```

```bash
BARRIERFREE_OCR_DIR=eval_data/이름/ocr python -m barrierfree     # 분석하면 OCR 결과 SRT가 남는다
python -m tests.eval.screen_recall eval_data/이름/screen.json eval_data/이름/ocr/<파일>.srt
```

## 한계

- 실수를 넣어 만든 정답은 실제 편집 실수의 분포와 다르다. 형식이 다른 영상의 실제 사례로 보완한다.
- M2(화면 정보)의 정답 지적은 이 도구로 만들 수 없어 사람이 직접 적는다(`score.py`의 JSON 형식).

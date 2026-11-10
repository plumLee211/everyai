"""STT/OCR 원시 결과의 파일 캐시. 인덱스는 SQLite(`analysis_cache`), 본문은 JSON 파일.

캐시 키는 영상 해시만으로 만들지 않는다. 엔진·모델·설정이 바뀌면 원시 결과도 달라지기 때문이다.
파일은 임시 이름으로 쓴 뒤 원자적으로 옮기고, 그 다음에 인덱스를 등록한다.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from barrierfree.storage.repository import Repository


class CacheType(StrEnum):
    STT = "STT"
    OCR = "OCR"


@dataclass(frozen=True)
class CacheKey:
    key: str
    cache_type: CacheType
    video_sha256: str
    engine_name: str
    model_name: str | None
    config_hash: str


def hash_file(path: str | Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        while chunk := f.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def make_key(
    video_sha256: str,
    cache_type: CacheType,
    engine_name: str,
    model_name: str | None,
    config: Mapping[str, object],
) -> CacheKey:
    config_hash = hashlib.sha256(
        json.dumps(config, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()[:16]
    raw = "|".join([video_sha256, cache_type.value, engine_name, model_name or "", config_hash])
    return CacheKey(
        key=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        cache_type=cache_type,
        video_sha256=video_sha256,
        engine_name=engine_name,
        model_name=model_name,
        config_hash=config_hash,
    )


class FileCache:
    def __init__(self, root: str | Path, repo: Repository) -> None:
        self._root = Path(root)
        self._repo = repo

    @staticmethod
    def _relative(key: CacheKey) -> str:
        return f"{key.cache_type.value.lower()}/{key.key}.json"

    def get(self, key: CacheKey) -> object | None:
        """적중하면 저장된 값을, 미적중·손상·파일 없음이면 None을 돌려준다."""
        rel = self._repo.find_cache(key.key)
        if rel is None:
            return None
        try:
            with (self._root / rel).open(encoding="utf-8") as f:
                data: object = json.load(f)
            return data
        except (OSError, ValueError):
            self._repo.delete_cache(key.key)
            return None

    def put(self, key: CacheKey, payload: object) -> None:
        rel = self._relative(key)
        target = self._root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, target)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        self._repo.register_cache(
            cache_key=key.key,
            cache_type=key.cache_type.value,
            video_sha256=key.video_sha256,
            engine_name=key.engine_name,
            model_name=key.model_name,
            config_hash=key.config_hash,
            file_path=rel,
        )

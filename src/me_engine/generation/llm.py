"""Thin DeepSeek/OpenAI-compatible JSON client with on-disk caching."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

from openai import OpenAI

from .config import GenConfig


class GenLLM:
    def __init__(self, config: GenConfig, cache_path: Path | None = None) -> None:
        self._config = config
        self._client = OpenAI(api_key=config.api_key, base_url=config.base_url, timeout=config.timeout)
        self._cache_path = cache_path
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(cache_path) as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, v TEXT)")

    def _key(self, system: str, user: str) -> str:
        h = hashlib.sha256(f"{self._config.model}\x00{system}\x00{user}".encode("utf-8"))
        return h.hexdigest()

    def _cache_get(self, key: str) -> str | None:
        if self._cache_path is None:
            return None
        with sqlite3.connect(self._cache_path) as conn:
            row = conn.execute("SELECT v FROM cache WHERE k=?", (key,)).fetchone()
        return row[0] if row else None

    def _cache_put(self, key: str, value: str) -> None:
        if self._cache_path is None:
            return
        with sqlite3.connect(self._cache_path) as conn:
            conn.execute("INSERT OR REPLACE INTO cache (k, v) VALUES (?, ?)", (key, value))

    def complete_json(self, system: str, user: str, *, retries: int = 3) -> dict:
        """Return parsed JSON object from the model, with cache + retries."""
        key = self._key(system, user)
        cached = self._cache_get(key)
        if cached is not None:
            try:
                return json.loads(cached)
            except json.JSONDecodeError:
                pass

        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                resp = self._client.chat.completions.create(
                    model=self._config.model,
                    messages=[
                        {"role": "system", "content": system + "\nRespond ONLY with a valid JSON object."},
                        {"role": "user", "content": user},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.2,
                )
                content = resp.choices[0].message.content or "{}"
                data = json.loads(content)
                self._cache_put(key, content)
                return data
            except Exception as exc:  # noqa: BLE001 - provider errors vary
                last_err = exc
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"LLM call failed after {retries} attempts: {last_err}")

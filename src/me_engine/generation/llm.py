"""Thin DeepSeek/OpenAI-compatible JSON client with on-disk caching."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from openai import OpenAI

from .config import GenConfig


_CACHE_BUSY_TIMEOUT_MS = 30_000  # runtime optimization (2026-08-26): see module note below
# output cap per call (deepseek-chat allows up to 8K); ME_LLM_MAX_TOKENS overrides
_MAX_TOKENS = int(os.environ.get("ME_LLM_MAX_TOKENS", "8192"))


# Spend cap (2026-10-22): DEEPSEEK_BALANCE_FLOOR=<usd> -- no call is made once the account balance is below it.
# The balance is read at most every 30 s (one cheap GET), so with many workers the cap holds to within cents.
_FLOOR = {"t": 0.0, "balance": None}
_floor_lock = threading.Lock()


def _check_floor(api_key: str) -> None:
    floor = os.environ.get("DEEPSEEK_BALANCE_FLOOR", "").strip()
    if not floor:
        return
    with _floor_lock:
        if time.time() - _FLOOR["t"] > 30:
            try:
                import httpx
                r = httpx.get("https://api.deepseek.com/user/balance",
                              headers={"Authorization": f"Bearer {api_key}"}, timeout=15)
                _FLOOR["balance"] = float(r.json()["balance_infos"][0]["total_balance"])
            except Exception:  # noqa: BLE001 - a failed read keeps the last value
                pass
            _FLOOR["t"] = time.time()
        bal = _FLOOR["balance"]
    if bal is not None and bal < float(floor):
        raise LLMBalanceExhausted(f"DEEPSEEK_BALANCE_EXHAUSTED: spend cap reached (balance ${bal:.2f} < floor ${float(floor):.2f})")


class LLMBalanceExhausted(BaseException):
    """DeepSeek refused for lack of balance. A BaseException on purpose, like web_evidence.SearchQuotaExhausted:
    the agents catch Exception and would carry on with empty answers. The VM worker stops on it."""


class AnswerCutOff(RuntimeError):
    """The model hit the output limit: the JSON is incomplete, and a retry of the same prompt would be too."""


# Wall-clock cap per request. DeepSeek, when busy, holds a request open for up to 30 minutes and sends
# keep-alive bytes meanwhile -- each byte resets the HTTP read timeout (ME_LLM_TIMEOUT), so that timeout
# never fires and one call can stall a whole report (seen 2026-10-21: 9+ minutes, no answer).
_CALL_DEADLINE_S = float(os.environ.get("ME_LLM_CALL_DEADLINE", "300"))


# Token accounting (cost measurement, 2026-10-21): every paid call's usage as the API reports it. DeepSeek
# splits input into cache hits (repeated prefix, ~1/50 the price) and misses. LLM_LOG=<path> writes one JSON
# line per call; a summary line is printed when the process exits.
_USAGE = {"calls": 0, "in_hit": 0, "in_miss": 0, "out": 0}
_usage_lock = threading.Lock()
# $ per 1M tokens, deepseek-flash (V4.1) off-peak; peak (01-04, 06-10 UTC weekdays) is double
_PRICE = {"in_hit": 0.003, "in_miss": 0.15, "out": 0.60}


def _account(system: str, usage) -> None:
    if usage is None:
        return
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    hit = int(getattr(usage, "prompt_cache_hit_tokens", 0) or 0)
    miss = int(getattr(usage, "prompt_cache_miss_tokens", prompt - hit) or 0)
    out = int(getattr(usage, "completion_tokens", 0) or 0)
    with _usage_lock:
        _USAGE["calls"] += 1
        _USAGE["in_hit"] += hit
        _USAGE["in_miss"] += miss
        _USAGE["out"] += out
    path = os.environ.get("LLM_LOG", "").strip()
    if path:
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"t": round(time.time(), 2), "system": system[:80], "in_hit": hit,
                                     "in_miss": miss, "out": out}) + "\n")
        except Exception:  # noqa: BLE001
            pass


def usage_summary() -> str:
    u = _USAGE
    usd = sum(u[k] * _PRICE[k] for k in _PRICE) / 1e6
    return (f"DeepSeek: {u['calls']} calls, input {u['in_hit'] + u['in_miss']:,} tokens "
            f"({u['in_hit']:,} cache hit), output {u['out']:,} tokens -- ~${usd:.3f} off-peak (x2 at peak)")


import atexit as _atexit  # noqa: E402
_atexit.register(lambda: _USAGE["calls"] and print(usage_summary(), flush=True))


def _repair_json(text: str) -> str:
    """The usual slips in a long model-written JSON: a missing comma between two values / members
    ('} {', '] [', '"a" "b"', '1 "b"' across a line break) and a trailing comma before '}' or ']'."""
    import re
    t = text.strip()
    t = re.sub(r",\s*([}\]])", r"\1", t)                                   # trailing comma
    t = re.sub(r'([}\]"]|\d|true|false|null)(\s*\n\s*)(["{\[])', r"\1,\2\3", t)  # missing comma at a line break
    t = re.sub(r'([}\]])(\s*)([{\[])', r"\1,\2\3", t)                      # '} {' / '] ['
    return t


def _with_deadline(seconds: float, fn, *args, **kwargs):
    """fn(*args, **kwargs), or TimeoutError after `seconds`. The stuck request is abandoned on a daemon
    thread (it ends when the server closes it); the caller retries with a fresh request."""
    box: dict = {}

    def run() -> None:
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller's thread
            box["error"] = exc
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise TimeoutError(f"no answer within {seconds:.0f}s (request abandoned)")
    if "error" in box:
        raise box["error"]
    return box["value"]


class GenLLM:
    """Thread-safety note (runtime optimization, 2026-08-26, added to support
    Demand-Usage's bounded-parallel evidence acquisition -- `agents.py`):
    the underlying `openai` SDK client is documented as safe for concurrent
    use from multiple threads (its HTTP transport pool handles this), so
    `complete_json` needs NO lock around the actual LLM call itself -- that
    would defeat the entire purpose of parallelism. The on-disk SQLite cache
    IS a real concurrency concern: every `_cache_get`/`_cache_put` already
    opened its own short-lived connection (never shared across calls/threads,
    so this was never a "one connection touched by many threads" bug), but
    SQLite's default rollback-journal mode serializes writers and its default
    5s busy-timeout can raise "database is locked" under genuine concurrent
    write pressure. WAL mode (readers don't block the writer, and vice versa)
    + a longer busy_timeout are the minimal, targeted fix -- no redesign, no
    change to what's cached or how the key is computed, and no behavior
    change at all for existing single-threaded (Desk Research) callers."""

    def __init__(self, config: GenConfig, cache_path: Path | None = None) -> None:
        self._config = config
        self._client = OpenAI(api_key=config.api_key, base_url=config.base_url, timeout=config.timeout)
        self._cache_path = cache_path
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(cache_path, timeout=_CACHE_BUSY_TIMEOUT_MS / 1000) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, v TEXT)")

    def _key(self, system: str, user: str) -> str:
        h = hashlib.sha256(f"{self._config.model}\x00{system}\x00{user}".encode("utf-8"))
        return h.hexdigest()

    def _connect(self) -> sqlite3.Connection:
        # `timeout=` is SQLite's busy-timeout (seconds) -- how long a
        # connection waits for a lock held by another connection/thread
        # before raising "database is locked", not a network/HTTP timeout.
        return sqlite3.connect(self._cache_path, timeout=_CACHE_BUSY_TIMEOUT_MS / 1000)

    def _cache_get(self, key: str) -> str | None:
        if self._cache_path is None:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT v FROM cache WHERE k=?", (key,)).fetchone()
        return row[0] if row else None

    def _cache_put(self, key: str, value: str) -> None:
        if self._cache_path is None:
            return
        with self._connect() as conn:
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

        _check_floor(self._config.api_key)          # cache hits above cost nothing; a paid call respects the cap
        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                resp = _with_deadline(_CALL_DEADLINE_S, self._client.chat.completions.create,
                    model=self._config.model,
                    messages=[
                        {"role": "system", "content": system + "\nRespond ONLY with a valid JSON object."},
                        {"role": "user", "content": user},
                    ],
                    response_format={"type": "json_object"},
                    # a malformed answer repeats at the same temperature: retry it a little warmer
                    temperature=0.2 if not isinstance(last_err, json.JSONDecodeError) else 0.5,
                    # without it the provider's default output cap cuts long JSON answers mid-string
                    max_tokens=_MAX_TOKENS,
                )
                content = resp.choices[0].message.content or "{}"
                _account(system, getattr(resp, "usage", None))
                if resp.choices[0].finish_reason == "length":
                    # the same prompt would be cut off again: fail now, so the caller can ask for less
                    raise AnswerCutOff(f"answer cut off at the {_MAX_TOKENS}-token output limit")
                try:
                    data = json.loads(content)
                except json.JSONDecodeError:
                    data = json.loads(_repair_json(content))      # still raises if the repair did not help
                    content = json.dumps(data, ensure_ascii=False)
                self._cache_put(key, content)
                return data
            except AnswerCutOff:
                raise
            except Exception as exc:  # noqa: BLE001 - provider errors vary
                if "Insufficient Balance" in str(exc) or "insufficient_balance" in str(exc).lower():
                    # no credit: every further call fails the same way -- stop the run, don't build empty reports
                    # (2026-10-22: the last markets crashed with an empty market size)
                    raise LLMBalanceExhausted(f"DEEPSEEK_BALANCE_EXHAUSTED: {exc}") from None
                last_err = exc
                time.sleep(1.5 * (attempt + 1))
        if isinstance(last_err, json.JSONDecodeError) and "[previous answer was not valid JSON" not in user:
            # every attempt came back as broken JSON (a country split, 2026-10-21): one last request that
            # says so -- a different prompt, so a different answer, not the same slip again
            return self.complete_json(system, user + f"\n\n[previous answer was not valid JSON ({last_err}); "
                                      "return the complete answer as strictly valid JSON]", retries=1)
        raise RuntimeError(f"LLM call failed after {retries} attempts: {last_err}")

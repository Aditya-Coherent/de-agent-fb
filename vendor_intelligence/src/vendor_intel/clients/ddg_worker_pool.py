"""
DDG Worker Pool — multiple isolated processes for DDG searches.
Each worker has its own DDGS session, user-agent, and delay.

NEW FILE — does not replace existing duckduckgo.py logic.
"""
from __future__ import annotations

import logging
import random
import time
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FuturesTimeoutError
from concurrent.futures import as_completed
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 OPR/110.0.0.0",
]


def _resolve_backend_param(backend: str) -> str:
    """deedy5/ddgs does not accept backend='auto' — use safe engine list."""
    raw = (backend or "").strip().lower()
    if raw in ("", "auto", "all"):
        return "duckduckgo,bing,brave,mojeek,google,yahoo"
    return backend


def _is_empty_ddgs_message(msg: str) -> bool:
    low = msg.lower()
    return "no results found" in low or "no results" in low and "found" in low


def _is_transient_engine_error(msg: str) -> bool:
    """Try next engine in DDG_POOL_BACKENDS instead of aborting the whole worker."""
    low = msg.lower()
    return any(
        x in low
        for x in (
            "connecttimeout",
            "timeout",
            "connection attempt failed",
            "winerror 10060",
            "captcha",
            "ratelimit",
            "too many requests",
            "403",
            "429",
            "no results found",
        )
    )


def _worker_search(
    query: str,
    worker_id: int,
    max_results: int,
    min_delay: float,
    max_delay: float,
    region: str,
    backend: str,
    timeout: int,
) -> dict[str, Any]:
    """Runs in a separate process — must stay module-level for Windows pickling."""
    delay = random.uniform(min_delay, max_delay)
    time.sleep(delay)

    ua = random.choice(USER_AGENTS)
    results: list[dict[str, str]] = []
    error: str | None = None
    backend_param = _resolve_backend_param(backend)
    engines = [e.strip() for e in backend_param.split(",") if e.strip()]
    # Google often rate-limits; duckduckgo can hang (WinError 10060). Always try bing next.
    lowered = {e.lower() for e in engines}
    if "google" in lowered and "bing" not in lowered:
        without_ddg = [e for e in engines if e.lower() != "duckduckgo"]
        engines = without_ddg + (["duckduckgo"] if "duckduckgo" in lowered else [])
        google_idx = next(i for i, e in enumerate(engines) if e.lower() == "google")
        engines = engines[: google_idx + 1] + ["bing"] + [
            e for e in engines[google_idx + 1 :] if e.lower() != "bing"
        ]
    elif "bing" not in lowered:
        # No google — still prefer bing over duckduckgo for reliability
        engines = ["bing"] + [e for e in engines if e.lower() != "bing"]
    engine_used = backend_param

    ddgs = None
    try:
        import warnings
        from ddgs import DDGS

        pool_timeout = min(max(10, timeout), 18)
        kwargs: dict[str, Any] = {"timeout": pool_timeout, "verify": True, "proxy": None}
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            ddgs = DDGS(**kwargs)
            for eng in engines:
                engine_used = eng
                try:
                    raw = ddgs.text(
                        query,
                        region=region,
                        max_results=max_results,
                        backend=eng,
                        safesearch="moderate",
                    )
                except Exception as exc:
                    msg = str(exc)[:300]
                    if _is_empty_ddgs_message(msg) or _is_transient_engine_error(msg):
                        continue
                    error = msg
                    break
                for r in raw or []:
                    if not isinstance(r, dict):
                        continue
                    href = r.get("href") or r.get("link") or ""
                    if not href:
                        continue
                    results.append(
                        {
                            "title": r.get("title", ""),
                            "href": href,
                            "body": r.get("body") or r.get("snippet") or "",
                        }
                    )
                if results:
                    break
            # Google often rate-limits; ddgs bing backend is faster than Bing HTML scrape
            if not results and "bing" not in {e.lower() for e in engines}:
                engine_used = "bing"
                try:
                    raw = ddgs.text(
                        query,
                        region=region,
                        max_results=max_results,
                        backend="bing",
                        safesearch="moderate",
                    )
                    for r in raw or []:
                        if not isinstance(r, dict):
                            continue
                        href = r.get("href") or r.get("link") or ""
                        if not href:
                            continue
                        results.append(
                            {
                                "title": r.get("title", ""),
                                "href": href,
                                "body": r.get("body") or r.get("snippet") or "",
                            }
                        )
                except Exception as exc:
                    msg = str(exc)[:300]
                    if not _is_empty_ddgs_message(msg) and not _is_transient_engine_error(msg):
                        error = msg
    except Exception as exc:
        msg = str(exc)[:300]
        if not _is_empty_ddgs_message(msg):
            error = msg
    finally:
        if ddgs is not None:
            close = getattr(ddgs, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    return {
        "worker_id": worker_id,
        "query": query,
        "results": results,
        "error": error,
        "delay_used": delay,
        "ua_used": ua[:40],
        "backend_used": engine_used,
    }


@dataclass
class DDGWorkerPool:
    """Pool of N isolated worker processes for DDG searches."""

    n_workers: int = 4
    min_delay: float = 3.0
    max_delay: float = 8.0
    region: str = "in-en"
    backend: str = "duckduckgo,bing,brave,mojeek,google,yahoo"
    timeout: int = 20
    _executor: ProcessPoolExecutor | None = field(default=None, init=False, repr=False)
    _dead_engines: set[str] = field(default_factory=set, init=False, repr=False)

    def __post_init__(self) -> None:
        import threading

        self._executor = ProcessPoolExecutor(max_workers=self.n_workers)
        # At most n_workers single searches in flight: callers wait for a free worker BEFORE the
        # per-search timeout starts. Previously many concurrent callers queued dozens of futures;
        # queue time counted against each timeout, so searches "timed out" while still queued
        # (marking healthy engines dead) and the backlog kept the process alive at exit.
        self._slots = threading.BoundedSemaphore(max(1, self.n_workers))

    def _future_timeout_sec(self) -> float:
        """Allow time for delay + each engine attempt (pool kills workers too early at 22s)."""
        engines = max(1, len([e for e in self._active_backend_param().split(",") if e.strip()]))
        per_engine = min(max(10, self.timeout), 18)
        # delay + google + bing inject + duckduckgo, with headroom
        budget = self.max_delay + per_engine * engines + 8.0
        if "google" in self._active_backend_param().lower():
            budget += per_engine  # injected bing retry after google
        return float(min(budget, 50))

    def _active_backend_param(self) -> str:
        engines = [e.strip() for e in self.backend.split(",") if e.strip()]
        live = [e for e in engines if e.lower() not in self._dead_engines]
        if not live:
            live = ["bing"] if "bing" in engines else engines[:1]
        return ",".join(live)

    def _note_outcome(self, data: dict[str, Any], *, timed_out: bool = False) -> None:
        eng = str(data.get("backend_used") or "").strip().lower()
        err = str(data.get("error") or "")
        hits = data.get("results") or []
        if hits:
            return
        if timed_out:
            if eng:
                self._dead_engines.add(eng)
            return
        if eng and (_is_transient_engine_error(err) or _is_empty_ddgs_message(err)):
            self._dead_engines.add(eng)
            if len(self._dead_engines) == 1:
                print(
                    f"  [DDGPool] Skipping slow engine '{eng}' for rest of run "
                    f"(using {self._active_backend_param()})",
                    flush=True,
                )

    def reset_dead_engines(self) -> None:
        self._dead_engines.clear()

    def search(self, query: str, max_results: int = 15) -> list[dict]:
        if self._executor is None:
            return []

        with self._slots:
            return self._search_one(query, max_results)

    def _search_one(self, query: str, max_results: int = 15) -> list[dict]:
        if self._executor is None:
            return []
        worker_id = random.randint(0, max(0, self.n_workers - 1))
        backend = self._active_backend_param()
        future = None
        try:
            future = self._executor.submit(
                _worker_search,
                query,
                worker_id,
                max_results,
                self.min_delay,
                self.max_delay,
                self.region,
                backend,
                self.timeout,
            )
            result = future.result(timeout=self._future_timeout_sec())
            self._note_outcome(result)
            wid = result.get("worker_id", worker_id)
            hits = result.get("results", [])
            if result.get("error"):
                err = str(result["error"])[:120]
                print(
                    f"  [DDGPool worker-{wid}] '{query[:50]}' failed: {err}",
                    flush=True,
                )
                logger.warning(
                    "[DDGPool worker-%s] '%s': %s",
                    wid,
                    query[:50],
                    err,
                )
                return []
            print(
                f"  [DDGPool worker-{wid}] '{query[:50]}' -> {len(hits)} hits "
                f"({result.get('backend_used', '')})",
                flush=True,
            )
            return hits
        except FuturesTimeoutError:
            if future is not None:
                future.cancel()  # drop it if still queued - never leave a backlog behind
            self._note_outcome({"backend_used": backend.split(",")[0], "error": "timeout"}, timed_out=True)
            print(
                f"  [DDGPool worker-{worker_id}] '{query[:50]}' -> timeout",
                flush=True,
            )
            logger.warning("[DDGPool worker-%s] timeout on '%s'", worker_id, query[:50])
            return []
        except Exception as exc:
            logger.warning("[DDGPool] unexpected error: %s", exc)
            return []

    def search_many(self, queries: list[str], max_results: int = 15) -> dict[str, list[dict]]:
        if not queries or self._executor is None:
            return {}

        backend = self._active_backend_param()
        futures: dict[Any, str] = {}
        for i, query in enumerate(queries):
            worker_id = i % max(1, self.n_workers)
            fut = self._executor.submit(
                _worker_search,
                query,
                worker_id,
                max_results,
                self.min_delay,
                self.max_delay,
                self.region,
                backend,
                self.timeout,
            )
            futures[fut] = query

        results: dict[str, list[dict]] = {}
        batch_timeout = self._future_timeout_sec() + max(4.0, self.n_workers * 2.5)
        try:
            for fut in as_completed(futures, timeout=batch_timeout):
                query = futures[fut]
                try:
                    data = fut.result()
                    self._note_outcome(data)
                    wid = data.get("worker_id", "?")
                    if data.get("error"):
                        print(
                            f"  [DDGPool worker-{wid}] '{query[:50]}' failed: "
                            f"{str(data['error'])[:80]}",
                            flush=True,
                        )
                        results[query] = []
                    else:
                        results[query] = data.get("results", [])
                        print(
                            f"  [DDGPool worker-{wid}] '{query[:50]}' -> "
                            f"{len(results[query])} hits ({data.get('backend_used', '')})",
                            flush=True,
                        )
                except FuturesTimeoutError:
                    self._note_outcome(
                        {"backend_used": backend.split(",")[0], "error": "timeout"},
                        timed_out=True,
                    )
                    logger.warning("[DDGPool] future timeout for '%s'", query[:50])
                    results[query] = []
                except Exception as exc:
                    logger.warning("[DDGPool] future error for '%s': %s", query[:50], exc)
                    results[query] = []
        except (FuturesTimeoutError, TimeoutError):
            # Batch deadline hit with futures still pending — don't crash the run.
            # Treat the unfinished queries as empty and cancel them.
            pending = [f for f in futures if not f.done()]
            for f in pending:
                f.cancel()
                results.setdefault(futures[f], [])
            self._note_outcome(
                {"backend_used": backend.split(",")[0], "error": "batch_timeout"},
                timed_out=True,
            )
            print(
                f"  [DDGPool] batch timeout: {len(pending)}/{len(futures)} searches "
                f"unfinished — continuing with partial results",
                flush=True,
            )
        for query in queries:
            results.setdefault(query, [])
        return results

    def shutdown(self, *, wait: bool = True) -> None:
        if self._executor:
            try:
                # Always drop still-queued searches; with wait=True only in-flight ones finish.
                self._executor.shutdown(wait=wait, cancel_futures=True)
            except TypeError:
                self._executor.shutdown(wait=wait)
            self._executor = None


_pool: DDGWorkerPool | None = None


def get_ddg_pool(
    n_workers: int = 4,
    min_delay: float = 3.0,
    max_delay: float = 8.0,
    region: str = "in-en",
    backend: str = "duckduckgo,bing,brave,mojeek,google,yahoo",
    timeout: int = 20,
) -> DDGWorkerPool:
    global _pool
    resolved = _resolve_backend_param(backend)
    if _pool is None:
        _pool = DDGWorkerPool(
            n_workers=n_workers,
            min_delay=min_delay,
            max_delay=max_delay,
            region=region,
            backend=resolved,
            timeout=timeout,
        )
        print(
            f"  [DDGPool] Initialized {n_workers} worker processes "
            f"(delay {min_delay}-{max_delay}s, backend={resolved}); "
            f"0 hits is normal — Bing HTML fills gaps",
            flush=True,
        )
    return _pool


def shutdown_ddg_pool(*, wait: bool = True) -> None:
    """Release DDG worker processes so the CLI exits cleanly (Windows atexit hang)."""
    global _pool
    if _pool is None:
        return
    try:
        _pool.shutdown(wait=wait)
    except Exception as exc:
        logger.debug("DDG pool shutdown: %s", exc)
    _pool = None


def reset_ddg_pool_circuit_breaker() -> None:
    """Clear per-run dead-engine skips (call at pipeline start)."""
    global _pool
    if _pool is not None:
        _pool.reset_dead_engines()

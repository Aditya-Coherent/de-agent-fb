"""Web search via ddgs.text() — https://github.com/deedy5/ddgs"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import threading
import time
import warnings
from dataclasses import dataclass

from vendor_intel.clients.ddgs_engines import normalize_text_backends, run_with_ddgs
from vendor_intel.clients.http_proxy import ensure_outbound_proxies

logger = logging.getLogger(__name__)

DDGS_DOCS = "https://github.com/deedy5/ddgs"


def _search_print(msg: str) -> None:
    print(f"  [search] {msg}", flush=True)


_DDGS_CLASS = None


def _ddgs_timeout() -> int:
    from vendor_intel.clients.ddgs_engines import ddgs_timeout

    return ddgs_timeout()


_ddg_request_lock = threading.Lock()


def _ddg_delay_bounds() -> tuple[float, float]:
    try:
        lo = float(os.getenv("DDG_REQUEST_DELAY_MIN", "2"))
        hi = float(os.getenv("DDG_REQUEST_DELAY_MAX", "4"))
    except ValueError:
        lo, hi = 2.0, 4.0
    if hi < lo:
        lo, hi = hi, lo
    return max(0.0, lo), max(lo, hi)


def ddg_request_delay_enabled() -> bool:
    return os.getenv("DDG_REQUEST_DELAY", "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def wait_before_ddg_https_request() -> float:
    """Optional pause before each ddgs API call."""
    if not ddg_request_delay_enabled():
        return 0.0
    lo, hi = _ddg_delay_bounds()
    if hi <= 0:
        return 0.0
    delay = random.uniform(lo, hi)
    with _ddg_request_lock:
        time.sleep(delay)
    logger.debug("ddgs throttle: slept %.2fs", delay)
    return delay


def reset_ddg_request_throttle() -> None:
    pass


def _load_ddgs():
    global _DDGS_CLASS
    if _DDGS_CLASS is not None:
        return _DDGS_CLASS
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        try:
            from ddgs import DDGS as _cls

            _DDGS_CLASS = _cls
            return _cls
        except ImportError:
            return None


@dataclass
class DuckResult:
    title: str
    link: str
    snippet: str
    engine: str = "ddgs"


def _parse_item(item: dict, *, backend_label: str) -> DuckResult | None:
    href = item.get("href") or item.get("link") or ""
    if not href:
        return None
    return DuckResult(
        title=(item.get("title") or "")[:200],
        link=href,
        snippet=(item.get("body") or item.get("snippet") or "")[:500],
        engine=backend_label,
    )


def _dedupe_key(link: str) -> str:
    return link.split("#")[0].rstrip("/").lower()


def _region_for_geo(geo: str) -> str:
    g = (geo or "").lower()
    if "india" in g:
        return "in-en"
    if any(x in g for x in ("united kingdom", " uk", "britain", "england")):
        return "uk-en"
    if any(x in g for x in ("united states", " usa", " u.s.")):
        return "us-en"
    if "germany" in g or "deutschland" in g:
        return "de-de"
    return "wt-wt"


def ddgs_backend_param() -> str:
    """Comma-separated backends for ddgs.text() — see deedy5/ddgs#engines."""
    param, _, dropped = normalize_text_backends()
    if dropped:
        _search_print(
            "DDGS skipped backends: " + ", ".join(dropped) + f" → using {param}"
        )
    return param


def configured_ddgs_backends() -> list[str]:
    _, names, _ = normalize_text_backends()
    return names


def _search_sync(query: str, max_results: int, *, region: str = "wt-wt") -> list[DuckResult]:
    """ddgs.text() with proxy pool rotation (search / scrape / fallbacks share pool)."""
    try:
        from vendor_intel.pipeline.cancel import PipelineCancelled, is_cancelled

        if is_cancelled():
            raise PipelineCancelled("Stopped by user.")
    except ImportError:
        pass

    if _load_ddgs() is None:
        _search_print("ddgs not installed — run: pip install -U ddgs")
        return []

    ensure_outbound_proxies(log_print=_search_print)
    backend = ddgs_backend_param()
    try:
        worker_count = int(os.getenv("DDG_WORKER_COUNT", "0") or "0")
    except ValueError:
        worker_count = 0
    try:
        worker_cap = int(os.getenv("DDG_WORKER_MAX", "4") or "4")
    except ValueError:
        worker_cap = 4
    if worker_count > 0:
        worker_count = min(worker_count, max(1, worker_cap))

    rows: list[DuckResult] = []
    seen: set[str] = set()
    label = f"ddgs_{backend.replace(',', '_')}"
    items: list | None = None
    pool_failed = False

    if worker_count > 0:
        try:
            from vendor_intel.clients.ddg_worker_pool import get_ddg_pool

            pool_backend = (
                os.getenv("DDG_POOL_BACKENDS", "").strip()
                or os.getenv("DDGS_BACKENDS", backend).strip()
                or backend
            )
            pool_min = float(
                os.getenv("DDG_POOL_DELAY_MIN", os.getenv("DDG_REQUEST_DELAY_MIN", "1.0"))
            )
            pool_max = float(
                os.getenv("DDG_POOL_DELAY_MAX", os.getenv("DDG_REQUEST_DELAY_MAX", "2.5"))
            )
            pool = get_ddg_pool(
                n_workers=worker_count,
                min_delay=pool_min,
                max_delay=pool_max,
                backend=pool_backend,
                timeout=_ddgs_timeout(),
                region=region,
            )
            raw_results = pool.search(query, max_results=max_results)
            if raw_results:
                items = raw_results
        except Exception as exc:
            pool_failed = True
            _search_print(
                f"DDG worker pool failed (workers={worker_count}): "
                f"{type(exc).__name__}: {exc}"
            )
            items = None

    skip_inthread = os.getenv("DDG_SKIP_INTHREAD_AFTER_POOL_FAIL", "true").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if items is None and pool_failed and skip_inthread:
        return rows

    if items is None:
        wait_before_ddg_https_request()
        retry_backends: list[str] = []
        pool_raw = (
            os.getenv("DDG_POOL_BACKENDS", "").strip()
            or os.getenv("DDGS_BACKENDS", backend).strip()
            or backend
        )
        for part in pool_raw.split(","):
            eng = part.strip().lower()
            if eng and eng not in retry_backends:
                retry_backends.append(eng)
        _, norm_names, _ = normalize_text_backends()
        for eng in norm_names:
            if eng not in retry_backends:
                retry_backends.append(eng)
        # google → bing → duckduckgo (ddg connect often hangs when google is rate-limited)
        ordered: list[str] = []
        if "google" in retry_backends:
            ordered.append("google")
        if "bing" not in ordered:
            ordered.append("bing")
        for eng in retry_backends:
            if eng not in ordered:
                ordered.append(eng)
        retry_backends = ordered

        items = []
        for eng in retry_backends:
            try:

                def _query(ddgs: object, engine: str = eng) -> list:
                    return list(
                        ddgs.text(  # type: ignore[attr-defined]
                            query,
                            region=region,
                            max_results=max_results,
                            backend=engine,
                            safesearch="moderate",
                        )
                    )

                batch = run_with_ddgs(_query, label=f"ddgs.text/{eng}")
                if batch:
                    items = batch
                    label = f"ddgs_{eng}"
                    break
            except Exception as exc:
                _search_print(
                    f"ddgs.text failed (backend={eng!r}, region={region}): "
                    f"{type(exc).__name__}: {exc}"
                )
                continue
        if not items:
            return []

    for item in items or []:
        if not isinstance(item, dict):
            continue
        parsed = _parse_item(item, backend_label=label)
        if not parsed:
            continue
        key = _dedupe_key(parsed.link)
        if key in seen:
            continue
        seen.add(key)
        rows.append(parsed)

    if not rows:
        _search_print(
            f"ddgs.text returned 0 results for {query[:50]!r} "
            f"(backend={backend!r}, region={region})"
        )
    else:
        proxy = (os.getenv("HTTPS_PROXY") or os.getenv("DDGS_PROXY") or "direct")[:50]
        logger.debug("ddgs.text %s hits for %r via %s", len(rows), query[:40], proxy)

    return rows[:max_results]


def _search_many_sync(
    queries: list[str],
    max_results: int,
    *,
    region: str = "wt-wt",
) -> dict[str, list[DuckResult]]:
    """Parallel pool search for Phase 2 discovery batches."""
    if not queries:
        return {}
    try:
        worker_count = int(os.getenv("DDG_WORKER_COUNT", "0") or "0")
    except ValueError:
        worker_count = 0
    if worker_count <= 0 or _load_ddgs() is None:
        out: dict[str, list[DuckResult]] = {}
        for q in queries:
            out[q] = _search_sync(q, max_results, region=region)
        return out

    ensure_outbound_proxies(log_print=_search_print)
    backend = ddgs_backend_param()
    try:
        worker_cap = int(os.getenv("DDG_WORKER_MAX", "4") or "4")
    except ValueError:
        worker_cap = 4
    worker_count = min(worker_count, max(1, worker_cap))

    from vendor_intel.clients.ddg_worker_pool import get_ddg_pool

    pool_backend = (
        os.getenv("DDG_POOL_BACKENDS", "").strip()
        or os.getenv("DDGS_BACKENDS", backend).strip()
        or backend
    )
    pool_min = float(
        os.getenv("DDG_POOL_DELAY_MIN", os.getenv("DDG_REQUEST_DELAY_MIN", "1.0"))
    )
    pool_max = float(
        os.getenv("DDG_POOL_DELAY_MAX", os.getenv("DDG_REQUEST_DELAY_MAX", "2.5"))
    )
    pool = get_ddg_pool(
        n_workers=worker_count,
        min_delay=pool_min,
        max_delay=pool_max,
        backend=pool_backend,
        timeout=_ddgs_timeout(),
        region=region,
    )
    raw_map = pool.search_many(queries, max_results=max_results)
    label = f"ddgs_{pool_backend.replace(',', '_')}"
    out_map: dict[str, list[DuckResult]] = {}
    for q in queries:
        rows: list[DuckResult] = []
        seen: set[str] = set()
        for item in raw_map.get(q) or []:
            if not isinstance(item, dict):
                continue
            parsed = _parse_item(item, backend_label=label)
            if not parsed:
                continue
            key = _dedupe_key(parsed.link)
            if key in seen:
                continue
            seen.add(key)
            rows.append(parsed)
        out_map[q] = rows[:max_results]
    return out_map


async def duckduckgo_search(
    query: str,
    max_results: int = 15,
    *,
    geo: str = "",
) -> list[DuckResult]:
    """Async wrapper for ddgs.text() (name kept for existing imports)."""
    region = _region_for_geo(geo)
    return await asyncio.to_thread(_search_sync, query, max_results, region=region)


async def duckduckgo_search_many(
    queries: list[str],
    max_results: int = 15,
    *,
    geo: str = "",
) -> dict[str, list[DuckResult]]:
    """Batch async search — uses worker pool when DDG_WORKER_COUNT > 0."""
    region = _region_for_geo(geo)
    return await asyncio.to_thread(
        _search_many_sync, queries, max_results, region=region
    )


ddgs_search = duckduckgo_search


def duckduckgo_available() -> bool:
    return _load_ddgs() is not None


def ddgs_available() -> bool:
    return duckduckgo_available()


def duckduckgo_backend_name() -> str:
    _load_ddgs()
    return f"ddgs(backend={ddgs_backend_param()})"


def network_search_blocked() -> bool:
    return False


def reset_network_search_state() -> None:
    reset_ddg_request_throttle()
    try:
        from vendor_intel.clients.ddg_worker_pool import reset_ddg_pool_circuit_breaker

        reset_ddg_pool_circuit_breaker()
    except Exception:
        pass

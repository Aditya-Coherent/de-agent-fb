"""Company news via ddgs library (ddgs.news or ddgs.text — no manual DDG HTML scrape)."""
from __future__ import annotations

import asyncio
import logging
import os
import time
import warnings
from dataclasses import dataclass
from typing import Any

from vendor_intel.clients.ddgs_engines import normalize_news_backends, run_with_ddgs
from vendor_intel.clients.duckduckgo import (
    _region_for_geo,
    duckduckgo_search,
    wait_before_ddg_https_request,
)
from vendor_intel.clients.http_proxy import ensure_outbound_proxies

logger = logging.getLogger(__name__)

_news_api_disabled_until: float = 0.0
_COOLDOWN_SEC = 900
_warned_fallback = False


@dataclass
class DdgNewsHit:
    title: str
    url: str
    snippet: str
    source: str
    published_at: str


def _use_ddg_news_api() -> bool:
    return os.getenv("DDG_NEWS_API", "").strip().lower() in ("1", "true", "yes")


def _is_rate_limited(exc: BaseException) -> bool:
    text = str(exc).lower()
    name = type(exc).__name__.lower()
    return "ratelimit" in name or ("403" in text and "rate" in text)


def _news_api_sync(
    query: str, max_results: int, *, geo: str = ""
) -> list[DdgNewsHit]:
    """ddgs.news() — backends: bing, duckduckgo, yahoo (deedy5/ddgs#4-news)."""
    global _news_api_disabled_until

    if time.time() < _news_api_disabled_until:
        return []

    rows: list[DdgNewsHit] = []
    backend, _ = normalize_news_backends()
    region = _region_for_geo(geo)
    ensure_outbound_proxies()
    try:
        wait_before_ddg_https_request()

        def _collect(ddgs: object) -> list[DdgNewsHit]:
            hits: list[DdgNewsHit] = []
            if not hasattr(ddgs, "news"):
                return hits
            for item in ddgs.news(  # type: ignore[attr-defined]
                query,
                region=region,
                max_results=max_results,
                backend=backend,
                safesearch="moderate",
            ):
                if not isinstance(item, dict):
                    continue
                url = item.get("url") or item.get("link") or ""
                if not url:
                    continue
                hits.append(
                    DdgNewsHit(
                        title=(item.get("title") or "")[:200],
                        url=url,
                        snippet=(item.get("body") or item.get("excerpt") or "")[:500],
                        source=(item.get("source") or "")[:80],
                        published_at=(item.get("date") or item.get("published") or "")[:40],
                    )
                )
            return hits

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            rows = run_with_ddgs(_collect, label="ddgs.news")
    except Exception as exc:
        if _is_rate_limited(exc):
            _news_api_disabled_until = time.time() + _COOLDOWN_SEC
            logger.warning("[news] ddgs.news rate-limited — using ddgs.text instead.")
        else:
            logger.debug("ddgs.news failed: %s", exc)
        return []
    return rows


async def _news_from_ddgs_text(
    query: str,
    max_results: int,
    *,
    geo: str = "",
) -> list[DdgNewsHit]:
    global _warned_fallback
    hits: list[DdgNewsHit] = []
    try:
        rows = await duckduckgo_search(query, max_results=max_results, geo=geo)
    except Exception as exc:
        logger.debug("ddgs.text news query failed: %s", exc)
        rows = []

    if rows and not _warned_fallback:
        _warned_fallback = True
        print("  [news] Using ddgs.text for company news.", flush=True)

    for r in rows:
        hits.append(
            DdgNewsHit(
                title=r.title,
                url=r.link,
                snippet=r.snippet,
                source=(r.engine or "ddgs")[:80],
                published_at="",
            )
        )
    return hits[:max_results]


async def ddg_news_search(
    query: str,
    max_results: int = 8,
    *,
    geo: str = "",
    settings: Any | None = None,
) -> list[DdgNewsHit]:
    """
    News-like hits via ddgs only.
    Default: ddgs.text. Set DDG_NEWS_API=true to try ddgs.news first.
    """
    del settings
    if _use_ddg_news_api():
        api_rows = await asyncio.to_thread(
            _news_api_sync, query, max_results, geo=geo
        )
        if api_rows:
            return api_rows

    return await _news_from_ddgs_text(query, max_results, geo=geo)

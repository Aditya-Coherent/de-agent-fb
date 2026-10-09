"""Wikipedia opensearch API — free fallback when web search returns few hits."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import quote

from vendor_intel.clients.http_proxy import httpx_async_client

logger = logging.getLogger(__name__)

_USER_AGENT = "VendorIntelPipeline/1.0 (research; contact: local)"
_API = "https://en.wikipedia.org/w/api.php"


@dataclass
class WikiResult:
    title: str
    link: str
    snippet: str


def _geo_site(geo: str) -> str:
    g = (geo or "").lower()
    if "india" in g:
        return "https://en.wikipedia.org"
    return "https://en.wikipedia.org"


async def wikipedia_search(
    query: str,
    *,
    max_results: int = 15,
    geo: str = "",
    timeout: float = 20.0,
) -> list[WikiResult]:
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "format": "json",
        "srlimit": max(max_results, 5),
        "origin": "*",
    }
    headers = {"User-Agent": _USER_AGENT}
    try:
        async with httpx_async_client(timeout=timeout, headers=headers) as client:
            r = await client.get(_API, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        logger.debug("Wikipedia search failed %r: %s", query[:50], exc)
        return []

    base = _geo_site(geo)
    rows: list[WikiResult] = []
    for item in (data.get("query") or {}).get("search") or []:
        title = (item.get("title") or "").strip()
        if not title:
            continue
        slug = quote(title.replace(" ", "_"), safe="/()")
        rows.append(
            WikiResult(
                title=title[:200],
                link=f"{base}/wiki/{slug}",
                snippet=(item.get("snippet") or "").replace("<", "")[:500],
            )
        )
        if len(rows) >= max_results:
            break
    return rows


async def wikipedia_available() -> bool:
    try:
        rows = await wikipedia_search("test", max_results=1)
        return len(rows) > 0
    except Exception:
        return False

"""Brave Search API (optional — free tier at https://api.search.brave.com/)."""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from vendor_intel.clients.browser_headers import browser_headers
from vendor_intel.config import Settings


@dataclass
class BraveHit:
    title: str
    link: str
    snippet: str


def brave_configured(settings: Settings) -> bool:
    return bool((settings.brave_api_key or "").strip())


async def brave_web_search(
    query: str,
    settings: Settings,
    *,
    max_results: int = 20,
    country: str = "IN",
) -> list[BraveHit]:
    key = (settings.brave_api_key or "").strip()
    if not key:
        return []

    url = "https://api.search.brave.com/res/v1/web/search"
    headers = browser_headers(referer="https://search.brave.com/")
    headers["Accept"] = "application/json"
    headers["X-Subscription-Token"] = key
    params = {
        "q": query,
        "count": min(max_results, 20),
        "country": country[:2].upper() if country else "US",
        "search_lang": "en",
    }
    try:
        from vendor_intel.clients.http_proxy import httpx_async_client

        async with httpx_async_client(timeout=25.0) as client:
            r = await client.get(url, headers=headers, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception:
        return []

    rows: list[BraveHit] = []
    web = data.get("web") or {}
    for item in (web.get("results") or [])[:max_results]:
        link = item.get("url") or ""
        if not link:
            continue
        rows.append(
            BraveHit(
                title=(item.get("title") or "")[:200],
                link=link,
                snippet=(item.get("description") or "")[:500],
            )
        )
    return rows

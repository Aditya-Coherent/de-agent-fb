"""Serper.dev Google search API (optional — https://serper.dev/)."""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from vendor_intel.clients.browser_headers import browser_headers
from vendor_intel.config import Settings


@dataclass
class SerperHit:
    title: str
    link: str
    snippet: str


def serper_configured(settings: Settings) -> bool:
    return bool((settings.serper_api_key or "").strip())


async def serper_web_search(
    query: str,
    settings: Settings,
    *,
    max_results: int = 20,
    geo: str = "",
) -> list[SerperHit]:
    key = (settings.serper_api_key or "").strip()
    if not key:
        return []

    gl = "in" if geo and "india" in geo.lower() else "us"
    payload = {"q": query, "num": min(max_results, 20), "gl": gl}
    try:
        hdrs = browser_headers(referer="https://serper.dev/")
        hdrs["X-API-KEY"] = key
        hdrs["Content-Type"] = "application/json"
        from vendor_intel.clients.http_proxy import httpx_async_client

        async with httpx_async_client(timeout=25.0) as client:
            r = await client.post(
                "https://google.serper.dev/search",
                headers=hdrs,
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
    except Exception:
        return []

    rows: list[SerperHit] = []
    for item in (data.get("organic") or [])[:max_results]:
        link = item.get("link") or ""
        if not link:
            continue
        rows.append(
            SerperHit(
                title=(item.get("title") or "")[:200],
                link=link,
                snippet=(item.get("snippet") or "")[:500],
            )
        )
    return rows

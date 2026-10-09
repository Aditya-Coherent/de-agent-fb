"""
SSC — Server-Side Content fetch (HTML/markdown from origin, no browser crawl).

Fast enrichment path (~5–15s per batch of 4) vs full smart_crawl (~60–120s/site).
Uses ddgs.extract (remote fetch) as primary; optional local httpx.
"""
from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlparse

import httpx


async def fetch_server_side_content(domain: str, *, timeout: float = 12.0) -> dict[str, Any]:
    """
    Fetch homepage text server-side. Returns smart_crawl-compatible dict.
    """
    dom = (domain or "").strip().lower().removeprefix("www.")
    if not dom:
        return {"error": "no_domain", "data": {}, "source": "ssc"}

    url = f"https://{dom}/"
    text = ""
    source = "ssc"
    final_url = url

    try:
        from vendor_intel.scraping.ddgs_extract import fetch_page_via_ddgs_extract

        page = await asyncio.to_thread(fetch_page_via_ddgs_extract, url)
        if page.alive and len((page.text or "").strip()) >= 80:
            text = (page.text or "")[:12000]
            source = "ddgs_extract"
            final_url = page.final_url or url
    except Exception:
        pass

    if len(text) < 80:
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=timeout,
                headers={"User-Agent": "Mozilla/5.0 (compatible; VendorIntel/1.0)"},
            ) as client:
                r = await client.get(url)
                if r.status_code < 400 and len(r.text) > 200:
                    try:
                        import trafilatura

                        extracted = trafilatura.extract(
                            r.text, include_comments=False, favor_precision=False
                        )
                        text = (extracted or r.text)[:12000]
                    except Exception:
                        text = r.text[:12000]
                    source = "httpx_trafilatura"
                    final_url = str(r.url)
        except Exception as exc:
            return {
                "error": str(exc)[:200],
                "domain": dom,
                "data": {},
                "source": "ssc_failed",
            }

    if len(text) < 80:
        return {
            "error": "thin_content",
            "domain": dom,
            "data": {},
            "source": source,
        }

    host = urlparse(final_url).netloc or dom
    return {
        "domain": dom,
        "source": source,
        "data": {
            "company": {"name": dom, "website": host},
            "business": {"products": [], "services": []},
            "intel": {"summary": text[:5000]},
        },
        "pages": [{"url": final_url, "text": text}],
    }

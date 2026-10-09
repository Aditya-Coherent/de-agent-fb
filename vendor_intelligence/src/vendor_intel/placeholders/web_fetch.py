"""Backward-compatible re-exports — website scrape via ddgs.extract()."""
from __future__ import annotations

from vendor_intel.clients.duckduckgo import _ddgs_timeout
from vendor_intel.scraping.fetch import (
    SCRAPING_ENABLED,
    check_url_alive,
    fetch_page,
    fetch_page_html,
    fetch_page_text,
)

WEB_FETCH_ENABLED = SCRAPING_ENABLED
FETCH_TIMEOUT = _ddgs_timeout()


async def fetch_page_html_async(url: str) -> tuple[bool, str, str]:
    return await fetch_page_html(url)


def _body_fallback(html: str) -> str:
    from vendor_intel.scraping.html_extract import body_fallback

    return body_fallback(html)


__all__ = [
    "WEB_FETCH_ENABLED",
    "FETCH_TIMEOUT",
    "check_url_alive",
    "fetch_page",
    "fetch_page_html",
    "fetch_page_html_async",
    "fetch_page_text",
]

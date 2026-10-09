"""
Canonical website fetch — primary: ddgs.extract(); optional Selenium fallback.

Search uses ddgs.text(); company pages use ddgs.extract() per ddgs docs.
"""
from __future__ import annotations

import asyncio
import os

from vendor_intel.scraping.ddgs_extract import fetch_page_via_ddgs_extract
from vendor_intel.scraping.html_extract import extract_text_from_html
from vendor_intel.scraping.page_result import PageFetchResult

SCRAPING_ENABLED = True

# ddgs | selenium | ddgs_then_selenium
_SCRAPE_BACKEND = (os.getenv("SCRAPE_BACKEND") or "ddgs").strip().lower()


def scrape_backend() -> str:
    if _SCRAPE_BACKEND in ("ddgs", "selenium", "ddgs_then_selenium"):
        return _SCRAPE_BACKEND
    return "ddgs"


def _is_thin_content(result: PageFetchResult) -> bool:
    """True when ddgs returned nav/JS-blocked junk with very little real text."""
    text = (result.text or "").strip()
    if len(text) < 200:
        return True
    # JS-blocked pages (Xiaomi pattern: "JavaScript is not available")
    if "javascript is not available" in text.lower():
        return True
    # Page is almost entirely markdown links — no real prose
    link_lines = sum(1 for ln in text.splitlines() if ln.strip().startswith("["))
    total_lines = max(len(text.splitlines()), 1)
    if link_lines / total_lines > 0.80 and len(text) < 1500:
        return True
    return False


def _fetch_page_sync(url: str) -> PageFetchResult:
    backend = scrape_backend()
    if backend == "selenium":
        from vendor_intel.scraping.selenium_browser import fetch_page_html as selenium_fetch

        r = selenium_fetch(url)
        return PageFetchResult(
            url=r.url,
            final_url=r.final_url,
            alive=r.alive,
            text=r.visible_text or extract_text_from_html(r.html, visible_text=r.visible_text),
            html=r.html,
            title=r.title,
            source="selenium",
            error=r.error,
        )

    ddgs_result = fetch_page_via_ddgs_extract(url)

    # If ddgs returned thin/nav-only content, try httpx for richer extraction
    if backend in ("ddgs", "ddgs_then_selenium") and _is_thin_content(ddgs_result):
        try:
            from vendor_intel.scraping.httpx_fetch import fetch_page_httpx
            httpx_result = fetch_page_httpx(url)
            if httpx_result.alive and len((httpx_result.text or "").strip()) > len(
                (ddgs_result.text or "").strip()
            ):
                return httpx_result
        except Exception:
            pass  # httpx unavailable or failed — fall through to ddgs result

    if ddgs_result.alive or backend == "ddgs":
        return ddgs_result

    from vendor_intel.scraping.selenium_browser import fetch_page_html as selenium_fetch

    r = selenium_fetch(url)
    if r.alive:
        return PageFetchResult(
            url=r.url,
            final_url=r.final_url,
            alive=True,
            text=r.visible_text or extract_text_from_html(r.html, visible_text=r.visible_text),
            html=r.html,
            title=r.title,
            source="selenium",
            error="",
        )
    return PageFetchResult(
        url=ddgs_result.url,
        final_url=ddgs_result.final_url,
        alive=False,
        text="",
        source="ddgs",
        error=ddgs_result.error or r.error,
    )


async def fetch_page(url: str) -> PageFetchResult:
    if not SCRAPING_ENABLED:
        u = url if url.startswith("http") else f"https://{url}"
        return PageFetchResult(url=u, final_url=u, alive=False, html="")
    return await asyncio.to_thread(_fetch_page_sync, url)


async def check_url_alive(url: str) -> tuple[bool, str]:
    if not SCRAPING_ENABLED:
        u = url if url.startswith("http") else f"https://{url}"
        return True, u
    result = await fetch_page(url)
    return result.alive, result.final_url


async def fetch_page_html(url: str) -> tuple[bool, str, str]:
    result = await fetch_page(url)
    html = result.html
    if not html and result.text and result.text.lstrip().startswith("<"):
        html = result.text
    return result.alive, result.final_url, html


async def fetch_page_text(url: str, max_chars: int = 4000) -> str:
    result = await fetch_page(url)
    if not result.alive:
        return ""
    if result.text and not result.html:
        return result.text[:max_chars]
    return extract_text_from_html(
        result.html or result.text,
        visible_text=result.text,
        max_chars=max_chars,
    )

"""Company website scrape via ddgs.extract() / httpx (profile / corporate / full)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

from vendor_intel.scraping.fetch import check_url_alive, fetch_page_text
from vendor_intel.scraping.page_lead import fetch_page_lead, lead_from_markdown

ScrapeMode = Literal["full", "profile", "corporate"]

# Subpages that reveal what a company does — tried after the homepage.
# These are the same paths a researcher would browse to understand the business.
_FUNCTION_SUBPAGES = [
    "/about",
    "/about-us",
    "/products",
    "/solutions",
    "/services",
    "/what-we-do",
    "/company",
]


@dataclass
class WebsiteScrapeResult:
    url: str
    final_url: str
    alive: bool
    text: str
    mode: str = "full"


async def _try_geo_scrape(
    domain_or_url: str,
    geo: str,
    max_chars: int,
) -> WebsiteScrapeResult | None:
    """
    Attempt to fetch a geo-specific page (e.g. apple.com/in for India).
    Returns a result if the geo URL has substantially more India-relevant content
    than the bare domain URL would.
    """
    try:
        from vendor_intel.scraping.httpx_fetch import fetch_page_httpx_geo

        url = domain_or_url.strip()
        if not url.startswith("http"):
            url = f"https://{url}"

        result = await asyncio.to_thread(fetch_page_httpx_geo, url, geo, max_chars=max_chars)
        if result.alive and len((result.text or "").strip()) >= 150:
            lead = lead_from_markdown(result.text, max_lines=25, max_chars=min(max_chars, 2800))
            return WebsiteScrapeResult(
                url=url, final_url=result.final_url, alive=True, text=lead or result.text[:max_chars]
            )
    except Exception:
        pass
    return None


async def _fetch_subpage_text(base_url: str, path: str, max_chars: int = 1200) -> str:
    """Fetch one subpage; return extracted text or empty string on any failure."""
    try:
        from vendor_intel.scraping.httpx_fetch import fetch_page_httpx

        target = base_url.rstrip("/") + path
        result = await asyncio.to_thread(fetch_page_httpx, target, max_chars=max_chars)
        if result.alive and len((result.text or "").strip()) >= 120:
            return result.text.strip()[:max_chars]
    except Exception:
        pass
    return ""


async def _enrich_with_subpages(
    base_url: str,
    homepage_text: str,
    *,
    max_extra_chars: int = 2000,
    max_subpages: int = 3,
) -> str:
    """Try _FUNCTION_SUBPAGES and append any useful content to homepage_text.

    Stops early once max_extra_chars of new content is gathered or max_subpages
    are tried. Only adds pages that contribute non-duplicate content.
    """
    if len(homepage_text.strip()) >= 1200:
        # Homepage already rich — skip subpages to save time
        return homepage_text

    extra_parts: list[str] = []
    extra_chars = 0
    tried = 0

    for path in _FUNCTION_SUBPAGES:
        if tried >= max_subpages or extra_chars >= max_extra_chars:
            break
        text = await _fetch_subpage_text(base_url, path)
        if text and text[:80] not in homepage_text:  # avoid duplication
            extra_parts.append(f"\n[{path}]\n{text}")
            extra_chars += len(text)
            tried += 1

    if extra_parts:
        return homepage_text + "".join(extra_parts)
    return homepage_text


async def scrape_company_website(
    domain_or_url: str,
    *,
    max_chars: int = 4000,
    mode: ScrapeMode = "profile",
    geo_hint: str = "",
    enrich_subpages: bool = True,
) -> WebsiteScrapeResult:
    """
    Scrape a company website.

    geo_hint: if provided (e.g. "india"), tries country-specific URL paths first
    (/in, /en-in …) using httpx so we get localised content and clear geo signals.

    enrich_subpages: when homepage text is thin (<1200 chars), also fetches
    /about, /products, /services etc. to improve company_function classification.
    """
    url = domain_or_url.strip()
    if not url.startswith("http"):
        url = f"https://{url}"

    # --- Geo-aware scrape: try /in, /en-in etc. first for India queries
    if geo_hint:
        geo_result = await _try_geo_scrape(url, geo_hint, max_chars)
        if geo_result:
            if enrich_subpages:
                geo_result.text = await _enrich_with_subpages(url, geo_result.text)
            return geo_result

    if mode in ("corporate", "profile"):
        max_lines = 25 if mode == "corporate" else 20
        cap = 2800 if mode == "corporate" else 2200
        alive, final_url, text = await fetch_page_lead(
            url, max_lines=max_lines, max_chars=min(max_chars, cap)
        )
        if enrich_subpages and alive:
            text = await _enrich_with_subpages(final_url or url, text)
        return WebsiteScrapeResult(
            url=url, final_url=final_url, alive=alive, text=text, mode=mode
        )

    alive, final_url = await check_url_alive(url)
    text = ""
    if alive:
        text = await fetch_page_text(final_url or url, max_chars=max_chars)
        if enrich_subpages:
            text = await _enrich_with_subpages(final_url or url, text)
    return WebsiteScrapeResult(
        url=url, final_url=final_url or url, alive=alive, text=text, mode=mode
    )

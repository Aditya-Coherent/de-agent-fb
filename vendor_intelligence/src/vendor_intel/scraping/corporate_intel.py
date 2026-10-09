"""Corporate structure signals: ddgs.extract on about-pages + parent/subsidiary parsing."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from vendor_intel.scraping.fetch import fetch_page
from vendor_intel.scraping.page_lead import lead_from_markdown, extract_headings_and_lead

_ABOUT_PATHS = (
    "/",
    "/about",
    "/about-us",
    "/about_us",
    "/company",
    "/who-we-are",
    "/our-company",
    "/corporate",
    "/our-story",
    "/overview",
)

_PARENT_PATTERNS = (
    re.compile(
        r"(?:subsidiary|division)\s+of\s+([A-Z][A-Za-z0-9&'.,\s-]{2,60})",
        re.I,
    ),
    re.compile(
        r"(?:owned|acquired)\s+by\s+([A-Z][A-Za-z0-9&'.,\s-]{2,60})",
        re.I,
    ),
    re.compile(
        r"part\s+of\s+(?:the\s+)?([A-Z][A-Za-z0-9&'.,\s-]{2,60})\s+group",
        re.I,
    ),
    re.compile(
        r"parent\s+company[:\s]+([A-Z][A-Za-z0-9&'.,\s-]{2,60})",
        re.I,
    ),
    re.compile(
        r"member\s+of\s+([A-Z][A-Za-z0-9&'.,\s-]{2,60})",
        re.I,
    ),
    re.compile(
        r"([A-Z][A-Za-z0-9&'.,\s-]{2,50})\s+group\s+company",
        re.I,
    ),
)


@dataclass
class CorporateIntelResult:
    domain: str
    pages_fetched: int
    combined_text: str
    parent_hint: str
    subsidiary_hints: list[str]
    alive: bool


def parse_corporate_signals(text: str) -> tuple[str, list[str]]:
    if not text:
        return "", []
    parent = ""
    subs: list[str] = []
    for pat in _PARENT_PATTERNS:
        for m in pat.finditer(text):
            cand = m.group(1).strip().strip(".,;")
            if len(cand) < 3 or len(cand.split()) > 8:
                continue
            low = cand.lower()
            if low in ("our", "the", "a", "an", "we", "its"):
                continue
            if "subsidiary" in pat.pattern or "division" in pat.pattern:
                subs.append(cand)
            elif not parent:
                parent = cand
    return parent, subs[:5]


def _lead_from_page_result(text: str, html: str, *, max_lines: int, max_chars: int) -> str:
    body = (text or "").strip()
    if body and not body.lstrip().startswith("<"):
        return lead_from_markdown(body, max_lines=max_lines, max_chars=max_chars)
    if html or body:
        return extract_headings_and_lead(html or body, max_lines=max_lines, max_chars=max_chars)
    return ""


async def _fetch_path(
    base_url: str, path: str, *, max_lines: int, max_chars: int
) -> str:
    url = urljoin(base_url if base_url.endswith("/") else base_url + "/", path.lstrip("/"))
    result = await fetch_page(url)
    if not result.alive:
        return ""
    return _lead_from_page_result(
        result.text, result.html, max_lines=max_lines, max_chars=max_chars
    )


async def gather_corporate_intel(
    domain_or_url: str,
    *,
    max_lines: int = 25,
    max_chars: int = 2800,
    max_pages: int = 6,
    geo_hint: str = "",
) -> CorporateIntelResult:
    url = domain_or_url.strip()
    if not url.startswith("http"):
        url = f"https://{url}"

    # For geo-specific queries, try the country URL first to get localised content.
    # E.g. for India: vivo.com/in, apple.com/in, motorola.com/en-in
    if geo_hint:
        try:
            from vendor_intel.scraping.httpx_fetch import fetch_page_httpx_geo

            import asyncio as _asyncio
            geo_result = await _asyncio.to_thread(
                fetch_page_httpx_geo, url, geo_hint, max_chars=max_chars
            )
            if geo_result.alive and len((geo_result.text or "").strip()) >= 150:
                from vendor_intel.scraping.page_lead import lead_from_markdown

                geo_lead = lead_from_markdown(
                    geo_result.text, max_lines=max_lines, max_chars=max_chars
                )
                if geo_lead:
                    parsed = urlparse(url)
                    domain = parsed.netloc.lower().removeprefix("www.")
                    parent, subs = parse_corporate_signals(geo_lead)
                    return CorporateIntelResult(
                        domain=domain,
                        pages_fetched=1,
                        combined_text=geo_lead,
                        parent_hint=parent,
                        subsidiary_hints=subs[:5],
                        alive=True,
                    )
        except Exception:
            pass  # geo fetch failed — fall through to standard page crawl

    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    domain = parsed.netloc.lower().removeprefix("www.")

    chunks: list[str] = []
    pages = 0
    alive_any = False

    for path in _ABOUT_PATHS[:max_pages]:
        lead = await _fetch_path(base, path, max_lines=max_lines, max_chars=max_chars)
        if lead:
            alive_any = True
            pages += 1
            chunks.append(f"--- {path or '/'} ---\n{lead}")

    combined = "\n\n".join(chunks)[: max_chars * 2]
    parent, subs = parse_corporate_signals(combined)
    return CorporateIntelResult(
        domain=domain,
        pages_fetched=pages,
        combined_text=combined,
        parent_hint=parent,
        subsidiary_hints=subs,
        alive=alive_any,
    )

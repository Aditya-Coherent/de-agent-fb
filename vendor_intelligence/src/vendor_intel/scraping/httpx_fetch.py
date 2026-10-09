"""
Geo-aware httpx-based page fetcher — fallback for when ddgs.extract() returns
thin/nav-only content (JS-blocked pages).

For India queries we try country-specific URLs first:
  apple.com/in, motorola.com/en-in, vivo.com/in, samsung.com/in …
These pages serve localised content with "India" geo signals.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura

from vendor_intel.scraping.page_result import PageFetchResult

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
_INDIA_UA_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en-GB;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}
_DEFAULT_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
}

# Country-specific path variants to try, in priority order.
# We probe these before the bare domain URL so geo content loads first.
_GEO_URL_VARIANTS: dict[str, list[str]] = {
    "india": ["/in", "/en-in", "/in/en", "/india"],
    "us": ["/us", "/en-us", "/en/us"],
    "uk": ["/uk", "/en-gb", "/gb"],
    "germany": ["/de", "/en-de"],
    "australia": ["/au", "/en-au"],
    "canada": ["/ca", "/en-ca"],
    "uae": ["/ae", "/en-ae"],
    "china": ["/cn", "/zh-cn"],
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_tags(html: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", html)).strip()


def _extract_text(html: str, max_chars: int = 4000) -> str:
    text = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        favor_precision=False,
    ) or ""
    if len(text.strip()) < 100:
        text = _strip_tags(html)
    return text[:max_chars].strip()


def _build_country_urls(base_url: str, geo: str) -> list[str]:
    """Return geo-specific URL variants to try before the bare URL."""
    norm = geo.strip().lower()
    paths = _GEO_URL_VARIANTS.get(norm, [])
    if not paths:
        return []
    parsed = urlparse(base_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    return [urljoin(base, p) for p in paths]


def _fetch_url_sync(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 18.0,
    max_chars: int = 4000,
) -> PageFetchResult:
    hdrs = headers or _DEFAULT_HEADERS
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            headers=hdrs,
        ) as client:
            resp = client.get(url)
            if resp.status_code >= 400:
                return PageFetchResult(
                    url=url,
                    final_url=str(resp.url),
                    alive=False,
                    error=f"HTTP {resp.status_code}",
                )
            html = resp.text or ""
            text = _extract_text(html, max_chars=max_chars)
            alive = len(text.strip()) >= 80
            return PageFetchResult(
                url=url,
                final_url=str(resp.url),
                alive=alive,
                text=text,
                html=html[:8000],
                source="httpx",
            )
    except Exception as exc:
        return PageFetchResult(
            url=url,
            final_url=url,
            alive=False,
            error=f"{type(exc).__name__}: {exc}"[:300],
        )


def fetch_page_httpx(url: str, *, max_chars: int = 4000) -> PageFetchResult:
    """Direct httpx fetch without geo specialisation."""
    url = url.strip()
    if not url.startswith("http"):
        url = f"https://{url}"
    return _fetch_url_sync(url, max_chars=max_chars)


def fetch_page_httpx_geo(
    url: str,
    geo: str,
    *,
    max_chars: int = 4000,
) -> PageFetchResult:
    """
    Try geo-specific URL variants first (e.g. apple.com/in for India),
    then fall back to the original URL.
    Returns the first result with substantial text.
    """
    url = url.strip()
    if not url.startswith("http"):
        url = f"https://{url}"

    geo_norm = geo.strip().lower()
    india_headers = _INDIA_UA_HEADERS if geo_norm == "india" else None

    for geo_url in _build_country_urls(url, geo_norm):
        result = _fetch_url_sync(
            geo_url,
            headers=india_headers or _DEFAULT_HEADERS,
            max_chars=max_chars,
        )
        if result.alive and len(result.text.strip()) >= 150:
            return result

    return _fetch_url_sync(url, headers=india_headers or _DEFAULT_HEADERS, max_chars=max_chars)

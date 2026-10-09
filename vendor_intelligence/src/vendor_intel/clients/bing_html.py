"""Bing HTML search fallback when DDGS / DuckDuckGo are blocked or rate-limited."""
from __future__ import annotations

import logging
import re
from html import unescape
from urllib.parse import quote_plus

from vendor_intel.clients.browser_headers import browser_headers
from vendor_intel.clients.http_proxy import httpx_client
from vendor_intel.clients.duckduckgo import DuckResult

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
# Bing 2025+ layout: result blocks use b_algo; real URLs are in <cite>, not ck/a redirects.
_BING_BLOCK = re.compile(
    r'<li[^>]*class="[^"]*b_algo[^"]*"[^>]*>(.*?)</li>',
    re.DOTALL | re.IGNORECASE,
)
_BING_H2 = re.compile(
    r'<h2[^>]*>\s*<a[^>]+href="[^"]*"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_BING_CITE = re.compile(r"<cite[^>]*>([^<]+)</cite>", re.IGNORECASE)
_SKIP_HOSTS = (
    "bing.com",
    "microsoft.com",
    "msn.com",
    "google.com",
    "duckduckgo.com",
)


def _clean(text: str) -> str:
    return unescape(_TAG_RE.sub("", text)).strip()


def _cite_to_url(cite: str) -> str:
    raw = unescape(cite).strip()
    for sep in ("›", "\u203a", "»", ">"):
        if sep in raw:
            raw = raw.split(sep, 1)[0].strip()
    raw = raw.split()[0] if raw else ""
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw.rstrip("/")
    if "." in raw:
        return f"https://{raw.lstrip('/')}".rstrip("/")
    return ""


_bing_failures = 0
_BING_MAX_FAILURES = 3


def search_bing_html_try(
    query: str,
    max_results: int = 12,
    *,
    geo: str = "",
    force: bool = False,
) -> list[DuckResult]:
    """At most a few Bing HTML attempts per process after repeated failures."""
    global _bing_failures
    if not force and _bing_failures >= _BING_MAX_FAILURES:
        return []
    market = "en-IN" if geo and "india" in geo.lower() else "en-US"
    rows = search_bing_html(query, max_results=max_results, market=market, timeout=14.0)
    if not rows:
        _bing_failures += 1
    else:
        _bing_failures = 0
    return rows


def search_bing_html(
    query: str,
    max_results: int = 20,
    *,
    timeout: float = 14.0,
    market: str = "en-IN",
) -> list[DuckResult]:
    """
    Direct Bing web results page (no API key). Used when ddgs returns 0.
    Parses <cite> display URLs (Bing wraps titles in /ck/a redirect links).
    """
    headers = browser_headers(referer="https://www.bing.com/")
    headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

    try:
        with httpx_client(timeout=timeout, headers=headers) as client:
            resp = client.get(
                "https://www.bing.com/search",
                params={"q": query, "setlang": "en", "cc": market[:2].lower()},
            )
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        print(
            f"  [search] Bing HTML fallback failed ({type(exc).__name__}): {str(exc)[:90]}",
            flush=True,
        )
        logger.debug("Bing HTML failed for %r: %s", query[:50], exc)
        return []

    rows: list[DuckResult] = []
    seen: set[str] = set()

    for block in _BING_BLOCK.finditer(html):
        hm = _BING_H2.search(block.group(1))
        cm = _BING_CITE.search(block.group(1))
        if not hm or not cm:
            continue
        title = _clean(hm.group(1))
        link = _cite_to_url(cm.group(1))
        if not title or len(title) < 3 or not link:
            continue
        host = link.split("/")[2].lower() if "/" in link else ""
        if any(s in host for s in _SKIP_HOSTS):
            continue
        key = link.split("#")[0].rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        snippet_m = re.search(
            r'<p[^>]*class="[^"]*b_lineclamp[^"]*"[^>]*>(.*?)</p>',
            block.group(1),
            re.DOTALL | re.I,
        )
        snippet = _clean(snippet_m.group(1))[:500] if snippet_m else ""
        rows.append(
            DuckResult(title=title[:200], link=link, snippet=snippet, engine="bing_html")
        )
        if len(rows) >= max_results:
            break

    if rows:
        print(
            f"  [search] Bing HTML fallback OK ({len(rows)} hits) for {query[:50]!r}",
            flush=True,
        )
    return rows

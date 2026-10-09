"""SearXNG meta-search backup (local Docker + optional extra URLs)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import quote_plus

import httpx

from vendor_intel.clients.browser_headers import browser_headers

_TAG_RE = re.compile(r"<[^>]+>")
_RESULT_BLOCK = re.compile(
    r'<article[^>]+class="[^"]*result[^"]*"[^>]*>(.*?)</article>',
    re.DOTALL | re.IGNORECASE,
)
_RESULT_LINK = re.compile(
    r'<h3>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_RESULT_SNIPPET = re.compile(
    r'<p[^>]+class="[^"]*content[^"]*"[^>]*>(.*?)</p>',
    re.DOTALL | re.IGNORECASE,
)
_ENGINE_ERRORS = re.compile(r"class=\"response-error\">([^<]+)</td>", re.IGNORECASE)
_NO_RESULTS = re.compile(r"No results were found", re.IGNORECASE)


@dataclass
class SearxResult:
    title: str
    link: str
    snippet: str


def searxng_urls(primary: str = "") -> list[str]:
    """Local instance first, then SEARXNG_EXTRA_URLS env (comma-separated)."""
    urls: list[str] = []
    if primary.strip():
        urls.append(primary.strip().rstrip("/"))
    extra = (os.getenv("SEARXNG_EXTRA_URLS") or "").strip()
    for part in extra.split(","):
        u = part.strip().rstrip("/")
        if u and u not in urls:
            urls.append(u)
    return urls


def _client_headers(base: str) -> dict[str, str]:
    hdrs = browser_headers(referer=base + "/")
    hdrs["Accept"] = "application/json, text/html;q=0.9, */*;q=0.8"
    return hdrs


def _parse_json_payload(data: dict, max_results: int) -> list[SearxResult]:
    rows: list[SearxResult] = []
    for item in (data.get("results") or [])[:max_results]:
        link = item.get("url") or item.get("link") or ""
        if not link:
            continue
        rows.append(
            SearxResult(
                title=(item.get("title") or "")[:200],
                link=link,
                snippet=(item.get("content") or item.get("snippet") or "")[:500],
            )
        )
    return rows


def _parse_html_results(html: str, max_results: int) -> list[SearxResult]:
    if _NO_RESULTS.search(html):
        return []
    rows: list[SearxResult] = []
    seen: set[str] = set()
    for block in _RESULT_BLOCK.finditer(html):
        lm = _RESULT_LINK.search(block.group(1))
        if not lm:
            continue
        link = unescape(lm.group(1).strip())
        if not link.startswith("http") or "127.0.0.1" in link:
            continue
        key = link.split("#")[0].rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        title = _TAG_RE.sub("", unescape(lm.group(2))).strip()[:200]
        sm = _RESULT_SNIPPET.search(block.group(1))
        snippet = _TAG_RE.sub("", unescape(sm.group(1))).strip()[:500] if sm else ""
        if not title:
            continue
        rows.append(SearxResult(title=title, link=link, snippet=snippet))
        if len(rows) >= max_results:
            break
    return rows


def _engine_failure_hint(html: str) -> str:
    errs = _ENGINE_ERRORS.findall(html)
    if not errs:
        return ""
    return "; ".join(e[:60] for e in errs[:3])


async def _searxng_request_json(
    client: httpx.AsyncClient,
    base: str,
    query: str,
) -> tuple[int, dict | None]:
    params = {"q": query, "format": "json", "categories": "general"}
    for method in ("get", "post"):
        try:
            if method == "get":
                r = await client.get(f"{base}/search", params=params)
            else:
                r = await client.post(f"{base}/search", data=params)
            if r.status_code == 200 and "json" in (r.headers.get("content-type") or ""):
                return r.status_code, r.json()
            if r.status_code == 403:
                return 403, None
        except (httpx.HTTPError, ValueError):
            continue
    return 0, None


async def _searxng_request_html(
    client: httpx.AsyncClient,
    base: str,
    query: str,
) -> tuple[int, str]:
    data = {"q": query, "category_general": "1"}
    try:
        r = await client.post(f"{base}/search", data=data)
        return r.status_code, r.text
    except httpx.HTTPError:
        return 0, ""


async def searxng_search(
    query: str,
    base_url: str,
    max_results: int = 15,
    timeout: float = 25.0,
) -> list[SearxResult]:
    base = base_url.rstrip("/")
    hdrs = _client_headers(base)
    try:
        from vendor_intel.clients.http_proxy import httpx_async_client

        async with httpx_async_client(timeout=timeout, headers=hdrs) as client:
            status, data = await _searxng_request_json(client, base, query)
            if status == 200 and data:
                rows = _parse_json_payload(data, max_results)
                if rows:
                    return rows

            _, html = await _searxng_request_html(client, base, query)
            if not html:
                return []
            rows = _parse_html_results(html, max_results)
            if not rows:
                hint = _engine_failure_hint(html)
                if hint:
                    print(
                        f"  [search] SearXNG at {base} returned 0 hits "
                        f"(engines: {hint}). "
                        "Recreate container: docker compose down && docker compose up -d",
                        flush=True,
                    )
                elif status == 403:
                    print(
                        f"  [search] SearXNG JSON API 403 at {base} — "
                        "mount config/searxng/settings.yml (limiter: false) and restart Docker",
                        flush=True,
                    )
            return rows
    except httpx.HTTPError:
        return []


async def searxng_search_any(
    query: str,
    urls: list[str],
    max_results: int = 15,
) -> tuple[list[SearxResult], str]:
    """Try each SearXNG base URL until one returns results."""
    for base in urls:
        rows = await searxng_search(query, base, max_results=max_results)
        if rows:
            return rows, base
    return [], ""


async def searxng_ping(base_url: str) -> bool:
    """True when the instance returns at least one search hit (JSON or HTML)."""
    rows = await searxng_search("test connectivity", base_url, max_results=3, timeout=12.0)
    return bool(rows)


async def searxng_ping_any(urls: list[str]) -> tuple[bool, str]:
    for base in urls:
        if await searxng_ping(base):
            return True, base
    return False, ""


async def searxng_json_api_ok(base_url: str) -> bool:
    """Whether format=json is allowed (not 403)."""
    base = base_url.rstrip("/")
    hdrs = _client_headers(base)
    try:
        from vendor_intel.clients.http_proxy import httpx_async_client

        async with httpx_async_client(timeout=8.0, headers=hdrs) as client:
            status, _ = await _searxng_request_json(client, base, "ping")
            return status == 200
    except httpx.HTTPError:
        return False

"""Live web evidence via `ddgs` (multi-backend: Bing, Mojeek, Google,
DuckDuckGo, with fallback). Degrades gracefully to [] on any failure.

Replaces the previous single-backend raw DuckDuckGo-HTML scrape, which was
failing (ConnectTimeout) in this environment — meaning every Desk Research
generation was silently running on zero real web evidence, with the LLM
estimating market size/regions/segments/pricing from its own training
knowledge alone. `ddgs` has been confirmed to actually return results here.
"""
from __future__ import annotations


def search_snippets(query: str, max_results: int = 6) -> list[str]:
    """Return short text snippets for a query. Never raises — returns [] on failure."""
    try:
        from ddgs import DDGS

        results = DDGS().text(query, max_results=max_results)
    except Exception:  # noqa: BLE001 - never let a search failure break generation
        return []

    snippets: list[str] = []
    for r in results:
        title = (r.get("title") or "").strip()
        body = (r.get("body") or "").strip()
        if not body:
            continue
        text = f"{title}: {body}" if title else body
        if len(text) > 30:
            snippets.append(text)
    return snippets


def gather(queries: list[str], per_query: int = 4, cap: int = 12) -> list[str]:
    """Run several queries and return a de-duplicated, capped snippet list."""
    out: list[str] = []
    seen: set[str] = set()
    for q in queries:
        for s in search_snippets(q, max_results=per_query):
            key = s[:80].lower()
            if key not in seen:
                seen.add(key)
                out.append(s)
            if len(out) >= cap:
                return out
    return out


def search_snippets_with_sources(query: str, max_results: int = 6) -> list[dict]:
    """Same as `search_snippets`, but also returns each result's URL/domain --
    additive, new function (never raises; degrades to []), so existing
    `search_snippets`/`gather` callers (Desk Research's `agents.py`) are
    completely untouched. Used by `AiAgentsFleet` to compute a
    per-section confidence label from source-domain diversity."""
    try:
        from ddgs import DDGS

        results = DDGS().text(query, max_results=max_results)
    except Exception:  # noqa: BLE001
        return []

    out: list[dict] = []
    for r in results:
        title = (r.get("title") or "").strip()
        body = (r.get("body") or "").strip()
        url = (r.get("href") or "").strip()
        if not body:
            continue
        text = f"{title}: {body}" if title else body
        if len(text) > 30:
            domain = ""
            if url:
                domain = url.split("//")[-1].split("/")[0].removeprefix("www.")
            out.append({"text": text, "url": url, "domain": domain, "title": title})
    return out


def gather_with_sources(queries: list[str], per_query: int = 4, cap: int = 12) -> list[dict]:
    """Source-tracked version of `gather`. Returns
    `[{"text": ..., "url": ..., "domain": ...}, ...]`, de-duplicated by text
    and capped the same way."""
    out: list[dict] = []
    seen: set[str] = set()
    for q in queries:
        for item in search_snippets_with_sources(q, max_results=per_query):
            key = item["text"][:80].lower()
            if key not in seen:
                seen.add(key)
                out.append(item)
            if len(out) >= cap:
                return out
    return out


def fetch_page_text(url: str, timeout: float = 8.0, max_chars: int = 20_000) -> str:
    """Visible text of a web page (tags stripped, first `max_chars`), for
    cited-figure extraction. Never raises -- returns "" on any failure
    (blocked, timeout, non-HTML)."""
    try:
        import html as _html
        import re as _re

        import httpx

        resp = httpx.get(url, timeout=timeout, follow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0 (compatible; DeAgent/1.0)"})
        if resp.status_code != 200 or "html" not in resp.headers.get("content-type", "html"):
            return ""
        raw = _re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", resp.text)
        raw = _re.sub(r"(?i)<(br|p|li|h\d|div|tr)[^>]*>", "\n", raw)
        text = _html.unescape(_re.sub(r"<[^>]+>", " ", raw))
        text = _re.sub(r"[ \t\r\f\v]+", " ", text)
        return _re.sub(r"\n\s*", "\n", text).strip()[:max_chars]
    except Exception:  # noqa: BLE001 - never let a page fetch break generation
        return ""

"""Best-effort live web evidence (DuckDuckGo HTML). Degrades gracefully to []."""
from __future__ import annotations

import html
import re

import httpx

_DDG = "https://html.duckduckgo.com/html/"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"


def search_snippets(query: str, max_results: int = 6, timeout: float = 15.0) -> list[str]:
    """Return short text snippets for a query. Never raises — returns [] on failure."""
    try:
        resp = httpx.post(
            _DDG,
            data={"q": query},
            headers={"User-Agent": _UA},
            timeout=timeout,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception:
        return []

    snippets: list[str] = []
    for m in re.finditer(r'class="result__snippet"[^>]*>(.*?)</a>', resp.text, flags=re.S):
        text = re.sub(r"<[^>]+>", " ", m.group(1))
        text = re.sub(r"\s+", " ", html.unescape(text)).strip()
        if len(text) > 30:
            snippets.append(text)
        if len(snippets) >= max_results:
            break
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

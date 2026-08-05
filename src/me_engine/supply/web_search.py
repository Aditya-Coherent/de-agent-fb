"""Real multi-backend web search via the `ddgs` library.

Replaces `me_engine.generation.web_evidence`'s single-backend raw
DuckDuckGo-HTML scrape, which has been failing (ConnectTimeout) in this
environment all session -- `ddgs` routes through multiple backends (Bing,
Mojeek, Google, DuckDuckGo) with fallback, and has been confirmed to
actually return results here. Degrades to [] on any failure, same
graceful-degradation contract as web_evidence.gather().
"""
from __future__ import annotations


def search_snippets(query: str, max_results: int = 6) -> list[str]:
    try:
        from ddgs import DDGS

        results = DDGS().text(query, max_results=max_results)
    except Exception:  # noqa: BLE001 - never let a search failure break the pipeline
        return []

    out: list[str] = []
    for r in results:
        title = (r.get("title") or "").strip()
        body = (r.get("body") or "").strip()
        if not body:
            continue
        out.append(f"{title}: {body}" if title else body)
    return out


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

"""Live web evidence via `ddgs` (multi-backend: Bing, Mojeek, Google,
DuckDuckGo, with fallback). Degrades gracefully to [] on any failure.

Replaces the previous single-backend raw DuckDuckGo-HTML scrape, which was
failing (ConnectTimeout) in this environment — meaning every demand-side
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

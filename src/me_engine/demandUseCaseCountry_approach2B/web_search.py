"""Demand-usage-local web search that preserves the result URL.

`me_engine.generation.web_evidence.gather` (shared Desk Research infra --
never modified by this pipeline, per scope) discards the URL and returns
bare text snippets. That's not enough for an Evidence Retrieval Plan, which
must cite a real `source_URL` for every extracted fact -- not a label the
LLM invents. Same `ddgs` backend as `web_evidence.py`, degrades to `[]` on
any failure -- never raises, never blocks a run.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str  # "title: body" combined text -- same shape as web_evidence's bare snippets


def search(query: str, max_results: int = 5) -> list[SearchResult]:
    """Return real search results (with URL) for one query. Never raises."""
    try:
        from ddgs import DDGS

        raw = DDGS().text(query, max_results=max_results)
    except Exception:  # noqa: BLE001 -- never let a search failure break a run
        return []

    out: list[SearchResult] = []
    for r in raw:
        title = (r.get("title") or "").strip()
        url = (r.get("href") or "").strip()
        body = (r.get("body") or "").strip()
        if not body:
            continue
        text = f"{title}: {body}" if title else body
        out.append(SearchResult(title=title, url=url, snippet=text))
    return out

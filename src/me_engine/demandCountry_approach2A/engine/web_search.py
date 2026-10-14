"""Demand-usage-local web search that preserves the result URL.

`me_engine.generation.web_evidence.gather` (shared Desk Research infra --
never modified by this pipeline, per scope) discards the URL and returns
bare text snippets. That's not enough for an Evidence Retrieval Plan, which
must cite a real `source_URL` for every extracted fact -- not a label the
LLM invents. Same `ddgs` backend as `web_evidence.py`, degrades to `[]` on
any failure -- never raises, never blocks a run.

2A addition: when the (opt-in) Google AI Overview backend is running
(`ai_overview.py`), the overview is used as a LEAD only: its cited pages are
fetched and appended as ordinary results under their own URLs; the overview
text itself never reaches the extractor (system.md §3.4).
"""
from __future__ import annotations

from dataclasses import dataclass

from me_engine.generation import web_evidence

from . import ai_overview


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
    answer = ai_overview.ask(query)
    if answer:
        seen = {r.url for r in out}
        for url, text in ai_overview.cited_page_results(query, answer):
            if url not in seen:
                out.append(SearchResult(title=f"cited page ({url})", url=url, snippet=text))
    return out


def gather(queries: list[str], per_query: int = 4, cap: int = 12) -> list[str]:
    """`web_evidence.gather` plus the pages cited by the AI Overview of the
    first query (lead-only), which take the last slots when the list is full."""
    snippets = web_evidence.gather(queries, per_query=per_query, cap=cap)
    answer = ai_overview.ask(queries[0]) if queries else None
    if answer:
        cited = [f"({url}) {text}" for url, text in ai_overview.cited_page_results(queries[0], answer)]
        if cited:
            snippets = snippets[: max(0, cap - len(cited))] + cited
    return snippets

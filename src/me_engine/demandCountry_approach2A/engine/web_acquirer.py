"""Web-page acquirer -- one rung in the Evidence Router chain.

Proven weakest by the cross-market validation for a specific, fixable
reason: `ddgs`'s search-result SNIPPET is search-engine meta text (often
navigation copy, "Homepage - EIA", category blurbs), not the page's real
body -- so even a correctly-targeted URL frequently fails extraction simply
because the number never appears in that short snippet.

Fix: fetch the ACTUAL page body (HTML -> text, via `requests`+`bs4`, both
already project dependencies) for the top-ranked result(s) and extract
against that real content, falling back to the bare snippet only if the
fetch itself fails (timeout, paywall, robots block, non-HTML/binary
content -- a PDF at that URL is `pdf_acquirer`'s job, not this one).
"""
from __future__ import annotations

from typing import Any

from me_engine.generation.llm import GenLLM

from . import ai_overview, asset_verifier, evidence, web_search

_MAX_PAGE_CHARS = 6000  # keep the LLM prompt bounded -- most facts sit near the top of a page anyway
_MAX_RESULTS_TO_FETCH = 2  # only the top-ranked results are worth the extra fetch cost


def extract_page_text(content: bytes) -> str | None:
    """Pure HTML-body -> plain-text extraction, factored out of
    `_fetch_page_text` so `evidence_router`'s Phase 2 LLM-claim rung
    (`DEMAND_SIDE_EVIDENCE_PLAN.md`) can run the SAME extraction against a
    candidate URL's already-verified content, instead of a bespoke second
    copy of this parsing. Never raises -- returns None on any failure."""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(content, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = " ".join(soup.get_text(separator=" ").split())
        return text[:_MAX_PAGE_CHARS] if text else None
    except Exception:  # noqa: BLE001
        return None


def _fetch_page_text(url: str) -> str | None:
    """Best-effort full-page text extraction. Never raises, never blocks
    the chain -- returns None on any failure (network, non-HTML, etc.).

    Delegates redirect-following and content-type discipline to the shared
    `asset_verifier` module (Phase 1, `DEMAND_SIDE_EVIDENCE_PLAN.md`) --
    a URL that actually resolves to a PDF/Excel/JSON asset rather than a
    real page is `pdf_acquirer`'s/`tabular_acquirer`'s/`api_acquirer`'s job,
    not this one, so it is rejected here rather than mis-parsed as HTML."""
    if not url:
        return None
    verification = asset_verifier.verify_asset(url, "html")
    if not verification.is_accessible or not verification.matches_expected_type or not verification.content:
        return None
    return extract_page_text(verification.content)


def acquire(
    llm: GenLLM, source: dict[str, Any], geography: str, *, unit: str, context: str,
    emitter: Any | None = None, stage: str = "evidence_search", bucket: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Runs ONE web source: a real `ddgs` search, then a real page-body
    fetch for the top result(s), then extraction against the fetched body
    (falling back to the bare snippet if the fetch failed). Returns
    (fact_or_None, attempt_record)."""
    query = source["search_query_template"].replace("{country}", geography)
    results = web_search.search(query, max_results=5)
    attempt: dict[str, Any] = {
        "source_type": "web_page", "source_name": source["source_name"], "search_query": query,
        "num_results": len(results), "found": False,
    }
    if emitter is not None:
        emitter.emit(stage, "search_query", bucket=bucket, country=geography, query={"search_query": query, "num_results": len(results)})
    if not results:
        attempt["rejection_reason"] = "no_source_found"
        return None, attempt

    enriched: list[web_search.SearchResult] = []
    for i, r in enumerate(results):
        if i < _MAX_RESULTS_TO_FETCH and not ai_overview.is_ai_overview_url(r.url):
            page_text = _fetch_page_text(r.url)
            snippet = f"{r.title}: {page_text}" if page_text else r.snippet
        else:
            snippet = r.snippet
        enriched.append(web_search.SearchResult(title=r.title, url=r.url, snippet=snippet))

    fact, rejection = evidence.extract_fact_from_source(
        llm, enriched, unit=unit, context=context, source_name=source["source_name"],
        geography=geography, emitter=emitter, stage=stage, bucket=bucket,
    )
    attempt["found"] = fact is not None
    if fact is not None:
        attempt.update({
            "value": fact["value"], "unit": fact["unit"], "year": fact.get("year"),
            "source_url": fact.get("source_url", ""), "citation": fact.get("citation", ""),
            "evidence_type": fact.get("evidence_type"),
        })
    else:
        # `rejection` is populated only when a candidate WAS found but
        # rejected by the extraction-quality gate (`fact_validator.py`) --
        # e.g. a real number that measures the wrong metric/geography.
        # Falling all the way through with nothing found at all is the
        # ordinary `extraction_failed` case.
        attempt["rejection_reason"] = rejection or "extraction_failed"
    return fact, attempt

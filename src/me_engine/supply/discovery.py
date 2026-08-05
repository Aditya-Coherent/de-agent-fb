"""Miniature company discovery — seconds, not the ~30 min full
vendor-intelligence pipeline (multi-phase widening + Selenium + heavy
agentic validation). Own implementation living in de-agent-fb (per
instruction: don't touch/reuse vendor-intelligence's actual pipeline) —
shape borrowed from reading its Phase 1 ("compile query -> a few targeted
search prompts -> free search") after deciding a rough estimate doesn't need
that pipeline's depth.

Degrades gracefully to LLM-only knowledge when live web search is
unavailable/blocked (same pattern as `me_engine.generation.web_evidence`
elsewhere in this codebase) — this is meant for a rough, fast supply-side
seed list, not an exhaustive registry.
"""
from __future__ import annotations

from pathlib import Path

from ..generation import web_evidence
from ..generation.config import GenConfig
from ..generation.llm import GenLLM
from .companies import Company

_SCOPE_SYSTEM = (
    "You are a market-research analyst. Given a market name (and optionally a country), "
    "identify the industry vertical and 3-5 short search terms useful for finding companies "
    "that sell into this market. Respond ONLY with a JSON object: "
    '{"industry_vertical": "<short label>", "search_terms": ["<term>", ...]}'
)

_COMPANY_SYSTEM = (
    "You are a market-research analyst building a rough (not exhaustive) list of companies "
    "that sell into a given market. Use the web evidence snippets if present, and otherwise "
    "your own knowledge of well-known and mid-size players in this space — do not skip major "
    "global players just because they weren't in the search results. Only include a company if "
    "you are reasonably confident it is a real, specific business (not a generic placeholder "
    "like 'Avocado Oil Chile' invented just to fill a country slot) — fewer real companies is "
    "better than padding with invented ones. Include each company's country/HQ and its real "
    "website domain if known. Skip pure retailers/supermarkets unless the market itself is "
    'retail. Respond ONLY with a JSON object: {"companies": [{"name": "...", "country": "...", '
    '"likely_website": "<domain, or empty string if unsure>", "functionality": "<one short '
    'phrase>"}, ...]} with between 15 and 40 companies.'
)


def _compile_scope(llm: GenLLM, market_name: str, country: str | None) -> dict:
    user = f"Market: {market_name}" + (f"\nCountry: {country}" if country else "")
    return llm.complete_json(_SCOPE_SYSTEM, user)


def discover_companies(
    market_name: str,
    config: GenConfig,
    country: str | None = None,
    cache_path: Path | None = None,
) -> list[Company]:
    """Rough, fast company discovery for one market (optionally scoped to one
    country) — a handful of LLM/search calls, not a multi-phase crawl.
    Returns `Company` objects ready for `estimator.estimate_supply_side`."""
    llm = GenLLM(config, cache_path=cache_path)
    scope = _compile_scope(llm, market_name, country)
    terms = [str(t) for t in (scope.get("search_terms") or [market_name])][:3]

    snippets: list[str] = []
    if config.enable_web:
        queries = [f"{t} companies" + (f" {country}" if country else "") for t in terms]
        snippets = web_evidence.gather(queries, per_query=4, cap=20)

    evidence_block = "\n".join(f"- {s}" for s in snippets) if snippets else \
        "(no live search results available — use your own knowledge of this industry)"
    user = (
        f"Market: {market_name}\n"
        + (f"Country: {country}\n" if country else "")
        + f"Industry vertical: {scope.get('industry_vertical', '')}\n"
        + f"Web evidence:\n{evidence_block}"
    )
    data = llm.complete_json(_COMPANY_SYSTEM, user)

    companies: list[Company] = []
    seen: set[str] = set()
    for item in data.get("companies", []) or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        facts = {k: v for k, v in item.items() if k != "name" and v}
        companies.append(Company(name=name, facts=facts))
    return companies

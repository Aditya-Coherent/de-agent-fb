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
    "that manufacture (or own the brands of) the products in this market. Respond ONLY with a JSON object: "
    '{"industry_vertical": "<short label>", "search_terms": ["<term>", ...]}'
)

_COMPANY_SYSTEM = (
    "You are a market-research analyst building a rough (not exhaustive) list of companies "
    "that MANUFACTURE (or own the brands of) the products in a given market -- the companies whose "
    "own revenue makes up this market's size. Use the web evidence snippets if present, and otherwise "
    "your own knowledge of well-known and mid-size players in this space — do not skip major "
    "global players just because they weren't in the search results. Only include a company if "
    "you are reasonably confident it is a real, specific business (not a generic placeholder "
    "like 'Avocado Oil Chile' invented just to fill a country slot) — fewer real companies is "
    "better than padding with invented ones. Include each company's country/HQ and its real "
    "website domain if known. Do NOT list distributors, wholesalers, resellers, group purchasing "
    "organisations or retailers (they re-sell the manufacturers' product, so counting them double "
    "counts the market); if you mention one anyway, mark its role honestly. List each corporate "
    "group ONCE under its parent company -- not separately as the parent, its subsidiaries, its "
    "regional arms and its brands -- and give every company's owning parent company: the operating "
    "company that owns this brand/subsidiary, not a private-equity or investment holder (empty string "
    "if it is itself the parent / independent). "
    'Respond ONLY with a JSON object: {"companies": [{"name": "...", "country": "...", '
    '"likely_website": "<domain, or empty string if unsure>", "functionality": "<one short '
    'phrase>", "role": "<manufacturer|brand_owner|distributor|retailer|other>", '
    '"parent_company": "<owning parent company, or empty string>"}, ...]} with between 15 and '
    "40 companies."
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
    Returns `Company` objects ready for `graph.run_supply_side`, each carrying
    `role` and `parent_company` facts -- the graph's screen step uses those to
    drop distributors/retailers and collapse parent/brand duplicates
    (`companies.screen_companies`), so a saved-and-reloaded list is screened
    the same way."""
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

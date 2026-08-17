"""LangGraph StateGraph implementing the supply-side estimate — via the
bottom-up (country-wise) technique: sum individual companies' revenue from
the ground up, per country, then per country again up to a grand total.
This IS how supply-side sizing gets done; there is no separate "single
total" method sitting next to it — a single total is just `Σ(country_total)`
over this same breakdown.

Flow:
    geography -> companies_revenue -> coverage -> aggregate -> END

Own state, own nodes — separate from the Desk Research graph
(`me_engine.generation.graph`). Only reuses the generic `Company` sourcing
utilities in this same package (`companies.py` / `discovery.py` /
`verify.py`); its estimation math lives in this package's own
`estimator.py` tool functions, driven here by graph nodes.
"""
from __future__ import annotations

from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, StateGraph

from ..generation.config import GenConfig
from ..generation.llm import GenLLM
from .companies import Company
from .estimator import CountryBreakdown, estimate_company_countries, estimate_coverage_by_country
from .geography import discover_countries


class SupplySideState(TypedDict, total=False):
    market_name: str
    companies: list[Company]
    config: GenConfig
    llm: GenLLM
    countries: list[str]
    per_company_country_revenue: dict[str, dict[str, float]]
    per_company_source: dict[str, str]  # company -> "financial_api" | "web_evidence" | "no_real_data"
    named_totals: dict[str, float]
    coverage_fraction: dict[str, float]
    country_breakdown: list[CountryBreakdown]
    grand_total_usd_mn: float
    log: list[str]


def geography_node(state: SupplySideState) -> dict:
    if state.get("countries"):
        return {}
    countries = discover_countries(state["market_name"], state["config"])
    log = state.get("log", []) + [f"[geography] {len(countries)} countries/regions proposed"]
    return {"countries": countries, "log": log}


def companies_revenue_node(state: SupplySideState) -> dict:
    llm: GenLLM = state["llm"]
    countries = state["countries"]
    config: GenConfig = state["config"]
    per_company: dict[str, dict[str, float]] = {}
    per_source: dict[str, str] = {}
    for c in state["companies"]:
        revenue, source = estimate_company_countries(llm, state["market_name"], c, countries, config.enable_web)
        per_company[c.name] = revenue
        per_source[c.name] = source

    counts = {"financial_api": 0, "web_evidence": 0, "no_real_data": 0}
    for src in per_source.values():
        counts[src] = counts.get(src, 0) + 1
    log = state.get("log", []) + [
        f"[companies_revenue] estimated {len(per_company)} companies x {len(countries)} countries "
        f"(grounded: {counts['financial_api']} financial-data, {counts['web_evidence']} web-evidence, "
        f"{counts['no_real_data']} no real data found)"
    ]
    return {"per_company_country_revenue": per_company, "per_company_source": per_source, "log": log}


def coverage_node(state: SupplySideState) -> dict:
    llm: GenLLM = state["llm"]
    countries = state["countries"]
    per_company = state["per_company_country_revenue"]
    named_totals = {
        country: sum(per_company[name].get(country, 0.0) for name in per_company)
        for country in countries
    }
    coverage = estimate_coverage_by_country(llm, state["market_name"], countries, named_totals)
    log = state.get("log", []) + ["[coverage] estimated per-country named-supplier coverage fraction"]
    return {"named_totals": named_totals, "coverage_fraction": coverage, "log": log}


def aggregate_node(state: SupplySideState) -> dict:
    countries = state["countries"]
    per_company = state["per_company_country_revenue"]
    named_totals = state["named_totals"]
    coverage = state["coverage_fraction"]

    breakdown: list[CountryBreakdown] = []
    grand_total = 0.0
    for country in countries:
        named = named_totals[country]
        frac = coverage[country]
        country_total = named / frac if frac > 0 else named
        shares = {
            name: (per_company[name].get(country, 0.0) / country_total if country_total > 0 else 0.0)
            for name in per_company
        }
        breakdown.append(CountryBreakdown(
            country=country,
            named_total_usd_mn=named,
            coverage_fraction=frac,
            country_total_usd_mn=country_total,
            company_shares=shares,
        ))
        grand_total += country_total

    log = state.get("log", []) + [f"[aggregate] grand total = {grand_total:,.1f} USD Mn"]
    return {"country_breakdown": breakdown, "grand_total_usd_mn": grand_total, "log": log}


def build_graph():
    g = StateGraph(SupplySideState)
    g.add_node("geography", geography_node)
    g.add_node("companies_revenue", companies_revenue_node)
    g.add_node("coverage", coverage_node)
    g.add_node("aggregate", aggregate_node)

    g.set_entry_point("geography")
    g.add_edge("geography", "companies_revenue")
    g.add_edge("companies_revenue", "coverage")
    g.add_edge("coverage", "aggregate")
    g.add_edge("aggregate", END)
    return g.compile()


def run_supply_side(
    market_name: str,
    companies: list[Company],
    config: GenConfig,
    countries: list[str] | None = None,
    cache_path: Path | None = None,
) -> SupplySideState:
    if not companies:
        raise ValueError("No companies provided.")

    llm = GenLLM(config, cache_path=cache_path)
    graph = build_graph()
    init: SupplySideState = {
        "market_name": market_name,
        "companies": companies,
        "config": config,
        "llm": llm,
        "log": [],
    }
    if countries:
        init["countries"] = countries
    return graph.invoke(init, config={"recursion_limit": 20})

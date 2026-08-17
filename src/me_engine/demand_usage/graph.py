"""LangGraph phases for the usage/application-based demand-side pipeline.

V3: three separate, independently-invokable phases instead of one monolithic
run -- each phase reads the PRIOR phase's user-APPROVED checkpoint (see
`checkpoints.py`) and never re-derives what a human already approved (spec:
"downstream agents should NOT independently redefine [approved config]"):

    Phase 1 (setup.py):     market_setup_draft
                                -> human approves -> 00_setup_approved.json
    Phase 2 (taxonomy.py):  usage_decomposition -> usage_validation
                            -> usage_coverage -> demand_driver
                                -> human approves -> 01_taxonomy_approved.json
    Phase 3 (this module):  demand_quantification -> pricing -> calculate
                            -> driver_magnitude_validation -> triangulation
                            -> validation -> END

`calculate` is still the only node in Phase 3 with no LLM call -- pure
deterministic arithmetic over what every prior agent/human decided.
`driver_magnitude_validation` and `triangulation` are flag-only/informational
-- neither ever mutates a quantity or the final total (see their own module
docstrings).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from me_engine.generation.config import GenConfig
from me_engine.generation.llm import GenLLM

from . import agents, magnitude, triangulation


class UsageDemandState(TypedDict, total=False):
    market_name: str
    config: GenConfig
    llm: GenLLM
    log: list[str]

    # From the approved Phase 1 setup -- READ-ONLY here, never re-derived.
    scope: str
    countries: list[str]
    base_year: int
    market_unit: str
    physical_unit: str

    # From the approved Phase 2 taxonomy -- READ-ONLY here, never re-derived.
    usage_buckets: list[dict[str, str]]
    excluded_usage_buckets: list[dict[str, Any]]
    usage_classifications: dict[str, dict[str, Any]]
    missing_usage_flags: list[dict[str, Any]]
    coverage_pct: float
    overlap_flags: list[str]
    drivers: dict[str, dict[str, Any]]

    quantities: dict[str, dict[str, dict[str, Any]]]
    intensity_clamped_buckets: list[str]
    llm_call_failures: list[str]
    prices: dict[str, dict[str, Any]]

    usage_values: dict[str, dict[str, float]]
    country_totals: dict[str, float]
    total_market_value: float
    related_demand_country_totals: dict[str, float]
    related_demand_value: float
    unit_contract: dict[str, dict[str, Any]]
    unit_rejected_buckets: list[str]

    driver_facts: dict[str, dict[str, dict[str, Any]]]
    price_facts: dict[str, dict[str, Any]]

    driver_magnitude_flags: list[dict[str, Any]]
    triangulation: list[dict[str, Any]]
    triangulation_large_gap_flags: list[str]

    validation: dict[str, Any]
    quality_flags: list[dict[str, str]]


def build_sizing_graph():
    g = StateGraph(UsageDemandState)
    g.add_node("demand_quantification", agents.demand_quantification_node)
    g.add_node("pricing", agents.pricing_node)
    g.add_node("calculate", agents.calculate_node)
    g.add_node("driver_magnitude_validation", magnitude.driver_magnitude_validation_node)
    g.add_node("triangulation", triangulation.triangulation_node)
    g.add_node("validation", agents.validation_node)

    g.set_entry_point("demand_quantification")
    g.add_edge("demand_quantification", "pricing")
    g.add_edge("pricing", "calculate")
    g.add_edge("calculate", "driver_magnitude_validation")
    g.add_edge("driver_magnitude_validation", "triangulation")
    g.add_edge("triangulation", "validation")
    g.add_edge("validation", END)
    return g.compile()


def sizing_init_state(
    approved_setup: dict[str, Any],
    approved_taxonomy: dict[str, Any],
    config: GenConfig,
    llm: GenLLM,
) -> UsageDemandState:
    """Builds the Phase 3 initial state strictly from the two approved
    checkpoints -- the only two inputs Phase 3 is allowed to read for
    market/geography/usage/driver definition (spec section 12)."""
    return {
        "market_name": approved_setup["market_name"],
        "config": config,
        "llm": llm,
        "log": [],
        "scope": approved_setup["scope"],
        "countries": list(approved_setup["geography"]),
        "base_year": approved_setup["base_year"],
        "market_unit": approved_setup["market_unit"],
        "physical_unit": approved_setup["physical_unit"],
        "usage_buckets": approved_taxonomy["usage_buckets"],
        "excluded_usage_buckets": approved_taxonomy.get("excluded_usage_buckets", []),
        "missing_usage_flags": approved_taxonomy.get("missing_usage_flags", []),
        "coverage_pct": approved_taxonomy.get("coverage_pct"),
        "overlap_flags": approved_taxonomy.get("overlap_flags", []),
        "drivers": approved_taxonomy["drivers"],
    }


def run_sizing_phase(
    approved_setup: dict[str, Any],
    approved_taxonomy: dict[str, Any],
    config: GenConfig,
    cache_path: Path | None = None,
) -> UsageDemandState:
    from .quality import compute_provenance_quality_flags

    llm = GenLLM(config, cache_path=cache_path)
    graph = build_sizing_graph()
    init = sizing_init_state(approved_setup, approved_taxonomy, config, llm)
    final = graph.invoke(init, config={"recursion_limit": 20})
    flags = compute_provenance_quality_flags(final)
    if flags:
        codes = ", ".join(f.get("code", "") for f in flags)
        final.setdefault("log", []).append(f"Quality check flagged this run for review: {codes}")
    final["quality_flags"] = flags
    return final

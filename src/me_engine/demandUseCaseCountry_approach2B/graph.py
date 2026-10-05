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
                            -> magnitude_cross_validation -> validation -> END

`calculate` is still the only node in Phase 3 with no LLM call -- pure
deterministic arithmetic over what every prior agent/human decided.
`driver_magnitude_validation`, `triangulation`, and `magnitude_cross_validation`
(Phase 11, `DEMAND_SIDE_EVIDENCE_PLAN.md`) are all flag-only/informational --
none of them ever mutates a quantity or the final total (see their own module
docstrings).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from me_engine.generation.config import GenConfig
from me_engine.generation.llm import GenLLM

from . import agents, magnitude, magnitude_validation, triangulation


class UsageDemandState(TypedDict, total=False):
    market_name: str
    config: GenConfig
    llm: GenLLM
    log: list[str]
    # Runtime-hardening addendum (2026-08-24) -- optional incremental
    # progress recorder (see `progress.py`); read via `state.get("progress")`
    # everywhere, so its absence (every pre-existing test's bare state dict)
    # is a no-op, never a KeyError, never a behavior change.
    progress: Any
    # Additive audit/observability layer (`me_engine.audit`) -- optional
    # `AuditEmitter`, read via `state.get("emitter")` everywhere it's used,
    # exactly the same no-op-when-absent discipline as `progress` above.
    emitter: Any

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
    scope_validation_flags: dict[str, dict[str, Any]]
    # Phase 9 (DEMAND_SIDE_EVIDENCE_PLAN.md) -- aggregate "scope too narrow"
    # signal + any user-approved out-of-scope overrides, carried through
    # read-only into the final report/quality flags.
    scope_taxonomy_aggregate: dict[str, Any]
    scope_overrides: dict[str, str]
    scope_amendment_text: str
    missing_usage_flags: list[dict[str, Any]]
    coverage_pct: float
    overlap_flags: list[str]
    # Each driver dict MAY carry an `evidence_strategy` sub-dict + an
    # `evidence_strategy_flags` list (Phase 7, `DEMAND_SIDE_EVIDENCE_PLAN.md`)
    # -- an explicit, user-editable research plan (metric/unit/period,
    # preferred/fallback source classes, search query templates, derivation
    # path) proposed alongside the driver itself. Optional/additive: absence
    # never blocks anything downstream (see `evidence_strategy.py`).
    drivers: dict[str, dict[str, Any]]

    quantities: dict[str, dict[str, dict[str, Any]]]
    intensity_clamped_buckets: list[str]
    # Correctness-audit fix (2026-08-26, CEMENT_FORENSIC_AUDIT.md finding
    # A-1/C-3) -- buckets where a claimed-zero intensity was rejected for
    # lacking strong evidence; see agents.py::demand_quantification_node.
    zero_intensity_rejected_buckets: list[str]
    llm_call_failures: list[str]
    prices: dict[str, dict[str, Any]]

    usage_values: dict[str, dict[str, float]]
    country_totals: dict[str, float]
    total_market_value: float
    related_demand_country_totals: dict[str, float]
    related_demand_value: float
    unit_contract: dict[str, dict[str, Any]]
    unit_rejected_buckets: list[str]
    # Methodology fix (2026-08-26, CRUDE_OIL_METHODOLOGY_FIX) -- see
    # agents.py::_check_target_commodity_unit / calculate_node.
    target_commodity_unit_mismatch_flags: list[str]

    driver_facts: dict[str, dict[str, dict[str, Any]]]
    price_facts: dict[str, dict[str, Any]]
    # Evidence Retrieval Plans -- source_priority -> search_query -> source_URL
    # -> extracted_fact chain. Driver: one plan per bucket, executed per
    # country (a driver genuinely varies by geography). Intensity/price: one
    # plan + one execution per bucket, geography "global" (market-wide
    # constants in this pipeline's design).
    retrieval_plans: dict[str, dict[str, Any]]
    retrieval_attempts: dict[str, dict[str, list[dict[str, Any]]]]
    intensity_retrieval_plans: dict[str, dict[str, Any]]
    intensity_retrieval_attempts: dict[str, dict[str, list[dict[str, Any]]]]
    price_retrieval_plans: dict[str, dict[str, Any]]
    price_retrieval_attempts: dict[str, dict[str, list[dict[str, Any]]]]
    # Price-basis resolution (spec section 8/12) -- the priced unit may be one
    # step removed from final_demand_unit (e.g. price the FUEL, not the
    # vehicle-km); when so, an evidence-backed conversion factor bridges them.
    price_basis_records: dict[str, dict[str, Any]]
    pricing_conversion_factors: dict[str, dict[str, Any]]
    price_conversion_retrieval_attempts: dict[str, dict[str, list[dict[str, Any]]]]

    driver_magnitude_flags: list[dict[str, Any]]
    triangulation: list[dict[str, Any]]
    triangulation_large_gap_flags: list[str]
    # Phase 11 (DEMAND_SIDE_EVIDENCE_PLAN.md) -- broader, generic magnitude/
    # cross-source validation (driver/intensity/derived-quantity/price
    # magnitude, country-vs-parent, usage-vs-parent-category, year/period
    # consistency). Additive alongside `driver_magnitude_flags`/
    # `triangulation` above -- neither of those nodes' behavior changed.
    magnitude_validation_records: list[dict[str, Any]]
    magnitude_validation_flags: list[dict[str, Any]]

    validation: dict[str, Any]
    quality_flags: list[dict[str, str]]


def build_sizing_graph():
    g = StateGraph(UsageDemandState)
    g.add_node("demand_quantification", agents.demand_quantification_node)
    g.add_node("pricing", agents.pricing_node)
    g.add_node("calculate", agents.calculate_node)
    g.add_node("driver_magnitude_validation", magnitude.driver_magnitude_validation_node)
    g.add_node("triangulation", triangulation.triangulation_node)
    g.add_node("magnitude_cross_validation", magnitude_validation.magnitude_cross_validation_node)
    g.add_node("validation", agents.validation_node)

    g.set_entry_point("demand_quantification")
    g.add_edge("demand_quantification", "pricing")
    g.add_edge("pricing", "calculate")
    g.add_edge("calculate", "driver_magnitude_validation")
    g.add_edge("driver_magnitude_validation", "triangulation")
    g.add_edge("triangulation", "magnitude_cross_validation")
    g.add_edge("magnitude_cross_validation", "validation")
    g.add_edge("validation", END)
    return g.compile()


def sizing_init_state(
    approved_setup: dict[str, Any],
    approved_taxonomy: dict[str, Any],
    config: GenConfig,
    llm: GenLLM,
    emitter: Any | None = None,
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
        "scope_validation_flags": approved_taxonomy.get("scope_validation_flags", {}),
        "scope_taxonomy_aggregate": approved_taxonomy.get("scope_taxonomy_aggregate", {}),
        "scope_overrides": approved_taxonomy.get("scope_overrides", {}),
        "scope_amendment_text": approved_taxonomy.get("scope_amendment_text", ""),
        "missing_usage_flags": approved_taxonomy.get("missing_usage_flags", []),
        "coverage_pct": approved_taxonomy.get("coverage_pct"),
        "overlap_flags": approved_taxonomy.get("overlap_flags", []),
        "drivers": approved_taxonomy["drivers"],
        "emitter": emitter,
    }


def run_sizing_phase(
    approved_setup: dict[str, Any],
    approved_taxonomy: dict[str, Any],
    config: GenConfig,
    cache_path: Path | None = None,
    root: Path | None = None,
    run_id: str | None = None,
    source: str = "cli",
) -> UsageDemandState:
    """Unchanged sizing logic/order -- the only addition (runtime-hardening,
    2026-08-24) is an incremental `progress.py` recorder, injected into the
    initial state and updated once per top-level graph node via `graph.stream`
    (previously `graph.invoke`, which is otherwise behaviorally identical:
    same nodes, same edges, same final state -- `graph.stream(...,
    stream_mode="values")` simply yields that same cumulative state after
    each node instead of only at the very end, exactly the mechanism the
    backend SSE route already used for its live log). No node's own logic is
    touched."""
    from .progress import NODE_NAMES, ProgressRecorder
    from .quality import compute_provenance_quality_flags

    llm = GenLLM(config, cache_path=cache_path)
    graph = build_sizing_graph()
    market_name = approved_setup["market_name"]
    progress = ProgressRecorder(market_name, run_id=run_id, source=source, root=root)

    # Additive audit/observability layer (`me_engine.audit`) -- one
    # `AuditEmitter` per run, injected into the initial state exactly like
    # `progress` above, so every node function can pick it up via
    # `state.get("emitter")` (a no-op everywhere it's absent). Never raises:
    # if the audit package can't be constructed for any reason, sizing must
    # still proceed exactly as before.
    emitter = None
    try:
        from me_engine.audit.store import AuditEmitter

        emitter = AuditEmitter(run_id=run_id or market_name, approach="usage_based", market=market_name, root=root)
    except Exception:  # noqa: BLE001
        emitter = None

    init = sizing_init_state(approved_setup, approved_taxonomy, config, llm, emitter=emitter)
    init["progress"] = progress

    final: UsageDemandState = {}
    try:
        # Runtime-hardening fix (2026-08-25): `stream_mode="values"` yields
        # the UNTOUCHED initial state as item 0, then one item per node that
        # actually ran -- `zip(NODE_NAMES, graph.stream(...))` (the prior
        # version of this loop) stops at `len(NODE_NAMES)` items, which
        # silently DROPPED the true last yield (the state produced by
        # `validation_node`, the actual last node) and left `final` one node
        # short, missing `state["validation"]` entirely. Enumerating every
        # yielded item (never truncating) is what guarantees `final` always
        # ends up as the real final state, however many items are yielded.
        for i, step in enumerate(graph.stream(init, config={"recursion_limit": 20}, stream_mode="values")):
            final = step
            if i == 0:
                continue  # the untouched initial state -- no node has run yet
            node_idx = i - 1
            node_name = NODE_NAMES[node_idx] if node_idx < len(NODE_NAMES) else f"node_{node_idx}"
            progress.node_done(node_name)
            # Same loop `progress.node_done` already uses -- emit `stage_end`
            # HERE (once per node, after each node function returns) rather
            # than inside every node function itself, to avoid double-
            # emitting a stage boundary for the Phase 3 sizing nodes.
            if emitter is not None:
                emitter.emit(node_name, "stage_end", data={"node": node_name})
    except Exception as exc:  # noqa: BLE001 -- record, then re-raise unchanged
        progress.failed(exc)
        if emitter is not None:
            emitter.emit("sizing", "stage_end", status="failed", error=str(exc))
        raise

    # Never silently accept an incomplete final state. `validation_node` is
    # the LAST node in `build_sizing_graph` -- its absence means the stream
    # ended before the graph actually finished (a genuine LangGraph error or
    # interruption), and this run must be treated as FAILED, not written out
    # to disk as if it were a successful sizing result.
    #
    # Correctness-audit addendum (2026-08-26, `CEMENT_FORENSIC_AUDIT.md`) --
    # `correctness_gate.run_pre_write_gate` extends this same "never write a
    # broken result" discipline to a few more genuinely-broken-state checks
    # (see that module's own docstring for the hard-vs-soft split); its SOFT
    # flags are merged into `quality_flags` below, exactly like every other
    # quality warning already produced by `compute_provenance_quality_flags`.
    from .correctness_gate import run_pre_write_gate

    hard_failures, gate_soft_flags = run_pre_write_gate(final)
    if not final.get("validation"):
        hard_failures.insert(0, "'validation' is missing from the final state.")
    if hard_failures:
        err = RuntimeError(
            "Sizing graph produced a broken/incomplete final state -- treating this as a FAILED run, "
            "not a successful sizing result: " + "; ".join(hard_failures)
        )
        progress.failed(err)
        if emitter is not None:
            emitter.emit("sizing", "stage_end", status="failed", error=str(err))
        raise err

    flags = compute_provenance_quality_flags(final) + gate_soft_flags
    if flags:
        codes = ", ".join(f.get("code", "") for f in flags)
        final.setdefault("log", []).append(f"Quality check flagged this run for review: {codes}")
    final["quality_flags"] = flags
    progress.finished("completed")
    return final

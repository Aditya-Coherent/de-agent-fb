"""Regression test for the `graph.stream(..., stream_mode="values")`
truncation bug (fixed 2026-08-25): `stream_mode="values"` yields the
UNTOUCHED initial state as item 0, then one item per node -- for a 7-node
graph that's 8 yields, not 7. The prior code did
`zip(NODE_NAMES, graph.stream(...))`, which stops at `len(NODE_NAMES)` (7)
and silently drops the true last yield -- the state produced by
`validation_node`, the actual last node -- leaving the CLI's/backend's
written `02_sizing.json` with `"validation": null` and every plausibility/
evidence-quality/reported-market-view/residual field missing, even though
`total_market_value` (set earlier, by `calculate_node`) was present and
correct. This is exactly what silently happened to a real Cement regression
run.

Every node here is stubbed (no real LLM/network call) -- this test is about
`run_sizing_phase`'s STREAM-CONSUMPTION mechanics, not sizing logic. Real
node behavior is covered by each node's own test file (unchanged).

Run with: pytest tests/test_demand_usage_sizing_stream_regression.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from me_engine.demand_usage import agents, graph as graph_mod, magnitude, magnitude_validation, triangulation
from me_engine.generation.config import GenConfig


def _stub_config() -> GenConfig:
    return GenConfig(
        api_key="stub", base_url="https://example.invalid", model="stub-model", timeout=1.0,
        neon_url=None, enable_web=False, max_loop_iters=1, min_loop_iters=1,
        reward_threshold=0.9, per_region_shares=False,
    )


class _StubLLM:
    """Stands in for `GenLLM` -- no node stub below actually calls it, but
    `sizing_init_state` requires an `llm` object in the initial state."""


_VALIDATION_OUTPUT = {
    "validation": {
        "plausibility_pct": 77.0,
        "deterministic_pct": 88.0,
        "evidence_quality": {
            "observed_pct": 1.0, "derived_pct": 2.0,
            "source_found_unverified_pct": 3.0, "assumption_pct": 94.0,
        },
        "reported_market_view_usd_mn": 50.0,
        "residual_implied_usd_mn": 8.0,
    },
}


def _patch_all_nodes(monkeypatch, *, validation_output=_VALIDATION_OUTPUT):
    monkeypatch.setattr(agents, "demand_quantification_node", lambda state: {
        "quantities": {}, "driver_facts": {}, "intensity_clamped_buckets": [], "llm_call_failures": [],
        "retrieval_plans": {}, "retrieval_attempts": {}, "intensity_retrieval_plans": {},
        "intensity_retrieval_attempts": {},
    })
    monkeypatch.setattr(agents, "pricing_node", lambda state: {
        "prices": {}, "price_facts": {}, "price_retrieval_plans": {}, "price_retrieval_attempts": {},
        "price_basis_records": {}, "price_evidence_strategies": {}, "pricing_conversion_factors": {},
        "price_conversion_retrieval_attempts": {},
    })
    monkeypatch.setattr(agents, "calculate_node", lambda state: {
        "usage_values": {}, "country_totals": {}, "total_market_value": 42.0,
        "related_demand_country_totals": {}, "related_demand_value": 0.0,
        "unit_contract": {}, "unit_rejected_buckets": [],
    })
    monkeypatch.setattr(magnitude, "driver_magnitude_validation_node", lambda state: {"driver_magnitude_flags": []})
    monkeypatch.setattr(triangulation, "triangulation_node", lambda state: {"triangulation": [], "triangulation_large_gap_flags": []})
    monkeypatch.setattr(magnitude_validation, "magnitude_cross_validation_node", lambda state: {
        "magnitude_validation_records": [], "magnitude_validation_flags": [],
    })
    monkeypatch.setattr(agents, "validation_node", lambda state: dict(validation_output))


def _approved_setup() -> dict:
    return {
        "market_name": "Stub Market", "scope": "s", "geography": ["US", "China"],
        "base_year": 2025, "market_unit": "USD Mn", "physical_unit": "Units", "grounded": True,
    }


def _approved_taxonomy() -> dict:
    return {
        "market_name": "Stub Market",
        "usage_buckets": [{"name": "B", "description": "d", "bucket_category": "physical_consumption"}],
        "drivers": {"B": {"driver_name": "d", "unit": "u", "final_demand_unit": "u"}},
        "excluded_usage_buckets": [], "missing_usage_flags": [], "coverage_pct": 80.0, "overlap_flags": [],
    }


def test_run_sizing_phase_retains_true_final_state_including_validation(monkeypatch, tmp_path):
    """The core regression test: with ALL 7 nodes stubbed (so we control
    exactly what each contributes), the final state `run_sizing_phase`
    returns must contain everything the LAST node (`validation_node`)
    produced, not just what the second-to-last node produced."""
    _patch_all_nodes(monkeypatch)
    monkeypatch.setattr(graph_mod, "GenLLM", lambda config, cache_path=None: _StubLLM())

    final = graph_mod.run_sizing_phase(
        _approved_setup(), _approved_taxonomy(), _stub_config(), root=tmp_path,
    )

    assert final.get("validation") is not None, "validation_node's output must survive stream consumption"
    validation = final["validation"]
    assert validation["plausibility_pct"] == 77.0
    assert validation["deterministic_pct"] == 88.0
    assert validation["evidence_quality"]["observed_pct"] == 1.0
    assert validation["evidence_quality"]["derived_pct"] == 2.0
    assert validation["evidence_quality"]["source_found_unverified_pct"] == 3.0
    assert validation["evidence_quality"]["assumption_pct"] == 94.0
    assert validation["reported_market_view_usd_mn"] == 50.0
    assert validation["residual_implied_usd_mn"] == 8.0
    # Fields set by earlier nodes must ALSO still be present -- confirms this
    # is cumulative state, not just the last node's isolated return value.
    assert final.get("total_market_value") == 42.0
    assert "quality_flags" in final


def test_run_sizing_phase_marks_progress_completed_and_releases_no_lock_itself(monkeypatch, tmp_path):
    """`run_sizing_phase` itself only owns the progress recorder, not the
    lock (lock ownership is the CLI's/backend's job) -- confirms the
    progress file reaches status='completed' with the real final phase."""
    from me_engine.demand_usage.progress import progress_path
    import json

    _patch_all_nodes(monkeypatch)
    monkeypatch.setattr(graph_mod, "GenLLM", lambda config, cache_path=None: _StubLLM())

    graph_mod.run_sizing_phase(_approved_setup(), _approved_taxonomy(), _stub_config(), root=tmp_path)

    data = json.loads(progress_path("Stub Market", root=tmp_path).read_text(encoding="utf-8"))
    assert data["status"] == "completed"
    assert data["phase"] == "done"


def test_run_sizing_phase_raises_and_marks_failed_when_validation_node_produces_nothing(monkeypatch, tmp_path):
    """If the graph genuinely ends without `validation_node` contributing a
    `validation` key (e.g. it returned an empty dict), `run_sizing_phase`
    must raise -- never silently write out an incomplete result as if it
    were a successful run."""
    import json

    import pytest

    from me_engine.demand_usage.progress import progress_path

    _patch_all_nodes(monkeypatch, validation_output={})  # simulates the bug's symptom directly
    monkeypatch.setattr(graph_mod, "GenLLM", lambda config, cache_path=None: _StubLLM())

    with pytest.raises(RuntimeError, match="validation"):
        graph_mod.run_sizing_phase(_approved_setup(), _approved_taxonomy(), _stub_config(), root=tmp_path)

    data = json.loads(progress_path("Stub Market", root=tmp_path).read_text(encoding="utf-8"))
    assert data["status"] == "failed"
    assert data["errors"]


def test_stream_mode_values_yields_initial_state_plus_one_per_node():
    """Documents the exact LangGraph behavior that caused the bug: for an
    N-node linear graph, `stream(..., stream_mode='values')` yields N+1
    items (the untouched initial state, then one per node) -- NOT N items.
    A `zip(fixed_length_N_list, stream(...))` therefore always drops the
    true last item."""
    from langgraph.graph import END, StateGraph
    from typing import TypedDict

    class _S(TypedDict, total=False):
        x: int

    def a(state):
        return {"x": state.get("x", 0) + 1}

    def b(state):
        return {"x": state.get("x", 0) + 10}

    def c(state):
        return {"x": state.get("x", 0) + 100}

    g = StateGraph(_S)
    g.add_node("a", a)
    g.add_node("b", b)
    g.add_node("c", c)
    g.set_entry_point("a")
    g.add_edge("a", "b")
    g.add_edge("b", "c")
    g.add_edge("c", END)
    compiled = g.compile()

    steps = list(compiled.stream({"x": 0}, stream_mode="values"))
    assert len(steps) == 4  # 3 nodes + 1 initial-state yield
    assert steps[0] == {"x": 0}       # untouched initial state
    assert steps[-1] == {"x": 111}    # TRUE final state -- a length-3 zip would have dropped this

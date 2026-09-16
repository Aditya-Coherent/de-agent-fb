"""Pure unit tests for the deterministic Major Use Case rollup
(`me_engine.demand_usage_india.aggregation.rollup_major_use_cases`).

No LLM, no network, no LangGraph -- this function only regroups
already-computed `usage_values`/`total_market_value` numbers by each
bucket's `major_use_case` tag.

Run with: pytest tests/test_demand_usage_india_aggregation.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from me_engine.demand_usage_india import aggregation


def _state(**overrides) -> dict:
    base = {
        "usage_buckets": [
            {"name": "Household Pickle Preservation", "major_use_case": "Food Preservation"},
            {"name": "Restaurant Pickle Preservation", "major_use_case": "Food Preservation"},
            {"name": "Industrial Cleaning", "major_use_case": "Industrial Applications"},
        ],
        "countries": ["India"],
        "usage_values": {
            "Household Pickle Preservation": {"India": 40.0},
            "Restaurant Pickle Preservation": {"India": 20.0},
            "Industrial Cleaning": {"India": 15.0},
        },
        "total_market_value": 75.0,
    }
    base.update(overrides)
    return base


def test_rollup_groups_buckets_by_major_use_case():
    out = aggregation.rollup_major_use_cases(_state())
    assert out["major_use_case_totals"]["Food Preservation"] == 60.0
    assert out["major_use_case_totals"]["Industrial Applications"] == 15.0
    assert out["major_use_case_actual_use_case_counts"]["Food Preservation"] == 2
    assert out["major_use_case_actual_use_case_counts"]["Industrial Applications"] == 1


def test_rollup_totals_sum_to_total_market_value():
    out = aggregation.rollup_major_use_cases(_state())
    assert sum(out["major_use_case_totals"].values()) == out["india_market_total"]
    assert out["india_market_total"] == 75.0
    assert out["rollup_flags"] == []


def test_rollup_flags_a_genuine_mismatch():
    """Should never happen in practice (these are the same numbers
    `calculate_node` already summed, just regrouped) -- but if `usage_values`
    and `total_market_value` were ever inconsistent, the rollup must flag it
    rather than silently trust either number."""
    state = _state(total_market_value=999.0)
    out = aggregation.rollup_major_use_cases(state)
    assert out["rollup_flags"]
    assert out["rollup_flags"][0]["code"] == "major_use_case_rollup_mismatch"


def test_rollup_handles_bucket_with_no_major_use_case_tag_gracefully():
    state = _state(usage_buckets=[{"name": "Household Pickle Preservation"}],
                    usage_values={"Household Pickle Preservation": {"India": 10.0}},
                    total_market_value=10.0)
    out = aggregation.rollup_major_use_cases(state)
    assert out["major_use_case_totals"]["(unspecified)"] == 10.0
    assert out["rollup_flags"] == []


def test_rollup_handles_empty_state():
    out = aggregation.rollup_major_use_cases({})
    assert out["major_use_case_totals"] == {}
    assert out["india_market_total"] == 0.0
    assert out["rollup_flags"] == []

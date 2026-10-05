"""Deterministic Major Use Case rollup (India-only) -- pure arithmetic, no
LLM call, run AFTER Phase 3's sizing graph finishes.

Phase 3 (`me_engine.demandUseCaseCountry_approach2B.graph.build_sizing_graph`, reused
unchanged) already computes `usage_values[bucket_name][country] -> value`
and `total_market_value` over "a list of buckets" -- here, the flattened
Actual Use Cases. This module only REGROUPS those already-computed numbers
by each bucket's `major_use_case` tag (looked up from `usage_buckets`); it
never recomputes or adjusts a single quantity, price, or value.

Since India sizing always fixes `countries = ["India"]`, the India Market
Total is exactly `total_market_value` (single-country identity) -- the same
number `calculate_node` already produced, just regrouped/relabeled here.
"""
from __future__ import annotations

from typing import Any

_ROLLUP_TOLERANCE_USD_MN = 0.01  # float rounding only -- these are the SAME numbers, just regrouped


def rollup_major_use_cases(state: dict[str, Any]) -> dict[str, Any]:
    """Returns `{"major_use_case_totals": {...}, "india_market_total": float,
    "major_use_case_actual_use_case_counts": {...}, "rollup_flags": [...]}`.

    `state` is the final Phase 3 sizing state (same shape
    `run_sizing_phase`/`build_sizing_graph` produce) -- `usage_buckets` must
    carry each bucket's `major_use_case` tag (see `taxonomy.approve_taxonomy`)."""
    buckets = state.get("usage_buckets", []) or []
    usage_values = state.get("usage_values", {}) or {}
    countries = state.get("countries", []) or []
    total_market_value = float(state.get("total_market_value") or 0.0)

    major_use_case_totals: dict[str, float] = {}
    major_use_case_counts: dict[str, int] = {}
    for b in buckets:
        major = b.get("major_use_case") or "(unspecified)"
        bucket_total = sum(float(usage_values.get(b["name"], {}).get(c) or 0.0) for c in countries)
        major_use_case_totals[major] = major_use_case_totals.get(major, 0.0) + bucket_total
        major_use_case_counts[major] = major_use_case_counts.get(major, 0) + 1

    rollup_flags: list[dict[str, str]] = []
    rollup_sum = sum(major_use_case_totals.values())
    if abs(rollup_sum - total_market_value) > max(_ROLLUP_TOLERANCE_USD_MN, abs(total_market_value) * 1e-6):
        # Should never happen -- these are the SAME per-bucket numbers
        # `calculate_node` already summed into `total_market_value`, just
        # regrouped by a different key. Surfaced as a quality flag rather
        # than silently trusted, per this pipeline's own discipline.
        rollup_flags.append({
            "code": "major_use_case_rollup_mismatch",
            "severity": "critical",
            "message": (
                f"Major Use Case totals sum to ${rollup_sum:,.2f}M but total_market_value is "
                f"${total_market_value:,.2f}M -- the rollup should be an exact regrouping of the "
                "same numbers; investigate before trusting this run."
            ),
        })

    return {
        "major_use_case_totals": major_use_case_totals,
        "major_use_case_actual_use_case_counts": major_use_case_counts,
        # Single-country identity -- India sizing always fixes
        # countries=["India"], so the India Market Total IS the already-
        # computed total_market_value, never a fresh sum.
        "india_market_total": total_market_value,
        "rollup_flags": rollup_flags,
    }

"""Pre-write correctness gate for the Phase 3 (sizing) result -- correctness-
audit addendum (2026-08-26, `CEMENT_FORENSIC_AUDIT.md`).

Runs AFTER `validation_node` (the last graph node) but BEFORE `02_sizing.json`/
`.xlsx` are ever written. Two tiers, matching this pipeline's existing
"flag loudly, never silently hide, never silently block a legitimately-
flagged run" discipline:

  - HARD failures (`hard_failures`): the result is genuinely internally
    BROKEN, not just methodologically weak -- `validation` missing, a
    NaN/None total despite buckets being present, or a triangulation
    benchmark value that is still non-finite after normalization. These are
    the ONLY conditions that block the write -- `run_sizing_phase`/the
    backend route treat a non-empty `hard_failures` exactly like the
    pre-existing "validation missing" guard: mark the run FAILED, write a
    partial report, raise, never write `02_sizing.json` as if it were a
    successful result.
  - SOFT flags (`soft_flags`): genuine methodology-quality findings (zero-
    intensity rejections, residual double-conversion risk, unresolved
    severe scope/taxonomy overlap) -- these are ADDED to `quality_flags`
    (the SAME mechanism every other quality warning in this pipeline
    already uses: `implausible_total`, `high_assumption_share`, etc.) so
    they stay visible in `02_sizing.json`/`SIZE_RUN_RESULT.md`, but never
    block the write.
"""
from __future__ import annotations

import math
from typing import Any

from . import units


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def _double_conversion_risk(state: dict[str, Any]) -> list[str]:
    """A bucket is at risk of the exact double-conversion bug this audit
    found (finding A-3/C) when its `consumption_unit` describes an
    intensity conversion AND its `final_demand_unit` STILL equals the raw
    driver's own unit (i.e. `units.reconcile_final_demand_unit` did not, or
    could not, correct it) while `pricing_node`'s own price-basis record
    independently needed a bridging conversion factor -- the signature of a
    second, redundant conversion being applied on top of the first."""
    risky: list[str] = []
    drivers = state.get("drivers") or {}
    price_basis_records = state.get("price_basis_records") or {}
    for bucket_name, driver in drivers.items():
        consumption_unit = driver.get("consumption_unit") or ""
        if not consumption_unit:
            continue
        target = units.intensity_target_unit(consumption_unit)
        if not target:
            continue
        basis = price_basis_records.get(bucket_name) or {}
        if basis.get("same_as_final_unit"):
            continue  # pricing needed no bridge at all -- no risk regardless of driver labeling
        final_unit = driver.get("final_demand_unit") or ""
        if _norm(final_unit) == _norm(driver.get("unit") or "") and _norm(final_unit) != _norm(target):
            risky.append(bucket_name)
    return risky


def run_pre_write_gate(state: dict[str, Any]) -> tuple[list[str], list[dict[str, str]]]:
    """Returns (hard_failures, soft_flags). Pure function -- never mutates
    `state`, never raises itself (the caller decides what a non-empty
    `hard_failures` means)."""
    hard_failures: list[str] = []
    soft_flags: list[dict[str, str]] = []

    if not state.get("validation"):
        hard_failures.append(
            "'validation' is missing from the final state -- the run did not reach validation_node "
            "(the last graph node)."
        )

    total = state.get("total_market_value")
    buckets = state.get("usage_buckets") or []
    if buckets and (total is None or (isinstance(total, float) and math.isnan(total))):
        hard_failures.append("total_market_value is missing/NaN despite usage_buckets being present.")

    for b in state.get("triangulation") or []:
        val = b.get("value")
        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            hard_failures.append(
                f"triangulation benchmark '{b.get('category')}' has a non-finite value after normalization."
            )

    zero_rejected = state.get("zero_intensity_rejected_buckets") or []
    if zero_rejected:
        soft_flags.append({
            "code": "gate_zero_intensity_rejected", "severity": "info",
            "message": f"Pre-write gate: {len(zero_rejected)} bucket(s) had a claimed-zero intensity "
                       f"rejected and fell back to an explicit assumption: {zero_rejected}.",
        })

    risky = _double_conversion_risk(state)
    if risky:
        soft_flags.append({
            "code": "gate_double_conversion_risk", "severity": "warning",
            "message": f"Pre-write gate: {len(risky)} bucket(s) still show a possible double physical-"
                       f"unit conversion (an intensity conversion plus a separate pricing conversion to "
                       f"a unit the intensity step already reached): {risky}. Values NOT changed -- "
                       "review that driver's final_demand_unit/consumption_unit pairing.",
        })

    agg = state.get("scope_taxonomy_aggregate") or {}
    if agg.get("likely_scope_too_narrow") and float(agg.get("flagged_ratio") or 0) >= 0.5:
        soft_flags.append({
            "code": "gate_unresolved_severe_scope_overlap", "severity": "warning",
            "message": f"Pre-write gate: {agg.get('flagged_count')}/{agg.get('total_buckets')} usage "
                       "bucket(s) are flagged out-of-scope vs. the approved Market Setup scope "
                       f"({float(agg.get('flagged_ratio') or 0) * 100:.0f}%) -- severe, unresolved "
                       "scope/taxonomy tension.",
        })

    return hard_failures, soft_flags

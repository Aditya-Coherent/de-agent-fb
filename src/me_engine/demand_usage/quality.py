"""Sanity flags for a finished usage-based demand-side run.

Same philosophy as `me_engine.generation.quality`: surface the warning signs
a human reviewer would have to notice by reading the log line-by-line, so a
weak or ungrounded run looks visibly weak instead of looking like a normal,
confident number. Not a guarantee of correctness -- absence of flags just
means nothing on this checklist tripped.
"""
from __future__ import annotations

from typing import Any

_MIN_PLAUSIBLE_USD_MN = 10.0
_MAX_PLAUSIBLE_USD_MN = 5_000_000.0
_LOW_PLAUSIBILITY = 0.60
_HIGH_ASSUMPTION_SHARE = 0.5
_LARGE_REFERENCE_DELTA_PCT = 40.0
_LOW_OBSERVED_SHARE_PCT = 30.0


def compute_provenance_quality_flags(state: dict[str, Any]) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []

    total = state.get("total_market_value")
    if total is not None and not (_MIN_PLAUSIBLE_USD_MN <= float(total) <= _MAX_PLAUSIBLE_USD_MN):
        flags.append({
            "code": "implausible_total",
            "severity": "critical",
            "message": f"Bottom-up global total (${float(total):,.0f}M) is outside a plausible "
                       "range for a global market -- verify before trusting this run.",
        })

    excluded = state.get("excluded_usage_buckets") or []
    if excluded:
        flags.append({
            "code": "excluded_non_physical_usage",
            "severity": "info",
            "message": f"{len(excluded)} discovered usage bucket(s) were excluded before pricing "
                       f"because they describe financial/inventory activity, not genuine physical "
                       f"consumption: {[e.get('usage_name', e.get('name', '')) for e in excluded]}. "
                       "Kept in the audit trail (Excluded Usages sheet), not deleted.",
        })

    quantities = state.get("quantities", {}) or {}
    counts = {"observed": 0, "derived": 0, "assumption": 0}
    for per_country in quantities.values():
        for entry in per_country.values():
            prov = entry.get("provenance", "assumption")
            counts[prov] = counts.get(prov, 0) + 1
    total_entries = sum(counts.values())
    if total_entries and counts.get("assumption", 0) / total_entries >= _HIGH_ASSUMPTION_SHARE:
        flags.append({
            "code": "high_assumption_share",
            "severity": "warning",
            "message": f"{counts['assumption']}/{total_entries} country/bucket quantities are "
                       "unlabelled assumptions (no real observed or derived figure found) -- "
                       "this run leans heavily on estimation.",
        })
    if total_entries and counts.get("observed", 0) == 0:
        flags.append({
            "code": "no_observed_data",
            "severity": "warning",
            "message": "No country-level figure in this run was directly cited/observed in "
                       "evidence -- every quantity is derived or assumed.",
        })

    validation = state.get("validation", {}) or {}
    plaus = validation.get("plausibility_score")
    if plaus is not None and float(plaus) < _LOW_PLAUSIBILITY:
        flags.append({
            "code": "low_plausibility",
            "severity": "warning",
            "message": f"Validation plausibility ({float(plaus):.0%}) is below the usual bar.",
        })

    if validation.get("overlap_flags"):
        flags.append({
            "code": "usage_bucket_overlap",
            "severity": "warning",
            "message": f"Possible double-counting between usage buckets: "
                       f"{'; '.join(validation['overlap_flags'][:3])}",
        })

    if validation.get("dominant_bucket_flags"):
        flags.append({
            "code": "dominant_bucket_unit_mismatch_risk",
            "severity": "critical",
            "message": f"One usage bucket dominates the total, a symptom of a driver/price unit "
                       f"mismatch: {'; '.join(validation['dominant_bucket_flags'][:2])}",
        })

    if validation.get("outlier_flags"):
        flags.append({
            "code": "country_cell_outlier",
            "severity": "critical",
            "message": f"At least one country's value within a bucket is a wild outlier vs. its "
                       f"peers -- likely a compounding driver/intensity assumption error: "
                       f"{'; '.join(validation['outlier_flags'][:2])}",
        })

    if validation.get("llm_call_failures"):
        flags.append({
            "code": "llm_call_failure",
            "severity": "critical",
            "message": f"{len(validation['llm_call_failures'])} value(s) in this run are NOT genuine "
                       f"estimates -- the underlying LLM call itself failed (network/auth/rate-limit/"
                       f"TLS) and silently defaulted to 0/$1: {validation['llm_call_failures'][:6]}. "
                       "This run should be re-run, not trusted as-is.",
        })

    if validation.get("intensity_clamped_buckets"):
        flags.append({
            "code": "intensity_fraction_clamped",
            "severity": "critical",
            "message": f"An adjacent-industry driver's share factor was out of [0,1] bounds and "
                       f"had to be clamped for: {validation['intensity_clamped_buckets']} -- that "
                       "bucket's driver methodology likely needs a market-specific driver instead.",
        })

    coverage_pct = validation.get("coverage_pct")
    threshold = validation.get("coverage_threshold_pct")
    if isinstance(coverage_pct, (int, float)) and isinstance(threshold, (int, float)) and coverage_pct < threshold:
        residual = validation.get("residual_implied_usd_mn")
        flags.append({
            "code": "low_usage_coverage",
            "severity": "warning",
            "message": f"Estimated usage coverage ({coverage_pct:.1f}%, the pipeline's own bounded "
                       f"estimate -- NOT a verified measurement of the real market) is below the "
                       f"configured threshold ({threshold:.0f}%) -- Included Demand "
                       f"(${validation.get('included_demand_usd_mn', 0):,.1f}M) likely understates the "
                       f"real market. Residual/Uncovered is reported separately "
                       + (f"(${residual:,.1f}M, implied by the coverage gap, NOT built from evidence, "
                          f"NOT distributed into any bucket) " if residual is not None else "")
                       + "rather than silently inflating any existing bucket.",
        })

    if validation.get("unit_contract_violations"):
        flags.append({
            "code": "unit_contract_violation",
            "severity": "critical",
            "message": f"{len(validation['unit_rejected_buckets'])} bucket(s) were EXCLUDED from "
                       f"calculation because their unit string could not be unambiguously "
                       f"parsed/normalized (compound unit, or an explicit driver/intensity/"
                       f"final-unit period conflict) -- never assigned a fallback price or "
                       f"quantity: {validation['unit_contract_violations']}",
        })

    if validation.get("missing_category_flags"):
        flags.append({
            "code": "missing_usage_category",
            "severity": "warning",
            "message": f"Usage coverage review flagged possibly missing major physical "
                       f"application(s), evaluated before calculation (flag only -- not added "
                       f"to this run's total): {'; '.join(validation['missing_category_flags'][:3])}",
        })

    if not validation.get("arithmetic_ok", True):
        flags.append({
            "code": "arithmetic_mismatch",
            "severity": "critical",
            "message": "Country totals did not match the re-summed usage values -- a "
                       "calculation bug, not a data-quality issue.",
        })

    delta = validation.get("reference_delta_pct")
    if delta is not None and abs(float(delta)) >= _LARGE_REFERENCE_DELTA_PCT:
        flags.append({
            "code": "large_desk_research_divergence",
            "severity": "info",
            "message": f"This bottom-up total differs from the Desk Research reference by "
                       f"{float(delta):+.1f}% -- informational only, worth investigating why, "
                       "not evidence that either number is wrong.",
        })

    # --- V2: evidence-quality-oriented flags ---
    eq = validation.get("evidence_quality") or {}
    observed_pct = eq.get("observed_pct")
    if observed_pct is not None and float(observed_pct) < _LOW_OBSERVED_SHARE_PCT:
        flags.append({
            "code": "low_observed_share",
            "severity": "warning",
            "message": f"Only {float(observed_pct):.0f}% of driver/price inputs are directly "
                       "observed evidence -- this run leans heavily on derived allocations and "
                       "assumptions; treat the total as directional, not defensible on its own.",
        })

    if validation.get("price_plausibility_flags"):
        flags.append({
            "code": "price_implausible",
            "severity": "critical",
            "message": f"Price plausibility check failed: "
                       f"{'; '.join(validation['price_plausibility_flags'][:2])}",
        })

    if validation.get("unit_mismatch_flags"):
        flags.append({
            "code": "unit_mismatch",
            "severity": "critical",
            "message": f"Demand-quantity unit does not match the priced unit for at least one "
                       f"bucket: {'; '.join(validation['unit_mismatch_flags'][:2])}",
        })

    if validation.get("stale_source_flags"):
        flags.append({
            "code": "stale_evidence_source",
            "severity": "info",
            "message": f"{len(validation['stale_source_flags'])} driver evidence source(s) "
                       "predate 2022 -- informational, worth a fresher search if this market "
                       "moves quickly.",
        })

    # --- V3: driver-magnitude cross-check + expanded external triangulation ---
    dm_flags = validation.get("driver_magnitude_flags") or state.get("driver_magnitude_flags") or []
    if dm_flags:
        flags.append({
            "code": "driver_magnitude_implausible",
            "severity": "critical",
            "message": f"{len(dm_flags)} driver(s) diverge from an independent reference figure by "
                       f"{dm_flags[0].get('ratio', '3+')}x or more -- flagged for review, the original "
                       f"value/source/note are unchanged: {[f['bucket'] for f in dm_flags]}",
        })

    tri_gap_flags = validation.get("triangulation_large_gap_flags") or state.get("triangulation_large_gap_flags") or []
    if tri_gap_flags:
        flags.append({
            "code": "triangulation_large_gap",
            "severity": "info",
            "message": f"Large gap(s) vs. independent external benchmark(s) -- informational only, "
                       f"never used to adjust the bottom-up total: {'; '.join(tri_gap_flags[:2])}",
        })

    return flags

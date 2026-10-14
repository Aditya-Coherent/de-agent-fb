"""Driver-magnitude validation -- generic, market-independent sanity check
(spec section 16: "Add an evidence-based sanity check for driver magnitude").

For each usage bucket, runs a SECOND, independent evidence search for the
bucket's own driver total and compares it against the value already built
in `demand_quantification_node`. This is a code-level, deterministic
comparison of two evidence-derived numbers -- not an LLM opinion. Flag-only:
the original value, its source, and its provenance note are NEVER changed
here (spec: "Do not silently change the value. The original value, source,
and validation reason must remain visible."). No market-specific rules --
the same ratio check applies to barrels, tonnes, units, kWh, or anything
else, which is what makes it reusable across markets.
"""
from __future__ import annotations

from typing import Any

from me_engine.generation.llm import GenLLM

from . import evidence
from .web_search import gather

# Wide, deliberately -- this catches genuine scale errors (a unit/period
# mixup, an order-of-magnitude blowup), not routine source-to-source
# variance. A driver figure that's merely "different" between two
# independent, legitimate sources is normal; 3x+ apart usually means one of
# them is measuring something else.
_MAGNITUDE_RATIO_THRESHOLD = 3.0


def driver_magnitude_validation_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg = state["config"]
    llm: GenLLM = state["llm"]
    market = state["market_name"]
    log = state.setdefault("log", [])
    buckets = state.get("usage_buckets", [])
    drivers = state.get("drivers", {})
    quantities = state.get("quantities", {})

    flags: list[dict[str, Any]] = []
    for b in buckets:
        name = b["name"]
        driver = drivers.get(name, {})
        driver_name = driver.get("driver_name", name)
        unit = driver.get("unit", "units")
        per_country = quantities.get(name, {})
        if not per_country:
            continue

        # Comparison basis: the total ALREADY BUILT for this bucket, in its
        # own final-demand unit -- not a re-derivation, just what this run
        # actually used, so the check is honest about what it's validating.
        used_total = sum(v.get("value", 0.0) for v in per_country.values())
        if used_total <= 0:
            continue

        snippets: list[str] = []
        if cfg.enable_web:
            try:
                snippets = gather(
                    [f"global {market} {driver_name} total {unit}", f"worldwide {driver_name} {unit} statistics"],
                    per_query=4, cap=8,
                )
            except Exception as exc:  # noqa: BLE001
                log.append(f"[driver_magnitude] could not fetch reference evidence for '{name}': {exc}")
                continue

        ref_fact = evidence.extract_structured_fact(
            llm, snippets, unit=unit,
            context=(
                f"Independent reference figure for global '{driver_name}' ({unit}) -- used ONLY to "
                f"sanity-check the MAGNITUDE of the value already built for usage bucket '{name}' in "
                f"{market}; do not consider it a replacement value"
            ),
        )
        if ref_fact is None or not ref_fact.get("value") or ref_fact["value"] <= 0:
            continue  # no independent reference available -- nothing to compare, not a failure

        ratio = max(used_total, ref_fact["value"]) / max(min(used_total, ref_fact["value"]), 1e-9)
        if ratio >= _MAGNITUDE_RATIO_THRESHOLD:
            note = (
                f"'{name}' driver total ({used_total:,.2f} {unit}) differs from an independent "
                f"reference figure ({ref_fact['value']:,.2f} {unit}, source: {ref_fact.get('source') or 'n/a'}) "
                f"by {ratio:.1f}x -- flagged for review, value NOT changed."
            )
            flags.append({
                "bucket": name,
                "used_value": round(used_total, 4),
                "used_unit": unit,
                "reference_value": ref_fact["value"],
                "reference_source": ref_fact.get("source", ""),
                "reference_citation": ref_fact.get("citation", ""),
                "ratio": round(ratio, 2),
                "code": "driver_magnitude_implausible",
                "note": note,
            })
            log.append(f"[driver_magnitude] {note}")

    log.append(f"Driver magnitude review: {len(flags)} bucket(s) flagged out of {len(buckets)}")
    return {"driver_magnitude_flags": flags}

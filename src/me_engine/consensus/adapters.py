"""sigma_B (log-scale uncertainty of a bottom-up estimate) from each approach's
own evidence quality. Constants are fixed a priori -- set before any
triangulated result was compared with a reference -- and not tuned.
"""
from __future__ import annotations

import math
from typing import Any

SIGMA_MAX = 1.0


def sigma_signal_model(result: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """2A signal model (07_result.json): units uncertainty from the share of units
    resting on hard (trade / mass-balance / web) anchors, price uncertainty from
    how the price was set."""
    anchored = sum(c["units"] for c in result["countries"] if c["basis"].endswith("anchor"))
    coverage = anchored / result["global_units"] if result["global_units"] else 0.0
    sigma_units = 0.10 + 0.35 * (1 - coverage)
    price = result["price"]
    level = price.get("level") or {}
    if level.get("chosen_level") in ("vendor", "consumer"):
        sigma_price, price_basis = 0.12, "corroborated price-level evidence"
    elif price.get("curve"):
        sigma_price, price_basis = 0.25, "customs price curve, price level unconfirmed"
    elif (price.get("global_fallback") or {}).get("from_evidence"):
        sigma_price, price_basis = 0.30, "single global price from evidence"
    else:
        sigma_price, price_basis = 0.35, "single global LLM price"
    sigma = min(SIGMA_MAX, math.hypot(sigma_units, sigma_price))
    return sigma, {"anchored_unit_share": round(coverage, 3), "sigma_units": round(sigma_units, 3),
                   "sigma_price": sigma_price, "price_basis": price_basis}


def sigma_evidence_mix(section: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """Original 2A / 2B results: from the verified / derived / unverified /
    assumption mix of their inputs, plus critical quality flags."""
    eq = section.get("evidence_quality_pct") or section.get("evidence_quality") or {}
    assumption = float(eq.get("assumption") or eq.get("assumption_pct") or 0.0)
    unverified = float(eq.get("source_found_unverified") or eq.get("source_found_unverified_pct") or 0.0)
    critical = sum(1 for f in section.get("quality_flags") or [] if str(f).startswith("[critical]")
                   or (isinstance(f, dict) and f.get("severity") == "critical"))
    sigma = min(SIGMA_MAX, 0.15 + 0.6 * (assumption + 0.5 * unverified) / 100 + 0.1 * critical)
    return sigma, {"assumption_pct": assumption, "unverified_pct": unverified, "critical_flags": critical}

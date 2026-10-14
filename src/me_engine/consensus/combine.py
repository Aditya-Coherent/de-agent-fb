"""Inverse-variance blend of the bottom-up estimate and the published consensus."""
from __future__ import annotations

import math
from typing import Any

DISAGREEMENT_FLAG = 0.25  # |log(B/P)| above this -> flag for review


def blend(bottom_up: float, sigma_b: float, published: dict[str, Any] | None) -> dict[str, Any]:
    """Log-space inverse-variance weighting. Without a published consensus the
    bottom-up estimate stands alone."""
    if not published:
        return {"value_usd_mn": bottom_up, "weight_bottom_up": 1.0, "weight_published": 0.0,
                "sigma": sigma_b, "flag": "no published sizes found -- bottom-up only"}
    p, sigma_p = published["value_usd_mn"], published["sigma"]
    wb, wp = 1 / sigma_b ** 2, 1 / sigma_p ** 2
    value = math.exp((wb * math.log(bottom_up) + wp * math.log(p)) / (wb + wp))
    gap = math.log(bottom_up / p)
    return {"value_usd_mn": value, "weight_bottom_up": wb / (wb + wp), "weight_published": wp / (wb + wp),
            "sigma": (wb + wp) ** -0.5, "bottom_up_vs_published_pct": round(100 * (bottom_up / p - 1), 1),
            "flag": (f"bottom-up and published differ by {100 * (bottom_up / p - 1):+.0f}% -- review"
                     if abs(gap) > DISAGREEMENT_FLAG else None)}


def design_blend(bottom_up: float, published_values: list[float], weight_bottom_up: float = 0.5) -> dict[str, Any]:
    """Publisher rule of src/newinfo/system.md §12:
        C = median of published sizes (same scope, year, basis)
        P = w * B + (1 - w) * C,   k = P / B  (applied to price, never to quantity)
    Without published sizes P = B and k = 1. The raw bottom-up B is always
    returned beside P."""
    import statistics

    if not published_values or bottom_up <= 0:
        return {"method": "design", "bottom_up_usd_mn": bottom_up, "published_median_usd_mn": None,
                "value_usd_mn": bottom_up, "k": 1.0, "weight_bottom_up": 1.0, "weight_published": 0.0,
                "n_published": 0, "flag": "no published sizes found -- bottom-up only"}
    c = statistics.median(published_values)
    w = min(1.0, max(0.0, weight_bottom_up))
    p = w * bottom_up + (1 - w) * c
    return {"method": "design", "bottom_up_usd_mn": bottom_up, "published_median_usd_mn": c, "value_usd_mn": p,
            "k": p / bottom_up, "weight_bottom_up": w, "weight_published": 1 - w, "n_published": len(published_values),
            "bottom_up_vs_published_pct": round(100 * (bottom_up / c - 1), 1), "flag": None}

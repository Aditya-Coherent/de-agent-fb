"""User-selectable result caps — how many companies a run aims for.

A cap scales discovery breadth (volume prompts + widen loops), the discover/enrich
ceilings, and the export-row ceiling together, so the user can trade depth for speed.
Actual exported counts still depend on how many real companies pass quality.
"""
from __future__ import annotations

from typing import Any

# key -> {label, approx, time, discover, enrich, export, volume, widen}
CAP_TIERS: dict[str, dict[str, Any]] = {
    # Default for run_query.py: a ~10-15 min run that still keeps every web-grounded major player
    # (majors are injected separately and are never dropped by the cap). Discovery stops early
    # once batches saturate (<3% new companies) and only one widen pass is allowed.
    "fast": {
        "label": "Fast",
        "approx": "~60-120 companies (all majors kept)",
        "time": "~10-15 min (time-budgeted)",
        "discover": 150, "enrich": 150, "export": 150, "volume": 20, "widen": 1,
        "discovery": {
            "yield_stop_threshold": 0.05,
            "low_yield_consecutive": 6,
            "discovery_yield_min_unique": 80,
            "target_unique_companies": 150,
            "widen_if_unique_lt": 120,
            "discovery_force_widen_below": 50,
            "discovery_batch_min_growth": 0.03,
            "discovery_batch_stop_min_unique": 40,
            "phase1_smoke_max_prompts": 0,
            "phase1_global_smoke_max_prompts": 0,
        },
    },
    "focused": {
        "label": "Focused",
        "approx": "~25-50 companies",
        "time": "fastest (~10-15 min)",
        "discover": 120, "enrich": 120, "export": 60, "volume": 20, "widen": 2,
    },
    "standard": {
        "label": "Standard",
        "approx": "~50-90 companies",
        "time": "~15-25 min",
        "discover": 220, "enrich": 220, "export": 120, "volume": 32, "widen": 3,
    },
    "broad": {
        "label": "Broad",
        "approx": "~90-160 companies",
        "time": "~25-40 min",
        "discover": 340, "enrich": 340, "export": 190, "volume": 46, "widen": 4,
    },
    "maximum": {
        "label": "Maximum",
        "approx": "~150-280 companies",
        "time": "slowest (~40-70 min)",
        "discover": 500, "enrich": 500, "export": 300, "volume": 60, "widen": 5,
        # Old exhaustive behaviour: no time budget by default, no saturation stop.
        "exhaustive": True,
    },
}

DEFAULT_CAP = "broad"
CLI_DEFAULT_CAP = "fast"  # run_query.py default when --cap is not given


def cap_keys() -> list[str]:
    return list(CAP_TIERS.keys())


def cap_describe(key: str) -> str:
    t = CAP_TIERS.get(key)
    if not t:
        return ""
    return f"{t['label']}: {t['approx']} - {t['time']}"


def apply_cap(settings: Any, cap_key: str | None) -> Any:
    """Return settings with discover/enrich/export ceilings + discovery breadth set by the cap."""
    t = CAP_TIERS.get((cap_key or "").strip().lower())
    if not t:
        return settings
    update = {
        "pipeline_discover_max": t["discover"],
        "pipeline_global_discover_max": t["discover"],
        "pipeline_enrich_max": t["enrich"],
        "pipeline_global_enrich_max": t["enrich"],
        "pipeline_export_max_rows": t["export"],
        "pipeline_global_export_max_rows": t["export"],
        "volume_prompt_count": t["volume"],
        "pipeline_global_volume_prompt_count": t["volume"],
        "widen_loop_max": t["widen"],
    }
    for k, v in (t.get("discovery") or {}).items():
        if hasattr(settings, k):
            update[k] = v
    return settings.model_copy(update=update)


def is_exhaustive(cap_key: str | None) -> bool:
    t = CAP_TIERS.get((cap_key or "").strip().lower()) or {}
    return bool(t.get("exhaustive"))

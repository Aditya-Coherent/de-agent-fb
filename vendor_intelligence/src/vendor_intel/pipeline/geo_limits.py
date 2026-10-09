"""Geography-aware pipeline caps — global runs aim for higher recall with fair quality."""
from __future__ import annotations

from vendor_intel.config import Settings

_GLOBAL_ALIASES = frozenset(
    {"global", "worldwide", "international", "all regions", "all", "world"}
)


def _int_or(val, default: int) -> int:
    try:
        return int(val) if val is not None else int(default)
    except (TypeError, ValueError):
        return int(default)


def is_global_geography(geo: str | None) -> bool:
    g = (geo or "global").strip().lower()
    if not g:
        return True
    return g in _GLOBAL_ALIASES


def pipeline_limits(
    settings: Settings,
    *,
    recall: bool,
    country: str | None,
) -> dict[str, int | float]:
    """Resolved discover/enrich/export thresholds for this run."""
    if recall:
        return {
            "discover": 220,
            "enrich": 220,
            "min_conf": 0.0,
            "export_max": 280,
            "export_min": 0,
            "min_quality": 0.0,
            "smoke_prompts": 0,
            "volume_prompts": 50,
            "discovery_prompts": 24,
        }

    global_run = is_global_geography(country)
    if global_run:
        return {
            "discover": int(
                getattr(settings, "pipeline_global_discover_max", 280) or 280
            ),
            "enrich": int(
                getattr(settings, "pipeline_global_enrich_max", 280) or 280
            ),
            "min_conf": float(
                getattr(settings, "pipeline_global_min_export_confidence", 0.50) or 0.50
            ),
            "export_max": int(
                getattr(settings, "pipeline_global_export_max_rows", 240) or 240
            ),
            "export_min": int(
                getattr(settings, "pipeline_global_export_min_rows", 0) or 0
            ),
            "min_quality": float(
                getattr(settings, "pipeline_global_min_quality", 0.48) or 0.48
            ),
            # 0 is a valid value (skip the Phase 1 smoke searches) — don't let `or` turn it into 10.
            "smoke_prompts": _int_or(getattr(settings, "phase1_global_smoke_max_prompts", None), 10),
            "volume_prompts": int(
                getattr(settings, "pipeline_global_volume_prompt_count", 26) or 26
            ),
            "discovery_prompts": int(
                getattr(settings, "phase1_global_discovery_prompts", 20) or 20
            ),
        }

    # Regional quality runs: higher headroom for demo exports
    return {
        "discover": int(getattr(settings, "pipeline_discover_max", 250) or 250),
        "enrich": int(getattr(settings, "pipeline_enrich_max", 250) or 250),
        "min_conf": float(
            getattr(settings, "pipeline_min_export_confidence", 0.50) or 0.50
        ),
        "export_max": int(getattr(settings, "pipeline_export_max_rows", 200) or 200),
        "export_min": int(getattr(settings, "pipeline_export_min_rows", 0) or 0),
        "min_quality": 0.48,
        "smoke_prompts": _int_or(getattr(settings, "phase1_smoke_max_prompts", None), 4),
        "volume_prompts": int(getattr(settings, "volume_prompt_count", 36) or 36),
        "discovery_prompts": 18,
    }

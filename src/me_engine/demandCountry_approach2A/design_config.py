"""Frozen run configuration for the 2A/2B/2C demand-side system
(`src/newinfo/system.md` §15, constants in `src/newinfo/documentation.md` Part C).

Every "design default" is a starting point to be tuned on golden runs. A run
records `config_hash()` so a change in any threshold is visible in its
outputs. Override per run with a JSON file (`DEAGENT_DESIGN_CONFIG=<path>`)
holding any subset of the keys below.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "llm": {"models": {"fast": "deepseek-chat", "reasoning": "deepseek-chat"}},
    "ai_overview": {"enabled": False, "only_when_tiers_1_4_fail": True, "max_queries_per_run": 50},
    "anchor": {"band": 1.5, "target": "ceiling"},          # target: "ceiling" (band edge) | "total" (2A total)
    "agent": {
        "max_passes": 3,
        "min_score": 7.0,                                  # plausibility 0-10
        "min_reviewer": 5.0,                               # reviewer rubric floor (not in system.md; see plausibility.accepted)
        "weights": {"reviewer": 0.40, "physical": 0.20, "coverage": 0.20, "soft_flags": 0.10, "mix": 0.10},
        "soft_flag_penalty": 1.0,                          # points off the 10-point soft-flag component per flag
    },
    "checks": {
        "sum_tolerance": 0.001,
        "share_tolerance": 0.001,
        "triangulation_warn": 0.25,
        "use_case_dominance": 0.50,
        "min_verified_coverage": 0.50,
        "min_explained_share_2c": 0.60,
        "min_cell_share_2c": 0.005,
        "price_outlier_iqr": 3.0,
    },
    # Per-capita band (physical plausibility), in the run's physical unit per
    # person per year. Markets without a band skip the check (reported as such).
    "per_capita_bands": {"olive oil": {"unit": "kg", "floor": 0.0, "ceiling": 30.0}},
    "publisher": {"enabled": False, "weight_bottom_up": 0.5},
    "pricing": {"curve_min_points": 5, "min_import_tonnes": 500.0, "value_basis": "manufacturer"},
    "display": {"value_sig_figs": 3, "pct_decimals": 1},
}


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load(override: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = DEFAULTS
    path = os.environ.get("DEAGENT_DESIGN_CONFIG")
    if path and Path(path).exists():
        cfg = _merge(cfg, json.loads(Path(path).read_text(encoding="utf-8")))
    if override:
        cfg = _merge(cfg, override)
    return copy.deepcopy(cfg)


def config_hash(cfg: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:12]


def per_capita_band(cfg: dict[str, Any], market_name: str) -> dict[str, Any] | None:
    return (cfg.get("per_capita_bands") or {}).get(market_name.strip().lower())

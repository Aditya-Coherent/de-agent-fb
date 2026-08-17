"""Phase 2: Usage/Application Taxonomy + Demand Driver Draft -- the SECOND
user approval gate.

Runs the EXISTING decomposition/validation/coverage/driver agents
(`agents.py`, unchanged) from the APPROVED Phase 1 market setup -- never
re-deriving scope or geography (spec section 12). Usage buckets and their
proposed demand drivers are bundled into ONE draft object, because the spec
treats them as a single approval unit (edit usage, edit driver, change
physical unit -- all before one approval).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from me_engine.generation.config import GenConfig
from me_engine.generation.llm import GenLLM

from . import agents, checkpoints

_REQUIRED_BUCKET_FIELDS = ("name", "description", "bucket_category")


def run_taxonomy_phase(
    approved_setup: dict[str, Any],
    config: GenConfig,
    cache_path: Path | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    market_name = approved_setup["market_name"]
    llm = GenLLM(config, cache_path=cache_path)
    state: dict[str, Any] = {
        "market_name": market_name,
        "config": config,
        "llm": llm,
        "log": [],
        # Approved setup's scope stands in for market_understanding_node's
        # output -- usage_decomposition/usage_coverage read this same shape.
        "market_definition": {"text": approved_setup["scope"], "grounded": approved_setup.get("grounded", False)},
        "countries": list(approved_setup["geography"]),
    }
    state.update(agents.usage_decomposition_node(state))
    state.update(agents.usage_validation_node(state))
    state.update(agents.usage_coverage_node(state))
    state.update(agents.demand_driver_node(state))

    draft = {
        "market_name": market_name,
        "usage_buckets": state.get("usage_buckets", []),
        "excluded_usage_buckets": state.get("excluded_usage_buckets", []),
        "usage_classifications": state.get("usage_classifications", {}),
        "missing_usage_flags": state.get("missing_usage_flags", []),
        "coverage_pct": state.get("coverage_pct"),
        "overlap_flags": state.get("overlap_flags", []),
        "drivers": state.get("drivers", {}),
        "log": state.get("log", []),
    }
    checkpoints.write_json(checkpoints.taxonomy_draft_path(market_name, root), draft)
    return draft


def approve_taxonomy(edited: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    """Validates and writes the approved taxonomy checkpoint -- the ONLY
    gate before Phase 3 (sizing) may run. Only `usage_buckets` and `drivers`
    are load-bearing downstream; everything else is audit context carried
    through into the final report unchanged."""
    market_name = str(edited.get("market_name") or "").strip()
    if not market_name:
        raise ValueError("approved taxonomy is missing market_name")
    buckets = edited.get("usage_buckets") or []
    if not buckets:
        raise ValueError("approved taxonomy must have at least one usage bucket")
    drivers = edited.get("drivers") or {}

    normalized_buckets: list[dict[str, str]] = []
    for b in buckets:
        missing = [f for f in _REQUIRED_BUCKET_FIELDS if not b.get(f)]
        if missing:
            raise ValueError(f"usage bucket {b!r} is missing required field(s): {missing}")
        name = str(b["name"]).strip()
        if name not in drivers:
            raise ValueError(f"usage bucket '{name}' has no approved demand driver")
        normalized_buckets.append({
            "name": name,
            "description": str(b["description"]).strip(),
            "bucket_category": str(b["bucket_category"]).strip(),
        })

    approved = {
        "market_name": market_name,
        "usage_buckets": normalized_buckets,
        "drivers": drivers,
        "excluded_usage_buckets": edited.get("excluded_usage_buckets", []),
        "missing_usage_flags": edited.get("missing_usage_flags", []),
        "coverage_pct": edited.get("coverage_pct"),
        "overlap_flags": edited.get("overlap_flags", []),
        "approved": True,
    }
    checkpoints.write_json(checkpoints.taxonomy_approved_path(market_name, root), approved)
    return approved


def load_approved_taxonomy(market_name: str, root: Path | None = None) -> dict[str, Any]:
    data = checkpoints.read_json(checkpoints.taxonomy_approved_path(market_name, root))
    if data is None:
        raise FileNotFoundError(
            f"No approved taxonomy found for '{market_name}' -- run Phase 2 (taxonomy) and approve "
            "it before Phase 3 (size)."
        )
    return data

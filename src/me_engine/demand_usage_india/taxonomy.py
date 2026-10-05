"""Phase 2 (India-only): Major Use Case + India Actual Use Case + Demand
Driver Draft -- the SECOND user approval gate, bundled as ONE approval unit
per the user's confirmed decision (same philosophy as the non-India
pipeline's own bucket+driver bundling).

Runs the EXISTING decomposition/validation/coverage/driver agents
(`me_engine.demandUseCaseCountry_approach2B.agents`, unchanged) to discover Major Use Cases,
then this package's own `actual_use_case_agents.india_actual_use_case_node`
to discover India Actual Use Cases nested under each retained Major Use
Case, then reuses `demand_driver_node` UNCHANGED on the flattened
Actual-Use-Case list (Phase 3's sizing graph only ever reads `name`/
`description`/`bucket_category` off a bucket dict -- the extra
`major_use_case` tag rides along inert).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from me_engine.demandUseCaseCountry_approach2B import agents
from me_engine.generation.config import GenConfig
from me_engine.generation.llm import GenLLM

from . import actual_use_case_agents, checkpoints

_REQUIRED_ACTUAL_USE_CASE_FIELDS = ("name", "description", "bucket_category", "major_use_case")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_taxonomy_phase(
    approved_setup: dict[str, Any],
    config: GenConfig,
    cache_path: Path | None = None,
    root: Path | None = None,
    emitter: Any | None = None,
) -> dict[str, Any]:
    market_name = approved_setup["market_name"]
    llm = GenLLM(config, cache_path=cache_path)
    state: dict[str, Any] = {
        "market_name": market_name,
        "config": config,
        "llm": llm,
        "log": [],
        "market_definition": {"text": approved_setup["scope"], "grounded": approved_setup.get("grounded", False)},
        # India-locked -- approved_setup["geography"] is always ["India"]
        # (see `setup.approve_setup`), never re-derived here.
        "countries": list(approved_setup["geography"]),
        "physical_unit": approved_setup.get("physical_unit", ""),
        "emitter": emitter,
    }
    if emitter is not None:
        emitter.emit("taxonomy_india", "stage_start", data={"node": "run_taxonomy_phase"})

    # Step 1-2: discover + validate Major Use Cases -- REUSED, UNCHANGED.
    state.update(agents.usage_decomposition_node(state))
    state.update(agents.scope_taxonomy_validation_node(state))
    state.update(agents.usage_validation_node(state))
    state.update(agents.usage_coverage_node(state))
    major_use_cases = state.get("usage_buckets", [])

    # Step 3-4: discover India Actual Use Cases nested under each retained
    # Major Use Case, applying the Step-4 segmentation-dimension guardrail --
    # the ONE genuinely new LLM node in this package.
    state.update(actual_use_case_agents.india_actual_use_case_node(state))
    actual_use_cases = state.get("actual_use_cases", [])

    # Step 5: demand_driver_node REUSED, UNCHANGED -- it only reads
    # `state["usage_buckets"]`, so the flattened Actual-Use-Case list is
    # substituted in as "the buckets" Phase 3 already expects. Each driver
    # ends up keyed by Actual Use Case name (not Major Use Case name).
    state["usage_buckets"] = actual_use_cases
    state.update(agents.demand_driver_node(state))

    if emitter is not None:
        for auc in actual_use_cases:
            emitter.emit(
                "taxonomy_india", "taxonomy_generated", bucket=auc.get("name"),
                data={
                    "name": auc.get("name"), "description": auc.get("description"),
                    "bucket_category": auc.get("bucket_category"), "major_use_case": auc.get("major_use_case"),
                },
            )
        emitter.emit("taxonomy_india", "stage_end", data={"node": "run_taxonomy_phase"})

    draft = {
        "market_name": market_name,
        "major_use_cases": major_use_cases,
        "actual_use_cases": actual_use_cases,
        "rejected_actual_use_case_candidates": state.get("rejected_actual_use_case_candidates", []),
        "excluded_usage_buckets": state.get("excluded_usage_buckets", []),
        "usage_classifications": state.get("usage_classifications", {}),
        "scope_validation_flags": state.get("scope_validation_flags", {}),
        "scope_taxonomy_aggregate": state.get("scope_taxonomy_aggregate", {}),
        "missing_usage_flags": state.get("missing_usage_flags", []),
        "coverage_pct": state.get("coverage_pct"),
        "overlap_flags": state.get("overlap_flags", []),
        "drivers": state.get("drivers", {}),
        "log": state.get("log", []),
    }
    checkpoints.write_json(checkpoints.taxonomy_draft_path(market_name, root), draft)
    return draft


def approve_taxonomy(
    edited: dict[str, Any], root: Path | None = None,
    approved_by: str | None = None, emitter: Any | None = None,
) -> dict[str, Any]:
    """Validates and writes the approved taxonomy checkpoint -- the ONLY
    gate before Phase 3 (sizing) may run. Mirrors
    `demand_usage.taxonomy.approve_taxonomy`'s validation shape, but each
    leaf bucket is an Actual Use Case and must additionally carry a
    `major_use_case` tag."""
    market_name = str(edited.get("market_name") or "").strip()
    if not market_name:
        raise ValueError("approved taxonomy is missing market_name")
    actual_use_cases = edited.get("actual_use_cases") or []
    if not actual_use_cases:
        raise ValueError("approved taxonomy must have at least one Actual Use Case")
    drivers = edited.get("drivers") or {}

    normalized_actual_use_cases: list[dict[str, str]] = []
    for auc in actual_use_cases:
        missing = [f for f in _REQUIRED_ACTUAL_USE_CASE_FIELDS if not auc.get(f)]
        if missing:
            raise ValueError(f"Actual Use Case {auc!r} is missing required field(s): {missing}")
        name = str(auc["name"]).strip()
        if name not in drivers:
            raise ValueError(f"Actual Use Case '{name}' has no approved demand driver")
        normalized_actual_use_cases.append({
            "name": name,
            "description": str(auc["description"]).strip(),
            "bucket_category": str(auc["bucket_category"]).strip(),
            "major_use_case": str(auc["major_use_case"]).strip(),
        })

    approved = {
        "market_name": market_name,
        "major_use_cases": edited.get("major_use_cases", []),
        "actual_use_cases": normalized_actual_use_cases,
        # Phase 3's sizing graph reads `usage_buckets` (unchanged) -- the
        # flattened, approved Actual Use Cases are what get sized.
        "usage_buckets": normalized_actual_use_cases,
        "rejected_actual_use_case_candidates": edited.get("rejected_actual_use_case_candidates", []),
        "drivers": drivers,
        "excluded_usage_buckets": edited.get("excluded_usage_buckets", []),
        "scope_validation_flags": edited.get("scope_validation_flags", {}),
        "scope_taxonomy_aggregate": edited.get("scope_taxonomy_aggregate", {}),
        "missing_usage_flags": edited.get("missing_usage_flags", []),
        "coverage_pct": edited.get("coverage_pct"),
        "overlap_flags": edited.get("overlap_flags", []),
        "approved": True,
        "approved_by": approved_by,
        "approved_at": _now_iso(),
    }
    checkpoints.write_json(checkpoints.taxonomy_approved_path(market_name, root), approved)
    return approved


def load_approved_taxonomy(market_name: str, root: Path | None = None) -> dict[str, Any]:
    data = checkpoints.read_json(checkpoints.taxonomy_approved_path(market_name, root))
    if data is None:
        raise FileNotFoundError(
            f"No approved India taxonomy found for '{market_name}' -- run Phase 2 (taxonomy) and "
            "approve it before Phase 3 (size)."
        )
    return data

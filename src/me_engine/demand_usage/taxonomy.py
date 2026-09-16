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

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from me_engine.generation.config import GenConfig
from me_engine.generation.llm import GenLLM

from . import agents, checkpoints, evidence_strategy

_REQUIRED_BUCKET_FIELDS = ("name", "description", "bucket_category")


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
        # Approved setup's scope stands in for market_understanding_node's
        # output -- usage_decomposition/usage_coverage read this same shape.
        "market_definition": {"text": approved_setup["scope"], "grounded": approved_setup.get("grounded", False)},
        "countries": list(approved_setup["geography"]),
        # Target-Commodity Demand Relevance fix (2026-08-26,
        # CRUDE_OIL_METHODOLOGY_FIX) -- both `scope_taxonomy_validation_node`
        # and `demand_driver_node` need the market's OWN physical unit to
        # judge/anchor whether a usage bucket's demand can be connected to
        # THIS commodity, not whether the bucket happens to describe a
        # downstream/refined product.
        "physical_unit": approved_setup.get("physical_unit", ""),
        "emitter": emitter,
    }
    if emitter is not None:
        emitter.emit("taxonomy", "stage_start", data={"node": "run_taxonomy_phase"})
    state.update(agents.usage_decomposition_node(state))
    state.update(agents.scope_taxonomy_validation_node(state))
    state.update(agents.usage_validation_node(state))
    state.update(agents.usage_coverage_node(state))
    state.update(agents.demand_driver_node(state))

    if emitter is not None:
        for b in state.get("usage_buckets", []):
            emitter.emit(
                "taxonomy", "taxonomy_generated", bucket=b.get("name"),
                data={
                    "name": b.get("name"), "description": b.get("description"),
                    "bucket_category": b.get("bucket_category"),
                },
            )
        emitter.emit("taxonomy", "stage_end", data={"node": "run_taxonomy_phase"})

    draft = {
        "market_name": market_name,
        "usage_buckets": state.get("usage_buckets", []),
        "excluded_usage_buckets": state.get("excluded_usage_buckets", []),
        "usage_classifications": state.get("usage_classifications", {}),
        "scope_validation_flags": state.get("scope_validation_flags", {}),
        # Phase 9 (DEMAND_SIDE_EVIDENCE_PLAN.md) -- aggregate "is the scope
        # itself too narrow for this taxonomy" signal, surfaced BEFORE
        # approval alongside the per-bucket flags it's computed from.
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

    # Phase 9 (DEMAND_SIDE_EVIDENCE_PLAN.md) -- an out_of_scope bucket may
    # never silently ride into sizing just because the user left it in the
    # edited draft untouched. The user must explicitly acknowledge it via
    # `scope_overrides: {bucket_name: "<justification>"}` -- a non-empty
    # justification string -- or remove/edit the bucket instead. Backward
    # compatible: a draft with no `scope_validation_flags` at all (pre-Phase-9
    # checkpoint, or a hand-built test fixture) never triggers this gate.
    scope_flags = edited.get("scope_validation_flags") or {}
    scope_overrides = {
        str(k).strip(): str(v).strip()
        for k, v in (edited.get("scope_overrides") or {}).items()
        if str(v).strip()
    }

    normalized_buckets: list[dict[str, str]] = []
    for b in buckets:
        missing = [f for f in _REQUIRED_BUCKET_FIELDS if not b.get(f)]
        if missing:
            raise ValueError(f"usage bucket {b!r} is missing required field(s): {missing}")
        name = str(b["name"]).strip()
        if name not in drivers:
            raise ValueError(f"usage bucket '{name}' has no approved demand driver")
        flag = scope_flags.get(name) or {}
        if flag.get("classification") == "out_of_scope" and name not in scope_overrides:
            raise ValueError(
                f"usage bucket '{name}' is flagged out_of_scope vs. the approved Market Setup "
                "scope (reason: " + str(flag.get("reason") or "unspecified") + "). Remove/edit this "
                "bucket, or explicitly retain it by providing a non-empty justification in "
                "scope_overrides['" + name + "'] before approving."
            )
        normalized_buckets.append({
            "name": name,
            "description": str(b["description"]).strip(),
            "bucket_category": str(b["bucket_category"]).strip(),
        })

    # Only overrides for buckets actually retained in this approval are kept
    # -- an override for a bucket the user ended up removing is stale, not
    # a meaningful amendment to the approved scope.
    retained_names = {b["name"] for b in normalized_buckets}
    active_overrides = {k: v for k, v in scope_overrides.items() if k in retained_names}
    scope_amendment_text = ""
    if active_overrides:
        notes = [
            f"'{name}' (flagged {scope_flags.get(name, {}).get('classification', 'out_of_scope')}: "
            f"{scope_flags.get(name, {}).get('reason', 'unspecified')}) retained -- {justification}"
            for name, justification in active_overrides.items()
        ]
        scope_amendment_text = (
            "Approved scope was extended beyond the original Market Setup statement by explicit "
            "user override for: " + "; ".join(notes)
        )

    # Phase 7 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) -- re-run the deterministic
    # Evidence Strategy validation at approval time, AFTER any human edits to
    # the usage bucket text or the strategy's own fields, so the approved
    # checkpoint's `evidence_strategy_flags` reflect what the user actually
    # approved, not a stale pre-edit verdict. A driver with no
    # `evidence_strategy` at all is untouched (backward compatible with
    # taxonomy drafts that predate this field) -- never a hard requirement.
    desc_by_name = {b["name"]: b["description"] for b in normalized_buckets}
    for bucket_name, driver in drivers.items():
        strategy = driver.get("evidence_strategy") if isinstance(driver, dict) else None
        if not isinstance(strategy, dict):
            continue
        driver["evidence_strategy_flags"] = evidence_strategy.validate_evidence_strategy(
            strategy, bucket_name=bucket_name, bucket_description=desc_by_name.get(bucket_name, ""),
        )

    approved = {
        "market_name": market_name,
        "usage_buckets": normalized_buckets,
        "drivers": drivers,
        "excluded_usage_buckets": edited.get("excluded_usage_buckets", []),
        # Full flag set is carried through UNFILTERED for audit, even for
        # buckets no longer present in `usage_buckets` -- never silently
        # deleted just because the user removed the bucket itself.
        "scope_validation_flags": scope_flags,
        "scope_taxonomy_aggregate": edited.get("scope_taxonomy_aggregate", {}),
        "scope_overrides": active_overrides,
        "scope_amendment_text": scope_amendment_text,
        "missing_usage_flags": edited.get("missing_usage_flags", []),
        "coverage_pct": edited.get("coverage_pct"),
        "overlap_flags": edited.get("overlap_flags", []),
        "approved": True,
        # Additive audit fields -- `approved_by` is optional/backward
        # compatible (None if not supplied); `approved_at` is a real UTC
        # timestamp captured at the moment of this approval call.
        "approved_by": approved_by,
        "approved_at": _now_iso(),
    }
    if emitter is not None:
        for bucket_name, justification in active_overrides.items():
            flag = scope_flags.get(bucket_name) or {}
            emitter.emit(
                "taxonomy_approval", "scope_override", bucket=bucket_name,
                data={
                    "bucket": bucket_name,
                    "original_classification": flag.get("classification"),
                    "original_reason": flag.get("reason"),
                    "justification": justification,
                    "approved_by": approved_by,
                    "approved_at": approved["approved_at"],
                },
            )
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

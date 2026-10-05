"""Phase 1: Market Setup Draft (India-only variant) -- the FIRST user
approval gate.

Reuses `me_engine.demandUseCaseCountry_approach2B.setup.market_setup_draft_node` COMPLETELY
UNCHANGED (same web-grounded LLM call for scope/base_year/market_unit/
physical_unit). The only India-specific behavior lives here, deterministically,
in code -- never delegated to the LLM: after the shared node returns its
draft, `geography` is forcibly overridden to `["India"]`, regardless of what
the LLM proposed. This is a market-agnostic methodology choice (no regional
split, no other countries), not something an LLM call should be trusted to
enforce on its own.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from me_engine.demandUseCaseCountry_approach2B.setup import market_setup_draft_node
from me_engine.generation.config import GenConfig
from me_engine.generation.llm import GenLLM

from . import checkpoints

_INDIA_GEOGRAPHY = ["India"]
_SETUP_FIELDS = ("market_name", "scope", "geography", "base_year", "market_unit", "physical_unit")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_setup_phase(
    market_name: str,
    config: GenConfig,
    cache_path: Path | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Runs the SAME single-node Phase 1 draft as the non-India pipeline, then
    deterministically locks `geography` to `["India"]` before writing the
    draft checkpoint. Returns the draft dict (same shape written to disk)."""
    llm = GenLLM(config, cache_path=cache_path)

    # Additive audit/observability layer (`me_engine.audit`) -- optional
    # `AuditEmitter`, mirrors `demand_usage.setup.run_setup_phase`'s own
    # discipline: never raises if the audit package can't be constructed.
    emitter = None
    try:
        from me_engine.audit.store import AuditEmitter

        emitter = AuditEmitter(run_id=market_name, approach="usage_based_india", market=market_name, root=root)
    except Exception:  # noqa: BLE001
        emitter = None

    state: dict[str, Any] = {
        "market_name": market_name, "config": config, "llm": llm, "log": [], "emitter": emitter,
    }
    draft = market_setup_draft_node(state)

    # India-locked geography override (spec: no regional split, no other
    # countries) -- deterministic, applied in code AFTER the shared LLM node
    # returns, never left to the LLM's own judgment.
    if draft.get("geography") != _INDIA_GEOGRAPHY:
        draft.setdefault("log", []).append(
            f"[market_setup_india] geography overridden to {_INDIA_GEOGRAPHY} "
            f"(LLM proposed {draft.get('geography')!r}) -- this methodology is India-only."
        )
    draft["geography"] = list(_INDIA_GEOGRAPHY)

    checkpoints.write_json(checkpoints.setup_draft_path(market_name, root), draft)
    return draft


def approve_setup(
    edited: dict[str, Any], root: Path | None = None,
    approved_by: str | None = None, emitter: Any | None = None,
) -> dict[str, Any]:
    """Validates and writes the approved setup checkpoint -- the ONLY gate
    before Phase 2 (taxonomy) may run. Same validation shape as
    `demand_usage.setup.approve_setup`, plus a hard India-only geography
    lock: `geography` is forced to `["India"]` regardless of what `edited`
    contains, even if a caller (or a stale/hand-edited draft) supplies
    something else."""
    market_name = str(edited.get("market_name") or "").strip()
    if not market_name:
        raise ValueError("approved setup is missing market_name")
    missing = [f for f in _SETUP_FIELDS if not edited.get(f) and edited.get(f) != 0]
    if missing:
        raise ValueError(f"approved setup is missing required field(s): {missing}")
    try:
        base_year = int(edited["base_year"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"approved setup's base_year must be an integer, got {edited['base_year']!r}") from exc

    approved = {
        "market_name": market_name,
        "scope": str(edited["scope"]).strip(),
        # India-only lock (spec step 2) -- never trust `edited["geography"]`,
        # even at this final gate; some other value could only ever have
        # reached here via a hand-edited draft file or a malformed request.
        "geography": list(_INDIA_GEOGRAPHY),
        "base_year": base_year,
        "market_unit": str(edited["market_unit"]).strip(),
        "physical_unit": str(edited["physical_unit"]).strip(),
        "grounded": bool(edited.get("grounded", False)),
        "approved": True,
        "approved_by": approved_by,
        "approved_at": _now_iso(),
    }
    checkpoints.write_json(checkpoints.setup_approved_path(market_name, root), approved)
    return approved


def load_approved_setup(market_name: str, root: Path | None = None) -> dict[str, Any]:
    data = checkpoints.read_json(checkpoints.setup_approved_path(market_name, root))
    if data is None:
        raise FileNotFoundError(
            f"No approved India market setup found for '{market_name}' -- run Phase 1 (setup) and "
            "approve it before Phase 2 (taxonomy)."
        )
    return data

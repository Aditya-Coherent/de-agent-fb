"""Phase 1 (2A): Market Setup draft -- `engine.setup.market_setup_draft_node`
(forked from 2B) proposes scope, country list, base year and units; only the
checkpoint location differs."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from me_engine.generation.config import GenConfig
from me_engine.generation.llm import GenLLM

from . import checkpoints
from .engine.setup import market_setup_draft_node

_SETUP_FIELDS = ("market_name", "scope", "geography", "base_year", "market_unit", "physical_unit")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_setup_phase(
    market_name: str, config: GenConfig, cache_path: Path | None = None, root: Path | None = None,
) -> dict[str, Any]:
    llm = GenLLM(config, cache_path=cache_path)
    state: dict[str, Any] = {"market_name": market_name, "config": config, "llm": llm, "log": []}
    draft = market_setup_draft_node(state)
    draft["approach"] = "2A"
    checkpoints.write_json(checkpoints.setup_draft_path(market_name, root), draft)
    return draft


def approve_setup(edited: dict[str, Any], root: Path | None = None, approved_by: str | None = None) -> dict[str, Any]:
    market_name = str(edited.get("market_name") or "").strip()
    if not market_name:
        raise ValueError("approved setup is missing market_name")
    missing = [f for f in _SETUP_FIELDS if not edited.get(f) and edited.get(f) != 0]
    if missing:
        raise ValueError(f"approved setup is missing required field(s): {missing}")
    geography = [str(g).strip() for g in edited["geography"] if str(g).strip()]
    if not geography:
        raise ValueError("approved setup must list at least one country")
    try:
        base_year = int(edited["base_year"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"base_year must be an integer, got {edited['base_year']!r}") from exc

    approved = {
        "market_name": market_name,
        "approach": "2A",
        "scope": str(edited["scope"]).strip(),
        "geography": geography,
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
        raise FileNotFoundError(f"No approved 2A setup for '{market_name}' -- run `setup` and `approve-setup` first.")
    return data

"""Phase 2 (2A): the single country-level total-demand line and its driver.

2A sizes the WHOLE market per country in one line (no application split), so
the "taxonomy" here is exactly one physical-consumption bucket. Its demand
driver comes from 2B's `demand_driver_node` (reused unchanged), which proposes
the driver, unit, conversion and evidence strategy for that bucket. The
output has the same shape as a 2B approved taxonomy, so 2B's sizing graph
runs on it as-is.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from me_engine.demandUseCaseCountry_approach2B import agents, evidence_strategy
from me_engine.generation.config import GenConfig
from me_engine.generation.llm import GenLLM

from . import checkpoints


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def total_demand_bucket(market_name: str, physical_unit: str) -> dict[str, str]:
    unit = physical_unit or "physical units"
    return {
        "name": f"Total {market_name} demand",
        "description": (
            f"All final consumption of {market_name} in the country, measured in {unit}, across "
            "every application and end use combined (apparent consumption = production + imports "
            "- exports where data allows). Not split by application."
        ),
        "bucket_category": "physical_consumption",
    }


def run_driver_phase(
    approved_setup: dict[str, Any], config: GenConfig,
    cache_path: Path | None = None, root: Path | None = None,
) -> dict[str, Any]:
    market_name = approved_setup["market_name"]
    llm = GenLLM(config, cache_path=cache_path)
    bucket = total_demand_bucket(market_name, approved_setup.get("physical_unit", ""))
    state: dict[str, Any] = {
        "market_name": market_name,
        "config": config,
        "llm": llm,
        "log": [],
        "market_definition": {"text": approved_setup["scope"], "grounded": approved_setup.get("grounded", False)},
        "countries": list(approved_setup["geography"]),
        "physical_unit": approved_setup.get("physical_unit", ""),
        "usage_buckets": [bucket],
    }
    state.update(agents.demand_driver_node(state))
    draft = {
        "market_name": market_name,
        "approach": "2A",
        "usage_buckets": [bucket],
        "drivers": state.get("drivers", {}),
        "log": state.get("log", []),
        "note": "One country-level total-demand line. Edit the driver (name, unit, formula) if needed, then approve.",
    }
    checkpoints.write_json(checkpoints.driver_draft_path(market_name, root), draft)
    return draft


def approve_driver(edited: dict[str, Any], root: Path | None = None, approved_by: str | None = None) -> dict[str, Any]:
    market_name = str(edited.get("market_name") or "").strip()
    if not market_name:
        raise ValueError("driver draft is missing market_name")
    buckets = edited.get("usage_buckets") or []
    if len(buckets) != 1:
        raise ValueError(
            f"approach 2A sizes exactly ONE total-demand line per country; found {len(buckets)} bucket(s)"
        )
    bucket = buckets[0]
    for f in ("name", "description", "bucket_category"):
        if not bucket.get(f):
            raise ValueError(f"total-demand bucket is missing '{f}'")
    if bucket["bucket_category"] != "physical_consumption":
        raise ValueError("the total-demand bucket must be physical_consumption")
    drivers = edited.get("drivers") or {}
    if bucket["name"] not in drivers:
        raise ValueError(f"no demand driver for '{bucket['name']}'")
    driver = drivers[bucket["name"]]
    strategy = driver.get("evidence_strategy") if isinstance(driver, dict) else None
    if isinstance(strategy, dict):
        driver["evidence_strategy_flags"] = evidence_strategy.validate_evidence_strategy(
            strategy, bucket_name=bucket["name"], bucket_description=bucket["description"],
        )
    approved = {
        "market_name": market_name,
        "approach": "2A",
        "usage_buckets": [{k: str(bucket[k]).strip() for k in ("name", "description", "bucket_category")}],
        "drivers": drivers,
        "approved": True,
        "approved_by": approved_by,
        "approved_at": _now_iso(),
    }
    checkpoints.write_json(checkpoints.driver_approved_path(market_name, root), approved)
    return approved


def load_approved_driver(market_name: str, root: Path | None = None) -> dict[str, Any]:
    data = checkpoints.read_json(checkpoints.driver_approved_path(market_name, root))
    if data is None:
        raise FileNotFoundError(f"No approved 2A driver for '{market_name}' -- run `driver` and `approve-driver` first.")
    return data

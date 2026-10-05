"""Phase 3 (2A): sizing -- a thin wrapper over 2B's sizing graph.

2B's `run_sizing_phase` already works over "a list of buckets x a list of
countries"; with 2A's single total-demand bucket it yields exactly the
country totals 2A needs (quantity x price per country, deterministic
`calculate_node`, same evidence tagging, same validation flags). The only
addition is the region rollup.

2B's progress file and audit log are redirected to
`output/demand_country_2a/<slug>/sizing_trace/`, so they never land in a 2B run
folder of the same market.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from me_engine.demand_topdown.regions import country_region_map, rollup_regions
from me_engine.demandUseCaseCountry_approach2B.graph import run_sizing_phase as _run_sizing_phase_shared
from me_engine.generation.config import GenConfig

from . import checkpoints


def add_region_rollup(state: dict[str, Any]) -> dict[str, Any]:
    totals = state.get("country_totals") or {}
    out = dict(state)
    out["country_region"] = country_region_map(list(totals))
    out["region_totals"] = rollup_regions(totals)
    out["log"] = list(out.get("log") or []) + [
        "[2A] country -> region rollup: "
        + ", ".join(f"{r} ${v:,.1f}M" for r, v in out["region_totals"].items())
    ]
    return out


def run_sizing_phase(
    approved_setup: dict[str, Any],
    approved_driver: dict[str, Any],
    config: GenConfig,
    cache_path: Path | None = None,
    root: Path | None = None,
    run_id: str | None = None,
    source: str = "cli",
) -> dict[str, Any]:
    # 2B's sizing writes its progress file and audit log under
    # `<root>/output/demand_usage/<slug>/`. Point that root inside the 2A run
    # folder so a 2A run never writes into a 2B run of the same market.
    trace_root = checkpoints.run_dir(approved_setup["market_name"], root) / "sizing_trace"
    final_state = _run_sizing_phase_shared(
        approved_setup, approved_driver, config,
        cache_path=cache_path, root=trace_root, run_id=run_id, source=source,
    )
    return add_region_rollup(final_state)

"""Phase 3 (2A): sizing -- a thin wrapper over the sizing graph in `engine/`
(forked from 2B).

`engine.graph.run_sizing_phase` already works over "a list of buckets x a list of
countries"; with 2A's single total-demand bucket it yields exactly the
country totals 2A needs (quantity x price per country, deterministic
`calculate_node`, same evidence tagging, same validation flags). The only
addition is the region rollup.

After the engine, countries left as bare assumptions are re-estimated by the
assumptor (`assumptor/fill.py`), and mass-measured markets get a per-country price
(`price_curve.py`: customs unit values + a price-vs-income curve, system.md
§10) in place of the engine's single global price.

The engine's progress file and audit log are redirected to
`output/demand_country_2a/<slug>/sizing_trace/`, so they never land in a 2B run
folder of the same market.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from me_engine.generation.config import GenConfig
from me_engine.generation.llm import GenLLM

from . import checkpoints, design_config, price_curve
from .assumptor.fill import fill_assumptions
from .engine.buildup_writer import build_report_dict, write_buildup_workbook
from .engine.graph import run_sizing_phase as _run_engine_sizing_phase
from .topdown.regions import country_region_map, rollup_regions


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
    country_pricing: bool = True,
    say: Any = print,
) -> dict[str, Any]:
    # The engine's sizing writes its progress file and audit log under
    # `<root>/output/demand_usage/<slug>/`. Point that root inside the 2A run
    # folder so a 2A run never writes into a 2B run of the same market.
    trace_root = checkpoints.run_dir(approved_setup["market_name"], root) / "sizing_trace"
    final_state = _run_engine_sizing_phase(
        approved_setup, approved_driver, config,
        cache_path=cache_path, root=trace_root, run_id=run_id, source=source,
    )
    final_state = fill_assumptions(final_state, approved_setup["market_name"], GenLLM(config, cache_path=cache_path),
                                   root=root, say=say,
                                   ceiling_kg_per_capita=(design_config.per_capita_band(design_config.load(),
                                                          approved_setup["market_name"]) or {}).get("ceiling"))
    if country_pricing:
        final_state = add_country_prices(final_state, approved_setup, config, cache_path, root, say)
    return add_region_rollup(final_state)


def add_country_prices(state: dict[str, Any], approved_setup: dict[str, Any], config: GenConfig,
                       cache_path: Path | None = None, root: Path | None = None, say: Any = print) -> dict[str, Any]:
    bucket = next(iter(state.get("drivers") or {}), None)
    driver = (state.get("drivers") or {}).get(bucket) or {}
    if price_curve.tonnes_factor(driver.get("final_demand_unit") or driver.get("unit") or "") is None:
        return state
    countries = list(((state.get("quantities") or {}).get(bucket) or {}).keys())
    table = price_curve.country_prices(
        GenLLM(config, cache_path=cache_path), approved_setup["market_name"], approved_setup.get("scope", ""),
        countries, int(approved_setup.get("base_year") or 2025),
        checkpoints.run_dir(approved_setup["market_name"], root) / "price_curve", design_config.load(), say=say,
    )
    return price_curve.apply_country_prices(state, table) if table else state


def write_sizing_outputs(state: dict[str, Any], root: Path | None = None) -> tuple[Path, Path]:
    """Write `02_sizing.json` / `.xlsx` for a finished sizing state."""
    prefix = checkpoints.sizing_output_prefix(state["market_name"], root)
    report = price_curve.patch_report_rows(build_report_dict(state), state)
    report["methodology"] = "demand_side_country_total_2a"
    report["country_region"] = state.get("country_region", {})
    report["region_totals_usd_mn"] = state.get("region_totals", {})
    json_path = checkpoints.write_json(prefix.with_suffix(".json"), report)
    xlsx_path = write_buildup_workbook(state, prefix.with_suffix(".xlsx"))
    return json_path, xlsx_path

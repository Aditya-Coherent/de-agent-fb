"""Phases 4-5 (2A): top-down split of EVERY segmentation dimension.

Reads the country totals from `02_sizing.json`, proposes the market's
segmentation (Application included -- 2A has no bottom-up application
layer), estimates per-country shares, and after approval splits each
country total by those shares (`me_engine.demandCountry_approach2A.topdown`).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from me_engine.generation.config import GenConfig
from me_engine.generation.llm import GenLLM

from . import checkpoints
from .topdown import segments, writer

METHODOLOGY = (
    "Demand-Side 2A: total demand sized per country (quantity x price), summed country -> "
    "region -> global; every segmentation dimension split top-down from country totals."
)


def load_sizing(market_name: str, root: Path | None = None) -> dict[str, Any]:
    sizing = checkpoints.read_json(checkpoints.sizing_output_prefix(market_name, root).with_suffix(".json"))
    if sizing is None:
        raise FileNotFoundError(f"No 2A sizing result for '{market_name}' -- run `size` first.")
    return sizing


def run_segments_phase(
    market_name: str, config: GenConfig, cache_path: Path | None = None,
    root: Path | None = None, search: segments.SearchFn | None = None,
    llm: GenLLM | None = None,
) -> dict[str, Any]:
    sizing = load_sizing(market_name, root)
    setup_info = sizing.get("market_setup") or {}
    draft = segments.build_segments_draft(
        llm=llm or GenLLM(config, cache_path=cache_path),
        config=config,
        approach="2A",
        market_name=market_name,
        scope=setup_info.get("scope") or "",
        base_year=setup_info.get("base_year"),
        country_totals=sizing.get("country_totals_usd_mn") or {},
        bottom_up_dimensions=[],
        search=search,
    )
    checkpoints.write_json(checkpoints.segments_draft_path(market_name, root), draft)
    return draft


def approve_segments(edited: dict[str, Any], root: Path | None = None, approved_by: str | None = None) -> dict[str, Any]:
    approved = segments.approve_segments(edited, approved_by=approved_by)
    checkpoints.write_json(checkpoints.segments_approved_path(approved["market_name"], root), approved)
    return approved


def run_report_phase(market_name: str, root: Path | None = None,
                     write: bool = True) -> tuple[dict[str, Any], Path | None, Path | None]:
    approved = checkpoints.read_json(checkpoints.segments_approved_path(market_name, root))
    if approved is None:
        raise FileNotFoundError(
            f"No approved 2A segments for '{market_name}' -- run `segments` and `approve-segments` first."
        )
    report = segments.finalize_report(approved, METHODOLOGY)
    if not write:  # the 2A agent writes only after the hard checks pass
        return report, None, None
    return (report, *write_report(report, market_name, root))


def write_report(report: dict[str, Any], market_name: str, root: Path | None = None) -> tuple[Path, Path]:
    prefix = checkpoints.report_prefix(market_name, root)
    json_path = writer.write_report_json(report, prefix.with_suffix(".json"))
    xlsx_path = writer.write_report_workbook(report, prefix.with_suffix(".xlsx"))
    return json_path, xlsx_path

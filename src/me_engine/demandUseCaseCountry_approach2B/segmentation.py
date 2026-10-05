"""Phases 4-5 (2B): keep Application bottom-up, split every OTHER segment top-down.

2B already builds the market per application per country (Phase 3,
`02_sizing.json`). This module adds what the methodology asks for next:

    By Application  -> taken straight from the sizing build-up rows (bottom-up)
    Every other dimension (Product Type, Channel, ...) -> country total x share
                       (top-down, via `me_engine.demand_topdown`)
    Country -> Region -> Global rollup for every dimension.

It only READS `02_sizing.json`; nothing in Phases 1-3 changes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from me_engine.demand_topdown import segments, writer
from me_engine.generation.config import GenConfig
from me_engine.generation.llm import GenLLM

from . import checkpoints

METHODOLOGY = (
    "Demand-Side 2B: each application sized per country (quantity x price), applications summed "
    "to country, countries -> region -> global; Application stays bottom-up, every other "
    "segmentation dimension split top-down from country totals."
)
APPLICATION_DIMENSION = "By Application (bottom-up)"


def segments_draft_path(market_name: str, root: Path | None = None) -> Path:
    return checkpoints.run_dir(market_name, root) / "03_segments_draft.json"


def segments_approved_path(market_name: str, root: Path | None = None) -> Path:
    return checkpoints.run_dir(market_name, root) / "03_segments_approved.json"


def report_prefix(market_name: str, root: Path | None = None) -> Path:
    return checkpoints.run_dir(market_name, root) / "04_market"


def load_sizing(market_name: str, root: Path | None = None) -> dict[str, Any]:
    sizing = checkpoints.read_json(checkpoints.sizing_output_prefix(market_name, root).with_suffix(".json"))
    if sizing is None:
        raise FileNotFoundError(f"No 2B sizing result for '{market_name}' -- run `size` first.")
    return sizing


def application_dimension(sizing: dict[str, Any]) -> dict[str, Any]:
    """Application x country values from the Phase 3 build-up rows.

    Only Included-demand (physical consumption) buckets, the same set that
    makes up `country_totals_usd_mn`; inventory/reserve buckets stay out.
    """
    countries = list(sizing.get("country_totals_usd_mn") or {})
    applications = [
        b["name"] for b in sizing.get("usage_buckets") or []
        if b.get("bucket_category", "physical_consumption") == "physical_consumption"
    ]
    values = {c: {a: 0.0 for a in applications} for c in countries}
    for row in sizing.get("build_up_rows") or []:
        c, a = row.get("country"), row.get("usage_bucket")
        if c in values and a in values[c]:
            values[c][a] = round(float(row.get("value_usd_mn") or 0.0), 4)
    return {"dimension": APPLICATION_DIMENSION, "segments": applications, "country_values": values}


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
        approach="2B",
        market_name=market_name,
        scope=setup_info.get("scope") or "",
        base_year=setup_info.get("base_year"),
        country_totals=sizing.get("country_totals_usd_mn") or {},
        bottom_up_dimensions=[application_dimension(sizing)],
        search=search,
    )
    checkpoints.write_json(segments_draft_path(market_name, root), draft)
    return draft


def approve_segments(edited: dict[str, Any], root: Path | None = None, approved_by: str | None = None) -> dict[str, Any]:
    approved = segments.approve_segments(edited, approved_by=approved_by)
    checkpoints.write_json(segments_approved_path(approved["market_name"], root), approved)
    return approved


def run_report_phase(market_name: str, root: Path | None = None) -> tuple[dict[str, Any], Path, Path]:
    approved = checkpoints.read_json(segments_approved_path(market_name, root))
    if approved is None:
        raise FileNotFoundError(
            f"No approved 2B segments for '{market_name}' -- run `segments` and `approve-segments` first."
        )
    report = segments.finalize_report(approved, METHODOLOGY)
    prefix = report_prefix(market_name, root)
    json_path = writer.write_report_json(report, prefix.with_suffix(".json"))
    xlsx_path = writer.write_report_workbook(report, prefix.with_suffix(".xlsx"))
    return report, json_path, xlsx_path

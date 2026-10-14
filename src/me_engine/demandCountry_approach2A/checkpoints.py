"""Draft/approved JSON checkpoint paths for approach 2A.

Same `slugify`/`write_json`/`read_json` contract as
`engine.checkpoints` (2A's own fork of 2B's), rooted at
`output/demand_country_2a/<slug>/` so a 2A run never collides with a 2B run of
the same market.
"""
from __future__ import annotations

from pathlib import Path

from .engine.checkpoints import read_json, slugify, write_json

__all__ = ["read_json", "write_json", "slugify", "run_dir"]

_OUTPUT_ROOT = Path("output") / "demand_country_2a"


def run_dir(market_name: str, root: Path | None = None) -> Path:
    base = (Path(root) / _OUTPUT_ROOT) if root else _OUTPUT_ROOT
    d = base / slugify(market_name)
    d.mkdir(parents=True, exist_ok=True)
    return d


def setup_draft_path(market_name: str, root: Path | None = None) -> Path:
    return run_dir(market_name, root) / "00_setup_draft.json"


def setup_approved_path(market_name: str, root: Path | None = None) -> Path:
    return run_dir(market_name, root) / "00_setup_approved.json"


def driver_draft_path(market_name: str, root: Path | None = None) -> Path:
    return run_dir(market_name, root) / "01_driver_draft.json"


def driver_approved_path(market_name: str, root: Path | None = None) -> Path:
    return run_dir(market_name, root) / "01_driver_approved.json"


def sizing_output_prefix(market_name: str, root: Path | None = None) -> Path:
    return run_dir(market_name, root) / "02_sizing"


def segments_draft_path(market_name: str, root: Path | None = None) -> Path:
    return run_dir(market_name, root) / "03_segments_draft.json"


def segments_approved_path(market_name: str, root: Path | None = None) -> Path:
    return run_dir(market_name, root) / "03_segments_approved.json"


def report_prefix(market_name: str, root: Path | None = None) -> Path:
    return run_dir(market_name, root) / "04_market"

"""Shared draft/approved JSON checkpoint I/O for the India-only demand-usage
phase gates.

Thin mirror of `me_engine.demandUseCaseCountry_approach2B.checkpoints` -- same `slugify`/
`run_dir`/`write_json`/`read_json` contract -- but rooted at
`output/demand_usage_india/<slug>/` (a fully separate tree from
`output/demand_usage/<slug>/`), so an India run for a market never collides
with an existing non-India run of the same market name. Files:
`00_setup_draft.json` / `00_setup_approved.json`,
`01_taxonomy_draft.json` / `01_taxonomy_approved.json`,
`02_sizing.json` / `02_sizing.xlsx`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_OUTPUT_ROOT = Path("output") / "demand_usage_india"


def slugify(market_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", market_name.strip().lower()).strip("-")
    return slug or "market"


def run_dir(market_name: str, root: Path | None = None) -> Path:
    base = (Path(root) / _OUTPUT_ROOT) if root else _OUTPUT_ROOT
    d = base / slugify(market_name)
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_json(path: Path, data: dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def read_json(path: Path) -> dict[str, Any] | None:
    path = Path(path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def setup_draft_path(market_name: str, root: Path | None = None) -> Path:
    return run_dir(market_name, root) / "00_setup_draft.json"


def setup_approved_path(market_name: str, root: Path | None = None) -> Path:
    return run_dir(market_name, root) / "00_setup_approved.json"


def taxonomy_draft_path(market_name: str, root: Path | None = None) -> Path:
    return run_dir(market_name, root) / "01_taxonomy_draft.json"


def taxonomy_approved_path(market_name: str, root: Path | None = None) -> Path:
    return run_dir(market_name, root) / "01_taxonomy_approved.json"


def sizing_output_prefix(market_name: str, root: Path | None = None) -> Path:
    return run_dir(market_name, root) / "02_sizing"

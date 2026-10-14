"""Shared draft/approved JSON checkpoint I/O for the demand-usage phase gates.

Every phase (setup, taxonomy) writes a numbered draft file a human can edit;
nothing downstream may run until an "approved" file with the same schema
exists (see `setup.approve_setup` / `taxonomy.approve_taxonomy`). The CLI
(`demand_usage_pipeline.py`) and the backend API routes both read/write the
SAME on-disk files here -- a run started one way can be continued the other.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_OUTPUT_ROOT = Path("output") / "demand_usage"


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

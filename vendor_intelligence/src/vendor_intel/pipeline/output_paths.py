"""Resolved output directories for pipeline CSV/JSON exports."""
from __future__ import annotations

import os
from pathlib import Path


def market_query_output_dir(project_root: Path) -> Path:
    """
    Folder for run_query.py / run_market_queries.py results.

    Override in .env:
      MARKET_QUERY_OUTPUT_DIR=output/demo
    """
    raw = (os.getenv("MARKET_QUERY_OUTPUT_DIR") or "output/demo").strip() or "output/demo"
    path = Path(raw)
    if not path.is_absolute():
        path = project_root / path
    path.mkdir(parents=True, exist_ok=True)
    return path

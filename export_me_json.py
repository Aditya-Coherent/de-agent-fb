"""Export a generated ME workbook into a single hierarchical JSON:
geography (sheet) -> band (Market Value / ASP / Market Volume) -> segment
label -> {year: value}.

Reads only the already-generated ME workbook .xlsx -- doesn't touch the
live pipeline/agents, so it works on any run's output, past or future, as
long as the file exists. Reuses the same (band, label)-row discovery logic
as compare_me_workbooks.py (kept independent here on purpose -- this is a
read-only export, not a diff -- but the discovery approach is identical so
the two tools always agree on what a "row" means).

Shape:
    {
      "workbook": "ME - <Market>.xlsx",
      "geographies": {
        "<Geography>": {
          "<Band>": {
            "<Segment label>": {"2021": ..., "2022": ..., ..., "2033": ...},
            ...
          },
          ...
        },
        ...
      }
    }

Example:
    python export_me_json.py --market "Global Avocado Oil Market"
    python export_me_json.py --input "agentGeneratedMEWorkbook/ME - Global Avocado Oil Market.xlsx"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from openpyxl import load_workbook

from me_engine.domain.taxonomy import YEARS, Band
from me_engine.io.layout import COL_LABEL, COL_METRIC_START, metric_col

ROOT = Path(__file__).resolve().parent
ME_DIR = ROOT / "agentGeneratedMEWorkbook"
OUT_DIR = ROOT / "output" / "market_json"

_BAND_BY_TITLE = {b.value: b for b in Band}


def _grid(ws) -> list[tuple]:
    return list(ws.iter_rows(values_only=True))


def _cell(grid: list[tuple], row: int, col: int):
    r, c = row - 1, col - 1
    if 0 <= r < len(grid) and 0 <= c < len(grid[r]):
        return grid[r][c]
    return None


def _discover(grid: list[tuple]) -> dict[tuple, int]:
    """Same (band, label) -> row mapping compare_me_workbooks.py uses."""
    rows: dict[tuple, int] = {}
    current: Band | None = None
    for r, row in enumerate(grid, start=1):
        marker = row[COL_METRIC_START - 1] if len(row) >= COL_METRIC_START else None
        if isinstance(marker, str) and marker in _BAND_BY_TITLE:
            current = _BAND_BY_TITLE[marker]
            continue
        label = row[COL_LABEL - 1] if len(row) >= COL_LABEL else None
        if current is not None and isinstance(label, str) and label.strip():
            rows[(current, label)] = r
    return rows


def _find_me_workbook(market: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "", market.lower())
    candidates = sorted(ME_DIR.glob("ME*.xlsx"))
    for p in candidates:
        if re.sub(r"[^a-z0-9]+", "", p.stem.lower()).endswith(slug):
            return p
    raise FileNotFoundError(f"No ME workbook found for market '{market}' in {ME_DIR}")


def export_me_json(me_path: Path) -> dict[str, Any]:
    wb = load_workbook(me_path, data_only=True, read_only=True)
    geographies: dict[str, dict[str, dict[str, dict[str, float]]]] = {}

    for sheet_name in wb.sheetnames:
        grid = _grid(wb[sheet_name])
        rows = _discover(grid)
        if not rows:
            continue  # not a geography data sheet (e.g. a cover/TOC sheet)

        by_band: dict[str, dict[str, dict[str, float]]] = {}
        for (band, label), row in rows.items():
            year_values: dict[str, float] = {}
            for year in YEARS:
                val = _cell(grid, row, metric_col(year))
                if isinstance(val, (int, float)):
                    year_values[str(year)] = round(float(val), 4)
            if year_values:
                by_band.setdefault(band.value, {})[label] = year_values

        if by_band:
            geographies[sheet_name] = by_band

    return {"workbook": me_path.name, "geographies": geographies}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--market", default=None, help="Market name -- auto-finds its ME workbook in agentGeneratedMEWorkbook/")
    ap.add_argument("--input", default=None, help="Explicit path to an ME workbook .xlsx")
    ap.add_argument("--save-to", default=None, help="Output path (default: output/market_json/<slug>_ME.json)")
    args = ap.parse_args()

    if args.input:
        me_path = Path(args.input)
    elif args.market:
        me_path = _find_me_workbook(args.market)
    else:
        print("[ERROR] Pass --market or --input.")
        return 1

    if not me_path.exists():
        print(f"[ERROR] ME workbook not found: {me_path}")
        return 1

    result = export_me_json(me_path)

    slug = re.sub(r"[^a-z0-9]+", "_", me_path.stem.lower()).strip("_")
    out = Path(args.save_to) if args.save_to else OUT_DIR / f"{slug}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Workbook: {result['workbook']}")
    print(f"Geographies: {len(result['geographies'])}")
    for geo, bands in result["geographies"].items():
        band_summary = ", ".join(f"{b} ({len(segs)} rows)" for b, segs in bands.items())
        print(f"  {geo}: {band_summary}")
    print(f"\nSaved to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

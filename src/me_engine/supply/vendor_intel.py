"""Company list for Supply-Side from the vendor-intelligence pipeline.

The vendor-intelligence company finder lives in this repo at
`vendor_intelligence/` (its own src/ layout and .env). Supply-Side uses its
report as the company list:

    1. if my_companies/ already has a vendor-intelligence report for the
       market (e.g. olive_oil_market_global.csv), use it;
    2. otherwise run vendor_intelligence/run_query.py for the market, then copy
       its CSV (and the _coverage.json sidecar, when produced) into
       my_companies/ so later runs reuse it.

Quick LLM discovery (`discovery.py`) remains a separate fallback.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
VI_DIR = ROOT / "vendor_intelligence"
COMPANIES_DIR = ROOT / "my_companies"
_STOPWORDS = {"global", "market", "the"}


def _words(market: str) -> list[str]:
    return [w for w in re.sub(r"[^a-z0-9]+", " ", market.lower()).split() if w not in _STOPWORDS]


def report_basename(market: str) -> str:
    return "_".join(_words(market)) + "_market_global"


def find_company_file(market: str) -> Path | None:
    """An existing vendor-intelligence report for this market in my_companies/.
    Quick-discovery files (*_discovered.csv) and sidecars don't count; CSV
    preferred over XLSX."""
    words = _words(market)
    hits = []
    for f in COMPANIES_DIR.glob("*.*"):
        name = f.stem.lower()
        if f.suffix.lower() not in (".csv", ".xlsx") or name.endswith("_discovered") or name.endswith("_coverage"):
            continue
        if words and all(w in name for w in words):
            hits.append(f)
    return sorted(hits, key=lambda f: (f.suffix.lower() != ".csv", len(f.name)))[0] if hits else None


def coverage_for(company_file: Path) -> dict | None:
    """The _coverage.json sidecar written next to a vendor-intelligence report."""
    side = company_file.with_name(company_file.stem + "_coverage.json")
    if side.exists():
        try:
            return json.loads(side.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return None


def run_vendor_intelligence(market: str, time_budget_min: int = 15, timeout_min: int = 40) -> Path | None:
    """Run the in-repo vendor-intelligence pipeline for `market` (global) and
    copy its report into my_companies/. Returns the copied CSV, or None."""
    if not (VI_DIR / "run_query.py").exists():
        return None
    query = market if market.lower().rstrip().endswith("market") else f"{market} Market"
    out = report_basename(market)
    out_dir = VI_DIR / "output" / "supply_side"
    out_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "MARKET_QUERY_OUTPUT_DIR": str(out_dir), "PYTHONIOENCODING": "utf-8"}
    cmd = [sys.executable, "run_query.py", "--query", query, "--out", out, "--time-budget-min", str(time_budget_min)]
    proc = subprocess.run(cmd, cwd=VI_DIR, env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout_min * 60)
    if proc.returncode != 0 and "unrecognized arguments: --time-budget-min" in proc.stderr:
        proc = subprocess.run(cmd[:-2], cwd=VI_DIR, env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout_min * 60)
    (out_dir / f"{out}.log").write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")
    csv_path = next(iter(sorted(out_dir.glob(f"{out}.csv"))), None) or next(iter(sorted(VI_DIR.glob(f"output/**/{out}.csv"))), None)
    if csv_path is None:
        return None
    COMPANIES_DIR.mkdir(exist_ok=True)
    target = COMPANIES_DIR / f"{out}.csv"
    shutil.copy2(csv_path, target)
    side = csv_path.with_name(f"{out}_coverage.json")
    if side.exists():
        shutil.copy2(side, COMPANIES_DIR / f"{out}_coverage.json")
    return target


def company_file_for(market: str, run_if_missing: bool = True) -> tuple[Path | None, str]:
    """(file, how it was obtained) -- existing report, a fresh run, or nothing."""
    existing = find_company_file(market)
    if existing:
        return existing, f"vendor-intelligence report: {existing.name}"
    if run_if_missing:
        fresh = run_vendor_intelligence(market)
        if fresh:
            return fresh, f"vendor-intelligence report (run now): {fresh.name}"
    return None, "no vendor-intelligence report"

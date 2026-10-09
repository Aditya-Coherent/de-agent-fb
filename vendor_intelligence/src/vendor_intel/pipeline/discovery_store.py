"""Persistent cross-run discovery store — the union of independently-discovered companies per market.

Per-run web discovery is non-deterministic: each run surfaces a different ~60% slice of the market.
This store turns that volatility into steadily-rising cumulative coverage. Each run RE-INJECTS the
stored companies as ordinary CANDIDATES (is_seed=False), so they are re-crawled and re-classified
every run — the final export always reflects *current* classification, never a stale verdict, so a
noisy entry just gets re-filtered (it never pollutes output). After the run, the companies that
classified relevant are merged back in (union by domain). Best-effort; never raises.

Store lives at output/.discovery_store/<market_slug>__<geo_slug>.json, keyed by market AND
target geography so a run for "laptops in India" never re-injects / gets polluted by companies
confirmed for "laptops in Germany". Disable with PIPELINE_DISCOVERY_STORE=0.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any


def enabled() -> bool:
    return os.getenv("PIPELINE_DISCOVERY_STORE", "1").strip().lower() not in ("0", "false", "no", "off")


def _slug(market: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(market or "").lower()).strip("_")[:80] or "market"


def _geo_slug(geo: str) -> str:
    g = re.sub(r"[^a-z0-9]+", "_", str(geo or "").lower()).strip("_")[:40]
    return g or "global"


def _norm_dom(d: str) -> str:
    d = str(d or "").strip().lower()
    return d.removeprefix("http://").removeprefix("https://").removeprefix("www.").split("/")[0]


def _store_path(market: str, geo: str = ""):
    from pathlib import Path

    from vendor_intel.config import _project_root

    d = _project_root() / "output" / ".discovery_store"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        return None
    return d / f"{_slug(market)}__{_geo_slug(geo)}.json"


_FIELDS = ("role", "value_chain_section", "company_summary", "company_function")


def load_discovered(market: str, geo: str = "") -> list[dict[str, str]]:
    """Return previously-discovered companies for this market + geography (union of past runs).
    Each row carries name + domain, plus role/section/summary when known (so a carried company
    is output-complete)."""
    p = _store_path(market, geo)
    if not p or not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = data.get("companies") if isinstance(data, dict) else data
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        dom = _norm_dom(r.get("domain"))
        nm = str(r.get("name") or "").strip()
        if dom and "." in dom and nm and dom not in seen:
            seen.add(dom)
            row = {"name": nm, "domain": dom}
            for f in _FIELDS:
                if r.get(f):
                    row[f] = str(r.get(f))
            out.append(row)
    return out


def save_discovered(market: str, companies: list[dict[str, Any]], geo: str = "") -> int:
    """Union newly-confirmed companies into the store (keyed by domain, scoped to market+geo).
    Returns new store size."""
    p = _store_path(market, geo)
    if not p:
        return 0
    merged: dict[str, dict[str, str]] = {}
    for c in load_discovered(market, geo):
        merged[c["domain"]] = c
    for c in companies or []:
        dom = _norm_dom(c.get("domain"))
        nm = str(c.get("name") or c.get("company") or "").strip()
        if dom and "." in dom and nm:
            row = {"name": nm, "domain": dom}
            for f in _FIELDS:
                v = c.get(f) or (c.get("summary") if f == "company_summary" else "")
                if v:
                    row[f] = str(v)
            merged[dom] = row
    try:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"market": market, "geography": geo, "companies": list(merged.values())}, indent=2),
            encoding="utf-8",
        )
        tmp.replace(p)
    except Exception:
        pass
    return len(merged)

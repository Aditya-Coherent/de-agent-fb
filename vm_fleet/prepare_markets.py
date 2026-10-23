"""Turns the AgriIQX priority workbook into vm_fleet/markets.jsonl, one market per line:
{"order", "sheet", "row", "market", "slug", "definition": {definition, inclusions, exclusions}}.

The order is sheet 1 first, then 2, 3, 4, each in the sheet's own order, so the queue
runs the highest-priority titles first. `slug` matches run_fleet._slug, so a market's
output lands in AiAgentsFleet/output/<slug>/.

    python vm_fleet/prepare_markets.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
XLSX = ROOT / "AiAgentsFleet" / "AllMarkets" / "Copy of Priority 2 Whitepaper 400 Titles_ AgriIQX Platform.xlsx"
OUT = Path(__file__).resolve().parent / "markets.jsonl"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-") or "market"


def read_markets(xlsx: Path = XLSX) -> list[dict]:
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    out, seen = [], set()
    for ws in wb.worksheets:
        rows = ws.iter_rows(values_only=True)
        header = [str(h or "").strip().lower() for h in next(rows)]
        col = {k: next(i for i, h in enumerate(header) if k in h)
               for k in ("topic", "definition", "inclusion", "exclusion")}
        for r in rows:
            name = str(r[col["topic"]] or "").strip()
            if not name:
                continue
            s = slug(name)
            if s in seen:                      # one run per title, even if two sheets list it
                continue
            seen.add(s)
            out.append({"order": len(out) + 1, "sheet": ws.title, "row": r[0], "market": name, "slug": s,
                        "definition": {"definition": str(r[col["definition"]] or "").strip(),
                                       "inclusions": str(r[col["inclusion"]] or "").strip(),
                                       "exclusions": str(r[col["exclusion"]] or "").strip()}})
    return out


def read_csv_markets(csv_path: Path, slugs: list[str]) -> list[dict]:
    """Named markets from a tracker CSV (markets_priority1.csv: order, priority, sheet, s_no, market, slug,
    definition, inclusions, exclusions, ...) in the same shape as read_markets."""
    import csv
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        rows = {r["slug"]: r for r in csv.DictReader(fh)}
    missing = [s for s in slugs if s not in rows]
    if missing:
        raise SystemExit(f"not in {csv_path.name}: {', '.join(missing)}")
    return [{"order": 0, "sheet": f"priority{rows[s]['priority']}:{rows[s]['sheet']}", "row": rows[s]["s_no"],
             "market": rows[s]["market"], "slug": s,
             "definition": {"definition": rows[s]["definition"], "inclusions": rows[s]["inclusions"],
                            "exclusions": rows[s]["exclusions"]}} for s in slugs]


def main() -> int:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--also", default="", help="comma-separated slugs from markets_priority1.csv to queue first")
    p.add_argument("--then-priority1", action="store_true",
                   help="queue every Priority 1 market (markets_priority1.csv) AFTER the Priority 2 list")
    a = p.parse_args()
    markets = read_markets()
    p1_csv = XLSX.parent / "markets_priority1.csv"
    if a.also:
        extra = read_csv_markets(p1_csv, [s.strip() for s in a.also.split(",") if s.strip()])
        have = {m["slug"] for m in markets}
        markets = [m for m in extra if m["slug"] not in have] + markets
    if a.then_priority1:
        import csv
        with open(p1_csv, encoding="utf-8-sig", newline="") as fh:
            p1_slugs = [r["slug"] for r in csv.DictReader(fh) if r.get("slug")]
        have = {m["slug"] for m in markets}
        tail = [m for m in read_csv_markets(p1_csv, p1_slugs) if m["slug"] not in have]
        base = max(m["order"] for m in markets)
        for k, m in enumerate(tail, 1):           # numbered after Priority 2, so workers reach them last
            m["order"] = base + k
        markets += tail
    OUT.write_text("".join(json.dumps(m, ensure_ascii=False) + "\n" for m in markets), encoding="utf-8")
    print(f"Wrote {len(markets)} markets to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Scan the news for events that change a market's growth, and show how much
they move the forecast (optionally applying it to a Data file).

    python market_events.py --market "Global Crude Oil Market"
    python market_events.py --market "Global High Energy Laser Market" \
        --data-file "output/hel/Data File - ... (v8b, human structure).xlsx" \
        --human-data "src/newinfo/Data file-Global High Energy Laser Market.xlsx"

Writes output/events/<slug>.json and .md.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from demand_country_2a_agent import deepseek_config  # noqa: E402
from me_engine.generation import events_agent  # noqa: E402
from me_engine.generation.llm import GenLLM  # noqa: E402

DEFAULT_REGIONS = {"North America": ["U.S.", "Canada"], "Europe": ["Germany", "U.K.", "France", "Russia"],
                   "Asia Pacific": ["China", "India", "Japan"], "Latin America": ["Brazil", "Mexico"],
                   "Middle East": ["Saudi Arabia", "Iran", "Israel", "UAE"], "Africa": ["Nigeria", "South Africa"]}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--market", required=True)
    p.add_argument("--data-file", help="apply the event deltas to this human-structured Data file (copied)")
    p.add_argument("--human-data", help="human Data file: regions/segments come from its structure")
    p.add_argument("--max-age-days", type=int, default=540)
    args = p.parse_args()
    llm = GenLLM(deepseek_config("deepseek-chat"), cache_path=ROOT / "output/.gen_cache.db")
    regions, segments = DEFAULT_REGIONS, {}
    if args.human_data:
        from me_engine.generation.skeleton import load_skeleton_from_human_sheet
        sk = load_skeleton_from_human_sheet(Path(args.human_data), args.market)
        regions = {r: list(cs) for r, cs in sk.regions.items()}
        segments = {d: (t.get("__root__") or [])[:8] for d, t in sk.nesting.items()}
    geos = [c for cs in regions.values() for c in cs]
    res = events_agent.scan(llm, args.market, segments, regions, geos, args.max_age_days)
    applied = None
    if args.data_file:
        import shutil
        src = Path(args.data_file)
        dst = src.with_name(src.stem + " (event-adjusted).xlsx")
        shutil.copyfile(src, dst)
        applied = events_agent.apply(dst, res, regions)
        applied["file"] = str(dst)
    slug = re.sub(r"[^a-z0-9]+", "-", args.market.lower()).strip("-")
    out = ROOT / "output" / "events"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{slug}.json").write_text(json.dumps({"scan": res, "applied": applied}, indent=2, default=str), encoding="utf-8")
    md = f"# {args.market}\n\n" + events_agent.markdown(res, applied)
    (out / f"{slug}.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

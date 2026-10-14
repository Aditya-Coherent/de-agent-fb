"""Copy a human market's structure, then evaluate a generated market against it.

    # 1. structure only (spec + blank ME template for run_pipeline --template)
    python evaluate_market.py structure --market "Global High Energy Laser Market" \
        --human-data "src/newinfo/Data file-Global High Energy Laser Market.xlsx" \
        --human-me "src/newinfo/ME - Global High Energy Laser Market.xlsx" --out output/hel

    # 2. evaluate a generated Data file + ME against the human pair
    python evaluate_market.py evaluate --market ... --human-data ... --human-me ... \
        --gen-data "agentGeneratedInputSheet/Data File - ....xlsx" \
        --gen-me "agentGeneratedMEWorkbook/ME - ....xlsx" --out output/hel [--narrate]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from me_engine.generation import evaluator_agent as ev  # noqa: E402
from me_engine.generation import structure_agent as sa  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Structure agent + evaluator agent")
    p.add_argument("command", choices=["structure", "evaluate"])
    p.add_argument("--market", required=True)
    p.add_argument("--human-data", required=True)
    p.add_argument("--human-me", required=True)
    p.add_argument("--gen-data")
    p.add_argument("--gen-me")
    p.add_argument("--out", default="output/evaluation")
    p.add_argument("--tag", default="", help="suffix for the report files")
    p.add_argument("--narrate", action="store_true", help="add the LLM diagnosis (DeepSeek)")
    args = p.parse_args()
    out = ROOT / args.out
    spec = sa.extract(args.human_data, args.human_me, args.market)
    sa.save(spec, out / "structure_spec.json")
    if args.command == "structure":
        info = sa.blank_template(args.human_me, out / "ME_template_blank.xlsx")
        print(f"[structure] {len(spec['geographies'])} countries, {len(spec['dimensions'])} dimensions, "
              f"volume unit '{spec['me_layout']['volume_unit']}' (x{spec['me_layout']['volume_scale']:g}); "
              f"blank template: {info['cells_cleared']:,} numbers removed")
        for c in spec["checks"]:
            print(f"  [{'ok' if c['ok'] else 'FAIL'}] {c['check']}")
        return 0
    rep = ev.evaluate(spec, Path(args.human_data), Path(args.human_me), Path(args.gen_data), Path(args.gen_me))
    narrative = ""
    if args.narrate:
        from demand_country_2a_agent import deepseek_config
        from me_engine.generation.llm import GenLLM
        narrative = ev.narrate(GenLLM(deepseek_config("deepseek-chat"), cache_path=ROOT / "output/.gen_cache.db"), rep)
    tag = f"_{args.tag}" if args.tag else ""
    (out / f"evaluation{tag}.json").write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    md = ev.write_markdown(rep, out / f"evaluation{tag}.md", narrative)
    print(rep["verdict"]["summary"])
    print(json.dumps(rep["verdict"]["grades"]))
    print(f"[report] {md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Generate a synthetic Input Sheet from evidence only (Neon DB + live web).

The generator NEVER reads numeric values from the human input sheet — it uses the
human sheet only for its label skeleton (dimension/segment/geography names). All
numbers (anchor, CAGR, shares, ASP) are decided by a LangGraph agent fleet with an
evidence-refinement reward loop, then written to an Input Sheet workbook.

Example:
    python generate_synthetic_input.py \
        --market "Global Retail Self Scanning Solutions Market" \
        --skeleton "my_inputs/Data file-Global Retail Self Scanning Solutions Market Formula.xlsx" \
        --output "agentGeneratedInputSheet/SYN - Retail Self-Scanning.xlsx"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from me_engine.generation.config import GenConfig
from me_engine.generation.dynamic_skeleton import load_skeleton_from_db
from me_engine.generation.graph import run_generation
from me_engine.generation.sheet_writer import write_input_sheet
from me_engine.generation.skeleton import load_skeleton, load_skeleton_from_human_sheet


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate a synthetic Input Sheet from evidence only.")
    p.add_argument("--market", required=True, help="Market name")
    p.add_argument("--skeleton", default=None,
                   help="Human sheet to borrow LABELS ONLY from (reference-skeleton mode)")
    p.add_argument("--from-db", action="store_true",
                   help="Build the skeleton dynamically from the DB segmentation tree (any market)")
    p.add_argument("--output", required=True, help="Output .xlsx path")
    p.add_argument("--cache", default="output/.gen_cache.db", help="LLM cache path")
    p.add_argument("--save-state", default=None, help="Optional JSON dump of the final graph state")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    cfg = GenConfig.load(ROOT)
    if not cfg.is_online:
        print("ERROR: no LLM API key found in .env (DEEPSEEK_API_KEY / OPENAI_API_KEY).")
        return 2

    if args.from_db or not args.skeleton:
        if not cfg.neon_url:
            print("ERROR: --from-db needs NEON_DATABASE_URL in .env")
            return 2
        skeleton, _ev = load_skeleton_from_db(args.market, cfg)
        if skeleton is None:
            print(f"ERROR: could not build a DB skeleton for '{args.market}'. "
                  "Try --skeleton <reference.xlsx> instead.")
            return 2
        print(f"Skeleton source: Neon DB segmentation tree")
    else:
        skeleton = load_skeleton_from_human_sheet(Path(args.skeleton), args.market) or load_skeleton(Path(args.skeleton), args.market)
        print(f"Skeleton source: reference sheet real structure ({Path(args.skeleton).name})")
    print(f"Skeleton: {len(skeleton.geographies)} geographies, {len(skeleton.nesting)} dimensions")
    print(f"Model: {cfg.model} @ {cfg.base_url} | web={'on' if cfg.enable_web else 'off'} | "
          f"loop<= {cfg.max_loop_iters} reward>= {cfg.reward_threshold}")

    cache = ROOT / args.cache
    state = run_generation(args.market, skeleton, cfg, cache_path=cache)

    print("\n--- run log ---")
    for line in state.get("log", []):
        print("  " + line)
    print(f"\nfinal reward: {state.get('reward')}  after {state.get('iter')} loop iteration(s)")

    out = write_input_sheet(state, Path(args.output))
    print(f"\nWrote synthetic Input Sheet: {out}")

    if args.save_state:
        dump = {
            "market": args.market,
            "global_anchor_2025": state.get("global_anchor_2025"),
            "global_cagr": state.get("global_cagr"),
            "regional": state.get("regional"),
            "geo": state.get("geo"),
            "shares": state.get("shares"),
            "asp": state.get("asp"),
            "artifacts": state.get("artifacts"),
            "reward": state.get("reward"),
            "iterations": state.get("iter"),
            "log": state.get("log"),
        }
        Path(args.save_state).parent.mkdir(parents=True, exist_ok=True)
        Path(args.save_state).write_text(json.dumps(dump, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Saved state: {args.save_state}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

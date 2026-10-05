"""Bottom-up, usage/application-based demand-side market-size estimator --
India-only variant.

A separate, market-agnostic methodology alongside `demand_usage_pipeline.py`
(unchanged) that adds one more hierarchy level and locks geography to India
only:

    Market -> Major Use Case -> India -> Actual Use Case -> driver -> quantity
        -> price -> value
        -> Actual Use Case totals roll up to Major Use Case total
        -> Major Use Case totals roll up to India Market Total

Same three-phase, human-approved gate structure as the non-India pipeline
(see `me_engine.demand_usage_india.*` for the reuse points), with Phase 2
(Major Use Case + India Actual Use Case + Demand Driver) bundled into ONE
approval, per the user's confirmed decision.

Example, end to end:
    python demand_usage_india_pipeline.py setup --market "Vinegar"
    # (edit output/demand_usage_india/vinegar/00_setup_draft.json if desired)
    python demand_usage_india_pipeline.py approve-setup --market "Vinegar"

    python demand_usage_india_pipeline.py taxonomy --market "Vinegar"
    # (edit output/demand_usage_india/vinegar/01_taxonomy_draft.json if desired)
    python demand_usage_india_pipeline.py approve-taxonomy --market "Vinegar"

    python demand_usage_india_pipeline.py size --market "Vinegar"
    # -> output/demand_usage_india/vinegar/02_sizing.json / .xlsx
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from me_engine.demandUseCaseCountry_approach2B import run_lock  # noqa: E402
from me_engine.demand_usage_india import checkpoints, setup, taxonomy  # noqa: E402
from me_engine.demand_usage_india.buildup_writer import write_buildup_json, write_buildup_workbook  # noqa: E402
from me_engine.demand_usage_india.graph import run_sizing_phase  # noqa: E402
from me_engine.generation.config import GenConfig  # noqa: E402

# The market-level sizing lock (`me_engine.demandUseCaseCountry_approach2B.run_lock`) is keyed
# purely by market NAME -- shared, on purpose, with the non-India pipeline's
# own lock file location. Suffixing the lock's own key keeps an India run
# from colliding with a concurrent non-India run (or vice versa) for the
# same market name, without touching `run_lock.py` itself.
_LOCK_SUFFIX = " (India)"


def _cfg() -> GenConfig:
    cfg = GenConfig.load(ROOT)
    if not cfg.is_online:
        print("ERROR: no LLM API key found in .env (DEEPSEEK_API_KEY / OPENAI_API_KEY).")
        sys.exit(2)
    return cfg


def _cache_path(args: argparse.Namespace) -> Path:
    return ROOT / args.cache


def cmd_setup(args: argparse.Namespace) -> int:
    cfg = _cfg()
    draft = setup.run_setup_phase(args.market, cfg, cache_path=_cache_path(args))
    path = checkpoints.setup_draft_path(args.market)
    print(f"India Market setup draft written to {path}\n")
    print(json.dumps(draft, indent=2, ensure_ascii=False))
    print(
        "\nEVERY FIELD ABOVE IS EDITABLE (except geography, permanently locked to India) -- edit the "
        "draft file directly if desired, then run:\n"
        f'  python demand_usage_india_pipeline.py approve-setup --market "{args.market}"'
    )
    return 0


def cmd_approve_setup(args: argparse.Namespace) -> int:
    draft_path = Path(args.draft) if args.draft else checkpoints.setup_draft_path(args.market)
    edited = checkpoints.read_json(draft_path)
    if edited is None:
        print(f"ERROR: could not read draft at {draft_path}")
        return 2
    try:
        approved = setup.approve_setup(edited)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2
    print(f"India Market setup APPROVED for '{approved['market_name']}':")
    print(json.dumps(approved, indent=2, ensure_ascii=False))
    print(f'\nNext: python demand_usage_india_pipeline.py taxonomy --market "{approved["market_name"]}"')
    return 0


def cmd_taxonomy(args: argparse.Namespace) -> int:
    cfg = _cfg()
    try:
        approved_setup = setup.load_approved_setup(args.market)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        return 2
    draft = taxonomy.run_taxonomy_phase(approved_setup, cfg, cache_path=_cache_path(args))
    path = checkpoints.taxonomy_draft_path(args.market)
    print(f"Major Use Case + India Actual Use Case + demand-driver draft written to {path}\n")
    print(json.dumps(draft, indent=2, ensure_ascii=False))
    print(
        "\nMAJOR USE CASES, ACTUAL USE CASES, AND DRIVERS ABOVE ARE EDITABLE -- edit the draft file "
        "directly if desired, then run:\n"
        f'  python demand_usage_india_pipeline.py approve-taxonomy --market "{args.market}"'
    )
    return 0


def cmd_approve_taxonomy(args: argparse.Namespace) -> int:
    draft_path = Path(args.draft) if args.draft else checkpoints.taxonomy_draft_path(args.market)
    edited = checkpoints.read_json(draft_path)
    if edited is None:
        print(f"ERROR: could not read draft at {draft_path}")
        return 2
    try:
        approved = taxonomy.approve_taxonomy(edited)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2
    print(f"Taxonomy APPROVED for '{approved['market_name']}': {len(approved['actual_use_cases'])} Actual Use Case(s)")
    print(f'\nNext: python demand_usage_india_pipeline.py size --market "{approved["market_name"]}"')
    return 0


def cmd_size(args: argparse.Namespace) -> int:
    cfg = _cfg()
    try:
        approved_setup = setup.load_approved_setup(args.market)
        approved_taxonomy = taxonomy.load_approved_taxonomy(args.market)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        return 2

    try:
        lock = run_lock.acquire_size_lock(args.market + _LOCK_SUFFIX, source="cli")
    except run_lock.SizingLockError as exc:
        print(f"ERROR: {exc}")
        return 2

    try:
        state = run_sizing_phase(approved_setup, approved_taxonomy, cfg, cache_path=_cache_path(args), source="cli")
    finally:
        lock.release()

    print("\n--- run log ---")
    for line in state.get("log", []):
        print("  " + line)

    prefix = checkpoints.sizing_output_prefix(args.market)
    json_path = write_buildup_json(state, prefix.with_suffix(".json"))
    xlsx_path = write_buildup_workbook(state, prefix.with_suffix(".xlsx"))
    print(f"\nWrote {json_path} and {xlsx_path}")

    validation = state.get("validation", {})
    eq = validation.get("evidence_quality", {})
    print("\n=== Final result ===")
    print(f"Included Demand:              ${state.get('total_market_value', 0):,.1f}M")
    print(f"India Market Total:           ${state.get('india_market_total', 0):,.1f}M")
    print("Major Use Case totals:")
    for major, total in (state.get("major_use_case_totals") or {}).items():
        print(f"  - {major}: ${total:,.1f}M")
    print(f"Related Demand (inv/reserve): ${state.get('related_demand_value', 0):,.1f}M")
    print(f"Evidence quality: verified_observed {eq.get('observed_pct', 0):.1f}%, "
          f"derived {eq.get('derived_pct', 0):.1f}%, "
          f"source_found_unverified {eq.get('source_found_unverified_pct', 0):.1f}%, "
          f"assumption {eq.get('assumption_pct', 0):.1f}%")

    if state.get("quality_flags"):
        print("\nQuality flags:")
        for f in state["quality_flags"]:
            print(f"  [{f['severity']}] {f['code']}: {f['message']}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Three-phase, human-approved usage-based India-only demand-side estimator.")
    p.add_argument("--cache", default="output/.gen_cache.db", help="LLM cache path (shared with Desk Research)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("setup", help="Phase 1: generate the India-locked Market Setup draft")
    sp.add_argument("--market", required=True)
    sp.set_defaults(fn=cmd_setup)

    sp = sub.add_parser("approve-setup", help="Approve (optionally hand-edited) Market Setup draft")
    sp.add_argument("--market", required=True)
    sp.add_argument("--draft", default=None, help="Path to the draft JSON; defaults to the standard draft path")
    sp.set_defaults(fn=cmd_approve_setup)

    sp = sub.add_parser("taxonomy", help="Phase 2: generate the Major Use Case + India Actual Use Case + Driver draft")
    sp.add_argument("--market", required=True)
    sp.set_defaults(fn=cmd_taxonomy)

    sp = sub.add_parser("approve-taxonomy", help="Approve (optionally hand-edited) Taxonomy draft")
    sp.add_argument("--market", required=True)
    sp.add_argument("--draft", default=None)
    sp.set_defaults(fn=cmd_approve_taxonomy)

    sp = sub.add_parser("size", help="Phase 3: run the deterministic evidence-first sizing build-up + Major Use Case rollup")
    sp.add_argument("--market", required=True)
    sp.set_defaults(fn=cmd_size)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

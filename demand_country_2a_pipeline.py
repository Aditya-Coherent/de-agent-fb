"""Demand-Side Approach 2A: country-level total demand -> region -> global,
then a top-down split for every segment.

    Approved Market Setup -> Approved total-demand driver
        -> Evidence-first Quantity x Price per country -> Country Total
        -> Region -> Global                                   (bottom-up)
        -> every segment dimension = Country Total x share    (top-down)

Same human-approved gate structure as `demand_usage_pipeline.py` (2B). The
sizing step reuses 2B's evidence-first sizing graph with a single
total-demand line per country (see `me_engine.demandCountry_approach2A`).

Example, end to end:
    python demand_country_2a_pipeline.py setup --market "Vinegar"
    # (edit output/demand_country_2a/vinegar/00_setup_draft.json if desired)
    python demand_country_2a_pipeline.py approve-setup --market "Vinegar"

    python demand_country_2a_pipeline.py driver --market "Vinegar"
    python demand_country_2a_pipeline.py approve-driver --market "Vinegar"

    python demand_country_2a_pipeline.py size --market "Vinegar"
    # -> output/demand_country_2a/vinegar/02_sizing.json / .xlsx

    python demand_country_2a_pipeline.py segments --market "Vinegar"
    python demand_country_2a_pipeline.py approve-segments --market "Vinegar"
    python demand_country_2a_pipeline.py report --market "Vinegar"
    # -> output/demand_country_2a/vinegar/04_market.json / .xlsx
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from me_engine.demandCountry_approach2A import checkpoints, driver, segmentation, setup  # noqa: E402
from me_engine.demandCountry_approach2A.graph import run_sizing_phase  # noqa: E402
from me_engine.demandUseCaseCountry_approach2B import run_lock  # noqa: E402
from me_engine.demandUseCaseCountry_approach2B.buildup_writer import (  # noqa: E402
    build_report_dict, write_buildup_workbook,
)
from me_engine.demand_topdown.writer import format_terminal_summary  # noqa: E402
from me_engine.generation.config import GenConfig  # noqa: E402

# Keeps a 2A sizing run from colliding with a concurrent 2B run of the same market.
_LOCK_SUFFIX = " (2A)"
_CLI = "python demand_country_2a_pipeline.py"


def _cfg() -> GenConfig:
    cfg = GenConfig.load(ROOT)
    if not cfg.is_online:
        print("ERROR: no LLM API key found in .env (DEEPSEEK_API_KEY / OPENAI_API_KEY).")
        sys.exit(2)
    return cfg


def _cache_path(args: argparse.Namespace) -> Path:
    return ROOT / args.cache


def _read_draft(args: argparse.Namespace, default: Path) -> dict | None:
    path = Path(args.draft) if args.draft else default
    data = checkpoints.read_json(path)
    if data is None:
        print(f"ERROR: could not read draft at {path}")
    return data


def cmd_setup(args: argparse.Namespace) -> int:
    draft = setup.run_setup_phase(args.market, _cfg(), cache_path=_cache_path(args))
    print(f"2A market setup draft written to {checkpoints.setup_draft_path(args.market)}\n")
    print(json.dumps(draft, indent=2, ensure_ascii=False))
    print(f'\nEdit the draft if desired (countries, base year, units), then run:\n  {_CLI} approve-setup --market "{args.market}"')
    return 0


def cmd_approve_setup(args: argparse.Namespace) -> int:
    edited = _read_draft(args, checkpoints.setup_draft_path(args.market))
    if edited is None:
        return 2
    try:
        approved = setup.approve_setup(edited)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2
    print(f"2A setup APPROVED for '{approved['market_name']}': {len(approved['geography'])} countries")
    print(f'\nNext: {_CLI} driver --market "{approved["market_name"]}"')
    return 0


def cmd_driver(args: argparse.Namespace) -> int:
    cfg = _cfg()
    try:
        approved_setup = setup.load_approved_setup(args.market)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        return 2
    draft = driver.run_driver_phase(approved_setup, cfg, cache_path=_cache_path(args))
    print(f"2A total-demand driver draft written to {checkpoints.driver_draft_path(args.market)}\n")
    print(json.dumps({"usage_buckets": draft["usage_buckets"], "drivers": draft["drivers"]}, indent=2, ensure_ascii=False))
    print(f'\nEdit the driver if desired, then run:\n  {_CLI} approve-driver --market "{args.market}"')
    return 0


def cmd_approve_driver(args: argparse.Namespace) -> int:
    edited = _read_draft(args, checkpoints.driver_draft_path(args.market))
    if edited is None:
        return 2
    try:
        approved = driver.approve_driver(edited)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2
    print(f"2A driver APPROVED for '{approved['market_name']}'")
    print(f'\nNext: {_CLI} size --market "{approved["market_name"]}"')
    return 0


def cmd_size(args: argparse.Namespace) -> int:
    cfg = _cfg()
    try:
        approved_setup = setup.load_approved_setup(args.market)
        approved_driver = driver.load_approved_driver(args.market)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        return 2
    try:
        lock = run_lock.acquire_size_lock(args.market + _LOCK_SUFFIX, source="cli")
    except run_lock.SizingLockError as exc:
        print(f"ERROR: {exc}")
        return 2
    try:
        state = run_sizing_phase(approved_setup, approved_driver, cfg, cache_path=_cache_path(args), source="cli")
    finally:
        lock.release()

    print("\n--- run log ---")
    for line in state.get("log", []):
        print("  " + line)

    prefix = checkpoints.sizing_output_prefix(args.market)
    report = build_report_dict(state)
    report["methodology"] = "demand_side_country_total_2a"
    report["country_region"] = state.get("country_region", {})
    report["region_totals_usd_mn"] = state.get("region_totals", {})
    json_path = checkpoints.write_json(prefix.with_suffix(".json"), report)
    xlsx_path = write_buildup_workbook(state, prefix.with_suffix(".xlsx"))
    print(f"\nWrote {json_path} and {xlsx_path}")

    eq = (state.get("validation") or {}).get("evidence_quality", {})
    print("\n=== Country -> Region -> Global (USD Mn) ===")
    print(f"Global: ${state.get('total_market_value', 0):,.1f}M")
    for region, value in (state.get("region_totals") or {}).items():
        print(f"  {region}: ${value:,.1f}M")
        for country, cv in (state.get("country_totals") or {}).items():
            if state["country_region"].get(country) == region:
                print(f"      {country}: ${cv:,.1f}M")
    print(f"Evidence quality: verified_observed {eq.get('observed_pct', 0):.1f}%, "
          f"derived {eq.get('derived_pct', 0):.1f}%, "
          f"source_found_unverified {eq.get('source_found_unverified_pct', 0):.1f}%, "
          f"assumption {eq.get('assumption_pct', 0):.1f}%")
    if state.get("quality_flags"):
        print("\nQuality flags:")
        for f in state["quality_flags"]:
            print(f"  [{f['severity']}] {f['code']}: {f['message']}")
    print(f'\nNext: {_CLI} segments --market "{args.market}"')
    return 0


def cmd_segments(args: argparse.Namespace) -> int:
    cfg = _cfg()
    try:
        draft = segmentation.run_segments_phase(args.market, cfg, cache_path=_cache_path(args))
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        return 2
    print(f"2A segmentation draft written to {checkpoints.segments_draft_path(args.market)}\n")
    for d in draft["top_down_dimensions"]:
        print(f"[top-down] {d['dimension']} ({d['taxonomy_source']}, {d['evidence_status']}): {d['segments']}")
    print(f'\nDimensions, segments and shares are editable. Then run:\n  {_CLI} approve-segments --market "{args.market}"')
    return 0


def cmd_approve_segments(args: argparse.Namespace) -> int:
    edited = _read_draft(args, checkpoints.segments_draft_path(args.market))
    if edited is None:
        return 2
    try:
        approved = segmentation.approve_segments(edited)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2
    print(f"2A segmentation APPROVED for '{approved['market_name']}': "
          f"{len(approved['top_down_dimensions'])} dimension(s)")
    print(f'\nNext: {_CLI} report --market "{approved["market_name"]}"')
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    try:
        report, json_path, xlsx_path = segmentation.run_report_phase(args.market)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        return 2
    print(format_terminal_summary(report))
    print(f"\nWrote {json_path} and {xlsx_path}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Demand-Side 2A: country total demand -> global, top-down segments.")
    p.add_argument("--cache", default="output/.gen_cache.db", help="LLM cache path (shared with the other pipelines)")
    sub = p.add_subparsers(dest="cmd", required=True)

    commands = [
        ("setup", cmd_setup, "Phase 1: Market Setup draft (scope, countries, base year, units)", False),
        ("approve-setup", cmd_approve_setup, "Approve the (edited) Market Setup draft", True),
        ("driver", cmd_driver, "Phase 2: total-demand driver draft (one line per country)", False),
        ("approve-driver", cmd_approve_driver, "Approve the (edited) driver draft", True),
        ("size", cmd_size, "Phase 3: evidence-first country totals -> region -> global", False),
        ("segments", cmd_segments, "Phase 4: propose segments + per-country shares", False),
        ("approve-segments", cmd_approve_segments, "Approve the (edited) segmentation draft", True),
        ("report", cmd_report, "Phase 5: top-down split + final market report", False),
    ]
    for name, fn, help_text, has_draft in commands:
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--market", required=True)
        if has_draft:
            sp.add_argument("--draft", default=None, help="Path to the draft JSON; defaults to the standard path")
        sp.set_defaults(fn=fn)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

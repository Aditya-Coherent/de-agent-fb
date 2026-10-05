"""Bottom-up, usage/application-based demand-side market-size estimator --
V3: three-phase, human-approved pipeline.

Independent methodology alongside `generate_synthetic_input.py` (Desk
Research) and the supply-side company-based estimator. This pipeline
CONSTRUCTS the market size from underlying demand:

    Approved Market Setup -> Approved Usage/Driver Taxonomy
        -> Evidence-first Quantity -> Price -> Value -> Country Total
        -> Global Total

Each phase writes a draft JSON a human can edit; nothing downstream runs
until the matching `approve-*` subcommand writes the `*_approved.json` file
(see `me_engine.demandUseCaseCountry_approach2B.checkpoints`). Every number in the final output
carries a provenance tag (observed / derived / assumption).

Example, end to end:
    python demand_usage_pipeline.py setup --market "Crude Oil"
    # (edit output/demand_usage/crude-oil/00_setup_draft.json if desired)
    python demand_usage_pipeline.py approve-setup --market "Crude Oil"

    python demand_usage_pipeline.py taxonomy --market "Crude Oil"
    # (edit output/demand_usage/crude-oil/01_taxonomy_draft.json if desired)
    python demand_usage_pipeline.py approve-taxonomy --market "Crude Oil"

    python demand_usage_pipeline.py size --market "Crude Oil"
    # -> output/demand_usage/crude-oil/02_sizing.json / .xlsx

    # Approach 2B, part 2: Application stays bottom-up, every other segment
    # is split top-down from the country totals.
    python demand_usage_pipeline.py segments --market "Crude Oil"
    # (edit output/demand_usage/crude-oil/03_segments_draft.json if desired)
    python demand_usage_pipeline.py approve-segments --market "Crude Oil"
    python demand_usage_pipeline.py report --market "Crude Oil"
    # -> output/demand_usage/crude-oil/04_market.json / .xlsx
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from me_engine.demandUseCaseCountry_approach2B import checkpoints, run_lock, run_report, setup, taxonomy  # noqa: E402
from me_engine.demandUseCaseCountry_approach2B.buildup_writer import write_buildup_json, write_buildup_workbook  # noqa: E402
from me_engine.demandUseCaseCountry_approach2B import segmentation  # noqa: E402
from me_engine.demandUseCaseCountry_approach2B.graph import run_sizing_phase  # noqa: E402
from me_engine.demand_topdown.writer import format_terminal_summary  # noqa: E402
from me_engine.generation.config import GenConfig  # noqa: E402


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
    print(f"Market setup draft written to {path}\n")
    print(json.dumps(draft, indent=2, ensure_ascii=False))
    print(
        "\nEVERY FIELD ABOVE IS EDITABLE -- edit the draft file directly if desired, then run:\n"
        f'  python demand_usage_pipeline.py approve-setup --market "{args.market}"'
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
    print(f"Market setup APPROVED for '{approved['market_name']}':")
    print(json.dumps(approved, indent=2, ensure_ascii=False))
    print(f'\nNext: python demand_usage_pipeline.py taxonomy --market "{approved["market_name"]}"')
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
    print(f"Taxonomy + demand-driver draft written to {path}\n")
    print(json.dumps(draft, indent=2, ensure_ascii=False))
    print(
        "\nUSAGE BUCKETS AND DRIVERS ABOVE ARE EDITABLE (add/remove/rename usage, edit driver, change "
        f"physical unit) -- edit the draft file directly if desired, then run:\n"
        f'  python demand_usage_pipeline.py approve-taxonomy --market "{args.market}"'
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
    print(f"Taxonomy APPROVED for '{approved['market_name']}': {len(approved['usage_buckets'])} usage bucket(s)")
    print(f'\nNext: python demand_usage_pipeline.py size --market "{approved["market_name"]}"')
    return 0


def cmd_size(args: argparse.Namespace) -> int:
    cfg = _cfg()
    try:
        approved_setup = setup.load_approved_setup(args.market)
        approved_taxonomy = taxonomy.load_approved_taxonomy(args.market)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        return 2

    # Runtime-hardening (2026-08-24): market-level sizing lock -- refuses a
    # second concurrent `size` run for the same market, whether the other
    # one is another CLI process or the backend SSE route (see
    # `run_lock.py`). Does not change sizing methodology at all.
    try:
        lock = run_lock.acquire_size_lock(args.market, source="cli")
    except run_lock.SizingLockError as exc:
        print(f"ERROR: {exc}")
        return 2

    try:
        try:
            state = run_sizing_phase(approved_setup, approved_taxonomy, cfg, cache_path=_cache_path(args), source="cli")
        except Exception as exc:  # noqa: BLE001 -- record a failure report, then re-raise unchanged
            # Reporting addendum (2026-08-26): never overwrites a prior
            # successful SIZE_RUN_RESULT.md -- only ever writes the separate
            # SIZE_RUN_RESULT.partial.md, from whatever `02_sizing.progress.json`
            # last recorded. Does not change this command's own error
            # handling/exit behavior at all (same exception, re-raised as-is).
            run_report.write_failure_report(args.market, error=str(exc))
            raise
    finally:
        lock.release()

    print("\n--- run log ---")
    for line in state.get("log", []):
        print("  " + line)

    prefix = checkpoints.sizing_output_prefix(args.market)
    json_path = write_buildup_json(state, prefix.with_suffix(".json"))
    xlsx_path = write_buildup_workbook(state, prefix.with_suffix(".xlsx"))
    print(f"\nWrote {json_path} and {xlsx_path}")

    # Reporting addendum (2026-08-26): one consolidated, human-readable
    # SIZE_RUN_RESULT.md, built entirely from the same `state` just written
    # to 02_sizing.json/.xlsx above -- no new calculation, purely a reporting
    # layer. `write_success_report` re-asserts `validation` is present (the
    # same guarantee `run_sizing_phase` already enforces) before writing.
    report_summary = run_report.build_success_summary(
        state, args.market, json_path=json_path, xlsx_path=xlsx_path,
        worker_count=getattr(cfg, "evidence_workers", None),
    )
    report_file = run_report.write_success_report(
        state, args.market, json_path=json_path, xlsx_path=xlsx_path,
        worker_count=getattr(cfg, "evidence_workers", None),
    )
    print("\n" + run_report.format_terminal_summary(report_summary, str(report_file)))

    validation = state.get("validation", {})
    eq = validation.get("evidence_quality", {})
    print("\n=== Final result ===")
    print(f"Included Demand:            ${state.get('total_market_value', 0):,.1f}M")
    print(f"Related Demand (inv/reserve): ${state.get('related_demand_value', 0):,.1f}M")
    print(f"Residual/Uncovered (implied): ${(validation.get('residual_implied_usd_mn') or 0):,.1f}M")
    print(f"Reported Market View:        ${validation.get('reported_market_view_usd_mn', 0):,.1f}M")
    print(f"Evidence quality: verified_observed {eq.get('observed_pct', 0):.1f}%, "
          f"derived {eq.get('derived_pct', 0):.1f}%, "
          f"source_found_unverified {eq.get('source_found_unverified_pct', 0):.1f}%, "
          f"assumption {eq.get('assumption_pct', 0):.1f}%")

    if state.get("quality_flags"):
        print("\nQuality flags:")
        for f in state["quality_flags"]:
            print(f"  [{f['severity']}] {f['code']}: {f['message']}")
    return 0


def cmd_segments(args: argparse.Namespace) -> int:
    cfg = _cfg()
    try:
        draft = segmentation.run_segments_phase(args.market, cfg, cache_path=_cache_path(args))
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        return 2
    print(f"Segmentation draft written to {segmentation.segments_draft_path(args.market)}\n")
    for d in draft["bottom_up_dimensions"]:
        print(f"[bottom-up] {d['dimension']}: {d['segments']}")
    for d in draft["top_down_dimensions"]:
        print(f"[top-down ] {d['dimension']} ({d['taxonomy_source']}, {d['evidence_status']}): {d['segments']}")
    print(
        "\nTOP-DOWN DIMENSIONS, SEGMENTS AND SHARES ARE EDITABLE -- edit the draft file if desired, then run:\n"
        f'  python demand_usage_pipeline.py approve-segments --market "{args.market}"'
    )
    return 0


def cmd_approve_segments(args: argparse.Namespace) -> int:
    draft_path = Path(args.draft) if args.draft else segmentation.segments_draft_path(args.market)
    edited = checkpoints.read_json(draft_path)
    if edited is None:
        print(f"ERROR: could not read draft at {draft_path}")
        return 2
    try:
        approved = segmentation.approve_segments(edited)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2
    print(f"Segmentation APPROVED for '{approved['market_name']}': "
          f"{len(approved['top_down_dimensions'])} top-down dimension(s)")
    print(f'\nNext: python demand_usage_pipeline.py report --market "{approved["market_name"]}"')
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
    p = argparse.ArgumentParser(description="Three-phase, human-approved usage-based demand-side estimator.")
    p.add_argument("--cache", default="output/.gen_cache.db", help="LLM cache path (shared with Desk Research)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("setup", help="Phase 1: generate the Market Setup draft")
    sp.add_argument("--market", required=True)
    sp.set_defaults(fn=cmd_setup)

    sp = sub.add_parser("approve-setup", help="Approve (optionally hand-edited) Market Setup draft")
    sp.add_argument("--market", required=True)
    sp.add_argument("--draft", default=None, help="Path to the draft JSON; defaults to the standard draft path")
    sp.set_defaults(fn=cmd_approve_setup)

    sp = sub.add_parser("taxonomy", help="Phase 2: generate the Usage/Driver Taxonomy draft")
    sp.add_argument("--market", required=True)
    sp.set_defaults(fn=cmd_taxonomy)

    sp = sub.add_parser("approve-taxonomy", help="Approve (optionally hand-edited) Taxonomy draft")
    sp.add_argument("--market", required=True)
    sp.add_argument("--draft", default=None)
    sp.set_defaults(fn=cmd_approve_taxonomy)

    sp = sub.add_parser("size", help="Phase 3: run the deterministic evidence-first sizing build-up")
    sp.add_argument("--market", required=True)
    sp.set_defaults(fn=cmd_size)

    sp = sub.add_parser("segments", help="Phase 4: propose segments + shares (Application stays bottom-up)")
    sp.add_argument("--market", required=True)
    sp.set_defaults(fn=cmd_segments)

    sp = sub.add_parser("approve-segments", help="Approve (optionally hand-edited) segmentation draft")
    sp.add_argument("--market", required=True)
    sp.add_argument("--draft", default=None)
    sp.set_defaults(fn=cmd_approve_segments)

    sp = sub.add_parser("report", help="Phase 5: top-down split + country/region/global market report")
    sp.add_argument("--market", required=True)
    sp.set_defaults(fn=cmd_report)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

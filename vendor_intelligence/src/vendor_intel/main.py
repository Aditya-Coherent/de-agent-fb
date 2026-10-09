"""CLI entry: python run_cli.py "your query" """

from __future__ import annotations

import argparse
import json

from vendor_intel.config import Settings
from vendor_intel.live_checks import LiveConfigError, print_run_banner, validate_live_settings
from vendor_intel.mock.fixtures import is_mock_run
from vendor_intel.orchestrator import run_pipeline_sync


def main() -> None:
    parser = argparse.ArgumentParser(description="Run vendor intelligence pipeline")
    parser.add_argument("query", nargs="?", default="Give me the best laptop companies in India")
    parser.add_argument("--mock", action="store_true", help="Force mock/demo mode (hardcoded data)")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Force live mode (USE_MOCK_DATA=false); requires ANTHROPIC_API_KEY",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print full JSON")
    parser.add_argument(
        "--csv-dir",
        default=None,
        help="Directory for CSV output (default: output/ under project root)",
    )
    parser.add_argument(
        "--no-csv",
        action="store_true",
        help="Skip writing CSV files (not recommended)",
    )
    args = parser.parse_args()

    settings = Settings.load()
    if args.live:
        settings = settings.model_copy(update={"use_mock_data": False, "mock_mode": False})
    if args.mock:
        settings = settings.model_copy(update={"mock_mode": True, "use_mock_data": True})

    warnings = validate_live_settings(settings)
    print_run_banner(settings, warnings)
    if args.no_csv:
        settings = settings.model_copy(update={"csv_output_enabled": False})
    if args.csv_dir:
        settings = settings.model_copy(update={"csv_output_dir": args.csv_dir})

    try:
        result = run_pipeline_sync(args.query, settings)
    except LiveConfigError as e:
        print(f"\nConfiguration error:\n{e}\n")
        raise SystemExit(1) from e
    if args.as_json:
        print(json.dumps(result.model_dump(), indent=2))
    else:
        print(f"\nQuery: {result.query}")
        print(f"Summary: {result.interpretation_summary}")
        print(f"Mode: {'mock' if is_mock_run(settings) else 'live'}")
        print(f"\nCompany list ({len(result.company_list)}):")
        for i, c in enumerate(result.company_list, 1):
            print(f"  {i}. {c.display_name} ({c.parent_group}) score={c.score} tier={c.tier}")
        print(f"\nParent groups ({len(result.parent_group_list)}):")
        for p in result.parent_group_list:
            print(f"  - {p.parent_name}: {', '.join(p.brands_in_company_list)} [{p.listing_mode}]")
        if result.suppressed_brands:
            print(f"\nSuppressed ({len(result.suppressed_brands)}):")
            for s in result.suppressed_brands[:8]:
                print(f"  - {s.name}: {s.reason[:60]}")
        if result.warnings:
            print("\nWarnings:", "; ".join(result.warnings))
        if result.csv_paths:
            print("\nCSV output:")
            for name, path in result.csv_paths.items():
                print(f"  {name}: {path}")


if __name__ == "__main__":
    main()

"""One-command orchestrator: whitepaper_fields.json -> consistency pass ->
validation -> charts -> content plan -> .docx, for one market already run
through `AiAgentsFleet/run_fleet.py`.

Usage:
    python AiAgentsFleet/report/run_report.py --market-slug <slug>

    # or straight from a market name (slugifies it the same way run_fleet.py does)
    python AiAgentsFleet/report/run_report.py --market "Global Vitamin C Market"

    # render anyway (with a visible warning box) if issues remain after
    # the auto-fix pass, instead of failing the run
    python AiAgentsFleet/report/run_report.py --market-slug <slug> --allow-warnings
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPORT_DIR = Path(__file__).resolve().parent
FLEET_DIR = REPORT_DIR.parent
sys.path.insert(0, str(FLEET_DIR.parent))  # repo root, so `AiAgentsFleet` is importable

from AiAgentsFleet import consistency  # noqa: E402


def _slug(market_name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", market_name.strip().lower()).strip("-")
    return s or "market"


def run_consistency_pass(market_dir: Path, market_name: str) -> tuple[list[str], bool]:
    """Reads whitepaper_fields.json, runs the consistency pass, overwrites
    it with the reconciled values (the ORIGINAL LLM/randomizer output is
    kept under "fields_before_consistency" for audit), and writes
    validation_report.json with both the before and after issue lists.

    Returns (after_issues, ok) -- ok is False if issues remain post-reconcile."""
    fields_path = market_dir / "whitepaper_fields.json"
    data = json.loads(fields_path.read_text(encoding="utf-8"))
    # Always reconcile from the ORIGINAL LLM/randomizer output when we have
    # it, so re-running the report is idempotent instead of reconciling an
    # already-reconciled file (and overwriting the audit copy with it).
    fields = data.get("fields_before_consistency") or data.get("fields", {})
    definition = data.get("market_definition") or data.get("definition_inclusion_exclusion")

    before_issues = consistency.validate(fields, market_name)
    fixed_fields, changes = consistency.reconcile(fields, market_name, definition)
    after_issues = consistency.validate(fixed_fields, market_name)

    data["fields_before_consistency"] = fields
    data["fields"] = fixed_fields
    data["consistency_changes"] = changes
    fields_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    report = {
        "market_name": market_name,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "before": {"issue_count": len(before_issues), "issues": before_issues},
        "after": {"issue_count": len(after_issues), "issues": after_issues},
        "changes_applied": changes,
    }
    (market_dir / "validation_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8",
    )

    print(f"Consistency pass: {len(before_issues)} issue(s) found before -> {len(after_issues)} remaining after.")
    for group, group_changes in changes.items():
        for c in group_changes:
            print(f"  [{group}] {c}")
    if after_issues:
        print("Remaining issues after auto-fix:")
        for i in after_issues:
            print(f"  {i}")
    return after_issues, len(after_issues) == 0


def main() -> int:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--market-slug")
    g.add_argument("--market")
    p.add_argument("--allow-warnings", action="store_true",
                    help="Render the report even if issues remain after the auto-fix pass (shows a warning box).")
    args = p.parse_args()

    slug = args.market_slug or _slug(args.market)
    market_dir = FLEET_DIR / "output" / slug
    fields_path = market_dir / "whitepaper_fields.json"
    if not fields_path.exists():
        print(f"ERROR: {fields_path} not found -- run "
              f"`python AiAgentsFleet/run_fleet.py --market \"...\"` for this market first.")
        return 2

    market_name = json.loads(fields_path.read_text(encoding="utf-8")).get("market_name", slug)
    remaining_issues, ok = run_consistency_pass(market_dir, market_name)
    if not ok and not args.allow_warnings:
        print(f"\nERROR: {len(remaining_issues)} issue(s) remain after the auto-fix pass -- "
              f"re-run with --allow-warnings to render anyway (with a visible warning box), "
              f"or inspect {market_dir / 'validation_report.json'}.")
        return 1

    steps = [
        # AI-written prose, number-guarded; cached in prose.json until the numbers
        # change. Runs before the charts: it also names each segment dimension
        # ("Shrimp Species", "Application Method"), which the chart labels use.
        [sys.executable, str(REPORT_DIR / "prose_writer.py"), "--market-slug", slug],
        [sys.executable, str(REPORT_DIR / "make_charts.py"), "--market-slug", slug],
        # The .docx is cloned from vitamin_c_market_whitepaper_v2.docx itself
        # (same layout and styling, without its disclaimer page). Prints a QA summary.
        [sys.executable, str(REPORT_DIR / "build_from_template.py"), "--market-slug", slug],
    ]
    for step in steps:
        print("+ " + " ".join(step))
        res = subprocess.run(step, cwd=str(REPORT_DIR))
        if res.returncode != 0:
            print(f"ERROR: step failed: {' '.join(step)}")
            return res.returncode

    print(f"\nDone: {market_dir / (slug + '-whitepaper.docx')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

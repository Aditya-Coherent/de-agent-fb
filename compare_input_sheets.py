"""Baseline-compare a synthetic Input Sheet against the human Input Sheet.

Example:
    python compare_input_sheets.py \
        --human "my_inputs/Data file-Global Retail Self Scanning Solutions Market Formula.xlsx" \
        --synthetic "agentGeneratedInputSheet/SYN - Retail Self-Scanning.xlsx" \
        --save-report "output/compare-retail-self-scanning.json"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from me_engine.generation.compare import compare, format_report


def main() -> int:
    p = argparse.ArgumentParser(description="Compare synthetic vs human Input Sheet.")
    p.add_argument("--human", required=True)
    p.add_argument("--synthetic", required=True)
    p.add_argument("--save-report", default=None)
    args = p.parse_args()

    rep = compare(Path(args.human), Path(args.synthetic))
    print(format_report(rep))

    if args.save_report:
        Path(args.save_report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.save_report).write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nSaved report: {args.save_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

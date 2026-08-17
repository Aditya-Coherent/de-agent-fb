"""Smoke-test runner: feeds `smoke_test_data_5000.json` through the REAL,
UNMODIFIED deterministic core of the Demand-Side pipeline.

Why this, and not `demand_usage_pipeline.py --market ...` 5000 times: the real
pipeline's only input is a market NAME -- every bucket/driver/quantity/price
is LLM-discovered from there. There is no existing "load a dataset" entry
point to target, and driving 5000 records through 5000 live LLM runs would
be neither a smoke test nor reproducible. Instead this script imports and
calls the pipeline's actual, unmodified functions -- the same ones
`demand_usage_pipeline.py` calls -- on synthetically-assembled state:

    me_engine.demand_usage.agents._FINANCIAL_ACTIVITY_TERMS   (deterministic
    me_engine.demand_usage.agents._INVENTORY_RESERVE_TERMS     classifiers used
                                                                 by usage_validation_node)
    me_engine.demand_usage.agents.calculate_node               (deterministic
                                                                 calculation +
                                                                 unit-contract gate)

Each record is evaluated as its OWN isolated single-bucket/single-country
state -- not grouped into a shared per-market state -- so one record's bad
data (e.g. a missing quantity) can never mask or crash the evaluation of
every other record. "duplicate_usage" is the one case that's inherently about
TWO records together, so it's checked separately as a structural pass over
the raw dataset, not through calculate_node.

Zero lines of `me_engine.demand_usage` are modified by this script or to
accommodate this dataset.

Usage:
    python run_pipeline_smoke_test.py --input smoke_test_data_5000.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from me_engine.demand_usage import agents  # noqa: E402  (path insert above)

_REQUIRED_IDENTITY_FIELDS = ["market_name", "country", "usage_bucket"]


def classify_bucket_category(usage_bucket: str, usage_description: str) -> str:
    """Calls the REAL deterministic regexes `usage_validation_node` uses --
    imported, not reimplemented."""
    text = f"{usage_bucket} {usage_description}"
    if agents._FINANCIAL_ACTIVITY_TERMS.search(text):
        return "excluded_financial_or_other"
    if agents._INVENTORY_RESERVE_TERMS.search(text):
        return "inventory_reserve"
    return "physical_consumption"


def _isolated_state(rec: dict[str, Any], category: str) -> dict[str, Any]:
    bucket = rec["usage_bucket"]
    country = rec["country"]
    return {
        "log": [],
        "usage_buckets": [{"name": bucket, "description": rec["usage_description"], "bucket_category": category}],
        "drivers": {bucket: {
            "unit": rec["driver_unit"], "final_demand_unit": rec["final_demand_unit"],
            "consumption_unit": rec["consumption_unit"],
        }},
        "quantities": {bucket: {country: {"value": rec["quantity"]}}},
        "prices": {bucket: {"asp_usd_per_unit": rec["asp_usd_per_unit"]}},
        "countries": [country],
    }


def run_smoke_test(records: list[dict[str, Any]]) -> dict[str, Any]:
    # --- Stage 1: ingestion gate -- required identity fields present? ---
    ingestible: list[dict[str, Any]] = []
    rejected_at_ingestion: list[dict[str, Any]] = []
    for rec in records:
        if any(not rec.get(f) for f in _REQUIRED_IDENTITY_FIELDS):
            rejected_at_ingestion.append(rec)
        else:
            ingestible.append(rec)

    # --- Stage 2: deterministic financial/inventory classification (real code) ---
    classification_counts = Counter()
    classification_correct = Counter()
    for rec in ingestible:
        cat = classify_bucket_category(rec["usage_bucket"], rec["usage_description"])
        rec["_classified_category"] = cat
        classification_counts[cat] += 1
        if rec["test_case_type"] == "financial_trading_usage":
            classification_correct["financial_trading_usage"] += int(cat == "excluded_financial_or_other")
        elif rec["test_case_type"] == "reserve_inventory_usage":
            classification_correct["reserve_inventory_usage"] += int(cat == "inventory_reserve")

    calculable = [r for r in ingestible if r["_classified_category"] != "excluded_financial_or_other"]
    financial_excluded_count = len(ingestible) - len(calculable)

    # --- Stage 3: per-record isolated calculate_node (real, unmodified) ---
    outcome_by_case: dict[str, Counter] = defaultdict(Counter)  # test_case_type -> {ok, unit_rejected, crashed}
    crashes: list[dict[str, Any]] = []
    sample_results: dict[str, dict[str, Any]] = {}  # first example per test_case_type

    for rec in calculable:
        case = rec["test_case_type"]
        state = _isolated_state(rec, rec["_classified_category"])
        try:
            out = agents.calculate_node(state)
            rejected = out.get("unit_rejected_buckets", [])
            if rejected:
                outcome_by_case[case]["unit_rejected"] += 1
            else:
                outcome_by_case[case]["ok"] += 1
            if case not in sample_results:
                sample_results[case] = {
                    "record_id": rec["record_id"], "total_market_value": out.get("total_market_value"),
                    "related_demand_value": out.get("related_demand_value"),
                    "unit_rejected_buckets": rejected,
                    "unit_contract_status": next(iter(out.get("unit_contract", {}).values()), {}).get("status"),
                }
        except Exception as exc:  # noqa: BLE001 -- a crash is a FINDING, not swallowed
            outcome_by_case[case]["crashed"] += 1
            crashes.append({
                "record_id": rec["record_id"], "test_case_type": case,
                "error": f"{type(exc).__name__}: {exc}",
            })

    # --- Structural duplicate check (inherently a two-record relationship) ---
    dup_groups: dict[tuple, list[int]] = defaultdict(list)
    for rec in records:
        if rec.get("market_name") and rec.get("country") and rec.get("usage_bucket"):
            key = (rec["market_name"], rec["country"], rec["usage_bucket"])
            dup_groups[key].append(rec["record_id"])
    genuine_duplicates = {k: v for k, v in dup_groups.items() if len(v) > 1}

    return {
        "total_records": len(records),
        "rejected_at_ingestion": len(rejected_at_ingestion),
        "rejected_at_ingestion_by_type": Counter(r["test_case_type"] for r in rejected_at_ingestion),
        "ingestible": len(ingestible),
        "classification_counts": classification_counts,
        "classification_accuracy": dict(classification_correct),
        "financial_excluded_count": financial_excluded_count,
        "calculable_records": len(calculable),
        "outcome_by_case": {k: dict(v) for k, v in outcome_by_case.items()},
        "crashes": crashes,
        "sample_results": sample_results,
        "genuine_duplicate_groups": len(genuine_duplicates),
        "genuine_duplicate_example": next(iter(genuine_duplicates.items()), None),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", default="smoke_test_data_5000.json")
    args = p.parse_args()

    records = json.loads(Path(args.input).read_text(encoding="utf-8"))
    print(f"Loaded {len(records)} synthetic records from {args.input}")

    result = run_smoke_test(records)

    print(f"\n=== Ingestion gate ===")
    print(f"  Ingestible (required fields present): {result['ingestible']}/{result['total_records']}")
    print(f"  Rejected at ingestion: {result['rejected_at_ingestion']}")
    for t, n in result["rejected_at_ingestion_by_type"].items():
        print(f"    - {t}: {n}")

    print(f"\n=== Deterministic financial/inventory classification (real regex, no LLM) ===")
    for cat, n in result["classification_counts"].items():
        print(f"  {cat}: {n}")
    print(f"  Classification accuracy on labeled edge cases:")
    for t, n in result["classification_accuracy"].items():
        print(f"    {t}: {n} correctly classified")
    print(f"  Financial records excluded before calculation: {result['financial_excluded_count']}")

    print(f"\n=== calculate_node (real, unmodified), per-record isolated evaluation ===")
    print(f"  Calculable records evaluated: {result['calculable_records']}")
    print(f"  {'test_case_type':28s} {'ok':>6s} {'unit_rejected':>14s} {'crashed':>8s}")
    for case, counts in sorted(result["outcome_by_case"].items()):
        print(f"  {case:28s} {counts.get('ok', 0):>6d} {counts.get('unit_rejected', 0):>14d} {counts.get('crashed', 0):>8d}")

    print(f"\n  Total crashes: {len(result['crashes'])}")
    for c in result["crashes"][:5]:
        print(f"    - record {c['record_id']} ({c['test_case_type']}): {c['error']}")
    if len(result["crashes"]) > 5:
        print(f"    ... and {len(result['crashes']) - 5} more")

    print(f"\n=== Structural duplicate check ===")
    print(f"  (market, country, usage_bucket) groups with >1 record: {result['genuine_duplicate_groups']}")
    if result["genuine_duplicate_example"]:
        print(f"  Example: {result['genuine_duplicate_example']}")

    print(f"\n=== Sample calculate_node result per test_case_type ===")
    for case, sample in result["sample_results"].items():
        print(f"  {case:28s} record {sample['record_id']}: total=${sample['total_market_value']}M "
              f"related=${sample['related_demand_value']}M unit_status={sample['unit_contract_status']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

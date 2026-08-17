"""Synthetic smoke-test dataset generator for the Demand-Side pipeline.

Generates records in the EXACT internal schema the pipeline's deterministic
calculation layer already consumes -- no new schema invented. Field names map
1:1 onto the real structures used throughout `me_engine.demand_usage`:

    market_name          -- state["market_name"]
    country               -- an entry of state["countries"] / quantities[bucket][country]
    usage_bucket          -- usage_buckets[i]["name"]
    usage_description     -- usage_buckets[i]["description"]
    bucket_category        -- usage_buckets[i]["bucket_category"] (buildup_writer.py, agents.py)
    driver_name           -- drivers[bucket]["driver_name"]
    driver_unit            -- drivers[bucket]["unit"]
    final_demand_unit      -- drivers[bucket]["final_demand_unit"]
    consumption_unit        -- drivers[bucket]["consumption_unit"]
    quantity               -- quantities[bucket][country]["value"]
    provenance             -- quantities[bucket][country]["provenance"]
    asp_usd_per_unit         -- prices[bucket]["asp_usd_per_unit"]
    year                    -- driver_raw["year"] (evidence.py fact shape)
    test_case_type          -- NOT part of the pipeline's schema; a label this
                                generator adds so every edge case stays traceable
                                through the smoke test (never fed into the real
                                pipeline as if it were a real field).

Every record is SYNTHETIC. Nothing here is, or should ever be presented as,
real market data.

Usage:
    python generate_smoke_test_data.py --count 5000 --seed 42 \
        --output smoke_test_data_5000.json
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Value pools -- realistic, but synthetic. Reuses the same style of market/
# country names already exercised elsewhere in this repo's own test runs
# (Avocado Oil, Crude Oil, Smart Phone, ...), purely as plausible labels --
# no real market data is looked up or asserted here.
# --------------------------------------------------------------------------- #
MARKETS = [
    "Global Avocado Oil Market", "Global Crude Oil Market", "Global Smart Phone Market",
    "Global Adhesives & Bonding Market", "Global Organic Baby Food Market",
    "Global Industrial Lubricants Market", "Global Recycled Plastics Market",
    "Global Cold Chain Logistics Market", "Global Solar Panel Market",
    "Global Electric Vehicle Battery Market", "Global Coffee Market",
    "Global Water Filtration Market", "Global Home Fitness Equipment Market",
]

COUNTRIES = [
    "United States", "China", "India", "Japan", "Germany", "United Kingdom", "France",
    "Brazil", "Canada", "South Korea", "Mexico", "Australia", "Saudi Arabia",
    "Rest of Europe", "Rest of Asia Pacific", "Rest of World",
]

# Generic usage/application labels -- market-agnostic on purpose (this dataset
# tests the pipeline's SCHEMA/CALCULATION handling, not per-market accuracy).
PHYSICAL_USAGES = [
    ("Culinary & Food Applications", "Direct consumption in cooking, food preparation, or as a food ingredient."),
    ("Industrial Manufacturing Input", "Used as a raw material or processing input in industrial manufacturing."),
    ("Cosmetics & Personal Care", "Incorporated into skincare, haircare, or personal care formulations."),
    ("Pharmaceutical & Medical Use", "Used as an excipient or active ingredient in pharmaceutical formulations."),
    ("Transportation Fuel Use", "Refined or consumed directly as a transportation fuel."),
    ("Petrochemical Feedstock", "Used as a raw material for petrochemical/plastics production."),
    ("Construction & Building Materials", "Used as an input in construction materials or building applications."),
    ("Electronics & Component Manufacturing", "Used as a component or material in electronics manufacturing."),
    ("Packaging Applications", "Used in packaging materials for consumer or industrial goods."),
    ("Textile & Apparel Production", "Used as a raw material or processing agent in textile manufacturing."),
]

FINANCIAL_USAGES = [
    ("Financial Trading & Investment", "Traded as a financial instrument (futures, options) for speculation and hedging."),
    ("Futures Market Speculation", "Bought and sold on futures markets purely for price speculation, no physical delivery."),
    ("Portfolio Hedging Activity", "Used as a hedge within an investment portfolio, not physically consumed."),
]

RESERVE_USAGES = [
    ("Strategic Reserves & Stockpiling", "Held in government strategic reserves for future supply security."),
    ("Commercial Inventory Stock Build-up", "Held in commercial inventory/warehousing stock, not currently consumed."),
    ("Government Reserves Holding", "Purchased and held in government reserves for emergency use."),
]

DRIVER_UNITS = [
    "tonnes per year", "barrels per year", "units per year", "liters per year",
    "vehicle-kilometers per year", "kWh per year", "households", "million tonnes per year",
    "billion barrels per year", "thousand units per year", "kg per year",
]

CURRENCY_TAINTED_UNITS = ["USD per tonne", "US$ per barrel", "$ per unit"]
COMPOUND_UNITS = [
    "GW and million units", "barrels and tonnes", "units, kWh", "tonnes; barrels",
    "million units and GW",
]

DRIVER_NAME_TEMPLATES = [
    "Global {noun} consumption volume", "Global {noun} production volume",
    "Global {noun} installed base", "Global {noun} throughput",
]
DRIVER_NOUNS = ["product", "material", "component", "feedstock", "unit"]

TEST_CASE_TYPES = [
    "valid", "compound_unit", "period_mismatch", "zero_quantity", "negative_quantity",
    "missing_value", "duplicate_usage", "implausible_large_driver",
    "financial_trading_usage", "reserve_inventory_usage", "missing_required_field",
]


def _driver_name(rng: random.Random) -> str:
    return rng.choice(DRIVER_NAME_TEMPLATES).format(noun=rng.choice(DRIVER_NOUNS))


def _base_record(rng: random.Random, test_case_type: str) -> dict[str, Any]:
    market = rng.choice(MARKETS)
    country = rng.choice(COUNTRIES)
    usage_name, usage_desc = rng.choice(PHYSICAL_USAGES)
    unit = rng.choice(DRIVER_UNITS)
    return {
        "market_name": market,
        "country": country,
        "usage_bucket": usage_name,
        "usage_description": usage_desc,
        "bucket_category": "physical_consumption",
        "driver_name": _driver_name(rng),
        "driver_unit": unit,
        "final_demand_unit": unit,
        "consumption_unit": "",
        "quantity": round(rng.uniform(100.0, 500_000.0), 2),
        "provenance": rng.choice(["observed", "derived", "assumption"]),
        "asp_usd_per_unit": round(rng.uniform(1.0, 5_000.0), 2),
        "year": rng.randint(2020, 2026),
        "test_case_type": test_case_type,
    }


def _make_record(rng: random.Random, test_case_type: str) -> dict[str, Any]:
    rec = _base_record(rng, test_case_type)

    if test_case_type == "valid":
        return rec

    if test_case_type == "compound_unit":
        bad_unit = rng.choice(COMPOUND_UNITS)
        rec["driver_unit"] = bad_unit
        rec["final_demand_unit"] = bad_unit
        return rec

    if test_case_type == "period_mismatch":
        base = rng.choice(["barrels", "tonnes", "units", "liters"])
        rec["driver_unit"] = f"{base} per day"
        rec["final_demand_unit"] = f"{base} per year"
        return rec

    if test_case_type == "zero_quantity":
        rec["quantity"] = 0.0
        return rec

    if test_case_type == "negative_quantity":
        rec["quantity"] = -round(rng.uniform(1.0, 1000.0), 2)
        return rec

    if test_case_type == "missing_value":
        # Required identity fields stay present; the VALUE fields are missing --
        # a distinct failure mode from a missing required field (below).
        if rng.random() < 0.5:
            rec["quantity"] = None
        else:
            rec["asp_usd_per_unit"] = None
        return rec

    if test_case_type == "duplicate_usage":
        # Marked so the generator/consumer can pair it with its sibling --
        # actual pairing happens at generation time (see generate_records()),
        # this branch just produces one half of the pair.
        return rec

    if test_case_type == "implausible_large_driver":
        rec["quantity"] = round(rng.uniform(1e13, 1e16), 2)  # absurdly large on its face
        return rec

    if test_case_type == "financial_trading_usage":
        name, desc = rng.choice(FINANCIAL_USAGES)
        rec["usage_bucket"] = name
        rec["usage_description"] = desc
        rec["bucket_category"] = "physical_consumption"  # naive guess -- the
        # classifier under test is supposed to catch this by TEXT, not because
        # we pre-labelled it correctly; that would defeat the point of the test.
        return rec

    if test_case_type == "reserve_inventory_usage":
        name, desc = rng.choice(RESERVE_USAGES)
        rec["usage_bucket"] = name
        rec["usage_description"] = desc
        rec["bucket_category"] = "physical_consumption"  # same reasoning as above
        return rec

    if test_case_type == "missing_required_field":
        field = rng.choice(["market_name", "country", "usage_bucket"])
        rec[field] = None
        return rec

    raise ValueError(f"unknown test_case_type: {test_case_type}")  # pragma: no cover


def generate_records(count: int, seed: int) -> list[dict[str, Any]]:
    """Deterministic given (count, seed) -- uses a private Random instance,
    never the global `random` module, so this never depends on or mutates
    process-wide random state."""
    rng = random.Random(seed)

    # 70% valid, remaining 30% split evenly across the 10 edge-case types.
    n_valid = round(count * 0.7)
    n_edge_each, remainder = divmod(count - n_valid, len(TEST_CASE_TYPES) - 1)

    plan: list[str] = ["valid"] * n_valid
    for i, case_type in enumerate(t for t in TEST_CASE_TYPES if t != "valid"):
        n = n_edge_each + (1 if i < remainder else 0)
        plan.extend([case_type] * n)
    rng.shuffle(plan)

    records: list[dict[str, Any]] = []
    for case_type in plan:
        rec = _make_record(rng, case_type)
        records.append(rec)

    # Second pass: pair up "duplicate_usage" records so duplicates actually
    # overlap in (market, country, usage_bucket) instead of being independent
    # draws that might coincidentally never collide.
    dup_indices = [i for i, r in enumerate(records) if r["test_case_type"] == "duplicate_usage"]
    for j in range(0, len(dup_indices) - 1, 2):
        i1, i2 = dup_indices[j], dup_indices[j + 1]
        records[i2]["market_name"] = records[i1]["market_name"]
        records[i2]["country"] = records[i1]["country"]
        records[i2]["usage_bucket"] = records[i1]["usage_bucket"]
        records[i2]["usage_description"] = records[i1]["usage_description"]
        # deliberately give the duplicate a DIFFERENT quantity/price -- an
        # overlapping/duplicate usage entry with conflicting numbers is the
        # realistic failure mode, not an exact byte-for-byte repeat.

    for idx, rec in enumerate(records):
        rec["record_id"] = idx

    return records


# --------------------------------------------------------------------------- #
# Minimal schema validator -- self-check only, not a new schema definition.
# --------------------------------------------------------------------------- #
_REQUIRED_FIELDS = [
    "record_id", "market_name", "country", "usage_bucket", "usage_description",
    "bucket_category", "driver_name", "driver_unit", "final_demand_unit",
    "consumption_unit", "quantity", "provenance", "asp_usd_per_unit", "year",
    "test_case_type",
]


def validate_schema(records: list[dict[str, Any]]) -> list[str]:
    """Returns a list of problems (empty = fully schema-valid). A record is
    only expected to be FULLY populated if its own test_case_type isn't one
    of the ones that deliberately breaks a field (missing_value /
    missing_required_field) -- those still must have every KEY present, just
    with a None value, which is itself part of what the schema allows."""
    problems: list[str] = []
    for rec in records:
        missing_keys = [f for f in _REQUIRED_FIELDS if f not in rec]
        if missing_keys:
            problems.append(f"record {rec.get('record_id', '?')}: missing key(s) {missing_keys}")
        if rec.get("test_case_type") not in TEST_CASE_TYPES:
            problems.append(f"record {rec.get('record_id', '?')}: unknown test_case_type {rec.get('test_case_type')!r}")
    return problems


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--count", type=int, default=5000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--output", default="smoke_test_data_5000.json")
    args = p.parse_args()

    records = generate_records(args.count, args.seed)

    problems = validate_schema(records)
    if problems:
        print(f"[ERROR] Schema validation failed for {len(problems)} record(s):")
        for msg in problems[:10]:
            print(f"  - {msg}")
        return 1

    out_path = Path(args.output)
    out_path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")

    from collections import Counter
    dist = Counter(r["test_case_type"] for r in records)
    print(f"Generated {len(records)} SYNTHETIC records (seed={args.seed}) -> {out_path}")
    print("Test-case distribution:")
    for case_type in TEST_CASE_TYPES:
        print(f"  {case_type:28s} {dist.get(case_type, 0)}")
    print("\nThis dataset is entirely synthetic -- never present it as real market data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Unit-contract regression tests (demand-side pipeline, Tier 1).

Covers `me_engine.demandUseCaseCountry_approach2B.units` (pure, deterministic parser) and
`me_engine.demandUseCaseCountry_approach2B.agents.calculate_node`'s use of it as the
calculation-layer gate: a bucket whose unit contract fails must be excluded
from the total, never assigned a fallback price/quantity.

Run with: pytest tests/test_units_contract.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from me_engine.demandUseCaseCountry_approach2B import agents
from me_engine.demandUseCaseCountry_approach2B.units import parse_unit_string, periods_conflict, units_share_base


# --------------------------------------------------------------------------- #
# units.py -- pure parser tests, no LLM, no network
# --------------------------------------------------------------------------- #
def test_million_tonnes_parses_with_correct_scale():
    p = parse_unit_string("million tonnes per year")
    assert p is not None
    assert p.scale == 1e6
    assert p.base_unit == "tonnes"
    assert p.per_period == "year"


def test_billion_barrels_per_year_parses_with_correct_scale():
    p = parse_unit_string("billion barrels per year")
    assert p is not None
    assert p.scale == 1e9
    assert p.base_unit == "barrels"
    assert p.per_period == "year"


def test_compound_unit_gw_and_million_units_is_rejected():
    assert parse_unit_string("GW and million units") is None


def test_currency_containing_unit_is_rejected():
    assert parse_unit_string("USD per tonne") is None
    assert parse_unit_string("US$ per barrel") is None
    assert parse_unit_string("$100 per unit") is None


def test_day_vs_year_period_mismatch_is_detected():
    a = parse_unit_string("barrels per day")
    b = parse_unit_string("barrels per year")
    assert a is not None and b is not None
    assert periods_conflict(a, b) is True


def test_same_period_or_unstated_period_never_conflicts():
    a = parse_unit_string("barrels per year")
    b = parse_unit_string("million barrels per year")
    assert periods_conflict(a, b) is False  # same period
    c = parse_unit_string("barrels")  # no period stated
    assert periods_conflict(a, c) is False  # absence is never a conflict


def test_valid_compound_scale_normalizes_consistently():
    """The exact reported scenario: '45' in 'million tonnes' x '$450,000,000
    per million tonnes' must normalize to a sane per-tonne price ($450),
    not look like a $450M/unit blowup."""
    final_unit = parse_unit_string("million tonnes per year")
    assert final_unit is not None
    raw_asp = 450_000_000.0
    normalized_asp_per_tonne = raw_asp / final_unit.scale
    assert normalized_asp_per_tonne == 450.0

    # And the actual $ value (quantity_raw * asp_raw) is unaffected by scale --
    # it's already correct because both were quoted against the same unit.
    quantity_raw = 45.0  # "45 million tonnes"
    dollar_value = quantity_raw * raw_asp
    assert dollar_value == 45.0 * 450_000_000.0  # = $20.25B, a real petrochemical-feedstock-scale figure


def test_empty_and_none_are_rejected():
    assert parse_unit_string("") is None
    assert parse_unit_string(None) is None
    assert parse_unit_string("   ") is None


def test_units_share_base_ignores_scale_and_period():
    a = parse_unit_string("million barrels per year")
    b = parse_unit_string("barrels per day")
    assert units_share_base(a, b) is True  # same noun "barrels", scale/period irrelevant here
    c = parse_unit_string("tonnes per year")
    assert units_share_base(a, c) is False


# --------------------------------------------------------------------------- #
# calculate_node -- the authoritative gate. No LLM, no network: fabricated
# state exercises the deterministic arithmetic + rejection path directly.
# --------------------------------------------------------------------------- #
def _base_state(drivers, quantities, prices, bucket_categories=None):
    countries = ["US"]
    buckets = [
        {"name": name, "description": "x", "bucket_category": (bucket_categories or {}).get(name, "physical_consumption")}
        for name in drivers
    ]
    return {
        "log": [],
        "usage_buckets": buckets,
        "drivers": drivers,
        "quantities": quantities,
        "prices": prices,
        "countries": countries,
    }


def test_compound_unit_bucket_is_excluded_from_calculation_not_fallback_priced():
    state = _base_state(
        drivers={"Bad Bucket": {"unit": "GW and million units", "final_demand_unit": "GW and million units"}},
        quantities={"Bad Bucket": {"US": {"value": 100.0}}},
        prices={"Bad Bucket": {"asp_usd_per_unit": 50.0}},
    )
    out = agents.calculate_node(state)
    assert out["unit_rejected_buckets"] == ["Bad Bucket"]
    assert out["unit_contract"]["Bad Bucket"]["status"] == "rejected_compound_or_unparseable_unit"
    # Excluded means $0 contribution, not an invented number.
    assert out["usage_values"]["Bad Bucket"]["US"] == 0.0
    assert out["total_market_value"] == 0.0


def test_period_mismatch_bucket_is_excluded():
    state = _base_state(
        drivers={
            "Mismatched Bucket": {
                "unit": "barrels per day",
                "final_demand_unit": "barrels per year",
            }
        },
        quantities={"Mismatched Bucket": {"US": {"value": 1000.0}}},
        prices={"Mismatched Bucket": {"asp_usd_per_unit": 75.0}},
    )
    out = agents.calculate_node(state)
    assert out["unit_rejected_buckets"] == ["Mismatched Bucket"]
    assert out["unit_contract"]["Mismatched Bucket"]["status"] == "rejected_period_mismatch"
    assert out["usage_values"]["Mismatched Bucket"]["US"] == 0.0


def test_valid_compound_scale_bucket_calculates_and_normalizes_correctly():
    state = _base_state(
        drivers={
            "Petrochemical Feedstock": {
                "unit": "million tonnes per year",
                "final_demand_unit": "million tonnes per year",
            }
        },
        quantities={"Petrochemical Feedstock": {"US": {"value": 45.0}}},
        prices={"Petrochemical Feedstock": {"asp_usd_per_unit": 450_000_000.0}},
    )
    out = agents.calculate_node(state)
    assert out["unit_rejected_buckets"] == []
    contract = out["unit_contract"]["Petrochemical Feedstock"]
    assert contract["status"] == "ok"
    assert contract["normalized_asp_per_base_unit"] == 450.0  # sane $/tonne, not $450M/unit
    # value_usd_mn = 45.0 * 450_000_000.0 / 1e6 = 20,250.0 (US$ Mn)
    assert out["usage_values"]["Petrochemical Feedstock"]["US"] == 20250.0
    assert out["total_market_value"] == 20250.0


def test_pricing_conversion_factor_multiplies_in_when_present():
    """Price-basis bridge (spec section 8/12): a bucket priced against a
    DIFFERENT unit than final_demand_unit carries an evidence-backed
    conversion factor that must multiply into the value -- e.g. pricing the
    FUEL behind a vehicle-kilometers demand quantity."""
    state = _base_state(
        drivers={"Transportation Fuels": {"unit": "vehicle-km", "final_demand_unit": "vehicle-km"}},
        quantities={"Transportation Fuels": {"US": {"value": 1000.0}}},
        prices={"Transportation Fuels": {"asp_usd_per_unit": 100.0}},  # USD per barrel (the price basis)
    )
    state["pricing_conversion_factors"] = {"Transportation Fuels": {"value": 0.5, "evidence_type": "verified_observed"}}  # 0.5 barrels/vehicle-km
    out = agents.calculate_node(state)
    # 1000 vehicle-km * 0.5 barrels/vehicle-km * $100/barrel / 1e6 = $0.05M
    assert out["usage_values"]["Transportation Fuels"]["US"] == round(1000.0 * 0.5 * 100.0 / 1_000_000.0, 4)


def test_no_conversion_factor_behaves_exactly_as_before():
    """Absence of a `pricing_conversion_factors` entry (the ordinary case --
    priced unit == final_demand_unit) must reproduce today's math exactly,
    i.e. an implicit x1.0 multiplier, never a regression."""
    state = _base_state(
        drivers={"Good Bucket": {"unit": "tonnes per year", "final_demand_unit": "tonnes per year"}},
        quantities={"Good Bucket": {"US": {"value": 10.0}}},
        prices={"Good Bucket": {"asp_usd_per_unit": 100.0}},
    )
    out = agents.calculate_node(state)
    assert out["usage_values"]["Good Bucket"]["US"] == round(10.0 * 100.0 / 1_000_000.0, 4)


def test_mixed_run_excludes_only_the_bad_bucket_not_the_whole_run():
    state = _base_state(
        drivers={
            "Good Bucket": {"unit": "tonnes per year", "final_demand_unit": "tonnes per year"},
            "Bad Bucket": {"unit": "GW and million units", "final_demand_unit": "GW and million units"},
        },
        quantities={
            "Good Bucket": {"US": {"value": 10.0}},
            "Bad Bucket": {"US": {"value": 999.0}},
        },
        prices={
            "Good Bucket": {"asp_usd_per_unit": 100.0},
            "Bad Bucket": {"asp_usd_per_unit": 999.0},
        },
    )
    out = agents.calculate_node(state)
    assert out["unit_rejected_buckets"] == ["Bad Bucket"]
    assert out["usage_values"]["Good Bucket"]["US"] == round(10.0 * 100.0 / 1_000_000.0, 4)
    assert out["usage_values"]["Bad Bucket"]["US"] == 0.0
    # total reflects only the good bucket
    assert out["total_market_value"] == round(10.0 * 100.0 / 1_000_000.0, 4)

"""Phase 8 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) -- Price Basis + Price Evidence
Strategy unit tests: correct product-price basis, usage-unit -> product-price
mapping, invalid price unit, unit normalization, period mismatch,
circular-price derivation rejection.

Run with: pytest tests/test_demand_usage_pricing_strategy.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from me_engine.demand_usage import pricing_strategy


# --------------------------------------------------------------------------- #
# validate_price_basis -- correct product-price basis / usage-unit mapping
# --------------------------------------------------------------------------- #
def test_crude_oil_barrel_basis_has_no_flags():
    basis = {"price_basis_unit": "barrel", "same_as_final_unit": False,
              "conversion_factor_unit": "barrels of fuel per vehicle-km"}
    flags = pricing_strategy.validate_price_basis(basis, final_unit="vehicle-kilometers")
    assert flags == []


def test_cement_tonne_basis_has_no_flags():
    basis = {"price_basis_unit": "tonne", "same_as_final_unit": True, "conversion_factor_unit": ""}
    flags = pricing_strategy.validate_price_basis(basis, final_unit="tonnes of cement")
    assert flags == []


def test_avocado_oil_tonne_basis_has_no_flags():
    basis = {"price_basis_unit": "tonne", "same_as_final_unit": True, "conversion_factor_unit": ""}
    flags = pricing_strategy.validate_price_basis(basis, final_unit="tonnes of avocado oil")
    assert flags == []


def test_usage_unit_priced_directly_is_flagged():
    """The exact anti-pattern the plan calls out: 'USD per vehicle-km'
    proposed AS the price basis itself, not bridged via a conversion."""
    basis = {"price_basis_unit": "vehicle-kilometer", "same_as_final_unit": True, "conversion_factor_unit": ""}
    flags = pricing_strategy.validate_price_basis(basis, final_unit="vehicle-kilometers")
    codes = {f["code"] for f in flags}
    assert "non_commercial_price_basis" in codes
    assert "usage_unit_priced_directly" in codes


def test_usage_unit_bridged_via_conversion_is_not_flagged_for_direct_pricing():
    """Same activity unit, but this time genuinely bridged to a real
    commodity basis -- the 'usage_unit_priced_directly' flag must not fire
    since same_as_final_unit is False."""
    basis = {"price_basis_unit": "barrel", "same_as_final_unit": False,
              "conversion_factor_unit": "barrels of fuel per vehicle-km"}
    flags = pricing_strategy.validate_price_basis(basis, final_unit="vehicle-kilometers")
    assert flags == []


# --------------------------------------------------------------------------- #
# invalid price unit
# --------------------------------------------------------------------------- #
def test_missing_price_basis_unit_is_flagged():
    flags = pricing_strategy.validate_price_basis({"price_basis_unit": ""}, final_unit="tonnes")
    assert flags[0]["code"] == "missing_or_unparseable_price_unit"


def test_currency_tainted_price_basis_unit_is_flagged():
    """A price basis unit must be a PHYSICAL unit, never a currency string
    itself -- units.parse_unit_string already rejects currency-tainted
    strings; validate_price_basis surfaces that as its own flag."""
    flags = pricing_strategy.validate_price_basis({"price_basis_unit": "USD"}, final_unit="tonnes")
    assert flags[0]["code"] == "missing_or_unparseable_price_unit"


# --------------------------------------------------------------------------- #
# period mismatch
# --------------------------------------------------------------------------- #
def test_price_period_matches_final_unit_period_no_flag():
    flags = pricing_strategy.validate_price_period("year", "tonnes per year")
    assert flags == []


def test_price_period_mismatch_is_flagged():
    flags = pricing_strategy.validate_price_period("day", "tonnes per year")
    assert flags[0]["code"] == "price_period_mismatch"


def test_price_period_absent_on_final_unit_is_never_a_conflict():
    """No stated period on the final unit side is never treated as a
    conflict -- mirrors units.periods_conflict's own discipline."""
    flags = pricing_strategy.validate_price_period("day", "tonnes")
    assert flags == []


# --------------------------------------------------------------------------- #
# unit normalization
# --------------------------------------------------------------------------- #
def test_normalize_price_record_scales_down_to_single_base_unit():
    rec = pricing_strategy.normalize_price_record(
        raw_value=500_000_000.0, raw_unit="USD per million tonnes",
        source="LME", source_url="https://lme.example/x", year=2025,
        geography="global", verification_status="verified_observed",
    )
    assert rec["normalization_status"] == "ok"
    assert rec["normalized_price"] == 500.0
    assert rec["normalized_unit"] == "USD per tonnes"
    assert rec["raw_price"] == 500_000_000.0
    assert rec["raw_unit"] == "USD per million tonnes"
    assert rec["source"] == "LME"
    assert rec["year"] == 2025
    assert rec["verification_status"] == "verified_observed"


def test_normalize_price_record_no_scale_word_is_identity():
    rec = pricing_strategy.normalize_price_record(
        raw_value=75.5, raw_unit="USD per barrel", source="EIA", source_url="u",
        year=2025, geography="global", verification_status="derived",
        conversion_formula="x = y",
    )
    assert rec["normalized_price"] == 75.5
    assert rec["normalized_unit"] == "USD per barrel"
    assert rec["conversion_formula"] == "x = y"


def test_normalize_price_record_unparseable_unit_reports_status_never_invents():
    rec = pricing_strategy.normalize_price_record(
        raw_value=100.0, raw_unit="USD per vehicle and lane", source="s", source_url="u",
        year=None, geography="global", verification_status="assumption",
    )
    assert rec["normalization_status"] == "unparseable_price_unit"
    assert rec["normalized_price"] is None
    assert rec["normalized_unit"] is None
    assert rec["raw_price"] == 100.0  # raw data preserved even when normalization fails


def test_normalize_price_record_non_price_shaped_string_is_unparseable():
    rec = pricing_strategy.normalize_price_record(
        raw_value=10.0, raw_unit="tonnes", source="s", source_url="u", year=None,
        geography="global", verification_status="assumption",
    )
    assert rec["normalization_status"] == "unparseable_price_unit"


# --------------------------------------------------------------------------- #
# circular-price derivation rejection
# --------------------------------------------------------------------------- #
def test_circular_derivation_from_market_size_is_detected():
    fact = {"derivation_basis": "Implied price = total market size divided by assumed quantity"}
    assert pricing_strategy.is_circular_price_derivation(fact) is True


def test_circular_derivation_via_revenue_over_volume_is_detected():
    fact = {"citation": "Revenue divided by volume gives an implied price of $50/tonne"}
    assert pricing_strategy.is_circular_price_derivation(fact) is True


def test_backed_out_of_market_value_is_detected():
    fact = {"evidence_text": "Price backed out of the market value estimate for this bucket"}
    assert pricing_strategy.is_circular_price_derivation(fact) is True


def test_genuine_price_derivation_is_not_flagged_as_circular():
    fact = {"derivation_basis": "Derived from a verified per-tonne LME price via a published density ratio"}
    assert pricing_strategy.is_circular_price_derivation(fact) is False


def test_circular_check_handles_none_and_empty_fact():
    assert pricing_strategy.is_circular_price_derivation(None) is False
    assert pricing_strategy.is_circular_price_derivation({}) is False


# --------------------------------------------------------------------------- #
# build_price_evidence_strategy -- deterministic, no LLM
# --------------------------------------------------------------------------- #
def test_build_price_evidence_strategy_is_deterministic_and_forbids_circular_derivation():
    price_basis = {"price_basis_unit": "barrel", "rationale": "fuel is what's actually priced"}
    strategy = pricing_strategy.build_price_evidence_strategy(
        price_basis, market="Crude Oil", bucket_name="Transportation Fuels", driver_name="Road fuel consumption",
    )
    assert strategy["strategy_source"] == "deterministic_price_basis"
    assert strategy["physical_unit"] == "USD per barrel"
    assert "market size" in strategy["derivation_path"].lower() or "circular" in strategy["derivation_path"].lower()
    assert strategy["metric_required"]
    assert strategy["primary_source_types"]

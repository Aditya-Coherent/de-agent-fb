"""Phase 10 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) -- Derived-Demand Methodologies
unit tests: combined-provenance ranking (observed x observed / derived /
unverified / assumption), regional/global allocation, activity x intensity,
daily -> annual period conversion, invalid share, unit mismatch, period
mismatch, circular derivation, provenance preservation, formula persistence.

Run with: pytest tests/test_demand_usage_derivation.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from me_engine.demandUseCaseCountry_approach2B import derivation, reference_data


def _fact(value, status="verified_observed", **extra):
    fact = {
        "value": value, "unit": extra.pop("unit", "tonnes"), "year": extra.pop("year", 2025),
        "geography": extra.pop("geography", "Global"), "source": extra.pop("source", "Test Source"),
        "source_url": extra.pop("source_url", "https://example.com/data"),
        "evidence_type": status, "verification_status": status,
        "confidence": extra.pop("confidence", 0.9),
    }
    fact.update(extra)
    return fact


# --------------------------------------------------------------------------- #
# combined_verification_status -- the plan's own worked examples.
# --------------------------------------------------------------------------- #
def test_observed_times_observed_is_derived_never_verified_observed():
    """Never label a result verified_observed merely because the formula is
    deterministic -- the OUTPUT is a computed value, not itself observed."""
    status = derivation.combined_verification_status("verified_observed", "verified_observed")
    assert status == "derived"


def test_observed_times_derived_is_derived():
    assert derivation.combined_verification_status("verified_observed", "derived") == "derived"


def test_observed_times_unverified_is_unverified():
    assert derivation.combined_verification_status("verified_observed", "source_found_unverified") == "source_found_unverified"


def test_observed_times_assumption_is_assumption():
    assert derivation.combined_verification_status("verified_observed", "assumption") == "assumption"


def test_activity_intensity_end_to_end_status_matrix():
    """Same matrix, exercised through the real `derive_activity_intensity`
    entry point (not just the bare status-combination helper)."""
    activity = _fact(100.0, status="verified_observed", unit="vehicles")
    for intensity_status, expected in [
        ("verified_observed", "derived"),
        ("derived", "derived"),
        ("source_found_unverified", "source_found_unverified"),
        ("assumption", "assumption"),
    ]:
        intensity = _fact(2.0, status=intensity_status, unit="liters per vehicle")
        result = derivation.derive_activity_intensity(activity, intensity)
        assert result["verification_status"] == expected
        assert result["value"] == pytest.approx(200.0)


# --------------------------------------------------------------------------- #
# regional / global allocation
# --------------------------------------------------------------------------- #
def test_regional_allocation_matches_plans_worked_example():
    """Method: regional_total x country_share. Regional demand: 100M tonnes
    [verified_observed]. Germany share: 8% [verified_observed]. Derived
    Germany demand: 8M tonnes. Status: derived."""
    regional = _fact(100_000_000.0, status="verified_observed", geography="Europe")
    share = _fact(0.08, status="verified_observed", unit="share", geography="Germany")
    result = derivation.derive_regional_share(regional, share, geography="Germany")
    assert result["value"] == pytest.approx(8_000_000.0)
    assert result["verification_status"] == "derived"
    assert result["derivation_method"] == "regional_share"
    assert result["geography"] == "Germany"


def test_global_allocation_uses_global_share_method_name():
    global_fact = _fact(1_000_000.0, status="verified_observed", geography="Global")
    share = _fact(0.05, status="derived", unit="share")
    result = derivation.derive_global_share(global_fact, share, geography="Vietnam")
    assert result["value"] == pytest.approx(50_000.0)
    assert result["derivation_method"] == "global_share"
    # observed x derived -> derived
    assert result["verification_status"] == "derived"


def test_country_allocation_driver_itself_carries_provenance_not_arbitrary_split():
    """The allocation driver must be observed/derived with its own
    provenance, e.g. `reference_data.population_share` -- never a silent
    'global total / number of countries' or an arbitrary percentage."""
    countries = ["Germany", "France", "Italy"]
    share_value = reference_data.population_share("Germany", countries)
    regional = _fact(100_000_000.0, status="verified_observed")
    share = {
        "value": share_value, "unit": "share", "evidence_type": "derived",
        "verification_status": "derived", "confidence": 0.6,
        "source": "reference_data.population_share",
    }
    result = derivation.derive_regional_share(regional, share, geography="Germany", ratio_label="population_share")
    assert result["value"] == pytest.approx(100_000_000.0 * share_value)
    assert "population_share" in result["formula"]
    assert result["input_facts"]["population_share"]["source"] == "reference_data.population_share"


# --------------------------------------------------------------------------- #
# production x utilization_rate
# --------------------------------------------------------------------------- #
def test_production_utilization_derivation():
    production = _fact(500_000.0, status="verified_observed", unit="tonnes")
    utilization = _fact(0.75, status="derived", unit="share")
    result = derivation.derive_production_utilization(production, utilization, geography="Brazil")
    assert result["value"] == pytest.approx(375_000.0)
    assert result["derivation_method"] == "production_utilization"


# --------------------------------------------------------------------------- #
# activity x intensity
# --------------------------------------------------------------------------- #
def test_activity_times_intensity_basic():
    activity = _fact(1_000.0, status="verified_observed", unit="vehicles")
    intensity = _fact(50.0, status="verified_observed", unit="liters per vehicle")
    result = derivation.derive_activity_intensity(activity, intensity, output_unit="liters")
    assert result["value"] == pytest.approx(50_000.0)
    assert result["unit"] == "liters"
    assert result["verification_status"] == "derived"  # observed x observed -> derived, never verified_observed


# --------------------------------------------------------------------------- #
# daily -> annual (rate_conversion)
# --------------------------------------------------------------------------- #
def test_daily_to_annual_conversion():
    daily = _fact(1_000.0, status="verified_observed", unit="barrels per day")
    result = derivation.derive_rate_conversion(daily, to_period="year")
    assert result["value"] == pytest.approx(365_000.0)
    assert result["period"] == "year"
    assert result["derivation_method"] == "rate_conversion"
    # A fixed calendar constant is not empirical evidence -- it does not
    # itself downgrade a verified_observed daily figure any further than an
    # ordinary single-input derivation already would.
    assert result["verification_status"] == "derived"


def test_daily_to_annual_conversion_explicit_factor():
    daily = _fact(10.0, status="derived", unit="tonnes per day")
    result = derivation.derive_rate_conversion(daily, to_period="year", factor=360.0)
    assert result["value"] == pytest.approx(3_600.0)


def test_period_conversion_with_no_defined_factor_is_rejected():
    weekly = _fact(10.0, status="verified_observed", unit="tonnes", year=2025)
    with pytest.raises(derivation.DerivationError) as exc:
        derivation.derive_rate_conversion(weekly, to_period="year", from_period="week")
    assert exc.value.code in ("unsupported_method", "period_mismatch")


# --------------------------------------------------------------------------- #
# unit_conversion -- equivalent unit/period, same physical dimension.
# --------------------------------------------------------------------------- #
def test_unit_conversion_scale_change():
    fact = _fact(2.5, status="verified_observed", unit="million tonnes per year")
    result = derivation.derive_unit_conversion(fact, to_unit="tonnes per year")
    assert result["value"] == pytest.approx(2_500_000.0)
    assert result["derivation_method"] == "unit_conversion"


def test_unit_conversion_cross_dimensional_is_rejected_as_unit_mismatch():
    fact = _fact(100.0, status="verified_observed", unit="barrels per year")
    with pytest.raises(derivation.DerivationError) as exc:
        derivation.derive_unit_conversion(fact, to_unit="tonnes per year")
    assert exc.value.code == "unit_mismatch"


# --------------------------------------------------------------------------- #
# explicit_formula -- generic product/quotient of named inputs.
# --------------------------------------------------------------------------- #
def test_explicit_formula_product_and_quotient():
    inputs = {
        "capacity": _fact(1_000.0, status="verified_observed", unit="units"),
        "yield_rate": _fact(0.9, status="derived", unit="share"),
        "loss_factor": _fact(2.0, status="verified_observed", unit="ratio"),
    }
    result = derivation.derive_explicit_formula(
        inputs, numerator=["capacity", "yield_rate"], denominator=["loss_factor"], output_unit="units",
    )
    assert result["value"] == pytest.approx(1_000.0 * 0.9 / 2.0)
    assert result["derivation_method"] == "explicit_formula"


# --------------------------------------------------------------------------- #
# invalid share
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad_share", [-0.1, 1.5])
def test_invalid_share_is_rejected(bad_share):
    regional = _fact(100.0, status="verified_observed")
    share = _fact(bad_share, status="derived", unit="share")
    with pytest.raises(derivation.DerivationError) as exc:
        derivation.derive_regional_share(regional, share, geography="X")
    assert exc.value.code == "invalid_share"


# --------------------------------------------------------------------------- #
# unit mismatch / period mismatch (activity x intensity)
# --------------------------------------------------------------------------- #
def test_activity_intensity_period_mismatch_is_rejected():
    activity = _fact(100.0, status="verified_observed", unit="vehicles per day")
    intensity = _fact(2.0, status="verified_observed", unit="liters per vehicle per year")
    with pytest.raises(derivation.DerivationError) as exc:
        derivation.derive_activity_intensity(activity, intensity)
    assert exc.value.code == "period_mismatch"


# --------------------------------------------------------------------------- #
# missing inputs / negative quantities / missing provenance / unsupported method
# --------------------------------------------------------------------------- #
def test_missing_input_is_rejected():
    with pytest.raises(derivation.DerivationError) as exc:
        derivation.derive_regional_share(None, _fact(0.1, unit="share"), geography="X")
    assert exc.value.code == "missing_input"


def test_negative_quantity_is_rejected():
    regional = _fact(-5.0, status="verified_observed")
    share = _fact(0.5, status="derived", unit="share")
    with pytest.raises(derivation.DerivationError) as exc:
        derivation.derive_regional_share(regional, share, geography="X")
    assert exc.value.code == "negative_quantity"


def test_missing_provenance_is_rejected():
    with pytest.raises(derivation.DerivationError) as exc:
        derivation.build_derived_fact(
            method="explicit_formula", formula="x", value=1.0, unit="tonnes",
            inputs={"a": {"value": 1.0}},  # no verification_status/evidence_type
        )
    assert exc.value.code == "missing_provenance"


def test_unsupported_method_is_rejected():
    with pytest.raises(derivation.DerivationError) as exc:
        derivation.build_derived_fact(
            method="made_up_method", formula="x", value=1.0, unit="tonnes",
            inputs={"a": _fact(1.0)},
        )
    assert exc.value.code == "unsupported_method"


def test_derived_value_exceeding_parent_total_is_rejected():
    """A share > 1 is already rejected by `invalid_share`, but the
    exceeds-parent-total guard is independently exercised via the shared
    constructor for a hypothetical caller that bypasses the ratio helper."""
    total = _fact(100.0, status="verified_observed")
    with pytest.raises(derivation.DerivationError) as exc:
        derivation.build_derived_fact(
            method="explicit_formula", formula="x", value=150.0, unit="tonnes",
            inputs={"total": total}, parent_total=100.0,
        )
    assert exc.value.code == "exceeds_parent_total"


# --------------------------------------------------------------------------- #
# circular derivation
# --------------------------------------------------------------------------- #
def test_circular_derivation_from_published_market_size_is_rejected():
    circular_input = _fact(
        100.0, status="derived",
        derivation_basis="backed out by dividing the published market size by an assumed quantity",
    )
    other = _fact(0.5, status="verified_observed", unit="share")
    with pytest.raises(derivation.DerivationError) as exc:
        derivation.derive_regional_share(circular_input, other, geography="X")
    assert exc.value.code == "circular_derivation"


def test_circular_derivation_revenue_divided_by_assumed_volume_is_rejected():
    circular_input = _fact(
        100.0, status="derived",
        citation="implied price = revenue divided by an assumed volume",
    )
    reason = derivation.detect_circularity({"total": circular_input})
    assert reason is not None
    assert "circular" in reason.lower() or "market" in reason.lower() or "revenue" in reason.lower()


def test_circular_derivation_self_referential_output_id_is_rejected():
    """An output cannot feed back in as its own input -- final-demand-output
    lineage protection, not just the textual heuristic."""
    self_ref = _fact(10.0, status="derived", output_id="BucketA:Germany:driver")
    other = _fact(0.5, status="verified_observed", unit="share")
    with pytest.raises(derivation.DerivationError) as exc:
        derivation.derive_regional_share(self_ref, other, geography="Germany", output_id="BucketA:Germany:driver")
    assert exc.value.code == "circular_derivation"


def test_circular_derivation_transitive_lineage_is_rejected():
    """An input that is itself downstream (transitively) of the output being
    derived must also be rejected, not just a direct self-reference."""
    downstream_of_target = _fact(10.0, status="derived", derivation_lineage=("BucketA:Germany:driver",))
    other = _fact(0.5, status="verified_observed", unit="share")
    with pytest.raises(derivation.DerivationError) as exc:
        derivation.derive_regional_share(
            downstream_of_target, other, geography="Germany", output_id="BucketA:Germany:driver",
        )
    assert exc.value.code == "circular_derivation"


def test_non_circular_derivation_is_not_rejected():
    regional = _fact(100.0, status="verified_observed")
    share = _fact(0.1, status="derived", unit="share")
    result = derivation.derive_regional_share(regional, share, geography="X", output_id="BucketA:X:driver")
    assert result["value"] == pytest.approx(10.0)


# --------------------------------------------------------------------------- #
# provenance preservation / formula persistence
# --------------------------------------------------------------------------- #
def test_provenance_preservation_records_every_required_field():
    regional = _fact(100.0, status="verified_observed", source="USGS", source_url="https://usgs.gov/x", year=2024)
    share = _fact(0.08, status="verified_observed", unit="share")
    result = derivation.derive_regional_share(regional, share, geography="Germany", ratio_label="population_share")

    for field in (
        "derivation_method", "formula", "input_facts", "value", "unit", "year",
        "geography", "verification_status", "explanation",
    ):
        assert field in result, f"missing required provenance field: {field}"

    assert result["derivation_method"] == "regional_share"
    assert result["geography"] == "Germany"
    assert result["unit"] == regional["unit"]
    assert result["input_facts"]["total"]["source"] == "USGS"
    assert result["input_facts"]["total"]["verification_status"] == "verified_observed"
    assert result["input_facts"]["population_share"]["verification_status"] == "verified_observed"
    assert "Method: regional_share" in result["explanation"]
    assert "8" in result["explanation"]  # 8% share is stated in the human-readable explanation


def test_formula_persists_the_actual_deterministic_formula_executed():
    regional = _fact(100.0, status="verified_observed")
    share = _fact(0.08, status="verified_observed", unit="share")
    result = derivation.derive_regional_share(regional, share, geography="Germany", ratio_label="population_share")
    assert "regional_share" in result["formula"]
    assert "population_share" in result["formula"]
    assert "100.0000" in result["formula"] or "100" in result["formula"]


# --------------------------------------------------------------------------- #
# Evidence Strategy (Phase 7) integration.
# --------------------------------------------------------------------------- #
def test_annotate_strategy_derivation_preserves_original_text_and_adds_executed_record():
    strategy = {"derivation_path": "regional total x population share (in words)"}
    regional = _fact(100.0, status="verified_observed")
    share = _fact(0.08, status="verified_observed", unit="share")
    derived = derivation.derive_regional_share(regional, share, geography="Germany", ratio_label="population_share")

    annotated = derivation.annotate_strategy_derivation(strategy, derived)
    assert annotated["derivation_path"] == "regional total x population share (in words)"
    assert annotated["derivation_path_executed"]["method"] == "regional_share"
    assert "population_share" in annotated["derivation_path_executed"]["formula"]
    assert annotated["derivation_path_executed"]["verification_status"] == derived["verification_status"]
    # never mutates the caller's original strategy dict in place
    assert "derivation_path_executed" not in strategy

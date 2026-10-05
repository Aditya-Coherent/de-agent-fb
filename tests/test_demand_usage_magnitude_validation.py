"""Phase 11 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) -- Magnitude & Cross-Source
Validation tests. Pure-function tests need no mocking at all;
`magnitude_cross_validation_node` tests monkeypatch
`magnitude_validation.gather_independent_references` (the network/LLM
boundary) so no real acquisition happens.

Run with: pytest tests/test_demand_usage_magnitude_validation.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from me_engine.demandUseCaseCountry_approach2B import derivation, magnitude_validation as mv


def _ref(value, unit="barrels per day", source="EIA", geography=None, year=None,
         verification_status="verified_observed", **extra):
    return {
        "value": value, "unit": unit, "source": source, "geography": geography,
        "year": year, "verification_status": verification_status, **extra,
    }


# --------------------------------------------------------------------------- #
# 1. Normal magnitude match
# --------------------------------------------------------------------------- #
def test_normal_magnitude_match_is_not_flagged():
    out = mv.evaluate_magnitude(
        check_type="driver_magnitude", subject_label="'Transportation Fuels' driver total",
        value=1000.0, unit="barrels per day", references=[_ref(1050.0)],
    )
    assert out["classification"] == "match"
    assert out["flagged"] is False
    assert out["ratio"] == 1.05
    assert out["original_value"] == 1000.0  # never changed


# --------------------------------------------------------------------------- #
# 2. Small discrepancy -- below the flag threshold
# --------------------------------------------------------------------------- #
def test_small_discrepancy_is_reviewed_but_not_flagged():
    out = mv.evaluate_magnitude(
        check_type="driver_magnitude", subject_label="x", value=1000.0, unit="barrels per day",
        references=[_ref(2000.0)],
    )
    assert out["ratio"] == 2.0
    assert out["classification"] == "minor_discrepancy"
    assert out["flagged"] is False
    assert out["status"] == "reviewed"


# --------------------------------------------------------------------------- #
# 3. Large discrepancy -- flagged
# --------------------------------------------------------------------------- #
def test_large_discrepancy_is_flagged():
    out = mv.evaluate_magnitude(
        check_type="driver_magnitude", subject_label="x", value=1000.0, unit="barrels per day",
        references=[_ref(4000.0)],
    )
    assert out["ratio"] == 4.0
    assert out["classification"] == "large_discrepancy"
    assert out["flagged"] is True
    assert out["status"] == "flagged"
    assert out["reason"] == "magnitude_discrepancy"
    assert out["notes"]


# --------------------------------------------------------------------------- #
# 4. Order-of-magnitude mismatch
# --------------------------------------------------------------------------- #
def test_order_of_magnitude_mismatch():
    out = mv.evaluate_magnitude(
        check_type="driver_magnitude", subject_label="x", value=1000.0, unit="barrels per day",
        references=[_ref(18_000.0)],
    )
    assert out["classification"] == "order_of_magnitude_mismatch"
    assert out["flagged"] is True
    assert out["ratio"] == 18.0


def test_classify_discrepancy_boundaries():
    assert mv.classify_discrepancy(1.0) == "match"
    assert mv.classify_discrepancy(1.49) == "match"
    assert mv.classify_discrepancy(1.5) == "minor_discrepancy"
    assert mv.classify_discrepancy(2.99) == "minor_discrepancy"
    assert mv.classify_discrepancy(3.0) == "large_discrepancy"
    assert mv.classify_discrepancy(9.99) == "large_discrepancy"
    assert mv.classify_discrepancy(10.0) == "order_of_magnitude_mismatch"
    assert mv.classify_discrepancy(None) == "not_comparable"


# --------------------------------------------------------------------------- #
# 5. Country vs regional/global inconsistency
# --------------------------------------------------------------------------- #
def test_country_vs_regional_inconsistency_flagged():
    # Deliberately different geographies (country vs. "Global") -- this
    # check_type does NOT require matching geography (the reference is
    # meant to be a broader aggregate).
    out = mv.evaluate_magnitude(
        check_type="country_vs_regional_or_global", subject_label="US allocation",
        value=500.0, unit="barrels per day", geography="US",
        references=[_ref(50.0, geography="Global")],
    )
    assert out["flagged"] is True
    assert out["classification"] == "order_of_magnitude_mismatch"


# --------------------------------------------------------------------------- #
# 6. Country value exceeds its own parent total -- always flagged
# --------------------------------------------------------------------------- #
def test_country_exceeds_parent_total_is_always_flagged():
    out = mv.evaluate_magnitude(
        check_type="derived_quantity_magnitude", subject_label="US allocation",
        value=1_500.0, unit="barrels per day", geography="US",
        parent_total=1_000.0, parent_label="regional total",
    )
    assert out["flagged"] is True
    assert out["classification"] == "exceeds_parent_total"
    assert out["reason"] == "exceeds_parent_total"
    assert out["parent_ratio"] == 1.5
    assert out["original_value"] == 1_500.0  # never clamped/replaced


def test_country_within_parent_total_not_flagged():
    out = mv.evaluate_magnitude(
        check_type="derived_quantity_magnitude", subject_label="US allocation",
        value=200.0, unit="barrels per day", parent_total=1_000.0, parent_label="regional total",
    )
    assert out["flagged"] is False
    assert out["classification"] == "no_reference"


# --------------------------------------------------------------------------- #
# 7. Period mismatch -- incompatible, not compared
# --------------------------------------------------------------------------- #
def test_period_mismatch_reference_is_not_compared():
    out = mv.evaluate_magnitude(
        check_type="driver_magnitude", subject_label="x", value=1000.0, unit="barrels per year",
        references=[_ref(4000.0, unit="barrels per day")],
    )
    assert out["ratio"] is None
    assert out["references"][0]["compatible"] is False
    assert "period_mismatch" in out["references"][0]["incompatibility_reasons"]
    assert out["status"] == "incompatible_reference"
    assert out["flagged"] is False
    # The original value is still fully preserved, even though nothing was comparable.
    assert out["original_value"] == 1000.0


def test_year_gap_beyond_tolerance_is_period_mismatch():
    compat = mv.check_compatibility(
        "barrels per day", "barrels per day", subject_year=2025, reference_year=2010,
    )
    assert compat["compatible"] is False
    assert "period_mismatch" in compat["reasons"]


def test_year_gap_within_tolerance_is_compatible():
    compat = mv.check_compatibility(
        "barrels per day", "barrels per day", subject_year=2025, reference_year=2024,
    )
    assert compat["compatible"] is True


# --------------------------------------------------------------------------- #
# 8. Unit mismatch -- incompatible, not compared
# --------------------------------------------------------------------------- #
def test_unit_mismatch_reference_is_not_compared():
    out = mv.evaluate_magnitude(
        check_type="driver_magnitude", subject_label="x", value=1000.0, unit="barrels per day",
        references=[_ref(4000.0, unit="tonnes per day")],
    )
    assert out["ratio"] is None
    assert "unit_mismatch" in out["references"][0]["incompatibility_reasons"]
    assert out["status"] == "incompatible_reference"


def test_geography_mismatch_flagged_when_geography_required():
    compat = mv.check_compatibility(
        "barrels per day", "barrels per day", subject_geo="US", reference_geo="China",
        require_same_geography=True,
    )
    assert compat["compatible"] is False
    assert "geography_mismatch" in compat["reasons"]


# --------------------------------------------------------------------------- #
# 9. Multiple independent references
# --------------------------------------------------------------------------- #
def test_two_agreeing_references_note_higher_confidence():
    out = mv.evaluate_magnitude(
        check_type="driver_magnitude", subject_label="'X' driver total", value=1000.0,
        unit="barrels per day", references=[_ref(4000.0, source="EIA"), _ref(4100.0, source="IEA")],
    )
    assert out["references_agree"] is True
    assert out["flagged"] is True
    assert len(out["references"]) == 2
    assert any("2/2 independent reference(s) agree" in n for n in out["notes"])


def test_two_disagreeing_references_note_lower_confidence():
    out = mv.evaluate_magnitude(
        check_type="driver_magnitude", subject_label="'X' driver total", value=1000.0,
        unit="barrels per day", references=[_ref(4000.0, source="EIA"), _ref(9000.0, source="Some Blog")],
    )
    assert out["references_agree"] is False
    assert any("do NOT agree" in n for n in out["notes"])


# --------------------------------------------------------------------------- #
# 10. Unverified reference handling
# --------------------------------------------------------------------------- #
def test_unverified_reference_flag_notes_caution():
    out = mv.evaluate_magnitude(
        check_type="driver_magnitude", subject_label="'X' driver total", value=1000.0,
        unit="barrels per day",
        references=[_ref(4000.0, source="Some Blog", verification_status="source_found_unverified")],
    )
    assert out["flagged"] is True
    assert out["references"][0]["verification_status"] == "source_found_unverified"
    assert any("0/1 verified_observed" in n for n in out["notes"])
    assert any("treat this flag with caution" in n for n in out["notes"])


def test_verified_reference_does_not_add_caution_note():
    out = mv.evaluate_magnitude(
        check_type="driver_magnitude", subject_label="'X' driver total", value=1000.0,
        unit="barrels per day",
        references=[_ref(4000.0, source="EIA", verification_status="verified_observed")],
    )
    assert not any("treat this flag with caution" in n for n in out["notes"])


# --------------------------------------------------------------------------- #
# 11. Derived-quantity magnitude validation (Phase 10 integration)
# --------------------------------------------------------------------------- #
def test_derived_quantity_magnitude_uses_derivation_metadata_to_diagnose_origin():
    regional_fact = {
        "value": 1000.0, "unit": "barrels per day", "geography": "Global", "year": 2025,
        "source": "EIA", "evidence_type": "verified_observed", "verification_status": "verified_observed",
        "confidence": 0.9,
    }
    share_fact = {
        "value": 0.05, "unit": "share", "evidence_type": "derived", "verification_status": "derived",
        "confidence": 0.6, "source": "reference_data.population_share",
    }
    derived = derivation.derive_regional_share(
        regional_fact, share_fact, geography="US", ratio_label="population_share",
    )
    diagnosis = mv.diagnose_derivation_origin(derived)
    assert diagnosis["origin"] in ("allocation_share", "input_magnitude")
    assert diagnosis["derivation_method"] == "regional_share"
    assert diagnosis["formula"] == derived["formula"]

    # A country allocation can never exceed its own regional/global parent.
    out = mv.evaluate_magnitude(
        check_type="derived_quantity_magnitude", subject_label="US allocation", value=derived["value"],
        unit="barrels per day", geography="US", parent_total=regional_fact["value"],
        parent_label="regional/global total",
    )
    assert out["flagged"] is False  # 50 <= 1000, well within parent
    assert out["original_value"] == derived["value"]


def test_diagnose_derivation_origin_unknown_for_non_derived_fact():
    out = mv.diagnose_derivation_origin({"value": 10.0, "unit": "barrels"})
    assert out["origin"] == "unknown"


def test_diagnose_derivation_origin_activity_intensity_points_to_intensity():
    activity = {"value": 100.0, "unit": "vehicles", "evidence_type": "verified_observed", "verification_status": "verified_observed"}
    intensity = {"value": 500.0, "unit": "liters per vehicle", "evidence_type": "derived", "verification_status": "derived"}
    derived = derivation.derive_activity_intensity(activity, intensity)
    diagnosis = mv.diagnose_derivation_origin(derived)
    assert diagnosis["origin"] == "intensity"


# --------------------------------------------------------------------------- #
# 12. Price magnitude validation
# --------------------------------------------------------------------------- #
def test_price_magnitude_validation_flags_large_discrepancy():
    out = mv.evaluate_magnitude(
        check_type="price_magnitude", subject_label="'Transportation Fuels' price",
        value=1.0, unit="USD per barrel", verification_status="assumption",
        references=[_ref(75.0, unit="USD per barrel", source="NYMEX")],
    )
    assert out["flagged"] is True
    assert out["check_type"] == "price_magnitude"
    assert out["classification"] == "order_of_magnitude_mismatch"


def test_price_magnitude_validation_ok_when_close():
    out = mv.evaluate_magnitude(
        check_type="price_magnitude", subject_label="price", value=74.0, unit="USD per barrel",
        references=[_ref(75.0, unit="USD per barrel", source="NYMEX")],
    )
    assert out["flagged"] is False


# --------------------------------------------------------------------------- #
# 13. No silent correction -- the original value is NEVER replaced, in any
#     classification outcome.
# --------------------------------------------------------------------------- #
def test_no_silent_correction_across_every_classification():
    for ref_value, expect_flag in [(1050.0, False), (2000.0, False), (4000.0, True), (18000.0, True)]:
        out = mv.evaluate_magnitude(
            check_type="driver_magnitude", subject_label="x", value=1000.0, unit="barrels per day",
            source_name="Original Source", references=[_ref(ref_value)],
        )
        assert out["original_value"] == 1000.0
        assert out["original_unit"] == "barrels per day"
        assert out["original_source"] == "Original Source"
        assert out["flagged"] is expect_flag


def test_no_reference_available_is_not_a_failure():
    out = mv.evaluate_magnitude(
        check_type="driver_magnitude", subject_label="x", value=1000.0, unit="barrels per day",
        references=[],
    )
    assert out["status"] == "no_reference"
    assert out["flagged"] is False
    assert out["original_value"] == 1000.0


def test_missing_value_never_crashes():
    out = mv.evaluate_magnitude(
        check_type="driver_magnitude", subject_label="x", value=None, unit="barrels per day",
        references=[_ref(4000.0)],
    )
    assert out["flagged"] is False
    assert out["ratio"] is None


# --------------------------------------------------------------------------- #
# Evidence hierarchy classification -- generic, no market-specific lookup.
# --------------------------------------------------------------------------- #
def test_source_authority_rank_generic_keywords():
    assert mv.source_authority_rank("U.S. Energy Information Administration (government agency)")[1] == "government_or_statistical"
    assert mv.source_authority_rank("International Energy Agency")[1] == "international_organization"
    assert mv.source_authority_rank("Portland Cement Association")[1] == "industry_association"
    assert mv.source_authority_rank("Random Blog Post")[1] == "secondary_or_unclassified"
    assert mv.source_authority_rank("")[1] == "secondary_or_unclassified"


# --------------------------------------------------------------------------- #
# Year / period consistency across driver/intensity/price facts.
# --------------------------------------------------------------------------- #
def test_year_period_consistency_flags_large_year_gap():
    facts = {
        "driver": {"year": 2025, "unit": "barrels per day"},
        "intensity": {"year": 2010, "unit": "liters per vehicle"},
        "price": {"year": 2025, "unit": "USD per barrel"},
    }
    out = mv.check_year_period_consistency(facts)
    assert out["flagged"] is True
    assert any(m["kind"] == "year_gap" for m in out["mismatches"])


def test_year_period_consistency_flags_explicit_period_conflict():
    facts = {
        "driver": {"year": 2025, "unit": "barrels per year"},
        "intensity": {"year": 2025, "unit": "barrels per day"},
    }
    out = mv.check_year_period_consistency(facts)
    assert out["flagged"] is True
    assert any(m["kind"] == "period_conflict" for m in out["mismatches"])


def test_year_period_consistency_no_flag_when_consistent():
    facts = {
        "driver": {"year": 2025, "unit": "barrels per day"},
        "intensity": {"year": 2024, "unit": "liters per vehicle"},
        "price": {"year": 2025, "unit": "USD per barrel"},
    }
    out = mv.check_year_period_consistency(facts)
    assert out["flagged"] is False


def test_year_period_consistency_absence_is_not_a_conflict():
    facts = {"driver": {"unit": "barrels per day"}, "intensity": {"year": 2020, "unit": "liters per vehicle"}}
    out = mv.check_year_period_consistency(facts)
    assert out["flagged"] is False


# --------------------------------------------------------------------------- #
# Node-level tests -- `magnitude_cross_validation_node`. The acquisition
# boundary (`gather_independent_references`) is monkeypatched so no real
# LLM/network call happens; only the deterministic comparison logic is
# exercised end-to-end against realistic state shapes.
# --------------------------------------------------------------------------- #
class _StubConfig:
    enable_web = False


def _driver_fact(value, unit="barrels per day", **extra):
    return {
        "value": value, "unit": unit, "source": "EIA", "source_url": "https://eia.gov/x",
        "year": 2025, "evidence_type": "verified_observed", "verification_status": "verified_observed",
        "confidence": 0.9, **extra,
    }


def _node_state(**overrides):
    state = {
        "market_name": "Crude Oil",
        "config": _StubConfig(),
        "llm": object(),
        "log": [],
        "usage_buckets": [{"name": "Transportation Fuels", "description": "x", "bucket_category": "physical_consumption"}],
        "drivers": {"Transportation Fuels": {"driver_name": "Fuel consumption", "unit": "barrels per day"}},
        "quantities": {"Transportation Fuels": {"US": {"value": 1000.0, "unit": "barrels per day"}}},
        "driver_facts": {
            "Transportation Fuels": {
                "US": {"driver_raw": _driver_fact(1000.0), "intensity": _driver_fact(2.0, unit="liters per vehicle")},
            },
        },
        "prices": {},
        "price_facts": {},
        "usage_values": {"Transportation Fuels": {"US": 10.0}},
        "total_market_value": 10.0,
    }
    state.update(overrides)
    return state


def test_node_flags_large_driver_discrepancy(monkeypatch):
    monkeypatch.setattr(mv, "gather_independent_references", lambda *a, **k: [_ref(4000.0)])
    out = mv.magnitude_cross_validation_node(_node_state())
    driver_flags = [r for r in out["magnitude_validation_flags"] if r["check_type"] == "driver_magnitude"]
    assert len(driver_flags) == 1
    assert driver_flags[0]["original_value"] == 1000.0


def test_node_no_flags_when_reference_matches(monkeypatch):
    monkeypatch.setattr(mv, "gather_independent_references", lambda *a, **k: [_ref(1010.0)])
    out = mv.magnitude_cross_validation_node(_node_state())
    assert out["magnitude_validation_flags"] == []


def test_node_never_mutates_quantities_or_prices(monkeypatch):
    monkeypatch.setattr(mv, "gather_independent_references", lambda *a, **k: [_ref(9999.0)])
    state = _node_state()
    original_quantities = {k: dict(v) for k, v in state["quantities"].items()}
    original_prices = dict(state["prices"])
    mv.magnitude_cross_validation_node(state)
    assert state["quantities"] == original_quantities
    assert state["prices"] == original_prices


def test_node_flags_price_magnitude_discrepancy(monkeypatch):
    monkeypatch.setattr(mv, "gather_independent_references", lambda *a, **k: [_ref(75.0, unit="USD per barrel")])
    state = _node_state(
        prices={"Transportation Fuels": {"asp_usd_per_unit": 1.0, "priced_unit": "barrel", "evidence_type": "assumption"}},
        price_facts={"Transportation Fuels": {"source": "assumption", "year": 2025}},
    )
    out = mv.magnitude_cross_validation_node(state)
    price_flags = [r for r in out["magnitude_validation_flags"] if r["check_type"] == "price_magnitude"]
    assert len(price_flags) == 1


def test_node_flags_derived_quantity_exceeding_parent_total(monkeypatch):
    monkeypatch.setattr(mv, "gather_independent_references", lambda *a, **k: [])
    regional_fact = {
        "value": 100.0, "unit": "barrels per day", "geography": "Global", "year": 2025,
        "source": "EIA", "evidence_type": "verified_observed", "verification_status": "verified_observed",
        "confidence": 0.9,
    }
    share_fact = {
        "value": 5.0, "unit": "share", "evidence_type": "derived", "verification_status": "derived",
        "confidence": 0.6, "source": "reference_data.population_share",
    }
    # Deliberately invalid share (>1) is rejected by derivation.py itself --
    # instead, build a technically-valid derived fact then simulate a
    # blowup by overriding its value directly (mirrors a bug elsewhere in
    # the pipeline producing an implausible country figure).
    derived = derivation.derive_regional_share(regional_fact, {**share_fact, "value": 0.5}, geography="US")
    blown_up = {**derived, "value": 500.0}  # exceeds the regional parent total of 100
    state = _node_state(
        quantities={"Transportation Fuels": {"US": {"value": 500.0, "unit": "barrels per day"}}},
        driver_facts={
            "Transportation Fuels": {
                "US": {"driver_raw": blown_up, "intensity": _driver_fact(2.0, unit="liters per vehicle")},
            },
        },
    )
    out = mv.magnitude_cross_validation_node(state)
    derived_flags = [r for r in out["magnitude_validation_flags"] if r["check_type"] == "derived_quantity_magnitude"]
    assert len(derived_flags) == 1
    assert derived_flags[0]["classification"] == "exceeds_parent_total"
    assert derived_flags[0]["derivation_diagnosis"]["derivation_method"] == "regional_share"
    assert derived_flags[0]["original_value"] == 500.0  # never clamped back to 100

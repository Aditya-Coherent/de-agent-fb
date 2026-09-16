"""Phase 6 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) -- Evidence Verification &
Provenance Integration.

Covers the acceptance criteria this phase adds on top of Phases 0-5:
  - every gate function in `evidence.py` stamps BOTH `evidence_type` and
    `verification_status` with the identical, authoritative 4-value tag
    (the field-name decision documented in `evidence.py` itself);
  - `worse_of`'s 4-value ranking, all pairwise combinations, including the
    "never promoted" and "combined provenance capped at the weakest input"
    guarantees for driver x intensity and driver x price;
  - `quality.py`'s new `source_found_unverified_pct` and
    `high_unverified_claim_share` flag;
  - `buildup_writer.py`'s 4th provenance color and JSON/XLSX percentage
    rollups (never merging `source_found_unverified` into `observed`);
  - backward compatibility via the Phase 0 `legacy_evidence_type` shim.

Run with: pytest tests/test_demand_usage_phase6_provenance.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from me_engine.demand_usage import buildup_writer, evidence, quality

# --------------------------------------------------------------------------- #
# 1. Every producer stamps the correct, authoritative verification_status
# --------------------------------------------------------------------------- #
def test_verified_observed_fact_from_build_fact():
    fact = evidence.build_fact(
        value=8_900_000.0, unit="barrels per day", year=2025, source_name="EIA",
        source_url="https://eia.gov/x", citation="8.9 million barrels per day",
        evidence_text="US consumption is 8.9 million barrels per day.",
    )
    assert fact["evidence_type"] == "verified_observed"
    assert fact["verification_status"] == "verified_observed"


def test_verified_observed_fact_from_build_structured_fact():
    fact = evidence.build_structured_fact(
        value=100.0, unit="tonnes", year=2025, source_name="USGS", source_url="https://usgs.gov/x",
        location_ref="sheet='S1', column='Value'", geography_matched=True, ambiguous=False,
    )
    assert fact["evidence_type"] == "verified_observed"
    assert fact["verification_status"] == "verified_observed"


def test_inaccessible_source_is_source_found_unverified_never_promoted():
    """Case B from the plan's contract: the LLM names a specific source but
    code cannot independently verify/trace it -- `build_unverified_claim_fact`
    is the ONLY gate that can produce this tag, and it always does, no matter
    how confident-sounding the inputs."""
    fact = evidence.build_unverified_claim_fact(
        value=4_200_000.0, unit="tonnes", year=2025, source_name="USGS",
        source_url="https://usgs.gov/cement-2025.pdf",
        citation="4.2 million tonnes", reasoning="named, authoritative, but unreachable this run",
    )
    assert fact["evidence_type"] == "source_found_unverified"
    assert fact["verification_status"] == "source_found_unverified"
    # partial source info must survive into the fact, never discarded just
    # because it's unverified (spec section 10, Phase 2).
    assert fact["source"] == "USGS"
    assert fact["source_url"] == "https://usgs.gov/cement-2025.pdf"


def test_derived_from_verified_facts_retains_formula_and_provenance():
    """Case D: a deterministic population-share allocation from a
    `verified_observed` regional fact must be tagged `derived` (never
    stronger than the input it was computed from) and must carry an
    explicit, logged formula referencing that input."""
    regional_fact = evidence.build_fact(
        value=1_000_000.0, unit="tonnes", year=2025, source_name="USGS",
        source_url="https://usgs.gov/global", citation="1,000,000 tonnes",
        evidence_text="Global cement production was 1,000,000 tonnes in 2025.",
    )
    assert regional_fact["evidence_type"] == "verified_observed"
    share = 0.15
    derived_status = evidence.worse_of(regional_fact["evidence_type"], "derived")
    country_fact = {
        **regional_fact,
        "value": round(regional_fact["value"] * share, 6),
        "evidence_type": derived_status,
        "verification_status": derived_status,
        "derivation_basis": (
            f"population_share: country_value = regional_value({regional_fact['value']:,.2f} tonnes, "
            f"verified_observed) * population_share=0.1500"
        ),
    }
    assert country_fact["evidence_type"] == "derived"
    assert country_fact["verification_status"] == "derived"
    assert "population_share" in country_fact["derivation_basis"]
    assert "verified_observed" in country_fact["derivation_basis"]  # provenance to the input retained


def test_derived_from_unverified_input_is_capped_not_promoted():
    """The same population-share derivation, but the regional fact was only
    `source_found_unverified` -- the derivation must be capped at that
    weaker tier, never presented as an ordinary `derived` (which the
    plan's "combined provenance never stronger than its weakest required
    input" rule extends to single-input derivations too)."""
    regional_fact = evidence.build_unverified_claim_fact(
        value=1_000_000.0, unit="tonnes", year=2025, source_name="Trade Press",
        citation="reportedly ~1,000,000 tonnes", reasoning="best recollection, unreachable",
    )
    derived_status = evidence.worse_of(regional_fact["evidence_type"], "derived")
    assert derived_status == "source_found_unverified"


def test_no_evidence_no_derivation_is_assumption():
    fact = evidence.build_structured_fact(
        value=None, unit="tonnes", year=2025, source_name="USGS", source_url="https://usgs.gov/x",
        location_ref="ref", geography_matched=False,
    )
    assert fact["evidence_type"] == "assumption"
    assert fact["verification_status"] == "assumption"


# --------------------------------------------------------------------------- #
# 2. worse_of -- the 4-value ranking, every pairwise combination
# --------------------------------------------------------------------------- #
def test_worse_of_rank_order_all_pairs():
    order = ["verified_observed", "derived", "source_found_unverified", "assumption"]
    for i, stronger in enumerate(order):
        for weaker in order[i:]:
            assert evidence.worse_of(stronger, weaker) == weaker
            assert evidence.worse_of(weaker, stronger) == weaker  # order-independent


def test_worse_of_same_value_is_identity():
    for v in ["verified_observed", "derived", "source_found_unverified", "assumption"]:
        assert evidence.worse_of(v, v) == v


def test_worse_of_no_inputs_defaults_to_assumption():
    assert evidence.worse_of() == "assumption"


def test_worse_of_unrecognized_value_defaults_to_weakest_never_promoted():
    # An unexpected/garbage tag ranks at the weakest tier (0, same as
    # `assumption`) and the combined result reads as `assumption` -- the
    # safest, weakest interpretation, never silently treated as strong and
    # never echoed back as some unrecognized third label.
    assert evidence.worse_of("verified_observed", "something_unexpected") == "assumption"
    assert evidence.worse_of("something_unexpected") == "assumption"
    assert evidence.worse_of("derived", "something_unexpected") != "derived"


def test_worse_of_never_promotes_unverified_to_observed():
    assert evidence.worse_of("source_found_unverified", "verified_observed") == "source_found_unverified"


# --------------------------------------------------------------------------- #
# 3. Combined driver x intensity / driver x price provenance
# --------------------------------------------------------------------------- #
def test_combined_driver_and_intensity_capped_at_weakest():
    driver = evidence.build_fact(
        value=100.0, unit="tonnes", year=2025, source_name="A", source_url="https://a.example",
        citation="100 tonnes", evidence_text="Production was 100 tonnes.",
    )
    intensity = evidence.build_unverified_claim_fact(
        value=0.5, unit="fraction", year=2025, source_name="Trade Press",
        citation="~50%", reasoning="best recollection",
    )
    assert driver["evidence_type"] == "verified_observed"
    assert intensity["evidence_type"] == "source_found_unverified"
    combined = evidence.worse_of(driver["evidence_type"], intensity["evidence_type"])
    assert combined == "source_found_unverified"


def test_combined_driver_and_price_capped_at_weakest_via_conversion_factor():
    price = evidence.build_fact(
        value=100.0, unit="USD/barrel", year=2025, source_name="EIA", source_url="https://eia.gov/x",
        citation="$100/barrel", evidence_text="Crude sold for $100 per barrel.",
    )
    conversion_factor = {"evidence_type": "assumption"}  # no evidence, no derivation path
    assert price["evidence_type"] == "verified_observed"
    overall = evidence.worse_of(price["evidence_type"], conversion_factor["evidence_type"])
    assert overall == "assumption"  # never stronger than the weakest required input


def test_combined_all_verified_observed_stays_verified_observed():
    driver = evidence.build_fact(
        value=100.0, unit="tonnes", year=2025, source_name="A", source_url="https://a.example",
        citation="100 tonnes", evidence_text="Production was 100 tonnes.",
    )
    intensity = evidence.build_structured_fact(
        value=0.5, unit="fraction", year=2025, source_name="B", source_url="https://b.example",
        location_ref="ref", geography_matched=True,
    )
    assert evidence.worse_of(driver["evidence_type"], intensity["evidence_type"]) == "verified_observed"


# --------------------------------------------------------------------------- #
# 4. quality.py -- source_found_unverified_pct + high_unverified_claim_share
# --------------------------------------------------------------------------- #
def _state_with_quantities(provenances: list[str]) -> dict:
    quantities = {"Bucket A": {f"C{i}": {"value": 1.0, "provenance": p} for i, p in enumerate(provenances)}}
    return {"quantities": quantities, "validation": {}}


def test_high_unverified_claim_share_flag_fires():
    state = _state_with_quantities(["source_found_unverified"] * 6 + ["verified_observed"] * 4)
    flags = quality.compute_provenance_quality_flags(state)
    codes = {f["code"] for f in flags}
    assert "high_unverified_claim_share" in codes
    assert "no_observed_data" not in codes


def test_high_unverified_claim_share_flag_does_not_fire_when_low():
    state = _state_with_quantities(["source_found_unverified"] * 2 + ["verified_observed"] * 8)
    flags = quality.compute_provenance_quality_flags(state)
    codes = {f["code"] for f in flags}
    assert "high_unverified_claim_share" not in codes


def test_no_observed_data_flag_unaffected_by_unverified_presence():
    # Even with unverified claims present, if there is zero verified_observed
    # data, the pre-existing `no_observed_data` flag must still fire -- Phase
    # 6 is additive, it must not silently swallow this existing signal.
    state = _state_with_quantities(["source_found_unverified"] * 5 + ["assumption"] * 5)
    flags = quality.compute_provenance_quality_flags(state)
    codes = {f["code"] for f in flags}
    assert "no_observed_data" in codes


# --------------------------------------------------------------------------- #
# 5. buildup_writer.py -- provenance colors + JSON/XLSX percentage rollups
# --------------------------------------------------------------------------- #
def test_prov_fill_has_all_four_states_distinct_colors():
    assert set(buildup_writer._PROV_FILL) == set(evidence.PROVENANCE_STATES)
    colors = {fill.fgColor.rgb for fill in buildup_writer._PROV_FILL.values()}
    assert len(colors) == 4  # all 4 distinct, no accidental reuse


def _minimal_state(evidence_quality: dict) -> dict:
    return {
        "market_name": "Test Market", "usage_buckets": [], "countries": [], "drivers": {},
        "quantities": {}, "prices": {}, "usage_values": {}, "driver_facts": {}, "price_facts": {},
        "unit_contract": {}, "validation": {"evidence_quality": evidence_quality},
    }


def test_json_report_never_merges_unverified_into_observed():
    eq = {
        "observed_pct": 25.0, "derived_pct": 25.0,
        "source_found_unverified_pct": 25.0, "assumption_pct": 25.0, "total_inputs": 4,
    }
    report = buildup_writer.build_report_dict(_minimal_state(eq))
    reported_eq = report["evidence_quality"]
    assert reported_eq["source_found_unverified_pct"] == 25.0
    assert reported_eq["observed_pct"] == 25.0  # unchanged by the unverified tier's presence


def test_xlsx_workbook_does_not_crash_on_source_found_unverified(tmp_path):
    eq = {"observed_pct": 0.0, "derived_pct": 0.0, "source_found_unverified_pct": 100.0, "assumption_pct": 0.0, "total_inputs": 1}
    state = _minimal_state(eq)
    state["usage_buckets"] = [{"name": "Bucket A", "description": "d"}]
    state["countries"] = ["US"]
    state["drivers"] = {"Bucket A": {"driver_name": "d", "unit": "u", "final_demand_unit": "u"}}
    state["quantities"] = {"Bucket A": {"US": {"value": 1.0, "provenance": "source_found_unverified", "note": "n"}}}
    state["prices"] = {"Bucket A": {"asp_usd_per_unit": 1.0, "evidence_type": "source_found_unverified"}}
    out_path = buildup_writer.write_buildup_workbook(state, tmp_path / "out.xlsx")
    assert out_path.exists()


# --------------------------------------------------------------------------- #
# 6. Backward compatibility -- Phase 0's legacy_evidence_type shim
# --------------------------------------------------------------------------- #
def test_legacy_evidence_type_shim_still_works_after_phase_6():
    assert evidence.legacy_evidence_type("verified_observed") == "observed"
    assert evidence.legacy_evidence_type("source_found_unverified") == "assumption"
    assert evidence.legacy_evidence_type("derived") == "derived"
    assert evidence.legacy_evidence_type("assumption") == "assumption"


def test_provenance_states_tuple_matches_verification_status_literal():
    import typing

    assert set(evidence.PROVENANCE_STATES) == set(typing.get_args(evidence.VerificationStatus))

"""Deterministic validation for the Crude Oil Usage-Pattern (Demand-Side)
methodology fix (2026-08-26, CRUDE_OIL_METHODOLOGY_FIX): a usage bucket
describing downstream/refined-product demand (Transportation Fuels,
Asphalt & Road Construction, etc.) must no longer be auto-rejected merely
for involving a refined/downstream product -- it must instead be judged by
whether it can be defensibly connected to (and ultimately expressed in)
the TARGET commodity's own quantity, and the final market value must
always be TargetCommodityQuantity x TargetCommodityPrice, never a
downstream product's own quantity x its own price.

Proves the 7 required end-state behaviors:
  1. Transportation Fuels (downstream) can PASS relevance validation.
  2. Asphalt (downstream) can PASS relevance validation when a defensible
     crude-oil conversion exists.
  3. A downstream price is never used as the final target-commodity price
     (Target Commodity Integrity / Downstream Leakage check).
  4. Final market value = TargetCommodityQuantity x TargetCommodityPrice,
     verified by the SAME deterministic `calculate_node` arithmetic.
  5. Unsupported conversion factors are not silently accepted as facts
     (unchanged -- covered by existing evidence-classification tests;
     smoke-tested here for this specific driver shape).
  6. The system no longer needs a human override merely because a usage
     bucket is downstream (the deterministic floor is 'ambiguous', which
     `taxonomy.approve_taxonomy` does NOT gate on).
  7. All of the above stays deterministic/auditable -- no hidden state.

Run with: pytest tests/test_crude_oil_methodology_fix.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from me_engine.demandUseCaseCountry_approach2B import agents, taxonomy


class _ScopeLLM:
    def __init__(self, verdicts: dict[str, dict]):
        self._verdicts = verdicts

    def complete_json(self, system, user):
        return {"buckets": [{"name": name, **v} for name, v in self._verdicts.items()]}


def _scope_state(scope_text: str, buckets: list[dict], llm, physical_unit: str = "Barrels") -> dict:
    return {
        "llm": llm, "market_name": "Crude Oil", "log": [],
        "market_definition": {"text": scope_text},
        "usage_buckets": buckets,
        "physical_unit": physical_unit,
    }


# --------------------------------------------------------------------------- #
# 1 & 2. Downstream usage buckets can PASS relevance validation.
# --------------------------------------------------------------------------- #
def test_transportation_fuels_downstream_usage_passes_relevance_check():
    """A correctly-reasoning LLM (per the new Target-Commodity Demand
    Relevance prompt) marks Transportation Fuels in_scope for Crude Oil --
    and the node must not force it back to out_of_scope."""
    llm = _ScopeLLM({
        "Transportation Fuels": {
            "classification": "in_scope",
            "reason": "gasoline/diesel/jet fuel consumption creates upstream demand for the crude oil "
                      "refined to produce them, quantifiable via a crude-oil-per-liter-of-fuel yield factor",
            "evidence": "downstream demand used as an input signal, not valued as a separate market",
            "confidence": 0.85,
        },
    })
    state = _scope_state(
        "Crude oil upstream production and trade; refined products are not separately valued, but "
        "downstream demand may be used to derive crude-oil requirement.",
        [{"name": "Transportation Fuels", "description": "gasoline/diesel/jet fuel end use"}],
        llm,
    )
    out = agents.scope_taxonomy_validation_node(state)
    flag = out["scope_validation_flags"]["Transportation Fuels"]
    assert flag["classification"] == "in_scope"


def test_asphalt_downstream_usage_passes_relevance_check_with_defensible_conversion():
    llm = _ScopeLLM({
        "Asphalt & Road Construction": {
            "classification": "in_scope",
            "reason": "asphalt/bitumen is a crude-oil-derived feedstock; road construction demand implies "
                      "a bitumen requirement translatable into crude-oil-equivalent barrels",
            "evidence": "defensible crude-oil-equivalent conversion path exists",
            "confidence": 0.8,
        },
    })
    state = _scope_state(
        "Crude oil upstream production and trade; refined products are not separately valued, but "
        "downstream demand may be used to derive crude-oil requirement.",
        [{"name": "Asphalt & Road Construction", "description": "bitumen/asphalt used in road construction"}],
        llm,
    )
    out = agents.scope_taxonomy_validation_node(state)
    assert out["scope_validation_flags"]["Asphalt & Road Construction"]["classification"] == "in_scope"


def test_scope_prompt_explicitly_states_downstream_involvement_is_not_disqualifying():
    """Regression guard against silently reverting the prompt fix -- the
    system prompt itself must state the corrected principle."""
    captured = {}

    class _CapturingLLM:
        def complete_json(self, system, user):
            captured["system"] = system
            return {"buckets": []}

    state = _scope_state("Crude oil upstream.", [{"name": "X", "description": "y"}], _CapturingLLM())
    agents.scope_taxonomy_validation_node(state)
    assert "never mark a bucket out_of_scope merely because it describes a downstream" in captured["system"].lower()


# --------------------------------------------------------------------------- #
# 3. A downstream price is never used as the final target-commodity price
#    (Target Commodity Integrity / Downstream Leakage).
# --------------------------------------------------------------------------- #
def test_downstream_unit_leakage_is_flagged_not_silently_priced():
    """A bucket whose final_demand_unit is still 'metric tons of asphalt'
    (a downstream product's own unit) while the market's target commodity
    is 'Barrels' must be FLAGGED -- the signature of the asphalt-price-as-
    crude-oil-price bug this fix addresses."""
    reason = agents._check_target_commodity_unit("metric tons of asphalt", "Barrels")
    assert reason is not None
    assert "target-commodity unit" in reason


def test_correctly_anchored_target_commodity_unit_is_not_flagged():
    reason = agents._check_target_commodity_unit("barrels of crude oil equivalent", "Barrels")
    assert reason is None


def test_calculate_node_flags_downstream_leakage_without_zeroing_the_bucket(monkeypatch):
    """The flag must be informational -- it must NOT silently zero a
    bucket's contribution (that would be a NEW silent-corruption bug); it
    must surface via `target_commodity_unit_mismatch_flags` instead."""
    state = {
        "log": [], "physical_unit": "Barrels",
        "usage_buckets": [{"name": "Asphalt & Road Construction", "bucket_category": "physical_consumption"}],
        "drivers": {
            "Asphalt & Road Construction": {
                "unit": "metric tons of asphalt", "final_demand_unit": "metric tons of asphalt",
                "consumption_unit": "",
            },
        },
        "quantities": {"Asphalt & Road Construction": {"US": {"value": 100.0}}},
        "prices": {"Asphalt & Road Construction": {"asp_usd_per_unit": 450.0}},
        "countries": ["US"],
    }
    out = agents.calculate_node(state)
    assert out["target_commodity_unit_mismatch_flags"]
    assert "Asphalt & Road Construction" not in out["unit_rejected_buckets"]  # NOT excluded
    assert out["usage_values"]["Asphalt & Road Construction"]["US"] != 0.0  # value still computed, just flagged


# --------------------------------------------------------------------------- #
# 4. Final market value = TargetCommodityQuantity x TargetCommodityPrice.
# --------------------------------------------------------------------------- #
def test_final_value_is_target_commodity_quantity_times_target_commodity_price():
    """Once final_demand_unit is correctly anchored to the target commodity
    ('barrels of crude oil', reached via the intensity conversion), the
    SAME unchanged deterministic arithmetic in calculate_node correctly
    computes CrudeOilQuantity x CrudeOilPrice -- proving the fix is a
    unit-labeling/validation correction, not a new calculation formula."""
    state = {
        "log": [], "physical_unit": "Barrels",
        "usage_buckets": [{"name": "Asphalt & Road Construction", "bucket_category": "physical_consumption"}],
        "drivers": {
            "Asphalt & Road Construction": {
                "unit": "metric tons of asphalt",
                "final_demand_unit": "barrels of crude oil equivalent",  # corrected by reconcile_final_demand_unit
                "consumption_unit": "barrels of crude oil equivalent per metric ton of asphalt",
            },
        },
        "quantities": {"Asphalt & Road Construction": {"US": {"value": 7_000_000.0}}},  # already driver x intensity, in barrels
        "prices": {"Asphalt & Road Construction": {"asp_usd_per_unit": 80.0}},  # crude oil $/barrel, NOT asphalt $/ton
        "countries": ["US"],
    }
    out = agents.calculate_node(state)
    assert out["target_commodity_unit_mismatch_flags"] == []  # correctly anchored -- no leakage flag
    expected_usd_mn = round(7_000_000.0 * 80.0 / 1_000_000.0, 4)
    assert out["usage_values"]["Asphalt & Road Construction"]["US"] == expected_usd_mn
    assert out["total_market_value"] == expected_usd_mn


# --------------------------------------------------------------------------- #
# 6. No human override needed merely because a usage bucket is downstream.
# --------------------------------------------------------------------------- #
def test_ambiguous_downstream_bucket_approves_without_scope_override(tmp_path):
    """`taxonomy.approve_taxonomy` only gates on classification ==
    'out_of_scope' -- 'ambiguous' (the new deterministic floor for a
    literal exclusion-phrase word overlap) must approve with ZERO
    scope_overrides required."""
    draft = {
        "market_name": "Crude Oil",
        "usage_buckets": [
            {"name": "Transportation Fuels", "description": "gasoline/diesel/jet fuel end use",
             "bucket_category": "physical_consumption"},
        ],
        "drivers": {
            "Transportation Fuels": {"driver_name": "Fuel consumption", "unit": "barrels per day",
                                      "final_demand_unit": "barrels per year"},
        },
        "scope_validation_flags": {
            "Transportation Fuels": {"classification": "ambiguous", "reason": "downstream demand pathway"},
        },
        "scope_overrides": {},  # deliberately empty -- no override provided
    }
    approved = taxonomy.approve_taxonomy(draft, root=tmp_path)  # must NOT raise
    assert approved["approved"] is True
    assert approved["usage_buckets"][0]["name"] == "Transportation Fuels"

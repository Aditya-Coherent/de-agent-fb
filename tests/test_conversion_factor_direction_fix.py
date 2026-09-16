"""Regression tests for the price-basis conversion-factor DIRECTION fix
(2026-08-28), found via a live Crude Oil run: `pricing_node`'s conversion
factor is designed to be "<price_basis_unit> per <final_demand_unit>" so
`calculate_node` can multiply it straight in, but the LLM does not always
phrase it that way. Verified failure mode: Transportation Fuels returned
"liters of transportation fuel per barrel" (INVERTED -- final-unit per
price-basis-unit) while Lubricants and Greases returned "tonnes per liter"
(CORRECT -- price-basis-unit per final-unit) in the SAME live run. A blanket
"always divide" fix would have broken the correct case while fixing the
inverted one -- these tests prove the deterministic direction-detection
gets BOTH right, plus the ambiguous/ambiguous-safe fallback.

Run with: pytest tests/test_conversion_factor_direction_fix.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from me_engine.demand_usage import units


def test_inverted_factor_from_real_transportation_fuels_case_is_corrected():
    # Real values from the live Crude Oil run.
    corrected, was_inverted = units.reconcile_conversion_factor_direction(
        value=159.0, factor_unit="liters per barrel",
        price_basis_unit="barrel", final_demand_unit="liters of transportation fuel",
    )
    assert was_inverted is True
    assert corrected == 1.0 / 159.0


def test_correct_factor_from_real_lubricants_case_is_left_unchanged():
    # Real values from the SAME live run -- this one was already correct.
    corrected, was_inverted = units.reconcile_conversion_factor_direction(
        value=0.0009, factor_unit="tonnes per liter",
        price_basis_unit="tonne", final_demand_unit="liters of lubricant",
    )
    assert was_inverted is False
    assert corrected == 0.0009


def test_end_to_end_arithmetic_matches_after_correction():
    """The exact US Transportation Fuels chain: 825B liters, corrected
    factor, $75/barrel -- must land near the independently-computed correct
    value (~$389,150.9M), not the reported buggy $9,838,125,000.0M."""
    quantity_liters = 825_000_000_000.0
    asp_per_barrel = 75.0
    corrected_factor, was_inverted = units.reconcile_conversion_factor_direction(
        value=159.0, factor_unit="liters per barrel",
        price_basis_unit="barrel", final_demand_unit="liters of transportation fuel",
    )
    assert was_inverted is True
    value_usd_mn = quantity_liters * corrected_factor * asp_per_barrel / 1_000_000.0
    assert round(value_usd_mn, 1) == 389150.9


def test_non_rate_shaped_unit_is_left_unchanged():
    corrected, was_inverted = units.reconcile_conversion_factor_direction(
        value=42.0, factor_unit="", price_basis_unit="barrel", final_demand_unit="liters",
    )
    assert was_inverted is False
    assert corrected == 42.0


def test_ambiguous_unit_is_never_guessed_at():
    """Neither side of the unit clearly matches either role -- must NOT
    invert (better to leave a possibly-wrong value alone than confidently
    invert a value that might already be correct)."""
    corrected, was_inverted = units.reconcile_conversion_factor_direction(
        value=2.5, factor_unit="widgets per gadget",
        price_basis_unit="barrel", final_demand_unit="liters of transportation fuel",
    )
    assert was_inverted is False
    assert corrected == 2.5


def test_zero_value_is_never_divided():
    corrected, was_inverted = units.reconcile_conversion_factor_direction(
        value=0.0, factor_unit="liters per barrel", price_basis_unit="barrel", final_demand_unit="liters of fuel",
    )
    assert was_inverted is False
    assert corrected == 0.0


def test_pricing_node_applies_the_correction_and_logs_it(monkeypatch):
    from me_engine.demand_usage import agents, evidence_router, pricing_strategy

    class _Cfg:
        enable_web = True
        evidence_workers = 1

    class _LLM:
        def complete_json(self, system, user, **kwargs):
            return {
                "price_basis_unit": "barrel", "same_as_final_unit": False,
                "conversion_factor_name": "liters of fuel per barrel",
                "conversion_factor_unit": "liters per barrel",
                "rationale": "test",
            }

    monkeypatch.setattr(
        evidence_router, "propose_evidence_plan",
        lambda *a, **kw: {"unit": "liters per barrel", "sources": []},
    )
    monkeypatch.setattr(
        evidence_router, "execute_evidence_plan",
        lambda *a, **kw: ({
            "value": 159.0, "unit": "liters per barrel", "evidence_type": "assumption",
            "verification_status": "assumption", "confidence": 0.3, "source": "EIA",
            "citation": "test", "call_failed": False,
        }, [{"source_type": "web_page", "found": True}]),
    )
    monkeypatch.setattr(pricing_strategy, "is_circular_price_derivation", lambda fact: False)
    monkeypatch.setattr(
        evidence_router, "propose_pricing_evidence_plan",
        lambda *a, **kw: {"unit": "barrel", "sources": []},
    )

    state = {
        "config": _Cfg(), "llm": _LLM(), "market_name": "Crude Oil", "log": [],
        "usage_buckets": [{"name": "Transportation Fuels", "description": "d"}],
        "drivers": {"Transportation Fuels": {
            "driver_name": "VKT", "final_demand_unit": "liters of transportation fuel", "unit": "vehicle-km",
        }},
    }
    out = agents.pricing_node(state)
    conv = out["pricing_conversion_factors"]["Transportation Fuels"]
    assert conv["direction_corrected"] is True
    assert abs(conv["value"] - 1.0 / 159.0) < 1e-9
    assert any("expressed" in line and "backwards" in line for line in state["log"])

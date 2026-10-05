"""Regression tests for the Disposable Medical Gloves 2B run (2026-10-04),
which reported ~$4.9 trillion:

- Dental: quantity in single gloves, price $9.50 per BOX, factor "units of
  gloves per box" = 100. The factor is final-unit-per-price-basis (inverted)
  but its numerator shares the word "gloves" with the price basis "box of
  gloves", so the overlap check treated it as ambiguous and multiplied by 100
  instead of dividing: 10,000x too large.
- Clinical / Emergency: the effective price per glove ($5 and $12.50) is
  ~30-70x the other use cases (~$0.12-0.85), a box price applied to single
  gloves. Now raised as a critical `unit_price_outlier` flag.
"""
from __future__ import annotations

import pytest

from me_engine.demandUseCaseCountry_approach2B import quality, units


def test_dental_box_factor_is_inverted():
    corrected, was_inverted = units.reconcile_conversion_factor_direction(
        value=100.0, factor_unit="units of gloves per box",
        price_basis_unit="box of gloves", final_demand_unit="Units",
    )
    assert was_inverted is True
    assert corrected == pytest.approx(0.01)
    # 640M gloves x 0.01 box/glove x $9.50/box = $60.8M (was $608,000M)
    assert 640_000_000 * corrected * 9.5 / 1e6 == pytest.approx(60.8)


@pytest.mark.parametrize("factor_unit,basis,final,inverted", [
    ("liters per barrel", "barrel", "liters of transportation fuel", True),   # existing crude-oil case
    ("tonnes per liter", "tonne", "liters of lubricant", False),              # existing correct case
    ("gloves per box", "box", "gloves (single pieces)", True),
    ("boxes per glove", "box of gloves", "gloves (single pieces)", False),
    ("kg per pack", "pack of noodles", "kg of noodles", True),
])
def test_direction_cases(factor_unit, basis, final, inverted):
    _, was_inverted = units.reconcile_conversion_factor_direction(
        value=10.0, factor_unit=factor_unit, price_basis_unit=basis, final_demand_unit=final,
    )
    assert was_inverted is inverted


def test_ambiguous_factor_is_still_left_alone():
    # Same measure word on both sides -> no safe call; never guess.
    corrected, was_inverted = units.reconcile_conversion_factor_direction(
        value=2.0, factor_unit="gloves per unit", price_basis_unit="glove", final_demand_unit="Units",
    )
    assert (corrected, was_inverted) == (2.0, False)


def _gloves_state():
    unit = {"final_demand_unit": "Units"}
    prices = {
        "Surgical/Operating Room": 0.85, "Clinical Examination & Patient Care": 0.05,
        "Chemotherapy & Hazardous Drug Handling": 0.18, "Infection Control & Environmental Cleaning": 0.12,
        "Diagnostic & Laboratory Procedures": 0.08, "Emergency & First Response": 12.5,
        "Dental & Outpatient Specialty Care": 9.5,
    }
    return {
        "drivers": {n: dict(unit) for n in prices},
        "prices": {n: {"asp_usd_per_unit": p} for n, p in prices.items()},
        "pricing_conversion_factors": {
            "Clinical Examination & Patient Care": {"value": 100.0},
            "Diagnostic & Laboratory Procedures": {"value": 2.0},
            "Dental & Outpatient Specialty Care": {"value": 0.01},  # after the direction fix
        },
    }


def test_unit_price_outliers_catch_clinical_and_emergency():
    outliers = quality.unit_price_outliers(_gloves_state())
    names = {o.split("'")[1] for o in outliers}
    assert names == {"Clinical Examination & Patient Care", "Emergency & First Response"}


def test_unit_price_outlier_raises_a_critical_flag():
    flags = quality.compute_provenance_quality_flags(_gloves_state())
    assert any(f["code"] == "unit_price_outlier" and f["severity"] == "critical" for f in flags)


def test_consistent_prices_raise_no_outlier():
    state = _gloves_state()
    state["prices"]["Emergency & First Response"]["asp_usd_per_unit"] = 0.125
    state["pricing_conversion_factors"]["Clinical Examination & Patient Care"]["value"] = 1.0
    assert quality.unit_price_outliers(state) == []


def test_fewer_than_three_use_cases_never_flag():
    state = {"drivers": {"a": {"final_demand_unit": "t"}, "b": {"final_demand_unit": "t"}},
             "prices": {"a": {"asp_usd_per_unit": 1.0}, "b": {"asp_usd_per_unit": 1000.0}}}
    assert quality.unit_price_outliers(state) == []


def test_sibling_use_cases_never_share_a_reused_driver(monkeypatch):
    """Olive Oil 2B (2026-10-03): 'Foodservice' reused the 'Household olive oil
    consumption volume' driver that 'Home Culinary' had just saved."""
    from me_engine.demandUseCaseCountry_approach2B import agents, driver_library

    household = {"driver_name": "Household olive oil consumption volume", "driver_unit": "Tonnes",
                 "final_demand_unit": "Tonnes", "conversion_formula": "driver_value", "match_similarity": 0.9}
    monkeypatch.setattr(driver_library, "lookup", lambda desc, log=None: dict(household))
    monkeypatch.setattr(driver_library, "append", lambda *a, **k: None)

    class _Cfg:
        enable_web = False

    class _LLM:
        def complete_json(self, system, user, **kwargs):
            return {"driver_name": "Foodservice olive oil purchases", "unit": "Tonnes", "requires_conversion": False,
                    "intensity_kind": "rate_per_driver_unit", "consumption_unit": "", "final_demand_unit": "Tonnes",
                    "conversion_formula": "driver_value", "required_evidence": [], "rationale": "r"}

    state = {"config": _Cfg(), "llm": _LLM(), "market_name": "Olive Oil", "log": [], "countries": ["Spain"],
             "physical_unit": "Tonnes",
             "usage_buckets": [{"name": "Home Culinary Use", "description": "household cooking"},
                               {"name": "Foodservice & Restaurant Use", "description": "restaurants cooking"}]}
    out = agents.demand_driver_node(state)
    assert out["drivers"]["Home Culinary Use"]["driver_name"] == "Household olive oil consumption volume"
    assert out["drivers"]["Foodservice & Restaurant Use"]["driver_name"] == "Foodservice olive oil purchases"
    assert any("NOT reusing library driver" in line for line in state["log"])


# Olive Oil 2B (2026-10-04): meals x "liters per meal" was stored as Tonnes
# (US foodservice = 225 million t, ~1000x too high; the formula's "* 0.91"
# density text was never applied).
@pytest.mark.parametrize("src,dst,density,expected", [
    ("liters", "Tonnes", 0.91, 0.00091),
    ("liters", "Tonnes", None, 0.001),
    ("kg", "tonnes", None, 0.001),
    ("grams of vinegar", "metric tons", None, 1e-6),
    ("tonnes", "Tonnes", None, 1.0),
    ("ml", "litres", None, 0.001),
    ("barrels", "liters", None, 158.987),
    ("tonnes", "liters", 0.91, 1000 / 0.91),
])
def test_unit_scale(src, dst, density, expected):
    assert units.unit_scale(src, dst, density) == pytest.approx(expected)


@pytest.mark.parametrize("src,dst", [("meals", "Tonnes"), ("vehicle-km", "liters"), ("gloves", "boxes")])
def test_unit_scale_unknown_units_return_none(src, dst):
    assert units.unit_scale(src, dst) is None


def test_formula_density():
    assert units.formula_density("driver_value * intensity_value * 0.91") == 0.91
    assert units.formula_density("driver_value * intensity_value") is None
    assert units.formula_density("driver_value * intensity_value * 1000") is None


def test_olive_oil_foodservice_case_end_to_end():
    meals, liters_per_meal = 50_000_000_000, 0.0045   # US: 225 million "tonnes" before the fix
    scale = units.unit_scale("liters", "Tonnes", units.formula_density("driver_value * intensity_value * 0.91"))
    tonnes = meals * liters_per_meal * scale
    assert tonnes == pytest.approx(204_750)          # ~205 thousand tonnes, not 225 million

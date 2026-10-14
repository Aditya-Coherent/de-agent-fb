"""2A estimator agent: facts library, safe formula evaluation, and the rule
that every number comes from the library or is counted as an assumption."""
from __future__ import annotations

import pytest

from me_engine.demandCountry_approach2A import estimator, signal_model


class FakeLLM:
    def __init__(self, response):
        self.response = response
        self.prompts = []

    def complete_json(self, system, user, **_):
        self.prompts.append(user)
        return self.response


def _facts(tmp_path):
    estimator.add_facts("Consumer Electronics", [
        {"market": "Televisions", "country": "Germany", "metric": "annual_units_per_person", "value": 0.0556,
         "unit": "units/person/yr", "year": 2023, "kind": "trade", "source": "comtrade"},
        {"market": "Televisions", "country": "France", "metric": "annual_units_per_person", "value": 0.0600,
         "unit": "units/person/yr", "year": 2023, "kind": "trade", "source": "comtrade"},
        {"market": "Smartphones", "country": "Nigeria", "metric": "annual_units_consumed", "value": 2.0e7,
         "unit": "units", "year": 2023, "kind": "trade", "source": "comtrade"},
    ], root=tmp_path)
    return estimator.load_facts("Consumer Electronics", root=tmp_path)


def test_facts_library_upserts_by_identity(tmp_path):
    facts = _facts(tmp_path)
    assert len(facts) == 3
    estimator.add_facts("Consumer Electronics", [dict(facts[0], value=0.06)], root=tmp_path)
    again = estimator.load_facts("Consumer Electronics", root=tmp_path)
    assert len(again) == 3 and next(f for f in again if f["id"] == facts[0]["id"])["value"] == 0.06


def test_evaluate_is_arithmetic_only():
    assert estimator.evaluate("a * b / 2", {"a": 4, "b": 5}) == 10
    assert estimator.evaluate("sqrt(x)", {"x": 9}) == 3
    for bad in ("__import__('os')", "a.b", "open('x')", "c"):
        with pytest.raises(Exception):
            estimator.evaluate(bad, {"a": 1})


def test_estimate_uses_library_values_and_recomputes(tmp_path):
    facts = _facts(tmp_path)
    de = next(f for f in facts if f["country"] == "Germany")
    llm = FakeLLM({"formula": "per_person * pop", "estimate": 999,
                   "inputs": {"per_person": {"value": 0.07, "fact_id": de["id"]},  # copied wrong on purpose
                              "pop": {"value": 1.0e8, "fact_id": None, "assumption_reason": "population"}},
                   "confidence": 0.9, "reasoning": "peer per-capita"})
    out = estimator.estimate(llm, facts, market="Televisions", country="Egypt", metric="annual_units_per_person",
                             unit="units", gdp_of={"Germany": 70000, "France": 55000, "Egypt": 17000})
    assert out["value"] == pytest.approx(0.0556 * 1.0e8)  # library value, code's arithmetic -- not the LLM's 999
    assert out["corrected_inputs"] == ["per_person"] and out["n_assumptions"] == 1
    assert out["confidence"] == estimator.ASSUMPTION_CONFIDENCE_CAP


def test_estimate_without_any_fact_is_low_confidence(tmp_path):
    facts = _facts(tmp_path)
    llm = FakeLLM({"formula": "x", "inputs": {"x": {"value": 5e6, "fact_id": None, "assumption_reason": "guess"}},
                   "confidence": 0.9})
    out = estimator.estimate(llm, facts, market="Televisions", country="Peru", metric="annual_units_consumed",
                             unit="units")
    assert out["value"] == 5e6 and out["confidence"] <= 0.2


def test_estimate_rejects_unsafe_formula(tmp_path):
    facts = _facts(tmp_path)
    llm = FakeLLM({"formula": "__import__('os').getcwd()", "inputs": {}, "confidence": 1})
    assert estimator.estimate(llm, facts, market="Televisions", country="Peru", metric="x", unit="u") is None


def test_trade_anchor_only_for_import_dependent_countries():
    assert signal_model.trade_anchor({"imports_units": 5.5e6, "exports_units": 0.9e6, "year": 2023, "url": "u"})["units"] \
        == pytest.approx(4.6e6)
    assert signal_model.trade_anchor({"imports_units": 1e6, "exports_units": 8e6, "year": 2023, "url": "u"}) is None


def test_price_curve_recovers_elasticity():
    points = [(g, 50 * g ** 0.5, 1e6) for g in (2000, 8000, 20000, 50000, 80000)]
    a, e = signal_model.fit_price_curve(points)
    assert e == pytest.approx(0.5, abs=1e-6)


def test_percent_fact_used_as_fraction_and_context_inputs_are_not_assumptions(tmp_path):
    estimator.add_facts("Consumer Electronics", [
        {"market": "Televisions", "country": None, "metric": "retail_gross_margin_pct", "value": 20.0, "unit": "%",
         "year": None, "kind": "evidence", "source": "web"}], root=tmp_path)
    facts = estimator.load_facts("Consumer Electronics", root=tmp_path)
    m = facts[0]
    llm = FakeLLM({"formula": "1 / (1 - margin) * landed / landed", "confidence": 0.8,
                   "inputs": {"margin": {"value": 0.2, "fact_id": m["id"]},
                              "landed": {"value": 227.0, "context_key": "landed_avg_price_usd"}}})
    out = estimator.estimate(llm, facts, market="Televisions", country=None, metric="consumer_ratio", unit="ratio",
                             context={"landed_avg_price_usd": 227.0})
    assert out["value"] == pytest.approx(1.25) and out["n_assumptions"] == 0 and out["corrected_inputs"] == []
    assert out["confidence"] == 0.8


def test_pricing_agent_curve_and_level():
    from me_engine.demandCountry_approach2A import pricing_agent

    points = [(g, 3.0 * g ** 0.5, 1e6) for g in (3000, 9000, 20000, 45000, 70000, 90000)]
    points.append((30000, 5000.0, 1e6))  # one wild unit value; the robust fit must shrug it off
    curve = pricing_agent.study_curve(points)
    assert curve["elasticity"] == pytest.approx(0.5, abs=0.05)
    lo, hi = curve["elasticity_90ci"]
    assert lo <= curve["elasticity"] <= hi

    study = {"definition": {"level": "vendor"}, "vendor_asp": [{"usd": 300.0}, {"usd": 320.0}], "retail": []}
    lv = pricing_agent.level_factor(study, curve, landed_avg=250.0, gdp_of_country=lambda n: None)
    assert lv["chosen_level"] == "vendor" and lv["factor"] == pytest.approx(310 / 250)
    none = pricing_agent.level_factor({"definition": {"level": "unclear"}, "vendor_asp": [], "retail": []},
                                      curve, 250.0, lambda n: None)
    assert none["factor"] == 1.0

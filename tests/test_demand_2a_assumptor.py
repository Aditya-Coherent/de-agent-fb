"""Assumptor: strategies, learned policy (the RL update), realism guard,
gym episodes and the 2A fill -- all offline (synthetic covariates, fake LLM)."""
from __future__ import annotations

import math
import random

from me_engine.demandCountry_approach2A.assumptor import gym, strategies as S
from me_engine.demandCountry_approach2A.assumptor.agent import assume
from me_engine.demandCountry_approach2A.assumptor.covariates import Covariates
from me_engine.demandCountry_approach2A.assumptor.fill import fill_assumptions
from me_engine.demandCountry_approach2A.assumptor.policy import Policy, interval_score, richness


def _cov(n=30, seed=0):
    rng = random.Random(seed)
    countries, names = {}, {}
    for i in range(n):
        iso = f"C{i:02d}"
        countries[iso] = {"name": f"Country {i}", "region": "Europe" if i % 2 else "Asia",
                          "population": rng.uniform(1e6, 1e8), "gdp_pc_ppp": math.exp(rng.uniform(8, 11)),
                          "urban_pct": rng.uniform(30, 90)}
        names[f"country {i}"] = iso
    return Covariates({"countries": countries, "names": names})


def _truth(c):  # per-capita rises with income, Europe 3x Asia, plus noise
    return 0.002 * (c["gdp_pc_ppp"] / 10000) ** 0.8 * (3 if c["region"] == "Europe" else 1)


class FakeLLM:
    def __init__(self, data):
        self.data, self.calls = data, 0

    def complete_json(self, system, user, **_):
        self.calls += 1
        return self.data


def test_strategies_recover_a_clean_relationship():
    cov = _cov()
    isos = list(cov.countries)
    target = cov.get(isos[0])
    def income_only(c):
        return 0.002 * (c["gdp_pc_ppp"] / 10000) ** 0.8
    known = [S.Known(i, cov.get(i), income_only(cov.get(i))) for i in isos[1:]]
    preds = S.predict_all(target, known)
    assert set(preds) == set(S.DETERMINISTIC)
    assert abs(preds["elasticity"] - math.log(income_only(target))) < 1e-6   # exact relationship recovered


def test_llm_brain_analog_is_computed_by_code_and_bounded():
    cov = _cov()
    isos = list(cov.countries)
    known = [S.Known(cov.get(i)["name"], cov.get(i), 0.004) for i in isos[1:6]]
    llm = FakeLLM({"per_capita": 9.0, "analogs": ["Country 1", "Nowhere"], "adjustment": 50, "reason": "x"})
    out = S.llm_brain(llm, product="olive oil", metric="consumption", unit="kg", target_name="Country 0",
                      target=cov.get(isos[0]), known=known, lessons=[], display_scale=1000.0)
    assert math.isclose(math.exp(out["llm_prior"]), 0.009)          # 9 kg -> 0.009 t
    assert math.isclose(math.exp(out["llm_analog"]), 0.004 * 5.0)   # adjustment clipped to 5, unknown name ignored


def test_policy_learns_bias_and_calibrates_coverage():
    pol = Policy()
    rng = random.Random(3)
    for _ in range(600):
        truth = rng.gauss(0, 1)
        preds = {"peer_income": truth + 0.7 + rng.gauss(0, 0.2), "global_median": truth + rng.gauss(0, 1.5)}
        res = pol.combine("food", "n16+", preds)
        pol.update("food", "n16+", preds, res, truth)
    n, bias, sigma = pol._stats("food", "n16+", "peer_income")
    assert abs(bias - 0.7) < 0.1 and sigma < 0.5
    res = pol.combine("food", "n16+", {"peer_income": 1.7, "global_median": 1.0})
    assert res["weights"]["peer_income"] > 0.9 and abs(res["mu"] - 1.0) < 0.15   # bias removed, best strategy chosen
    assert 0.7 <= pol.track_record("food", "n16+")["coverage_80"] <= 0.9


def test_untrained_baseline_ignores_learning_and_backoff_works():
    pol = Policy()
    for _ in range(30):
        pol.update("food", "n<=3", {"peer_income": 1.0}, {"lo": -1, "hi": 1, "mu": 0}, 0.0)
    assert pol._stats("food", "n<=3", "peer_income")[1] > 0.5
    assert pol._stats("fertilizer", "n<=3", "peer_income")[1] > 0.5        # backs off to *|n<=3
    base = pol.combine("food", "n<=3", {"peer_income": 1.0}, learned=False)
    assert base["mu"] == 1.0 and base["bias"]["peer_income"] == 0.0
    assert interval_score(-1, 1, 2) == 2 + 10 * 1 and richness(3) == "n<=3" and richness(16) == "n16+"


def test_assume_realism_guard_and_track_record():
    cov = _cov()
    isos = list(cov.countries)
    known = {cov.get(i)["name"]: _truth(cov.get(i)) * cov.get(i)["population"] for i in isos[1:12]}
    pol = Policy()
    out = assume(product="x", metric="m", unit="t", country="Country 0", known=known, cov=cov, policy=pol,
                 domain="food")
    assert out["label"] == "estimated_from_facts" and out["p10"] < out["value"] < out["p90"]
    assert "no training history" in out["reason"]
    llm = FakeLLM({"per_capita": 1e6, "analogs": [], "adjustment": 1})       # absurd LLM answer
    pol.contexts["*|*"] = {"strategies": {"llm_prior": {"n": 500, "mean": 0.0, "m2": 0.0, "abs": 0.01}},
                           "scale": 1.0, "n": 500, "covered": 400}
    out = assume(product="x", metric="m", unit="t", country="Country 0", known=known, cov=cov, policy=pol,
                 domain="food", llm=llm, rng=random.Random(0), ceiling_per_capita=0.05)
    assert out["per_capita"] <= 0.05 + 1e-12 and out["flags"]


def test_gym_episode_hides_the_truth_and_learning_improves_calibration():
    cov = _cov(60)
    rng = random.Random(1)
    tasks = [gym.Task("food", f"p{k}", "t", {i: _truth(cov.get(i)) * cov.get(i)["population"] * rng.lognormvariate(0, 0.3)
                                             for i in cov.countries}) for k in range(6)]
    target, known, truth = gym._episode(tasks[0], cov, random.Random(2))
    assert target not in known and truth == tasks[0].values[target]
    pol = Policy()
    before = gym.evaluate(pol, tasks, cov, 300, learned=False)
    gym.train(pol, tasks, cov, 1500, say=lambda s: None)
    after = gym.evaluate(pol, tasks, cov, 300)
    assert after["interval_score"] < before["interval_score"]
    assert abs(after["coverage_80"] - 0.8) < abs(before["coverage_80"] - 0.8) + 0.05


def test_fill_replaces_engine_assumptions_only(tmp_path, monkeypatch):
    cov = _cov()
    monkeypatch.setattr(Covariates, "load", classmethod(lambda cls, root=None: cov))
    names = [cov.countries[i]["name"] for i in cov.countries]
    bucket = "Total X demand"
    q = {n: {"value": _truth(cov.get(n)) * cov.get(n)["population"], "provenance": "derived"} for n in names[1:15]}
    q[names[0]] = {"value": 50.0, "provenance": "assumption"}
    q["Rest of World"] = {"value": 1000.0, "provenance": "assumption"}
    state = {"drivers": {bucket: {"final_demand_unit": "tonnes"}}, "quantities": {bucket: q},
             "usage_values": {bucket: {n: v["value"] * 4000 / 1e6 for n, v in q.items()}}}
    state["country_totals"] = dict(state["usage_values"][bucket])
    out = fill_assumptions(state, "X", root=tmp_path, say=lambda s: None)
    cell = out["quantities"][bucket][names[0]]
    assert cell["provenance"] == "estimated_from_facts" and cell["engine_assumption"] == 50.0
    assert math.isclose(out["usage_values"][bucket][names[0]], cell["value"] * 4000 / 1e6, rel_tol=1e-3)
    assert out["quantities"][bucket]["Rest of World"]["provenance"] == "assumption"   # residual untouched
    assert math.isclose(out["total_market_value"], sum(out["country_totals"].values()), rel_tol=1e-6)

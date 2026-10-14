"""newinfo design (src/newinfo/system.md + documentation.md): ledger, checks,
plausibility, 2C core, price curve, Publisher, progress chart -- all offline."""
from __future__ import annotations

import json
import math

import pytest

from me_engine.consensus.combine import design_blend
from me_engine.demandCountry_approach2A import (
    checks, design_config, ledger as L, ledger_2a, plausibility, price_curve, strategy2c,
)
from me_engine.demandCountry_approach2A.progress import Progress


# --------------------------------------------------------------------------- #
# ledger
# --------------------------------------------------------------------------- #
def test_ledger_ids_labels_and_weakest_input_propagation():
    led = L.Ledger("R017", "2C")
    q = led.fact("Cake production, India", 378000, "t", "India", "QTY", "verified_observed",
                 source={"publisher": "x", "url": "https://x", "tier": 1, "quote": "378,000 t"})
    s = led.fact("Eggless share", 0.395, "share", "India", "SHR", "source_found_unverified")
    d = led.fact("Vinegar dose", 0.02, "t/t", "India", "SHR", "assumption", note="recipe-based dose")
    v = led.mul("Vinegar in eggless cakes", led.mul("eggless cakes", q, s, "t", "India", kind="QTY"), d, "t",
                "India", kind="QTY")
    assert q.number_id == "R017-2C-IND-QTY-0001"
    assert v.label == "derived" and v.weakest_input_label == "assumption"
    assert math.isclose(v.value, 2986.2, rel_tol=1e-9)


def test_ledger_refuses_unlabelled_nan_and_reasonless_assumptions():
    led = L.Ledger("R001", "2A")
    with pytest.raises(L.LedgerError):
        led.fact("x", 1.0, "t", "Spain", "QTY", "observed")
    with pytest.raises(L.LedgerError):
        led.fact("x", float("nan"), "t", "Spain", "QTY", "verified_observed")
    with pytest.raises(L.LedgerError):
        led.fact("x", 1.0, "t", "Spain", "QTY", "assumption")


def test_display_rules():
    assert L.fmt_usd(15.68e9) == "$15.7B" and L.fmt_usd(9.3e9) == "$9.3B" and L.fmt_usd(1.5e6) == "$1.5M"
    assert L.fmt_pct(0.592) == "59.2%"
    assert L.fmt_tonnes(2986.2) == "2,986 t" and L.fmt_tonnes(291_234) == "291,000 t"
    assert L.fmt_inr(125_000_000, 83.3).startswith("₹12.5 crore (US$1,500,000")


def test_ledger_write_and_documentation(tmp_path):
    led = L.Ledger("R001", "2A", 1)
    q = led.fact("Quantity, Spain", 480000, "t", "Spain", "QTY", "verified_observed")
    p = led.fact("Price, Spain", 4000, "USD/t", "Spain", "PRC", "verified_observed")
    led.mul("Value, Spain", q, p, "USD", "Spain")
    paths = led.write(tmp_path)
    data = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert len(data["numbers"]) == 3 and paths["csv"].exists()
    doc = L.write_documentation(led, tmp_path / "documentation.md", {"market": "Olive Oil", "base_year": 2025})
    text = doc.read_text(encoding="utf-8")
    assert "R001-2A-ESP-VAL-0003" in text and "$1.92B" in text


# --------------------------------------------------------------------------- #
# 2A ledger from a sizing state
# --------------------------------------------------------------------------- #
def _state(prices=None):
    bucket = "Total Olive Oil demand"
    fao = "FAOSTAT Food Balances 'Olive Oil', Domestic supply quantity, {c}, 2023: {raw} t; 2025 = average of the last 3 data years moved at -4.0%/yr"
    q = {"Spain": 480403.118, "Italy": 500000.0, "Rest of World": 1000000.0}
    raw = {"Spain": "493,000", "Italy": "510,000", "Rest of World": "3,200,000"}
    state = {
        "market_name": "Olive Oil", "base_year": 2025,
        "drivers": {bucket: {"driver_name": "Apparent consumption (FAOSTAT)", "unit": "tonnes",
                             "final_demand_unit": "tonnes", "library_status": "official_data"}},
        "quantities": {bucket: {c: {"value": v, "unit": "tonnes", "provenance": "derived"} for c, v in q.items()}},
        "driver_facts": {bucket: {c: {"driver_raw": {
            "source": "FAOSTAT Food Balances", "source_url": "https://www.fao.org/faostat/en/#data/FBS",
            "citation": fao.format(c="World minus listed countries" if c == "Rest of World" else c, raw=raw[c]),
            "evidence_type": "derived", "year": 2023}} for c in q}},
        "prices": {bucket: {"asp_usd_per_unit": 3500.0, "evidence_type": "assumption", "rationale": "IOC 2022"}},
        "triangulation": [{"value": 19420.0, "unit": "USD Mn", "year": 2025}],
    }
    state["usage_values"] = {bucket: {c: v * 3500 / 1e6 for c, v in q.items()}}
    state["country_totals"] = dict(state["usage_values"][bucket])
    state["total_market_value"] = sum(state["country_totals"].values())
    if prices:
        state = price_curve.apply_country_prices(state, prices)
    from me_engine.demandCountry_approach2A.graph import add_region_rollup
    return add_region_rollup(state)


def _price_table():
    return {"basis": "manufacturer", "basis_note": "customs unit value", "hs": {"codes": ["1509", "1510"]},
            "curve": {"a": 1.0, "b": 0.5, "n": 6, "rmse_log": 0.1}, "iso3": {"Spain": "ESP", "Italy": "ITA"},
            "population": {"ESP": {"value": 48e6}, "ITA": {"value": 59e6}},
            "prices": {
                "Spain": {"usd_per_t": 4200.0, "label": "verified_observed", "method": "customs_export", "year": 2024,
                          "source_url": "https://comtrade", "publisher": "UN Comtrade", "quote": "ESP export"},
                "Italy": {"usd_per_t": 5100.0, "label": "derived", "method": "curve", "year": 2024,
                          "gdp_pc_ppp": 60000.0, "quote": "curve"},
                "Rest of World": {"usd_per_t": 4650.0, "label": "derived", "method": "median_observed",
                                  "year": None, "quote": "median"},
            }}


def test_country_prices_recompute_values_and_ledger_matches_engine():
    state = _state(_price_table())
    assert math.isclose(state["country_totals"]["Spain"], 480403.118 * 4200 / 1e6, rel_tol=1e-6)
    led, facts = ledger_2a.build_2a_ledger(state, "R001", 1)
    assert led.mismatches == []
    spain_q = next(n for n in led.rows if n.name == "Quantity, Spain")
    assert spain_q.label == "derived" and spain_q.weakest_input_label == "verified_observed"
    raw = led.get(spain_q.input_ids[0])
    assert raw.value == 493000 and raw.year == 2023 and "stock" in raw.note
    glob = facts["global"]
    assert math.isclose(glob.value / 1e6, state["total_market_value"], rel_tol=1e-6)
    assert facts["bases"] == {"Spain": "manufacturer", "Italy": "manufacturer", "Rest of World": "manufacturer"}
    assert math.isclose(facts["per_capita_kg"]["Spain"], 480403.118 * 1000 / 48e6, rel_tol=1e-9)
    # Spain value rests on verified inputs only; Italy's price comes from the curve (derived from verified
    # customs data and World Bank GDP) -> still verified at the weakest input.
    assert checks.verified_value_share(led) > 0.5


def test_engine_price_assumption_drives_the_weakest_label():
    led, _ = ledger_2a.build_2a_ledger(_state(), "R001", 1)
    assert all(n.weakest_input_label == "assumption" for n in led.find(kind="VAL", name_prefix="Value, "))
    assert checks.verified_value_share(led) == 0.0


def test_evaluate_flags_triangulation_and_coverage_softly():
    cfg = design_config.load()
    state = _state()
    led, facts = ledger_2a.build_2a_ledger(state, "R001", 1)
    out = checks.evaluate(cfg, state, led, facts, design_config.per_capita_band(cfg, "Olive Oil"))
    assert out["hard"] == []
    assert {f["check"] for f in out["soft"]} >= {"triangulation", "evidence_coverage"}


# --------------------------------------------------------------------------- #
# hard checks
# --------------------------------------------------------------------------- #
def test_segment_tables_and_basis_mixing_are_hard():
    report = {"segmentation": [{"dimension": "Grade", "global_total": 100.0,
                                "shares": {"A": {"x": 0.7, "y": 0.2}},
                                "country_values": {"A": {"x": 70.0, "y": 20.0}}}]}
    flags = checks.segment_tables(report, {"A": 100.0})
    assert {f["check"] for f in flags} == {"mix_shares_sum", "segment_readd"}
    assert checks.has_hard(checks.basis_mixing({"A": "manufacturer", "B": "consumer"}))
    assert checks.basis_mixing({"A": "manufacturer", "B": "manufacturer"}) == []


def test_soft_checks():
    assert checks.dominance({"Foodservice": 5.95, "Other": 3.35})[0]["check"] == "use_case_dominance"
    assert checks.gap_2a_2b(9.3, 15.7)[0]["check"] == "gap_2a_2b"        # 0.592 < 0.667
    assert checks.gap_2a_2b(12.0, 15.7) == []
    assert checks.price_outliers({"a": 4000, "b": 4100, "c": 4200, "d": 4300, "e": 40000})[0]["scope"] == "E"
    assert checks.magnitude({"Greece": 12.0, "X": 45.0}, {"unit": "kg", "floor": 0, "ceiling": 30})[0]["scope"] == "X"


# --------------------------------------------------------------------------- #
# plausibility
# --------------------------------------------------------------------------- #
def test_plausibility_excludes_published_and_renormalises():
    cfg = design_config.load()
    p = plausibility.score(cfg, 8.0, {}, None, 1.0, [], None)
    # reviewer 8 (0.4), coverage 10 (0.2), soft 10 (0.1), mix 10 (0.1); physical dropped -> /0.8
    assert p["dropped"] == ["physical"] and math.isclose(p["score"], (3.2 + 2 + 1 + 1) / 0.8, rel_tol=1e-9)
    p2 = plausibility.score(cfg, 8.0, {"A": 10, "B": 50}, {"floor": 0, "ceiling": 30}, 0.0,
                            [{"check": "use_case_dominance"}], 3.0)
    assert p2["components"]["physical"] == 5.0 and p2["components"]["mix"] == 0.0
    assert plausibility.accepted(cfg, p, []) and not plausibility.accepted(cfg, p, [{"severity": "hard"}])


def test_best_pass_prefers_no_hard_flags_then_score_then_coverage():
    def rec(n, s, cov, hard=()):
        return {"pass": n, "hard_flags": list(hard), "plausibility": {"score": s, "components": {"coverage": cov}}}
    assert plausibility.best_pass([rec(1, 9, 1, ["x"]), rec(2, 6, 1)])["pass"] == 2
    assert plausibility.best_pass([rec(1, 7, 2), rec(2, 7, 5)])["pass"] == 2


# --------------------------------------------------------------------------- #
# 2C core -- golden numbers from documentation.md Part B.5
# --------------------------------------------------------------------------- #
def test_2c_white_vinegar_india_recipe_build():
    cfg = design_config.load()
    led = L.Ledger("R000", "2C")
    anchor = led.fact("White vinegar use, India", 291000, "t", "India", "QTY", "assumption",
                      note="top-down anchor; source and label not stated in the notes (documentation.md D.5)")
    ketchup = strategy2c.Cell("Ketchup", "India", [
        strategy2c.Factor("Ketchup production", 1_200_000, "t", "assumption", "source not stated (D.7)"),
        strategy2c.Factor("Vinegar share of ketchup recipe", 0.08, "share", "assumption", "recipe"),
        strategy2c.Factor("Share made with white vinegar", 0.70, "share", "assumption", "meaning not stated (D.6)")])
    cakes = strategy2c.Cell("Cakes", "India", [
        strategy2c.Factor("Cake production", 378_000, "t", "assumption", "source not stated (D.7)"),
        strategy2c.Factor("Eggless share of cakes", 0.395, "share", "assumption", "source not stated (D.7)"),
        strategy2c.Factor("Vinegar dose in eggless cakes", 0.02, "t/t", "assumption", "recipe-based dose")],
        price=strategy2c.Factor("Vinegar price", 125_000_000 / 2986.2, "INR/t", "assumption",
                                "back-calculated from ₹12.5 crore (documentation.md B.5 #39)"))
    tiny = strategy2c.Cell("Pickles (tiny)", "India", [], expected_share=0.001,
                           estimate=strategy2c.Factor("estimate", 250.0, "t", "estimated_from_facts", "peer ratio"))
    out = strategy2c.build(led, anchor, [ketchup, cakes, tiny], cfg)
    assert math.isclose(out["quantities"]["Ketchup"].value, 67_200, rel_tol=1e-9)
    assert math.isclose(out["quantities"]["Cakes"].value, 2_986.2, rel_tol=1e-9)
    assert math.isclose(out["values"]["Cakes"].value, 125_000_000, rel_tol=1e-9)
    assert out["estimated"] == ["Pickles (tiny)"] and out["quantities"]["Pickles (tiny)"].label == "estimated_from_facts"
    assert math.isclose(out["unattributed"].value, 291_000 - 67_200 - 2_986.2 - 250, rel_tol=1e-9)
    assert [f["check"] for f in out["flags"]] == ["recipe_coverage"]
    assert out["explained_share"].value < 0.60


def test_2c_type_mix_must_sum_to_one():
    flags = strategy2c.mix_checks({("Spain", "Home cooking"): {"EVOO": 0.6, "Refined": 0.3}})
    assert flags and flags[0]["severity"] == "hard"


# --------------------------------------------------------------------------- #
# price curve + Publisher
# --------------------------------------------------------------------------- #
def test_price_curve_fit_and_outliers():
    keep, drop = price_curve.drop_outliers([4000, 4100, 4200, 4300, 4400, 60000])
    assert drop == [5]
    pts = [(g, 100 * g ** 0.3) for g in (10000, 20000, 40000, 60000, 80000)]
    c = price_curve.fit_curve(pts)
    assert math.isclose(c["b"], 0.3, abs_tol=1e-9) and math.isclose(math.exp(c["a"]), 100, rel_tol=1e-6)
    assert price_curve.tonnes_factor("kg") == 1e-3 and price_curve.tonnes_factor("units") is None


def test_design_blend_states_k_and_keeps_raw():
    out = design_blend(11917.0, [19420.0, 15700.0, 14000.0], 0.5)
    assert out["published_median_usd_mn"] == 15700.0
    assert math.isclose(out["value_usd_mn"], 0.5 * 11917 + 0.5 * 15700)
    assert math.isclose(out["k"], out["value_usd_mn"] / 11917.0) and out["bottom_up_usd_mn"] == 11917.0
    assert design_blend(100.0, [])["k"] == 1.0


# --------------------------------------------------------------------------- #
# progress chart
# --------------------------------------------------------------------------- #
def test_progress_chart_classes_and_counters(tmp_path):
    p = Progress(tmp_path / "progress.mermaid")
    p.set("T2", "done", "13/13 countries")
    p.set("CHECKS", "failed")
    text = (tmp_path / "progress.mermaid").read_text(encoding="utf-8")
    assert 'T2["Tier 2: FAOSTAT<br/><b>13/13 countries</b>"]' in text
    assert "class T2,S_DONE done" in text and "CHECKS" in [l for l in text.splitlines() if l.endswith(" failed")][0]
    assert "class UN" not in text  # label words are never treated as nodes


def test_hs_subheadings_collapse_to_the_heading():
    assert price_curve.collapse_headings(["150910", "150990", "151000"]) == ["1509", "1510"]
    assert price_curve.collapse_headings(["847130"]) == ["847130"]


def test_reviewer_floor_blocks_acceptance_even_with_high_score():
    cfg = design_config.load()
    p = plausibility.score(cfg, 2.8, {"A": 5.0}, {"floor": 0, "ceiling": 30}, 1.0, [{"check": "x"}], None)
    assert p["score"] >= 7.0 and not plausibility.accepted(cfg, p, [])


def test_price_year_mismatch_is_soft():
    flags = checks.price_year({"country_prices": {"base_year": 2025, "off_year_prices": {"Syria": 2023}}})
    assert flags[0]["check"] == "price_year_mismatch" and flags[0]["severity"] == "soft"

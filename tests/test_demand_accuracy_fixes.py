"""2A/2B accuracy fixes: period/year guard, annual-flow drivers, use cases
anchored to the 2A country total, FAOSTAT official data."""
from me_engine.demand_topdown import official_data as od
from me_engine.demandUseCaseCountry_approach2B import anchor, period_guard
from me_engine.demandUseCaseCountry_approach2B.agents import enforce_annual_flow


# --- period / year guard ------------------------------------------------------

def test_2026_quarterly_figure_is_rejected():
    fact = {"value": 66e6, "year": 2026, "evidence_type": "verified_observed",
            "citation": "China's smartphone shipments came in at roughly 66 million units in the second quarter of 2026"}
    out, note = period_guard.check_driver_fact(fact, 2025, "smartphones")
    assert out is None and "2026" in note


def test_quarterly_figure_is_annualized_and_derived():
    fact = {"value": 70e6, "year": 2025, "evidence_type": "verified_observed",
            "citation": "shipments reached 70 million units in Q3 2025"}
    out, note = period_guard.check_driver_fact(fact, 2025, "smartphones")
    assert out["value"] == 280e6 and out["evidence_type"] == "derived" and "x4" in note


def test_quarterly_tracker_name_is_not_a_quarter():
    fact = {"value": 126e6, "year": 2024, "evidence_type": "source_found_unverified",
            "citation": "IDC Worldwide Quarterly Mobile Phone Tracker, 2024"}
    out, note = period_guard.check_driver_fact(fact, 2025, "smartphones (units shipped)")
    assert out is fact and note is None


def test_named_quarter_still_annualized():
    for text in ["shipments were 5.8 million in the third quarter", "Q2 2025 shipments of 30 million"]:
        out, _ = period_guard.check_driver_fact({"value": 1.0, "year": 2025, "citation": text}, 2025, "units")
        assert out["value"] == 4.0


def test_n_months_figure_annualized():
    fact = {"value": 121e6, "year": 2025, "citation": "Over the past five months, the country's smartphone shipments reached 121 million"}
    out, _ = period_guard.check_driver_fact(fact, 2025, "smartphones")
    assert abs(out["value"] - 121e6 * 12 / 5) < 1


def test_implausible_anchor_flags_without_scaling():
    quantities = {"A": {"UK": _q(14e6)}}
    ref = {"UK": {"quantity": 14.0, "unit": "smartphones", "provenance": "source_found_unverified"}}
    flags = anchor.apply_country_anchor(quantities, [{"name": "A"}], ref, [])
    assert flags[0]["code"] == "country_total_mismatch" and quantities["A"]["UK"]["value"] == 14e6


def test_full_year_figure_untouched():
    fact = {"value": 285e6, "year": 2025, "evidence_type": "verified_observed",
            "citation": "full-year 2025 shipments were 285 million units, Q4 was strong"}
    out, note = period_guard.check_driver_fact(fact, 2025, "smartphones")
    assert out is fact and note is None


def test_rate_unit_is_not_annualized():
    fact = {"value": 3.0, "year": 2024, "citation": "about 3 litres per month"}
    out, _ = period_guard.check_driver_fact(fact, 2025, "litres per month")
    assert out["value"] == 3.0


def test_year_from_citation_when_field_missing():
    fact = {"value": 1.0, "citation": "sales in 2027 are expected to reach 1 million"}
    out, _ = period_guard.check_driver_fact(fact, 2025, "units")
    assert out is None


# --- annual flow ---------------------------------------------------------------

def test_stock_driver_gets_yearly_purchase_rate():
    log = []
    d = enforce_annual_flow({"driver_name": "Smartphone users relying on navigation apps", "unit": "users",
                             "final_demand_unit": "users", "consumption_unit": "",
                             "conversion_formula": "driver_value"}, "smartphones", log, "Navigation")
    assert d["consumption_unit"] == "smartphones per user per year"
    assert d["final_demand_unit"] == "smartphones" and d["conversion_formula"] == "driver_value * intensity_value"
    assert log


def test_flow_driver_and_converted_driver_untouched():
    flow = {"driver_name": "Annual smartphone shipments", "unit": "units shipped", "consumption_unit": ""}
    assert enforce_annual_flow(flow, "smartphones", [], "x") is flow
    conv = {"driver_name": "Households", "unit": "households", "consumption_unit": "kg per household per year"}
    assert enforce_annual_flow(conv, "tonnes", [], "x") is conv


# --- 2B use cases vs 2A country total ------------------------------------------

def _q(v, unit="smartphones", prov="assumption"):
    return {"value": v, "unit": unit, "provenance": prov, "note": ""}


def test_use_cases_scaled_down_to_country_total():
    quantities = {"Consumer": {"India": _q(300.0)}, "Enterprise": {"India": _q(100.0)}}
    buckets = [{"name": "Consumer"}, {"name": "Enterprise"}]
    ref = {"India": {"quantity": 160.0, "unit": "smartphones (units shipped)", "provenance": "verified_observed"}}
    flags = anchor.apply_country_anchor(quantities, buckets, ref, [], target="total")
    assert abs(quantities["Consumer"]["India"]["value"] - 120.0) < 1e-6
    assert abs(quantities["Enterprise"]["India"]["value"] - 40.0) < 1e-6
    assert flags[0]["code"] == "use_cases_scaled_down_to_country_total"


def test_assumed_anchor_and_close_totals_change_nothing():
    quantities = {"A": {"X": _q(300.0)}, "B": {"Y": _q(100.0)}}
    buckets = [{"name": "A"}, {"name": "B"}]
    ref = {"X": {"quantity": 10.0, "unit": "smartphones", "provenance": "assumption"},
           "Y": {"quantity": 90.0, "unit": "smartphones", "provenance": "derived"}}
    assert anchor.apply_country_anchor(quantities, buckets, ref, []) == []
    assert quantities["A"]["X"]["value"] == 300.0 and quantities["B"]["Y"]["value"] == 100.0


def test_unit_conversion_and_under_coverage_flag():
    quantities = {"Household": {"Spain": _q(100.0, "kg")}}
    ref = {"Spain": {"quantity": 1.0, "unit": "tonnes", "provenance": "derived"}}
    flags = anchor.apply_country_anchor(quantities, [{"name": "Household"}], ref, [])
    assert flags[0]["code"] == "use_cases_below_country_total"
    assert quantities["Household"]["Spain"]["value"] == 100.0


def test_official_anchor_scales_use_cases_up():
    quantities = {"Household": {"Spain": _q(100.0, "tonnes")}, "Foodservice": {"Spain": _q(100.0, "tonnes")}}
    ref = {"Spain": {"quantity": 480.0, "unit": "tonnes", "provenance": "derived", "official": True,
                     "source": "FAOSTAT Food Balances"}}
    flags = anchor.apply_country_anchor(quantities, [{"name": "Household"}, {"name": "Foodservice"}], ref, [],
                                        target="total")
    assert flags[0]["code"] == "use_cases_scaled_up_to_country_total"
    assert quantities["Household"]["Spain"]["value"] == 240.0


def test_anchor_ceiling_target_scales_to_the_band_edge(monkeypatch):
    # design default (system.md §7.3): 400 vs 160 -> r = 2.5 > 1.5 -> f = 1.5 / 2.5 = 0.6 -> sum = 240 = 1.5 x 160
    monkeypatch.delenv("DEAGENT_ANCHOR_TARGET", raising=False)
    quantities = {"Consumer": {"India": _q(300.0)}, "Enterprise": {"India": _q(100.0)}}
    ref = {"India": {"quantity": 160.0, "unit": "smartphones", "provenance": "verified_observed"}}
    flags = anchor.apply_country_anchor(quantities, [{"name": "Consumer"}, {"name": "Enterprise"}], ref, [])
    assert abs(quantities["Consumer"]["India"]["value"] - 180.0) < 1e-6
    assert abs(quantities["Enterprise"]["India"]["value"] - 60.0) < 1e-6
    assert flags[0]["code"] == "use_cases_scaled_down_to_band_edge" and abs(flags[0]["factor"] - 0.6) < 1e-9
    assert quantities["Consumer"]["India"]["anchor_target"] == "ceiling"

    # official, below band: 200 vs 480 -> r = 0.4167 -> f = 0.6667 / 0.4167 = 1.6 -> sum = 320 = 480 / 1.5
    quantities = {"Household": {"Spain": _q(100.0, "tonnes")}, "Foodservice": {"Spain": _q(100.0, "tonnes")}}
    ref = {"Spain": {"quantity": 480.0, "unit": "tonnes", "provenance": "derived", "official": True}}
    flags = anchor.apply_country_anchor(quantities, [{"name": "Household"}, {"name": "Foodservice"}], ref, [])
    assert abs(quantities["Household"]["Spain"]["value"] - 160.0) < 1e-6
    assert flags[0]["code"] == "use_cases_scaled_up_to_band_edge"


def test_load_anchor_from_2a_output(tmp_path):
    import json
    d = tmp_path / "output" / "demand_country_2a" / "olive-oil"
    d.mkdir(parents=True)
    (d / "02_sizing.json").write_text(json.dumps({"build_up_rows": [
        {"country": "Spain", "quantity": 480000.0, "final_demand_unit": "tonnes", "provenance": "derived",
         "driver_source": "FAOSTAT Food Balances"},
    ]}), encoding="utf-8")
    ref = anchor.load_2a_country_anchor("Olive Oil", tmp_path)
    assert ref["Spain"]["quantity"] == 480000.0 and ref["Spain"]["official"] is True
    assert anchor.load_2a_country_anchor("Nothing Here", tmp_path) is None


# --- FAOSTAT official data -------------------------------------------------------

_CACHE = {
    "fbs": {
        "Olive Oil": {
            "Spain": {"2018": 500.0, "2019": 500.0, "2020": 500.0, "2021": 500.0, "2022": 500.0, "2023": 500.0},
            "China, mainland": {"2021": 30.0, "2022": 30.0, "2023": 30.0},
            "World": {"2021": 3000.0, "2022": 3000.0, "2023": 3000.0},
        },
        "Cocoa Beans and products": {"Spain": {"2023": 1.0}},
        "Grand Total": {"Spain": {"2023": 1.0}},
    },
    "rfb": {"Urea": {"India": {"2024": 100.0}}},
}


def test_item_match_is_strict_without_llm():
    assert od.match_item("Olive Oil", _CACHE) == ("fbs", "Olive Oil")
    assert od.match_item("Urea Fertilizer", _CACHE) == ("rfb", "Urea")
    assert od.match_item("Chocolate", _CACHE) is None
    assert od.match_item("Smartphones", _CACHE) is None


def test_llm_can_reject_a_shortlisted_item():
    class NoLLM:
        def complete_json(self, system, user):
            return {"match": 0}
    assert od.match_item("Olive Oil", _CACHE, NoLLM()) is None


def test_country_facts_project_alias_and_rest_of_world():
    facts = od.country_facts("fbs", "Olive Oil", ["Spain", "China", "Rest of World"], 2025, _CACHE)
    assert facts["Spain"]["value"] == 500.0 and facts["Spain"]["evidence_type"] == "derived"
    assert "China, mainland" in facts["China"]["citation"]
    assert facts["Rest of World"]["value"] == 3000.0 - 500.0 - 30.0


def test_base_year_value_is_observed():
    facts = od.country_facts("rfb", "Urea", ["India"], 2024, _CACHE)
    assert facts["India"]["value"] == 100.0 and facts["India"]["evidence_type"] == "verified_observed"


def test_project_uses_three_year_average_and_capped_trend():
    series = {"2018": 100.0, "2019": 100.0, "2020": 100.0, "2021": 200.0, "2022": 200.0, "2023": 200.0}
    value, last, trend = od._project(series, 2025)
    assert last == 2023 and trend == od.MAX_TREND
    assert abs(value - 200.0 * 1.05 ** 3) < 1e-6


# --- Rest of World from the worldwide total ----------------------------------

def test_rest_of_world_is_world_minus_listed():
    from me_engine.demandUseCaseCountry_approach2B.agents import rest_of_world_fact
    others = {"China": {"value": 285e6}, "India": {"value": 152e6}}
    world = {"value": 1240e6, "evidence_type": "verified_observed", "source": "IDC", "citation": "1.24 billion units in 2025"}
    f = rest_of_world_fact(world, others)
    assert f["value"] == 1240e6 - 437e6 and f["evidence_type"] == "derived"


def test_rest_of_world_rejects_figure_that_cannot_be_world():
    from me_engine.demandUseCaseCountry_approach2B.agents import rest_of_world_fact
    others = {"China": {"value": 285e6}}
    assert rest_of_world_fact({"value": 100e6}, others) is None     # smaller than listed
    assert rest_of_world_fact({"value": 9e12}, others) is None      # absurdly larger

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from me_engine.generation import usecase_builder as ub  # noqa: E402


def _durable(new=100.0, installed=2000.0, life=10.0, adoption=0.5, per=2.0, basis="assumption"):
    def inp(v, b=basis):
        return {"value": v, "unit": "", "basis": b, "reason": "r"}
    return {"archetype": "durable", "driver": "manufacturing_value_added_usd", "quantity_unit": "units",
            "world": {"new_machines_per_year": inp(new), "installed_machines": inp(installed),
                      "product_life_years": inp(life), "adoption_share": inp(adoption), "units_per_machine": inp(per)},
            "price_usd_per_unit": {"value": 1000.0, "per": "unit", "basis": "assumption", "reason": "r"}}


def test_machine_model_arithmetic():
    s = _durable()
    assert math.isclose(ub.world_units(s), (100 + 2000 / 10) * 0.5 * 2)  # new builds + replacements
    assert "300 units/yr" in ub.computed_text(s, ub.world_units(s)) and "100 for new builds" in ub.computed_text(
        s, ub.world_units(s))
    s["world"]["installed_machines"]["reason"] = "10,000 plants x 100 = 1,000,000"  # text is never read
    assert math.isclose(ub.world_units(s), 300.0)


def test_consumable_model_and_driver_activity():
    s = {"archetype": "consumable", "world": {"world_activity_per_year": {"value": 1e6}, "adoption_share": {"value": 0.2},
                                              "dose_per_activity_unit": {"value": 0.01}}}
    assert math.isclose(ub.world_units(s), 2000.0)
    s["world"]["world_activity_per_year"] = {"value": "driver"}
    assert ub.activity_from_driver(s) and math.isclose(ub.world_units(s, driver_total=5e5), 1000.0)


def test_missing_inputs_and_bad_archetype_rejected():
    s = _durable()
    s["world"].pop("installed_machines")
    with pytest.raises(ValueError, match="installed_machines"):
        ub.world_units(s)
    with pytest.raises(ValueError, match="archetype"):
        ub.world_units({**_durable(), "archetype": "gadget"})


def test_physical_bounds():
    assert ub.bound_violations(_durable()) == []
    v = ub.bound_violations(_durable(life=2, adoption=1.5, per=6, new=0))
    assert any("product_life_years" in x for x in v) and any("adoption_share" in x for x in v)
    assert any("units_per_machine" in x for x in v) and any("new_machines_per_year" in x for x in v)
    assert ub.bound_violations(_durable(life=40, adoption=1.0, per=4)) == []
    assert any("adoption_share" in x for x in ub.bound_violations(_durable(adoption=0.0)))


def test_circular_world_units_flagged():
    s = _durable()
    s["world"]["new_machines_per_year"]["reason"] = "welding market ~$2B divided by average system price $250k"
    assert ub.circular_reason(s)
    assert not ub.circular_reason(_durable())


def test_median_of_each_input_not_of_final_value():
    samples = [{"sample": i + 1, "spec": _durable(*x), "world_units": ub.world_units(_durable(*x))}
               for i, x in enumerate([(100, 2000, 10, 0.5, 2), (200, 4000, 20, 0.6, 2), (1000, 3000, 15, 0.4, 1)])]
    spec, info = ub.combine(samples)
    w = ub.world_inputs(spec)
    assert w == {"new_machines_per_year": 200, "installed_machines": 3000, "product_life_years": 15,
                 "adoption_share": 0.5, "units_per_machine": 2}
    assert math.isclose(ub.world_units(spec), (200 + 200) * 0.5 * 2)
    assert info["spread"]["new_machines_per_year"]["max_over_min"] == 10
    assert info["spread"]["world_units_per_sample"]["values"] == [300, 480, 480]


def test_median_of_two_cites_only_if_both_cite():
    a, b = _durable(new=100, basis="E1"), _durable(new=300, basis="assumption")
    spec, _ = ub.combine([{"sample": 1, "spec": a, "world_units": 1}, {"sample": 2, "spec": b, "world_units": 1}])
    assert spec["world"]["new_machines_per_year"]["value"] == 200 and         spec["world"]["new_machines_per_year"]["basis"] == "assumption"
    spec, _ = ub.combine([{"sample": 1, "spec": a, "world_units": 1},
                          {"sample": 2, "spec": _durable(new=300, basis="E2"), "world_units": 1}])
    assert spec["world"]["new_machines_per_year"]["basis"] == "E1 E2"


def test_samples_counting_another_unit_left_out_of_median():
    other = {**_durable(new=9e9), "quantity_unit": "t"}
    spec, info = ub.combine([{"sample": 1, "spec": _durable(), "world_units": 300},
                             {"sample": 2, "spec": other, "world_units": 1},
                             {"sample": 3, "spec": _durable(new=300), "world_units": 400}])
    assert info["excluded_samples"] == [2] and ub.world_inputs(spec)["new_machines_per_year"] == 200


def test_publisher_blocklist():
    assert ub.blocked("Coherent Market Insights estimates the market at $2B")
    assert ub.blocked("see www.coherentmarketinsights.com/report")
    assert not ub.blocked("CMI Group supplies boilers and steam turbines")  # a machine maker, not the publisher
    assert not ub.blocked("CMI Energy installed base of heat-recovery units")
    assert ub.blocked("According to CMI, demand grows", ("CMI",))  # acronyms can still be passed explicitly
    assert not ub.blocked("ACMI pumps and cmi-like text", ("CMI",))  # acronym: whole word, case-sensitive
    kept, n = ub.filter_snippets(["Pumps: 10,000 shipped", {"text": "x", "domain": "coherentmarketinsights.com"},
                                 {"text": "Turbines installed base", "domain": "iea.org"}])
    assert n == 1 and len(kept) == 2
    figs, n = ub.filter_published([{"publisher": "Coherent Market Insights", "usd_mn": 5},
                                   {"publisher": "Other Research", "usd_mn": 7}])
    assert n == 1 and figs == [{"publisher": "Other Research", "usd_mn": 7}]
    assert ub.blocked("Acme Research says", ("Acme Research",))  # generic: other publishers can be added


def test_mix_shares_are_value_weighted_by_use_case():
    from me_engine.generation import usecase_mix

    class FakeLLM:
        def complete_json(self, system, user):
            if "Welding" in user:
                return {"By Laser Type": {"top": {"Fiber": 1.0}, "sub": {"Fiber": {"CW": 0.9, "Pulsed": 0.1}}}}
            return {"By Laser Type": {"top": {"Fiber": 0.5, "Gas": 0.5}, "sub": {"Fiber": {"CW": 0.0, "Pulsed": 1.0}}}}

    ub_res = {"dimension": "By Application", "country_value": {"DE": 4.0, "IN": 1.0},
              "use_cases": [{"industry": "Ind", "use_case": "Welding", "values_mn": {"DE": 3.0, "IN": 0.0}},
                            {"industry": "Def", "use_case": "C-UAS", "values_mn": {"DE": 1.0, "IN": 1.0}}]}
    nesting = {"By Application": {"__root__": ["Ind", "Def"]},
               "By Laser Type": {"__root__": ["Fiber", "Gas"], "Fiber": ["CW", "Pulsed"]}}
    mix = usecase_mix.build(FakeLLM(), ub_res, nesting, "M", say=lambda *_: None)
    de, in_ = mix["shares"]["By Laser Type"]["DE"], mix["shares"]["By Laser Type"]["IN"]
    assert math.isclose(de["Fiber"], 3.5 / 4.0) and math.isclose(in_["Fiber"], 0.5)
    assert math.isclose(de["Fiber|CW"], 2.7 / 3.5) and math.isclose(in_["Fiber|CW"], 0.0)


# ---- physical units, price conversion, official anchor, ledger ------------------------
import json  # noqa: E402

import pytest  # noqa: E402


def test_price_per_kg_converted_to_tonnes():
    s = {"quantity_unit": "t", "price_usd_per_unit": {"value": 4.0, "per": "kg"}}
    assert math.isclose(ub.price_of(s), 4000.0)


def test_litre_price_to_tonnes_needs_density():
    s = {"quantity_unit": "t", "price_usd_per_unit": {"value": 2.0, "per": "L"}}
    with pytest.raises(ValueError):
        ub.price_of(s)
    s["density_kg_per_l"] = 0.8
    assert math.isclose(ub.price_of(s), 2.0 * 1000 / 0.8)  # 1 t = 1250 L


def test_count_units_and_mismatch():
    assert ub.price_conversion({"quantity_unit": "laser systems",
                                "price_usd_per_unit": {"value": 1, "per": "system"}}) == 1.0
    assert ub.price_conversion({"quantity_unit": "units", "price_usd_per_unit": {"value": 1}}) == 1.0
    with pytest.raises(ValueError):
        ub.price_conversion({"quantity_unit": "t", "price_usd_per_unit": {"value": 1, "per": "bottle"}})


GEOS = ["U.S.", "Germany", "India"]
SPEC = {"geographies": GEOS, "regions": {"North America": ["U.S."], "Europe": ["Germany"], "Asia Pacific": ["India"]}}
WB = {"USA": {"name": "United States", "region": "North America", "population": 3.3e8, "gdp_usd": 2.7e13,
              "manufacturing_value_added_usd": 2.5e12},
      "DEU": {"name": "Germany", "region": "Europe & Central Asia", "population": 8.4e7, "gdp_usd": 4.4e12},
      "IND": {"name": "India", "region": "South Asia", "population": 1.4e9, "gdp_usd": 3.5e12,
              "manufacturing_value_added_usd": 4.5e11}}
FAO = {"fbs": {"Olive Oil": {"United States of America": {"2021": 380e3, "2022": 400e3, "2023": 410e3},
                             "Germany": {"2021": 80e3, "2022": 82e3, "2023": 84e3},
                             "India": {"2021": 10e3, "2022": 11e3, "2023": 12e3}}}}


def _inp(v, basis="assumption", unit=""):
    return {"value": v, "unit": unit, "basis": basis, "reason": "r"}


class DesignLLM:
    def complete_json(self, system, user):
        if "Use case: Cooking" in user:
            return {"archetype": "consumable", "driver": "population", "buyers": "households", "quantity_unit": "t",
                    "units_label": "tonne", "activity_unit": "person",
                    "world": {"world_activity_per_year": {"value": "driver", "basis": "assumption", "reason": "people"},
                              "adoption_share": _inp(0.3, "E1", "share"),
                              "dose_per_activity_unit": {"value": 0.002, "unit": "t/person/yr", "basis": "assumption",
                                                         "reason": "two litres a year"}},
                    "price_usd_per_unit": {"value": 6.0, "per": "kg", "basis": "assumption", "reason": "ex-factory"}}
        return {"archetype": "consumable", "driver": "manufacturing_value_added_usd", "buyers": "cosmetics makers",
                "quantity_unit": "t", "activity_unit": "t of cosmetics",
                "world": {"world_activity_per_year": _inp(3e6, unit="t"), "adoption_share": _inp(0.5),
                          "dose_per_activity_unit": _inp(0.04, unit="t/t")},
                "price_usd_per_unit": {"value": 9000.0, "per": "t", "basis": "assumption", "reason": "bulk"}}


POP = 3.3e8 + 8.4e7 + 1.4e9


def _build(tmp_path):
    (tmp_path / ub.WB_CACHE).write_text(json.dumps(WB), encoding="utf-8")
    nesting = {"By Application": {"__root__": ["Food", "Personal Care"], "Food": ["Cooking"], "Personal Care": []}}
    return ub.build(DesignLLM(), SPEC, nesting, "Global Olive Oil Market", tmp_path,
                    lambda qs: ["30% of households cook with olive oil"], {}, say=lambda *_: None, fao_cache=FAO)


def test_build_sums_use_cases_and_records_units(tmp_path):
    res = _build(tmp_path)
    assert [r["use_case"] for r in res["use_cases"]] == ["Cooking", "Personal Care"]
    cook = res["use_cases"][0]
    assert math.isclose(cook["price_per_quantity_unit"], 6000.0)  # US$/kg -> US$/t by code
    assert math.isclose(sum(cook["units"].values()), POP * 0.3 * 0.002, rel_tol=1e-9)
    assert math.isclose(sum(res["use_cases"][1]["units"].values()), 3e6 * 0.5 * 0.04, rel_tol=1e-9)
    for g in GEOS:
        assert math.isclose(res["country_value"][g], sum(r["values_mn"][g] for r in res["use_cases"]))
    assert math.isclose(res["global_value"], sum(res["country_value"].values()))
    # Germany has no manufacturing figure: imputed, and the share is recorded
    assert res["use_cases"][1]["driver_meta"]["imputed_share"]["Germany"] == 1.0
    assert len(cook["samples"]) == ub.N_SAMPLES and cook["basis_counts"] == {"cited": 1, "assumption": 2}


def test_official_anchor_sets_level_keeps_split(tmp_path):
    res = _build(tmp_path)
    split_before = {g: res["use_cases"][0]["units"][g] / (res["use_cases"][0]["units"][g] + res["use_cases"][1]["units"][g])
                    for g in GEOS}
    official = ub.official_by_geo("Global Olive Oil Market", SPEC, tmp_path, None, fao_cache=FAO)
    assert official and official["item"] == "Olive Oil"
    res = ub.anchor_to_official(res, official, SPEC)
    for g in GEOS:
        tonnes = sum(r["units"][g] for r in res["use_cases"])
        assert math.isclose(tonnes, official["by_geo"][g], rel_tol=1e-9)
        assert math.isclose(res["use_cases"][0]["units"][g] / tonnes, split_before[g], rel_tol=1e-9)
    assert math.isclose(res["global_value"], sum(res["country_value"].values()))


def test_anchor_skipped_for_count_units(tmp_path):
    res = _build(tmp_path)
    res["use_cases"][1]["spec"]["quantity_unit"] = "bottles"
    out = ub.anchor_to_official(res, {"by_geo": {g: 1.0 for g in GEOS}}, SPEC)
    assert "official_anchor" not in out and any("not in mass/volume" in f for f in out["flags"])


def test_ledger_traces_every_value(tmp_path):
    res = _build(tmp_path)
    res = ub.anchor_to_official(res, ub.official_by_geo("Global Olive Oil Market", SPEC, tmp_path, None,
                                                        fao_cache=FAO), SPEC)
    led = ub.build_ledger(res, SPEC, run_id="t1")
    assert led.mismatches == []
    world = [n for n in led.rows if n.name == "Market value, world"][0]
    assert math.isclose(world.value, res["global_value"] * 1e6, rel_tol=1e-9)
    names = {n.name: n for n in led.rows}
    assert names["Food / Cooking: adoption_share"].label == "source_found_unverified"  # cites E1
    dose = names["Food / Cooking: dose_per_activity_unit"]
    assert dose.label == "assumption" and dose.note
    assert names["Food / Cooking: world quantity per year"].input_ids[0] == \
        names["Food / Cooking: driver population, world total"].number_id  # activity = the driver's world total
    assert names["Food / Cooking: price per t"].formula.endswith("× 1000")
    assert names["Personal Care / Personal Care: driver manufacturing_value_added_usd, Germany"].label == \
        "estimated_from_facts"
    assert world.weakest_input_label == "assumption"
    paths = led.write(tmp_path / "ledger")
    assert paths["csv"].exists()


def test_price_quoted_in_millions_of_dollars():
    s = {"quantity_unit": "units", "price_usd_per_unit": {"value": 2.5, "per": "million USD"}}
    assert math.isclose(ub.price_of(s), 2.5e6)
    s = {"quantity_unit": "t", "price_usd_per_unit": {"value": 1.2, "per": "thousand US$ per tonne"}}
    assert math.isclose(ub.price_of(s), 1200.0)
    s = {"quantity_unit": "t", "price_usd_per_unit": {"value": 3.0, "per": "USD per kg"}}
    assert math.isclose(ub.price_of(s), 3000.0)


# ---- durable goods: machines, samples, bounds, blocklist, published flags --------------
class MachineLLM:
    """A fake agent: sample-dependent answers, out-of-bounds first tries, and a log of prompts."""

    def __init__(self):
        self.users = []

    def complete_json(self, system, user):
        self.users.append(user)
        k = int(user.split("independent estimate #")[1].split(" ")[0])
        if "Use case: Pumps" in user:
            new, inst, life, a, per = [(100, 2000, 10, 0.5, 2), (200, 4000, 20, 0.6, 2), (1000, 3000, 15, 0.4, 1)][k - 1]
        elif "Use case: Fans" in user:  # life 2 years at first; corrected once code's arithmetic is shown
            fixed = "your inputs give" in user and "product_life_years = 2 is outside 5-40" in user
            new, inst, life, a, per = 50, 1000, 10 if fixed else 2, 1.0, 1
        else:  # Mixers: stays out of bounds whatever the feedback
            new, inst, life, a, per = 10, 100, 10, 0.5, 9
        return {"archetype": "durable", "driver": "manufacturing_value_added_usd", "machine": "one machine",
                "quantity_unit": "units",
                "world": {"new_machines_per_year": _inp(new, "E1"), "installed_machines": _inp(inst),
                          "product_life_years": _inp(life), "adoption_share": _inp(a), "units_per_machine": _inp(per)},
                "price_usd_per_unit": {"value": 10_000.0, "per": "unit", "basis": "assumption", "reason": "r"}}


SNIPPETS = ["Pumps: 50,000 units shipped per year", {"text": "Market to reach $5B", "title": "Coherent Market Insights",
                                                     "url": "https://www.coherentmarketinsights.com/x"},
            {"text": "CMI Group: boiler fleet doubles", "url": "https://example.org"},
            {"text": "Installed base of 1 million machines", "domain": "example.org"}]


def _build_machines(tmp_path, llm, published=None, **kw):
    (tmp_path / ub.WB_CACHE).write_text(json.dumps(WB), encoding="utf-8")
    nesting = {"By Application": {"__root__": ["Industry"], "Industry": ["Pumps", "Fans", "Mixers"]}}
    return ub.build(llm, SPEC, nesting, "Global Widget Market", tmp_path, lambda qs: list(SNIPPETS),
                    published or {}, say=lambda *_: None, fao_cache={}, **kw)


def test_durable_build_median_spread_and_driver_only_spreading(tmp_path):
    res = _build_machines(tmp_path, MachineLLM())
    pumps = {r["use_case"]: r for r in res["use_cases"]}["Pumps"]
    assert pumps["archetype"] == "durable" and len(pumps["samples"]) == 3
    assert math.isclose(pumps["world_units"], (200 + 3000 / 15) * 0.5 * 2)  # medians of each input
    assert pumps["spread"]["world_units_per_sample"]["values"] == [300, 480, 480]
    mva = {"U.S.": 2.5e12, "India": 4.5e11}
    mva["Germany"] = pumps["driver_values"]["Germany"]  # imputed from GDP
    for g in GEOS:  # units(c) = world units x driver(c) / sum(driver)
        assert math.isclose(pumps["units"][g], pumps["world_units"] * mva[g] / sum(mva.values()), rel_tol=1e-9)
    assert math.isclose(sum(pumps["units"].values()), pumps["world_units"], rel_tol=1e-12)
    assert pumps["basis_counts"] == {"cited": 1, "assumption": 5}


def test_bounds_feedback_retry_and_flag(tmp_path):
    res = _build_machines(tmp_path, MachineLLM())
    by = {r["use_case"]: r for r in res["use_cases"]}
    fans = by["Fans"]
    assert fans["bound_violations"] == [] and all(s["attempts"] == 2 for s in fans["samples"])
    assert "your inputs give (50 new machines/yr + 1,000 installed / 2 yr)" in fans["samples"][0]["feedback"][0]
    mixers = by["Mixers"]  # still out of bounds after every retry: kept, flagged, recorded
    assert all(s["kept_out_of_bounds"] and s["attempts"] == ub.ATTEMPTS_PER_SAMPLE for s in mixers["samples"])
    assert any("units_per_machine = 9" in v for v in mixers["bound_violations"])
    assert any("out of bounds" in f for f in mixers["flags"]) and mixers["world_mn"] > 0


def test_ledger_traces_durable_build(tmp_path):
    res = _build_machines(tmp_path, MachineLLM())
    led = ub.build_ledger(res, SPEC, run_id="t2")
    assert led.mismatches == []
    names = {n.name: n for n in led.rows}
    world = names["Industry / Pumps: world quantity per year"]
    assert math.isclose(world.value, 400.0) and world.formula.startswith("(") and len(world.input_ids) == 5
    assert names["Industry / Pumps: new_machines_per_year"].label == "source_found_unverified"
    assert names["Industry / Pumps: installed_machines"].label == "assumption"
    assert "median of 3 samples" in names["Industry / Pumps: installed_machines"].note
    share = names["Industry / Pumps: driver share, India"]
    q = names["Industry / Pumps: quantity, India"]
    assert q.input_ids == [world.number_id, share.number_id]


def test_blocklisted_publisher_never_reaches_the_agent(tmp_path):
    llm = MachineLLM()
    pub = {"Industry": [{"publisher": "Coherent Market Insights", "usd_mn": 900.0},
                        {"publisher": "Other Research", "usd_mn": 30.0}]}
    res = _build_machines(tmp_path, llm, pub)
    r = res["use_cases"][0]
    assert r["blocked"] == {"snippets": 1, "published_figures": 1}
    assert [e["text"] for e in r["evidence"]] == ["Pumps: 50,000 units shipped per year",
                                                  "CMI Group: boiler fleet doubles",  # kept by default
                                                  "Installed base of 1 million machines"]
    assert r["published_context"] == [{"publisher": "Other Research", "usd_mn": 30.0}]
    assert not any("Coherent" in u or "coherentmarketinsights" in u for u in llm.users)


def test_published_gap_is_flag_only_by_default(tmp_path):
    pub = {"Industry": [{"publisher": "Other Research", "usd_mn": 1e6}]}  # far above the bottom-up total
    llm = MachineLLM()
    res = _build_machines(tmp_path, llm, pub)
    base = _build_machines(tmp_path, MachineLLM())
    assert len(llm.users) == sum(r["attempts"] for r in res["use_cases"])  # no redesign calls
    assert math.isclose(res["global_value"], base["global_value"])  # number unchanged
    assert res["flags"] and "flagged only" in res["flags"][0]
    assert all(r["published_gap"]["published_min_mn"] == 1e6 for r in res["use_cases"])
    assert res["settings"]["reexamine_published"] is False


def test_published_reexamination_is_opt_in(tmp_path):
    pub = {"Industry": [{"publisher": "Other Research", "usd_mn": 1e6}]}
    llm = MachineLLM()
    res = _build_machines(tmp_path, llm, pub, reexamine_published=True)
    assert len(llm.users) > sum(r["attempts"] for r in res["use_cases"])  # redesign calls were made
    assert "re-examined" in res["flags"][0]

"""PLAN-04: the market definition states what is counted and at which
value-chain level; segments obey it (excluded ones dropped, overlaps and
false splits re-asked once, then merged / switched off); the MEA segment mix
is tied to the global mix by one re-ask, never by blending; and no sentence or
heat-map row may describe another value-chain level. LLM calls are mocked."""
from __future__ import annotations

import copy
import json

from AiAgentsFleet import market_definition
from AiAgentsFleet.cmi_report import model, segments_check
from AiAgentsFleet.cmi_report.groups import DEFAULT_COUNTRIES, GROUPS, mea_units
from AiAgentsFleet.cmi_report.guard import ClaimGuard, NumberGuard, clean_text, scope_violation

GEO = {r: list(c) for r, c in DEFAULT_COUNTRIES.items()}
UNITS = mea_units(GEO)

SHRIMP = {"market_name": "Super-Intensive Shrimp Farming Market",
          "measures": "Farm-gate value of shrimp produced in super-intensive systems.",
          "value_chain_level": "farm-gate output",
          "excluded_items": ["Traditional/extensive shrimp farming", "Shrimp hatcheries", "Shrimp feed",
                             "Aeration and monitoring equipment sales"]}
VITC = {"market_name": "Global Vitamin C Market",
        "measures": "Ex-factory sales of vitamin C as a bulk ingredient.", "value_chain_level": "manufacturer",
        "excluded_items": ["Animal feed and pet food", "Finished consumer products"]}


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def cell(share, cagr=5.0):
    return {"share_pct_of_region": share, "cagr_2026_2033_pct": cagr}


def split(region, named, rest):
    out = {c: cell(s, 5 + i * 0.3) for i, (c, s) in enumerate(zip(GEO[region], named))}
    out[f"Rest of {region}"] = cell(rest)
    return out


TYPES = [{"name": "Sodium Ascorbate", "share_pct_of_market": 30},
         {"name": "Ascorbic Acid (Synthetic)", "share_pct_of_market": 40},
         {"name": "Ascorbic Acid (Fermentation-Derived)", "share_pct_of_market": 20},
         {"name": "Calcium Ascorbate", "share_pct_of_market": 10}]
TYPE_NAMES = [t["name"] for t in TYPES]


def seg_global(**over):
    sg = {"type_cagr_2026_2033_pct": {n: 5.0 for n in TYPE_NAMES},
          "demand_segments": [{"name": "Dietary Supplements", "share_pct_2026": 40, "cagr_2026_2033_pct": 6},
                              {"name": "Pharmaceuticals", "share_pct_2026": 30, "cagr_2026_2033_pct": 5},
                              {"name": "Food & Beverage Fortification", "share_pct_2026": 20, "cagr_2026_2033_pct": 4},
                              {"name": "Animal Feed & Pet Food", "share_pct_2026": 10, "cagr_2026_2033_pct": 5}],
          "type_applicable": True, "demand_applicable": True,
          "segment_definitions": {
              "type": {"Sodium Ascorbate": "The sodium salt of ascorbic acid, a buffered form.",
                       "Ascorbic Acid (Synthetic)": "Ascorbic acid made by the Reichstein process.",
                       "Ascorbic Acid (Fermentation-Derived)": "Ascorbic acid made by two-step fermentation.",
                       "Calcium Ascorbate": "The calcium salt of ascorbic acid, for sensitive stomachs."},
              "demand": {"Dietary Supplements": "Makers of tablets, capsules and powders.",
                         "Pharmaceuticals": "Drug makers using vitamin C as an active ingredient.",
                         "Food & Beverage Fortification": "Food and drink producers adding it as a fortificant.",
                         "Animal Feed & Pet Food": "Animal feed and pet food producers."}},
          "allocation_rules": [],
          "type_split_check": [{"a": "Ascorbic Acid (Synthetic)", "b": "Ascorbic Acid (Fermentation-Derived)",
                                "buyers_differentiate": False, "basis": "production history"}],
          "type_merges": []}
    sg.update(over)
    return sg


def mix(type_shares, dem_shares):
    return {"type": {n: {"share_pct": s, "cagr_pct": 5} for n, s in type_shares.items()},
            "demand": {n: {"share_pct": s, "cagr_pct": 5} for n, s in dem_shares.items()}}


def seg_mea(type_shares=None, dem_shares=None):
    t = type_shares or {"Sodium Ascorbate": 30, "Ascorbic Acid": 60, "Calcium Ascorbate": 10}
    d = dem_shares or {"Dietary Supplements": 45, "Pharmaceuticals": 33, "Food & Beverage Fortification": 22}
    return {u: mix(t, d) for u in UNITS}


def definition_v4(**over):
    d = {"market_name": "Global Vitamin C Market", "measures": VITC["measures"], "value_chain_level": "manufacturer",
         "included": ["Ascorbic acid", "Ascorbate salts"], "excluded": ["Animal feed and pet food"],
         "unit": "USD million, market value", "check": {"first_problems": [], "reasked": False, "problems": []}}
    d.update(over)
    return d


def fleet(definition=None):
    series = {str(y): 1000 * 1.06 ** (y - 2025) for y in range(2025, 2034)}
    body = {"market_size": {"forecast_series_usd_mn": series},
            "regional": {"regions": {r: {"value_usd_mn": v, "cagr_2025_2030_pct": 6, "countries": []}
                                     for r, v in {"North America": 300, "Europe": 250, "Asia Pacific": 300,
                                                  "Latin America": 90, "Middle East & Africa": 60}.items()}},
            "segmentation_grade_form": {"by_grade_or_type": copy.deepcopy(TYPES)}}
    return {"market_name": "Global Vitamin C Market", "fields": body, "fields_raw": copy.deepcopy(body),
            "market_definition": definition if definition is not None else definition_v4()}


def cmi(sg=None, smea=None, review=None, use_cases=None):
    return {"fields": {
        "geography": {"countries": GEO},
        "countries_main": {"North America": {"United States": cell(88), "Canada": cell(12)},
                           "Europe": split("Europe", [25, 20, 18, 15, 10], 12),
                           "Asia Pacific": split("Asia Pacific", [40, 20, 15, 10, 7], 8),
                           "Latin America": split("Latin America", [45, 20, 10, 6, 5], 14)},
        "countries_mea": {"middle_east_share_of_mea_pct": 55, "Middle East": split("Middle East", [30, 25, 20, 10], 15),
                          "Africa": split("Africa", [30, 20, 15, 10, 5], 20)},
        "segments_global": sg or seg_global(type_merges=[{"merge": ["Ascorbic Acid (Synthetic)",
                                                                    "Ascorbic Acid (Fermentation-Derived)"],
                                                          "name": "Ascorbic Acid",
                                                          "definition": "L-ascorbic acid, whatever the process."}]),
        "segments_mea": smea or seg_mea(),
        "use_cases": use_cases or {"middle_east": [], "africa": []},
        **({"segment_review": review} if review else {})}}


# --------------------------------------------------------------------------- #
# 1. market definition
# --------------------------------------------------------------------------- #
class DefLLM:
    def __init__(self, answers):
        self.answers, self.prompts = list(answers), []

    def complete_json(self, system, user):
        self.prompts.append(user)
        return copy.deepcopy(self.answers.pop(0))


def _no_search(monkeypatch):
    monkeypatch.setattr(market_definition, "gather", lambda *a, **k: [])


GOOD_DEF = {"measures": "Farm-gate value of shrimp from super-intensive systems.", "value_chain_level": "farm-gate output",
            "included": ["Shrimp from lined ponds", "Shrimp from raceways"],
            "excluded": ["Extensive farming", "Equipment sales"], "unit": "USD million",
            "typical_published_size_usd_bn": None}


def test_definition_with_empty_lists_is_reasked_once_and_keeps_the_second_answer(monkeypatch):
    _no_search(monkeypatch)
    llm = DefLLM([{**GOOD_DEF, "included": [], "value_chain_level": "farming"}, GOOD_DEF])
    log = []
    d = market_definition.generate_market_definition("Shrimp Market", llm, None, log,
                                                     client={"exclusions": "shrimp feed"})
    assert len(llm.prompts) == 2
    assert "'included' is empty" in llm.prompts[1] and "value_chain_level" in llm.prompts[1]
    assert "CLIENT DEFINITION" in llm.prompts[0] and "shrimp feed" in llm.prompts[0]
    assert d["check"]["reasked"] and d["check"]["problems"] == []
    assert d["value_chain_level"] == "farm-gate output" and d["included"] == GOOD_DEF["included"]
    assert market_definition.is_current(d)


def test_definition_still_empty_after_reask_is_recorded_not_filled_in(monkeypatch):
    _no_search(monkeypatch)
    bad = {**GOOD_DEF, "excluded": []}
    d = market_definition.generate_market_definition("Shrimp Market", DefLLM([bad, bad]), None, [])
    assert d["excluded"] == []                                   # no generic filler
    assert any("'excluded' is empty" in p for p in d["check"]["problems"])
    m = model.build(fleet(definition={**definition_v4(), **d, "market_name": "Global Vitamin C Market"}), cmi())
    assert any("excluded' is empty" in b for b in m["segment_review"]["blockers"])


def test_level_is_normalised_to_one_of_the_six():
    assert market_definition.normalise_level("Farm-gate") == "farm-gate output"
    assert market_definition.normalise_level("Vendor/Software") == "vendor/software"
    assert market_definition.normalise_level("ingredient/formulation") is None


def test_model_definition_carries_measures_level_and_client_exclusions():
    f = fleet()
    f["definition_inclusion_exclusion"] = {"definition": "Vitamin C, the ingredient.",
                                           "exclusions": "Animal feed, multivitamin gummies"}
    d = model.definition(f)
    assert d["current"] and d["value_chain_level"] == "manufacturer" and d["measures"] == VITC["measures"]
    assert d["definition"] == "Vitamin C, the ingredient."       # the client's verbatim sentence stays
    assert d["included_items"] == ["Ascorbic acid", "Ascorbate salts"]
    assert "multivitamin gummies" in d["excluded_items"]         # client exclusion kept
    assert d["excluded_items"].count("Animal feed") == 0          # already covered by 'Animal feed and pet food'


def test_old_definition_is_a_blocker():
    m = model.build(fleet(definition={"included": "x", "excluded": "y", "value_chain_level": "n/a"}), cmi())
    assert any("predates PLAN-04" in b for b in m["segment_review"]["blockers"])


def test_definition_block_names_measures_and_level():
    from AiAgentsFleet.cmi_report.run_data import definition_block
    txt = definition_block(model.definition(fleet()))
    assert "Measures: Ex-factory sales" in txt and "Value-chain level: manufacturer" in txt


# --------------------------------------------------------------------------- #
# 2. segments obey the definition
# --------------------------------------------------------------------------- #
def test_an_excluded_segment_is_dropped_and_the_rest_renormalised():
    m = model.build(fleet(), cmi())
    dem = m["segments"]["Global"]["demand"]
    assert "Animal Feed & Pet Food" not in dem
    assert abs(sum(v["v26"] for v in dem.values()) - m["global"]["v26"]) < 1e-6
    # 40 / (40 + 30 + 20) of the market, not 40%
    assert abs(dem["Dietary Supplements"]["v26"] / m["global"]["v26"] - 40 / 90) < 1e-9
    assert any("dropped 'Animal Feed & Pet Food'" in c for c in m["segment_review"]["changes"])
    assert "Animal Feed & Pet Food" not in m["segment_definitions"]["demand"]


def test_a_farm_gate_market_has_no_equipment_segments():
    sg = {"demand_segments": [{"name": "Integrated Producers"}, {"name": "Monitoring and Automation Systems"},
                              {"name": "Independent Farms"}],
          "segment_definitions": {"demand": {"Integrated Producers": "Vertically integrated producers.",
                                             "Monitoring and Automation Systems": "Sensors sold to farms.",
                                             "Independent Farms": "Farms without integrated processing."}}}
    res = segments_check.resolve(sg, [], SHRIMP)
    assert res["names"]["demand"] == ["Integrated Producers", "Independent Farms"]


def test_a_buyer_is_not_the_excluded_activity():
    """'Food Processors' buy farm-gate shrimp; 'shrimp processing' (the activity) is what is excluded."""
    assert scope_violation("Food Processors & Exporters", {**SHRIMP, "excluded_items": ["Shrimp processing"]},
                           row=True) is None
    assert scope_violation("Shrimp hatcheries", SHRIMP, row=True)          # 'hatcheries' == 'hatchery'


def test_a_segment_the_included_list_does_not_cover_is_flagged_then_warned():
    dfn = {**VITC, "excluded_items": ["Retail sales of finished supplements"],
           "included_items": ["Dietary supplement ingredient sales", "Pharmaceutical-grade ascorbic acid",
                              "Food and beverage fortification ingredients"]}
    sg = seg_global(type_split_check=[], type_merges=[{"merge": ["Ascorbic Acid (Synthetic)",
                                                                 "Ascorbic Acid (Fermentation-Derived)"],
                                                       "name": "Ascorbic Acid", "definition": "L-ascorbic acid."}])
    probs = segments_check.check_global(sg, TYPE_NAMES, dfn)
    assert [(p["kind"], p["segments"]) for p in probs if p["dim"] == "demand"] ==         [("not_included", ["Animal Feed & Pet Food"])]
    assert "Animal Feed & Pet Food" in segments_check.resolve(sg, TYPE_NAMES, dfn)["names"]["demand"]  # kept
    m = model.build(fleet(definition={**definition_v4(), "included": dfn["included_items"],
                                      "excluded": dfn["excluded_items"]}),
                    cmi(sg=sg, review={"segments_global": {"problems": probs, "reasked": True}}))
    assert any("Animal Feed & Pet Food" in w and "included list" in w for w in m["segment_review"]["warnings"])
    # buyer types the included list does not enumerate are not judged
    assert segments_check.not_included(["Export Traders", "Retail Chains", "Foodservice"], SHRIMP | {
        "included_items": ["Shrimp from lined ponds", "Shrimp from raceways"]}) == []


def test_an_overlapping_pair_is_detected():
    defs = {"seaweed extracts": "Extracts of brown seaweed applied to foliage to improve stress tolerance.",
            "extract-based biostimulants": "Plant extracts applied to foliage.",
            "microbial biostimulants": "Living bacteria and fungi applied to soil."}
    names = ["Extract-Based Biostimulants", "Seaweed Extracts", "Microbial Biostimulants"]
    dfn = {"market_name": "Agricultural Biostimulants Market"}
    found = segments_check.overlaps(names, defs, dfn)
    assert [(a, b) for a, b, _ in found] == [("Extract-Based Biostimulants", "Seaweed Extracts")]
    # an allocation rule naming both separates them
    rule = ["Seaweed-derived products are counted under Seaweed Extracts, not Extract-Based Biostimulants."]
    assert segments_check.overlaps(names, defs, dfn, rule) == []
    # near-identical definitions overlap whatever the names
    same = {"a": "Liquid seaweed extract sprayed on leaves of fruit crops.",
            "b": "Liquid seaweed extract sprayed on leaves of vegetable crops."}
    assert segments_check.overlaps(["A", "B"], same, dfn)[0][2] > segments_check.OVERLAP_MAX


def test_overlap_and_false_split_trigger_one_reask_and_the_second_answer_is_kept():
    first = seg_global()
    second = seg_global(type_merges=[{"merge": ["Ascorbic Acid (Synthetic)", "Ascorbic Acid (Fermentation-Derived)"],
                                      "name": "Ascorbic Acid", "definition": "L-ascorbic acid."}],
                        demand_segments=[d for d in first["demand_segments"] if d["name"] != "Animal Feed & Pet Food"])
    fields, notes, log = {"segments_global": first}, [], []

    def ask(key, note):
        notes.append((key, note))
        return copy.deepcopy(second)
    rec = segments_check.review_global(fields, TYPE_NAMES, model.definition(fleet()), ask, log)
    assert len(notes) == 1 and notes[0][0] == "segments_global"
    kinds = {p["kind"] for p in rec["problems"]}
    assert {"excluded", "overlap", "false_split"} <= kinds
    assert "type_merges" in notes[0][1] and "Animal Feed & Pet Food" in notes[0][1]
    assert rec["reasked"] and rec["after"] == [] and fields["segments_global"] is not first
    m = model.build(fleet(), cmi(sg=fields["segments_global"], review={"segments_global": rec}))
    assert list(m["segments"]["Global"]["type"]) == ["Sodium Ascorbate", "Ascorbic Acid", "Calcium Ascorbate"]
    assert m["sections"]["type"]["show"] and m["segment_review"]["blockers"] == []


def test_still_overlapping_after_the_reask_is_merged():
    sg = seg_global(type_split_check=[], allocation_rules=[])
    rec = {"segments_global": {"problems": [{"kind": "overlap"}], "reasked": True}}
    m = model.build(fleet(), cmi(sg=sg, review=rec, smea=seg_mea(
        {"Sodium Ascorbate": 30, "Ascorbic Acid (Synthetic)": 40, "Ascorbic Acid (Fermentation-Derived)": 20,
         "Calcium Ascorbate": 10})))
    merged = "Ascorbic Acid (Synthetic) / Ascorbic Acid (Fermentation-Derived)"
    assert merged in m["segments"]["Global"]["type"]
    assert abs(m["segments"]["Global"]["type"][merged]["v26"] / m["global"]["v26"] - 0.6) < 1e-9
    assert abs(m["segments"]["Saudi Arabia"]["type"][merged]["v26"]
               / sum(v["v26"] for v in m["segments"]["Saudi Arabia"]["type"].values()) - 0.6) < 1e-9
    assert not any("overlap" in b for b in m["segment_review"]["blockers"])


def test_a_false_split_left_after_the_reask_switches_the_type_dimension_off():
    sg = seg_global(allocation_rules=["Synthetic ascorbic acid is counted under Ascorbic Acid (Synthetic), "
                                      "fermentation-derived under Ascorbic Acid (Fermentation-Derived)."])
    rec = {"segments_global": {"problems": [{"kind": "false_split"}], "reasked": True}}
    m = model.build(fleet(), cmi(sg=sg, review=rec))
    assert m["sections"]["type"]["show"] is False and "false split" in m["sections"]["type"]["why"]
    assert m["segment_review"]["resolved"]["type_off"]


def test_unreasked_segment_problems_are_blockers():
    m = model.build(fleet(), cmi(sg=seg_global()))
    assert any("(not re-asked)" in b and "Animal Feed" in b for b in m["segment_review"]["blockers"])


# --------------------------------------------------------------------------- #
# 3. MEA mix tied to the global mix: flagged, re-asked once, never blended
# --------------------------------------------------------------------------- #
def _biostim():
    """Microbial 9.4% globally but the largest type in every MEA country."""
    g = {"type": {"Acid-Based": 30.0, "Seaweed Extracts": 40.0, "Microbial": 9.4, "Other": 20.6}}
    bad = {u: {"type": {"Acid-Based": {"share_pct": 20}, "Seaweed Extracts": {"share_pct": 25},
                        "Microbial": {"share_pct": 40}, "Other": {"share_pct": 15}}} for u in UNITS}
    fixed = {u: {"type": {"Acid-Based": {"share_pct": 30}, "Seaweed Extracts": {"share_pct": 38},
                          "Microbial": {"share_pct": 14}, "Other": {"share_pct": 18}}} for u in UNITS}
    return g, bad, fixed


def test_a_divergent_mea_mix_is_flagged_and_reasked_once_with_the_global_mix_shown():
    g, bad, fixed = _biostim()
    units = {u: 10.0 + i for i, u in enumerate(UNITS)}
    fields, notes, log = {"segments_mea": bad}, [], []

    def ask(key, note):
        notes.append(note)
        return copy.deepcopy(fixed)
    rec = segments_check.review_mea(fields, units, g, ask, log)
    assert len(notes) == 1
    assert [p["segments"][0] for p in rec["problems"]] == ["Microbial"]
    assert rec["problems"][0]["ratio"] > 2
    assert "GLOBAL MIX" in notes[0] and '"Microbial": 9.4' in notes[0]
    assert fields["segments_mea"] == fixed and rec["after"] == []
    assert rec["changes"] == ["type/Microbial: 40.0% -> 14.0%"]


def test_still_divergent_after_the_reask_is_a_warning_and_not_blended():
    g, bad, _ = _biostim()
    units = {u: 10.0 for u in UNITS}
    fields = {"segments_mea": bad}
    rec = segments_check.review_mea(fields, units, g, lambda k, n: {**copy.deepcopy(bad),
                                                                     "mea_reason": {"Microbial": "Gulf greenhouses."}},
                                    [])
    assert rec["reasked"] and len(rec["after"]) == 1
    assert rec["after"][0]["mea_pct"] == 40.0                   # the agent's own number, not 50/50 with 9.4
    assert rec["after"][0]["mea_reason"] == "Gulf greenhouses."


def test_model_records_divergent_mix_as_warning_after_reask_and_blocker_without():
    smea = seg_mea({"Sodium Ascorbate": 80, "Ascorbic Acid": 15, "Calcium Ascorbate": 5})
    m = model.build(fleet(), cmi(smea=smea))
    assert any("MEA type mix: Sodium Ascorbate" in b and "(not re-asked)" in b
               for b in m["segment_review"]["blockers"])
    m = model.build(fleet(), cmi(smea=smea, review={"segments_mea": {"problems": [{}], "reasked": True}}))
    w = [x for x in m["segment_review"]["warnings"] if "Sodium Ascorbate" in x]
    assert w and "not blended" in w[0]
    sa = m["segments"]["Saudi Arabia"]["type"]
    assert abs(sa["Sodium Ascorbate"]["v26"] / sum(v["v26"] for v in sa.values()) - 0.80) < 1e-9


class MeaLLM:
    """segments_mea agent: returns `answers` in turn and records every prompt."""

    def __init__(self, answers):
        self.answers, self.prompts = [copy.deepcopy(a) for a in answers], []

    def complete_json(self, system, user):
        self.prompts.append(user)
        return self.answers.pop(0)


def _run_mea(monkeypatch, tmp_path, answers, only=("segments_mea",)):
    """run_data.run(only=segments_mea) on a fixture market with the LLM and search mocked."""
    from AiAgentsFleet.cmi_report import run_data
    d = tmp_path / "output" / "vc"
    d.mkdir(parents=True)
    f = fleet()
    f["fields"]["market_size"]["forecast_series_usd_mn"] = {str(y): 1000 * 1.06 ** (y - 2025) for y in range(2025, 2034)}
    (d / "whitepaper_fields.json").write_text(json.dumps(f), encoding="utf-8")
    (d / "cmi_fields.json").write_text(json.dumps(cmi()), encoding="utf-8")
    llm = MeaLLM(answers)
    monkeypatch.setattr(run_data, "FLEET", tmp_path)
    monkeypatch.setattr(run_data.GenConfig, "load", staticmethod(lambda root: None))
    monkeypatch.setattr(run_data, "GenLLM", lambda cfg, cache_path=None: llm)
    monkeypatch.setattr(run_data, "search", lambda *a, **k: [{"text": "Vitamin C demand in Egypt", "url": "u",
                                                              "domain": "fao.org"}])
    run_data._SHOWN.clear()
    run_data.run("vc", set(only))
    out = json.loads((d / "cmi_fields.json").read_text(encoding="utf-8"))["fields"]
    return llm.prompts, out


COPY = seg_mea()                                         # every country = the global mix
OWN = {u: mix({"Sodium Ascorbate": 20 if i % 2 else 40, "Ascorbic Acid": 70 if i % 2 else 50, "Calcium Ascorbate": 10},
              {"Dietary Supplements": 30 if i % 2 else 55, "Pharmaceuticals": 40 if i % 2 else 25,
               "Food & Beverage Fortification": 30 if i % 2 else 20})
       for i, u in enumerate(UNITS)}


def test_the_first_call_does_not_show_the_global_mix(monkeypatch, tmp_path):
    prompts, out = _run_mea(monkeypatch, tmp_path, [OWN])
    assert len(prompts) == 1                                 # country-specific, within x2: no re-ask
    assert "global_mix" not in prompts[0] and "GLOBAL MIX" not in prompts[0]
    g = next(x for x in GROUPS if x["key"] == "segments_mea")
    assert "global_mix" not in g["system"] and "mea_reason" not in g["schema"]
    assert out["segment_review"]["segments_mea"]["copied_first"] is False


def test_the_reask_shows_the_global_mix_for_a_x2_conflict(monkeypatch, tmp_path):
    bad = seg_mea({"Sodium Ascorbate": 80, "Ascorbic Acid": 15, "Calcium Ascorbate": 5})
    prompts, out = _run_mea(monkeypatch, tmp_path, [bad, OWN])
    assert len(prompts) == 2
    assert "GLOBAL MIX" not in prompts[0] and "GLOBAL MIX" in prompts[1] and '"Sodium Ascorbate": 30.0' in prompts[1]
    assert out["segments_mea"] == OWN | {"_sources": out["segments_mea"]["_sources"]}


def test_a_copied_mix_is_flagged_and_reasked(monkeypatch, tmp_path):
    prompts, out = _run_mea(monkeypatch, tmp_path, [COPY, OWN])
    rec = out["segment_review"]["segments_mea"]
    assert rec["copied_first"] and rec["reasked"] and rec["copied_after"] is False
    assert len(prompts) == 2 and "copy, not an estimate" in prompts[1] and "GLOBAL MIX" in prompts[1]
    assert out["segments_mea"]["Saudi Arabia"]["type"]["Sodium Ascorbate"]["share_pct"] == 40   # the re-ask kept
    m = model.build(fleet(), {"fields": out})
    assert "MEA mixes copy the global mix (first answer; the re-ask gave country-specific mixes)" in \
        m["segment_review"]["warnings"]


def test_a_copied_mix_reask_that_fails_x2_is_not_kept():
    g = {"type": {"A": 50.0, "B": 50.0}}
    copy_ = {u: {"type": {"A": {"share_pct": 50}, "B": {"share_pct": 50}}} for u in UNITS}
    wild = {u: {"type": {"A": {"share_pct": 95}, "B": {"share_pct": 5}}} for u in UNITS}
    fields = {"segments_mea": copy_}
    rec = segments_check.review_mea(fields, {u: 1.0 for u in UNITS}, g, lambda k, n: copy.deepcopy(wild), [])
    assert rec["copied_first"] and rec["copied_reask_rejected"] and fields["segments_mea"] is copy_
    assert rec["copied_after"] is True
    m = model.build(fleet(), cmi(smea=COPY, review={"segments_mea": rec}))
    assert "MEA mixes copy the global mix (still copied after one re-ask)" in m["segment_review"]["warnings"]


# --------------------------------------------------------------------------- #
# 3b. the false-split test applies to manufacturer / vendor / retail markets only
# --------------------------------------------------------------------------- #
SYSTEMS = ["Super-intensive lined ponds", "Super-intensive raceways", "Controlled-environment indoor farms (RAS)",
           "High-density biofloc systems"]
SYSTEM_SG = {
    "demand_segments": [{"name": "Processors", "share_pct_2026": 60, "cagr_2026_2033_pct": 8},
                        {"name": "Retail Chains", "share_pct_2026": 40, "cagr_2026_2033_pct": 7}],
    "segment_definitions": {
        "type": {"Super-intensive lined ponds": "HDPE-lined grow-out ponds stocked above 150 shrimp per square metre.",
                 "Super-intensive raceways": "Long flow-through channels with continuous water exchange.",
                 "Controlled-environment indoor farms (RAS)": "Enclosed tanks recirculating filtered water indoors.",
                 "High-density biofloc systems": "Zero-exchange tanks where microbial floc treats the water."},
        "demand": {"Processors": "Plants that buy whole shrimp to peel and freeze.",
                   "Retail Chains": "Supermarkets buying fresh shrimp."}},
    "type_split_check": [{"a": a, "b": b, "buyers_differentiate": False, "basis": "production history"}
                         for i, a in enumerate(SYSTEMS) for b in SYSTEMS[i + 1:]],
    "type_merges": []}


def test_a_farm_gate_market_keeps_its_production_system_split():
    assert segments_check.check_global(SYSTEM_SG, SYSTEMS, SHRIMP) == []
    res = segments_check.resolve(SYSTEM_SG, SYSTEMS, SHRIMP)
    assert res["names"]["type"] == SYSTEMS and res["type_off"] is None
    note = segments_check.reask_note_global([], SYSTEM_SG, SHRIMP)
    assert "distinct production systems" in note and "differ only by production history" not in note


def test_a_manufacturer_market_still_has_its_false_split_flagged():
    # an allocation rule separates the nested names, so only the false split is left to act on
    sg = seg_global(allocation_rules=["Synthetic ascorbic acid is counted under Ascorbic Acid (Synthetic), "
                                      "fermentation-derived under Ascorbic Acid (Fermentation-Derived)."])
    probs = segments_check.check_global(sg, TYPE_NAMES, model.definition(fleet()))
    assert any(p["kind"] == "false_split" for p in probs)
    assert segments_check.resolve(sg, TYPE_NAMES, model.definition(fleet()))["type_off"]
    # same answer, farm-gate level: no false split
    assert not any(p["kind"] == "false_split" for p in segments_check.check_global(sg, TYPE_NAMES, SHRIMP))


# --------------------------------------------------------------------------- #
# 4. scope guard: prose and heat-map rows
# --------------------------------------------------------------------------- #
def test_scope_violation_in_a_farm_gate_market():
    assert scope_violation("Aerator sales in Saudi Arabia add USD 4 million to the market.", SHRIMP)
    assert scope_violation("Technology spending by farms rises fastest in Egypt.", SHRIMP)
    assert scope_violation("Shrimp feed accounts for a growing share of the market.", SHRIMP)
    assert scope_violation("Intensive aeration and water circulation technology for shrimp ponds", SHRIMP, row=True)
    assert scope_violation("Monitoring and automation systems", SHRIMP, row=True)
    # fine: a driver that only mentions technology, or an explicit exclusion
    assert scope_violation("Better aeration lifts yields per hectare.", SHRIMP) is None
    assert scope_violation("Rising shrimp feed costs squeeze farm-gate value.", SHRIMP) is None
    assert scope_violation("Equipment sales are excluded from this market.", SHRIMP) is None
    assert scope_violation("Super-intensive lined ponds for white shrimp", SHRIMP, row=True) is None
    # a manufacturer market has no equipment-row rule, but excluded items still apply
    assert scope_violation("Fermentation technology", VITC, row=True) is None
    assert scope_violation("Animal feed and pet food producers", VITC, row=True)


def test_clean_text_drops_out_of_scope_sentences():
    cg = ClaimGuard([], None, [])
    cg.definition = SHRIMP
    dropped = []
    out = clean_text("Farm-gate output rises in Egypt. Aerator revenue is part of the market.",
                     NumberGuard({}), cg, dropped)
    assert out == "Farm-gate output rises in Egypt."
    assert dropped and "out of scope" in dropped[0]


def test_use_case_rows_out_of_scope_are_reasked_then_left_out():
    uc = {"middle_east": [{"use_case": "Super-intensive lined ponds"},
                          {"use_case": "Monitoring and automation systems for shrimp farms"}],
          "africa": [{"use_case": "Shrimp hatcheries"}, {"use_case": "Indoor RAS farms"}]}
    fields, notes = {"use_cases": copy.deepcopy(uc)}, []

    def ask(key, note):
        notes.append(note)
        return {"middle_east": uc["middle_east"], "africa": [{"use_case": "Indoor RAS farms"}]}
    rec = segments_check.review_use_cases(fields, SHRIMP, ask, [])
    assert len(notes) == 1 and len(rec["problems"]) == 2
    assert [r["use_case"] for r in fields["use_cases"]["middle_east"]] == ["Super-intensive lined ponds"]
    assert [r["use_case"] for r in fields["use_cases"]["africa"]] == ["Indoor RAS farms"]
    assert rec["removed"] == ["middle_east: Monitoring and automation systems for shrimp farms"]


def test_prompts_carry_the_value_chain_rules():
    sg = next(x for x in GROUPS if x["key"] == "segments_global")
    uc = next(x for x in GROUPS if x["key"] == "use_cases")
    assert "farm-gate output" in sg["system"] and "FORBIDDEN" in sg["system"]
    assert "type_split_check" in sg["schema"] and "type_merges" in sg["schema"]
    assert "farm-gate output" in uc["system"]
    assert json.dumps(segments_check.check_use_cases({"middle_east": [{"use_case": "Aerators"}]}, SHRIMP))


# --------------------------------------------------------------------------- #
# 5. overlapping types: a replacement taxonomy, merges only as the fallback
# --------------------------------------------------------------------------- #
BIO = ["Acid-Based Biostimulants", "Extract-Based Biostimulants", "Seaweed Extracts", "Microbial Biostimulants",
       "Other Biostimulants"]
BIO_DFN = {"market_name": "Agricultural Biostimulants Market", "value_chain_level": "manufacturer"}


def test_one_type_in_two_merges_is_rejected():
    sg = {"demand_segments": [{"name": "Cereals"}, {"name": "Fruits"}],
          "type_merges": [{"merge": ["Extract-Based Biostimulants", "Seaweed Extracts"],
                           "name": "Extract-Based Biostimulants (incl. Seaweed Extracts)"},
                          {"merge": ["Seaweed Extracts", "Other Biostimulants"],
                           "name": "Other Biostimulants (incl. Seaweed Extracts)"}]}
    assert segments_check.bad_merges(sg, BIO) == ["Seaweed Extracts"]
    assert segments_check.merge_map(sg, BIO) == {}               # every agent merge rejected
    probs = segments_check.check_global(sg, BIO, BIO_DFN)
    assert any(p["kind"] == "bad_merge" for p in probs)
    assert segments_check.wants_replacement(probs)                 # treated as still overlapping
    # fallback: the still-overlapping pair is merged by the model, nothing is double counted
    res = segments_check.resolve(sg, BIO, BIO_DFN)
    assert res["names"]["type"].count("Extract-Based Biostimulants / Seaweed Extracts") == 1
    assert "Seaweed Extracts" not in res["names"]["type"]


REPLACEMENT = [
    {"name": "Ascorbic Acid", "share_pct_2026": 55, "cagr_2026_2033_pct": 5.5,
     "definition": "Pure L-ascorbic acid powder or granules, whatever the production process."},
    {"name": "Sodium Ascorbate", "share_pct_2026": 25, "cagr_2026_2033_pct": 5.0,
     "definition": "The sodium salt, a buffered non-acidic form for tablets and meat curing."},
    {"name": "Calcium Ascorbate", "share_pct_2026": 12, "cagr_2026_2033_pct": 4.8,
     "definition": "The calcium salt, sold for sensitive stomachs and mineral fortification."},
    {"name": "Ascorbyl Esters", "share_pct_2026": 8, "cagr_2026_2033_pct": 6.5,
     "definition": "Fat-soluble palmitate and phosphate esters used in cosmetics and oils."}]
NEW = [r["name"] for r in REPLACEMENT]


def _replaced_answer(rows=REPLACEMENT):
    sg = seg_global(type_split_check=[], type_merges=[], type_replacement=copy.deepcopy(rows))
    sg["demand_segments"] = [d for d in sg["demand_segments"] if d["name"] != "Animal Feed & Pet Food"]
    return sg


def test_overlapping_types_reask_for_a_replacement_taxonomy_not_merges():
    note = segments_check.reask_note_global(segments_check.check_global(seg_global(), TYPE_NAMES,
                                                                        model.definition(fleet())),
                                            seg_global(), model.definition(fleet()))
    assert "REPLACEMENT taxonomy" in note and "mutually exclusive, collectively exhaustive list of 4-7 types" in note
    assert "standard segmentation that published reports on this market use" in note
    assert '"type_merges": [{"merge"' not in note


def test_a_replacement_taxonomy_is_accepted_and_the_mea_call_uses_its_names(monkeypatch, tmp_path):
    mea = {u: mix({"Ascorbic Acid": 50 if i % 2 else 60, "Sodium Ascorbate": 30 if i % 2 else 20,
                   "Calcium Ascorbate": 12, "Ascorbyl Esters": 8},
                  {"Dietary Supplements": 30 if i % 2 else 55, "Pharmaceuticals": 40 if i % 2 else 25,
                   "Food & Beverage Fortification": 30 if i % 2 else 20}) for i, u in enumerate(UNITS)}
    prompts, out = _run_mea(monkeypatch, tmp_path, [seg_global(), _replaced_answer(), mea],
                            only=("segments_global",))     # segments_mea is NOT asked for: it must still run
    assert len(prompts) == 3
    assert "REPLACEMENT taxonomy" in prompts[1]
    rec = out["segment_review"]["segments_global"]
    assert rec["replacement"] == "accepted" and [t["name"] for t in rec["types"]] == NEW
    assert out["segments_global"]["type_taxonomy"] == "replacement"
    assert all(n in prompts[2] for n in NEW) and "(Synthetic)" not in prompts[2]   # the MEA call: new names
    m = model.build(fleet(), {"fields": out})
    assert list(m["segments"]["Global"]["type"]) == NEW and m["sections"]["type"]["show"]
    assert abs(m["segments"]["Global"]["type"]["Ascorbic Acid"]["v26"] / m["global"]["v26"] - 0.55) < 1e-9
    assert m["segment_definitions"]["type"]["Ascorbyl Esters"].startswith("Fat-soluble")
    assert not any("type" in b for b in m["segment_review"]["blockers"])


def test_a_replacement_that_still_overlaps_falls_back_to_merges():
    still = copy.deepcopy(REPLACEMENT)
    still[3] = {"name": "Ascorbic Acid Derivatives", "share_pct_2026": 8, "cagr_2026_2033_pct": 6,
                "definition": "Esters and other derivatives."}           # contains 'Ascorbic Acid', no rule
    fields = {"segments_global": seg_global()}
    rec = segments_check.review_global(fields, TYPE_NAMES, model.definition(fleet()),
                                       lambda k, n: _replaced_answer(still), [])
    assert rec["replacement"].startswith("rejected")
    assert "type_taxonomy" not in fields["segments_global"] and "type_replacement" not in fields["segments_global"]
    assert segments_check.type_names_of(fields["segments_global"], TYPE_NAMES) == TYPE_NAMES   # fleet types kept


def test_mea_mix_with_other_type_names_is_a_blocker():
    sg = segments_check.with_replacement(_replaced_answer())
    m = model.build(fleet(), cmi(sg=sg, smea=seg_mea()))       # seg_mea uses the OLD type names
    assert any("does not use the global type names" in b for b in m["segment_review"]["blockers"])


def test_a_false_split_alone_also_asks_for_a_replacement():
    sg = seg_global(allocation_rules=["Synthetic ascorbic acid is counted under Ascorbic Acid (Synthetic), "
                                      "fermentation-derived under Ascorbic Acid (Fermentation-Derived)."])
    probs = [p for p in segments_check.check_global(sg, TYPE_NAMES, model.definition(fleet())) if p["dim"] == "type"]
    assert {p["kind"] for p in probs} == {"false_split"}
    assert "REPLACEMENT taxonomy" in segments_check.reask_note_global(probs, sg, model.definition(fleet()))
    # a farm-gate market has no false-split test, so nothing to replace
    assert not segments_check.wants_replacement(segments_check.check_global(SYSTEM_SG, SYSTEMS, SHRIMP))

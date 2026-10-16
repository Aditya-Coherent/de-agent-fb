"""PLAN-07: prose logic -- near-ties, ordinals, signed gaps, global vs MEA basis,
recommendations backed by their finding, contradictions with the data,
truthful methodology and boilerplate. LLM calls are mocked."""
from __future__ import annotations

import copy

from AiAgentsFleet.cmi_report import audit, charts, fmt, prose, rules
from AiAgentsFleet.cmi_report.guard import FALSE_METHOD, clean_text, contradiction_guard, fix_plurals
from AiAgentsFleet.cmi_report.register import FactGuard, build_register


# --------------------------------------------------------------------------- #
# a small model (shapes as model.build makes them)
# --------------------------------------------------------------------------- #
def node(v26, cagr, v25=None):
    v33 = v26 * (1 + cagr / 100) ** 7
    return {"v25": v25 if v25 is not None else v26 / (1 + cagr / 100), "v26": v26, "v33": v33, "cagr": cagr,
            "incr": v33 - v26, "series": {2026: v26, 2033: v33}}


def model() -> dict:
    regions = {"North America": node(115.3, 22.4), "Europe": node(83.1, 17.1), "Asia Pacific": node(109.9, 26.5),
               "Latin America": node(24.9, 19.6), "Middle East & Africa": node(16.8, 16.9)}
    countries = {
        "North America": {"United States": node(100.0, 22.5), "Canada": node(15.3, 21.6)},
        "Europe": {"Germany": node(30.0, 19.7), "France": node(25.0, 16.0), "Rest of Europe": node(28.1, 16.5)},
        "Asia Pacific": {"China": node(60.0, 27.0), "India": node(49.9, 26.0)},
        "Latin America": {"Brazil": node(14.0, 20.0), "Mexico": node(10.9, 19.0)},
        "Middle East": {"Saudi Arabia": node(3.4, 11.0), "UAE": node(2.5, 14.5), "Turkey": node(2.0, 12.0),
                        "Rest of Middle East": node(1.7, 11.5)},
        "Africa": {"South Africa": node(3.0, 11.5), "Egypt": node(2.0, 11.0), "Nigeria": node(1.2, 14.0),
                   "Rest of Africa": node(1.0, 12.0)},
    }
    seg = {"type": {"Microbial Biostimulants": node(40.0, 12.3), "Seaweed Extracts": node(30.0, 12.2),
                    "Humic Substances": node(30.0, 9.0)},
           "demand": {"Fruits & Vegetables": node(60.0, 11.0), "Cereals & Grains": node(40.0, 10.0)}}
    return {
        "market": "Global Widget Market",
        "global": node(349.9, 22.3),
        "regions": regions,
        "subregions": {"Middle East": node(9.6, 12.3), "Africa": node(7.2, 12.2)},
        "countries": countries,
        "country_series": {},
        "segments": {"Global": copy.deepcopy(seg), "Middle East": copy.deepcopy(seg), "Africa": copy.deepcopy(seg)},
        "dimensions": {"type": {"singular": "Product Type", "plural": "Product Types"},
                       "demand": {"singular": "Crop Type", "plural": "Crop Types"}},
        "sections": {k: {"show": True} for k in ("type", "demand", "regulation", "milestones", "vendor_mea",
                                                 "capabilities")},
        "profiled": {"Middle East": ["Saudi Arabia", "UAE", "Turkey", "Rest of Middle East"],
                     "Africa": ["South Africa", "Egypt", "Nigeria", "Rest of Africa"]},
        "scenarios": {"Base": {"cagr": 22.3, "series": {2033: 1300.0}}},
        "vendor_chart_global": [["Syngenta", 20.0], ["BASF", 12.0], ["Regional & other", 47.4]],
        "vendor_chart_mea": [["Syngenta", 30.0], ["UPL", 11.2], ["Regional & other", 35.7]],
        "vendor_share_global": [], "vendor_share_mea": [],
        "value_chain": [], "published_2025": [],
        "size_check": {"status": "unsourced"},
    }


AG = {"dynamics": {
    "mea_drivers": [{"name": "Severe water scarcity", "impact_pp": 2.5}, {"name": "Food security initiatives",
                                                                          "impact_pp": 2.0},
                    {"name": "Mobile internet penetration", "impact_pp": 1.8},
                    {"name": "Government agri-tech investments", "impact_pp": 1.5},
                    {"name": "Climate change adaptation needs", "impact_pp": 1.2},
                    {"name": "Youth agri-tech entrepreneurship", "impact_pp": 0.8}],
    "mea_restraints": [{"name": "Limited digital infrastructure", "impact_pp": -1.8},
                       {"name": "Low digital literacy", "impact_pp": -1.5},
                       {"name": "High cost of AI solutions", "impact_pp": -1.2},
                       {"name": "Data scarcity and fragmentation", "impact_pp": -1.0},
                       {"name": "Regulatory and data privacy gaps", "impact_pp": -0.8},
                       {"name": "Political and economic instability", "impact_pp": -0.5}]}}


def guards(geo=None):
    m, ag = model(), copy.deepcopy(AG)
    return m, ag, prose.make_guard(m, geo, ag), FactGuard(build_register(m, ag))


def kept(text, geo=None):
    m, ag, cg, ng = guards(geo)
    dropped: list[str] = []
    return clean_text(text, ng, cg, dropped), dropped


# --------------------------------------------------------------------------- #
# 1. near-ties
# --------------------------------------------------------------------------- #
def test_rank_calls_near_tie_similar():
    m = model()
    r = prose._rank(m["subregions"])                    # Middle East 12.3% vs Africa 12.2%
    assert r["fastest_growing"] == "Middle East and Africa (similar)"
    assert rules.tied("cagr", 12.3, 12.0) and not rules.tied("cagr", 12.3, 11.9)
    assert rules.tied("v26", 100, 97.5) and not rules.tied("v26", 100, 96)


def test_near_tie_fastest_is_dropped_unless_called_similar():
    out, why = kept("The Middle East is the faster-growing MEA subregion at a 12.3% CAGR.")
    assert out == "" and "tie threshold" in why[0]                 # FINDINGS X2
    out, _ = kept("Microbial Biostimulants are the fastest-growing Product Type at 12.3%.", "Global")
    assert out == ""
    ok = "Microbial Biostimulants and Seaweed Extracts grow at a similar rate, the fastest Product Types."
    assert kept(ok, "Global")[0] == ok


def test_check_ties_reports_near_ties():
    ties = audit.check_ties(model())
    assert any("subregions" in t and "near-tie" in t for t in ties)


def test_fallback_names_both_tied_leaders():
    m = model()
    txt = prose.seg_fallback(m["segments"]["Global"]["type"], "Product Type", "Global")
    assert "Microbial Biostimulants and Seaweed Extracts grow at a similar rate" in txt
    assert "Seaweed Extracts grows fastest" not in txt


# --------------------------------------------------------------------------- #
# 2. ordinals
# --------------------------------------------------------------------------- #
def test_wrong_ordinal_is_dropped():
    # FINDINGS D1: APAC is $110M, so Europe ($83.1M) is third
    out, why = kept("Europe is the second-largest region in 2026E at $83.1M, CMI estimates.")
    assert out == "" and "#2 is Asia Pacific" in why[0]
    ok = "Asia Pacific is the second-largest region in 2026E at $110M, CMI estimates."
    assert kept(ok)[0] == ok
    ok3 = "Europe is the third-largest region in 2026E at $83.1M, CMI estimates."
    assert kept(ok3)[0] == ok3


# --------------------------------------------------------------------------- #
# 3. signed gaps and the driver basis
# --------------------------------------------------------------------------- #
def test_negative_gap_prints_with_its_sign():
    m = model()
    reg = {e["id"]: e for e in build_register(m, copy.deepcopy(AG))}
    gap = reg["R-MIDDLEEAST-GAP"]                       # 16.9% - 22.3%
    assert gap["display"] == "-5.4" and fmt.pp(-5.4) == "-5.4 pp"
    assert reg["R-ASIAPACIFI-GAP"]["display"] == "+4.2"
    ng = FactGuard(list(reg.values()))
    assert not ng.bad("The Middle East & Africa grows 5.4 pp below the global market.")
    assert not ng.bad("The Middle East & Africa CAGR is -5.4 pp versus the world.")
    assert ng.bad("The Middle East & Africa grows 5.4 pp above the global market.")
    assert ng.bad("Middle East & Africa: +5.4 pp versus the global market.")


def test_drivers_reconcile_with_the_cagrs():
    # FINDINGS X15 (GenAI): net +3.0 pp while MEA grows 5.4 pp slower than global
    dyn = copy.deepcopy(AG["dynamics"])
    rules.reconcile_drivers(dyn, 22.3, 16.9)
    total = sum(x["impact_pp"] for x in dyn["mea_drivers"] + dyn["mea_restraints"])
    assert round(22.3 + total, 1) == 16.9 and dyn["impact_basis"] == "pp_vs_global"
    assert all(x["impact_pp"] > 0 for x in dyn["mea_drivers"]) and all(x["impact_pp"] < 0 for x in dyn["mea_restraints"])
    drv = [x["impact_pp"] for x in dyn["mea_drivers"]]
    assert drv == sorted(drv, reverse=True)                        # order kept
    assert rules.reconcile_drivers(dyn, 22.3, 16.9) is dyn and dyn["mea_drivers"][0]["impact_pp_agent"] == 2.5
    # numbers that would need a > 4x rescale become relative scores without pp
    odd = {"mea_drivers": [{"name": "A", "impact_pp": 5.0}], "mea_restraints": [{"name": "B", "impact_pp": -0.1}]}
    rules.reconcile_drivers(odd, 10.0, 5.0)
    assert odd["impact_basis"] == "score" and "impact_pp" not in odd["mea_drivers"][0]
    assert odd["mea_drivers"][0]["impact_score"] == 5 and odd["mea_restraints"][0]["impact_score"] == -1


def test_driver_register_and_chart_follow_the_basis(tmp_path):
    m, ag = model(), copy.deepcopy(AG)
    prose.prepare(m, ag)
    reg = {e["id"]: e for e in build_register(m, ag)}
    assert reg["D-LIMITEDDIG"]["display"].startswith("-") and "global CAGR baseline" in reg["D-LIMITEDDIG"]["fact"]
    assert charts.drivers_restraints(ag["dynamics"]["mea_drivers"], ag["dynamics"]["mea_restraints"],
                                     tmp_path / "d.png").exists()


def test_global_slot_drops_mea_basis_sentences():
    m, ag, _, ng = guards()
    dropped: list[str] = []
    v = {"global": "Limited digital infrastructure, assessed at -1.8 percentage points of MEA CAGR.",
         "mea": "Limited digital infrastructure, assessed at -1.8 percentage points of MEA CAGR."}
    out = prose._clean(v, ng, prose.make_guard(m, "Global", ag), dropped, [])
    assert "global" not in out and out["mea"] == v["mea"]                       # FINDINGS X14
    assert any("MEA-basis" in d for d in dropped)


# --------------------------------------------------------------------------- #
# 4. recommendations follow from their finding
# --------------------------------------------------------------------------- #
class Judge:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def complete_json(self, system, user):
        self.calls.append(user)
        return self.answers.pop(0) if self.answers else {"support": "yes", "geography_matches_focus": True}


def test_unsupported_recommendation_is_dropped():
    m, ag, _, ng = guards()
    act = {"lead": "Secure fermentation-derived supply contracts with Chinese producers.",
           "text": "Finding: Humic Substances grow at a 9.0% CAGR."}
    judge = Judge([{"support": "no", "why": "the finding is about another segment"},
                   {"support": "no", "why": "still unrelated"}])
    writer = Judge([{"action": {"lead": "Expand humic supply in Egypt and Nigeria.",
                                "text": "Finding: Humic Substances grow at a 9.0% CAGR."}}])
    kept_, log = prose.check_actions([act], m, ag, judge, writer, ng, {})
    assert kept_ == [] and any("action dropped" in x for x in log)
    assert len(writer.calls) == 1                                  # regenerated once, then dropped


def test_regenerated_action_is_kept_when_supported():
    m, ag, _, ng = guards()
    act = {"lead": "Pilot report tools for cooperatives.", "text": "Finding: Humic Substances grow at a 9.0% CAGR."}
    good = {"lead": "Prioritise Nigeria for distribution build-out over 12-18 months.",
            "text": "Finding: Nigeria is the fastest-growing country in Africa at 14.0%."}
    judge = Judge([{"support": "no", "why": "unrelated"}, {"support": "yes", "geography_matches_focus": True}])
    kept_, _ = prose.check_actions([act], m, ag, judge, Judge([{"action": good}]), ng, {})
    assert kept_ == [good]


def test_action_outside_mea_or_contradicting_data_fails():
    m, ag, _, ng = guards()
    out_of_focus = {"lead": "Localise content in Asia Pacific and Latin America.",
                    "text": "Finding: Nigeria is the fastest-growing country in Africa at 14.0%."}
    why, _ = prose.action_problem(out_of_focus, m, ag, Judge([{"support": "yes", "geography_matches_focus": False}]))
    assert why and "geography" in why
    # FINDINGS C11: Saudi Arabia and South Africa both grow below their peer average -- no judge needed
    target = {"lead": "Target high-growth markets like Saudi Arabia and South Africa.",
              "text": "Finding: Nigeria is the fastest-growing country in Africa at 14.0%."}
    judge = Judge([])
    why, _ = prose.action_problem(target, m, ag, judge)
    assert why and "Saudi Arabia" in why and judge.calls == []


# --------------------------------------------------------------------------- #
# 5. contradictions with the data (FINDINGS quotes)
# --------------------------------------------------------------------------- #
def test_b2_local_distribution_claim_contradicts_vendor_shares():
    s = ("Italpollina holds 5.1% and Biolchim 4.1%, while regional and other suppliers retain 35.7%, a CMI "
         "assessment that local distribution and technical support shape share more than global brand scale.")
    _, _, cg, _ = guards()
    assert "35.7%" in contradiction_guard(s, cg)
    # the true direction passes: MEA's long tail (35.7%) is smaller than the global one (47.4%)
    ok = "Regional and other vendors hold 35.7%, a smaller long tail than globally, a more concentrated field."
    assert contradiction_guard(ok, cg) is None


def test_d4_high_mobile_penetration_contradicts_restraint():
    s = "Smartphone chatbots deliver agronomic knowledge, capitalising on high mobile internet penetration in MEA."
    _, _, cg, _ = guards()
    assert "Limited digital infrastructure" in contradiction_guard(s, cg)
    assert contradiction_guard("Mobile advisory reaches farmers in Germany with high broadband coverage.", cg) is None


def test_c11_momentum_for_a_below_average_entity():
    _, _, cg, _ = guards()
    m = model()
    m["subregions"]["Africa"] = node(7.2, 6.8)
    m["subregions"]["Middle East"] = node(9.6, 6.9)
    m["regions"]["Middle East & Africa"] = node(16.8, 6.85)
    cg2 = prose.make_guard(m, None, AG)
    s = "Africa, the smaller subregion at $236M in 2026E on CMI estimates, shows particular momentum."
    assert "momentum" in contradiction_guard(s, cg2)
    assert contradiction_guard("Nigeria shows strong momentum at a 14.0% CAGR.", cg) is None
    assert "Egypt" in contradiction_guard("Nigeria and Egypt combine scale with momentum.", cg)
    # 'X grows faster than Y' between two named peers
    assert contradiction_guard("Egypt grows faster than Nigeria.", cg)
    assert contradiction_guard("Nigeria grows faster than Egypt.", cg) is None


def test_b7_tighter_registration_does_not_sustain_demand():
    _, _, cg, _ = guards()
    s = "Export sectors, plus tightened fertilizer registration protocols before market entry, sustain demand."
    assert "barrier" in contradiction_guard(s, cg)


def test_dangling_these_factors_in_a_finding_is_dropped():
    out, why = kept("Finding: CMI assessments estimate the relative contribution of these factors to MEA growth.")
    assert out == "" and "not in the text" in why[0]


# --------------------------------------------------------------------------- #
# 6. truthful methodology
# --------------------------------------------------------------------------- #
def test_methodology_wording_matches_the_pipeline():
    m = model()
    for chk in ({"status": "unsourced"}, {"status": "sourced", "source_is_cmi": True},
                {"status": "sourced", "source_is_cmi": False}):
        m["size_check"] = chk
        t = prose.method_text(m, "Widget")
        assert not FALSE_METHOD.search(t) and "desk research" in t and "single data register" in t
        assert "agents" not in t and "re-asked" not in t                  # no pipeline jargon in client text
        assert "published figure" not in t and "published estimate" not in t
    assert "headline is a CMI estimate" in prose.method_text({**m, "size_check": {"status": "unsourced"}}, "Widget")
    assert not any(FALSE_METHOD.search(t + " " + d) for t, d in charts.PROCESS)
    assert "method_para" not in prose.chapters(m, AG)["ch9"]["slots"]
    out, why = kept("The estimates rest on CMI's methodology, which combines primary interviews and trade data.")
    assert out == "" and "research step" in why[0]


# --------------------------------------------------------------------------- #
# 7. boilerplate and structure
# --------------------------------------------------------------------------- #
def test_repeated_phrase_rewritten_or_removed():
    m, ag, _, ng = guards()
    trends = [{"lead": f"T{i}", "text": f"Demand rises for reason {w}. This trend is most visible in the Middle East, "
                                          f"where {w} buyers act."}
              for i, w in enumerate(["food", "feed", "pharma", "cosmetic"])]
    specs = prose.chapters(m, ag)
    slots = {"trend_paras": copy.deepcopy(trends)}
    log = prose.rewrite_repeated_phrases(slots, m, ag, None, ng, specs)       # no LLM: removed
    texts = [t["text"] for t in slots["trend_paras"]]
    assert sum("most visible" in t for t in texts) == 1 and len(log) == 3

    class Rewriter:
        def complete_json(self, system, user):
            import json
            items = json.loads(user)
            return {"rewrites": [{"id": x["id"], "text": "Middle East buyers lead this shift."} for x in items]}
    slots = {"trend_paras": copy.deepcopy(trends)}
    prose.rewrite_repeated_phrases(slots, m, ag, Rewriter(), ng, specs)
    texts = [t["text"] for t in slots["trend_paras"]]
    assert sum("most visible" in t for t in texts) == 1
    assert sum("Middle East buyers lead this shift." in t for t in texts) == 3


def test_key_conclusions_topped_up_to_three():
    slots = {"key_conclusions": ["Only one."],
             "share_para": "Asia Pacific gains share to 2033F at a 26.5% CAGR, CMI forecasts. Europe loses share, "
                           "growing at 17.1%, below the global 22.3% CAGR."}
    out = prose.fill_conclusions(slots["key_conclusions"], slots)
    assert len(out) == 3 and out[0] == "Only one."
    assert prose.fill_conclusions(["One."], {"key_conclusions": ["One."]}) == []      # heading removed


def test_profiles_open_with_a_lead_sentence():
    m = model()
    d, parent = m["countries"]["Middle East"]["UAE"], m["subregions"]["Middle East"]
    txt = prose.with_lead("UAE", "Controlled-environment farms drive adoption.", d, parent, "the Middle East")
    assert txt.startswith(f"UAE is a {fmt.usd(d['v26'])} market in 2026E") and fmt.pct(d["cagr"]) in txt
    assert fmt.share(d["v26"], parent["v26"]) in txt
    assert prose.with_lead("UAE", None, d, parent, "the Middle East").startswith("UAE is a")
    own = f"The UAE is {fmt.usd(d['v26'])} in 2026E at a {fmt.pct(d['cagr'])} CAGR. Farms drive it."
    assert prose.with_lead("UAE", own, d, parent, "the Middle East") == own


def test_plural_segment_names_take_plural_verbs():
    names = ["Microbial Biostimulants", "Cereals & Grains", "Hydroponics", "Sodium Ascorbate"]
    s = "Microbial Biostimulants remains the largest, Cereals & Grains is next, Hydroponics is small."
    assert fix_plurals(s, names) == "Microbial Biostimulants remain the largest, Cereals & Grains are next, " \
                                    "Hydroponics is small."


def test_domain_sanity_drops_flagged_sentences():
    m = model()
    slots = {"country_profiles": {"India": "India's market grows on pharma demand in cities and towns. India is "
                                           "also a key producer of vitamin C for export markets worldwide."}}

    class Sanity:
        def complete_json(self, system, user):
            line = next(x for x in user.splitlines() if "key producer" in x)
            return {"flagged": [{"id": int(line.split(".")[0]), "why": "India imports most of its vitamin C"}]}
    log = prose.domain_sanity(slots, m, Sanity())
    assert "key producer" not in slots["country_profiles"]["India"] and len(log) == 1

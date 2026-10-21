"""PLAN-05: vendor shares only when sourced, a positioning table otherwise,
verified entities, merges only for wholly owned subsidiaries, one exclusion
list, one vendor year, and vendor text that uses only the final vendor data."""
import copy
import json

from AiAgentsFleet.cmi_report import entities, model
from AiAgentsFleet.cmi_report.guard import vendor_clean, vendor_problem
from test_segments import cmi as seg_cmi, fleet as seg_fleet

MARKET = "Global Vitamin C Market"
YEAR = model.VENDOR_YEAR


def snip(text, dom="industry.example.com"):
    return {"text": text, "url": f"https://{dom}/x", "domain": dom}


SNIPS = [snip(f"In {YEAR}, CSPC Pharmaceutical Group held a 22% share of the global vitamin C market."),
         snip(f"Shandong Luwei Pharmaceutical accounted for 14% of vitamin C market sales in {YEAR}."),
         snip("Northeast Pharmaceutical Group (NEPG) has a 12% share of the vitamin C market."),
         snip(f"BASF SE held 9% of the vitamin C market in {YEAR}, according to the report.")]


def row(name, share, quote=""):
    return {"name": name, "share_pct": share, "share_quote": quote, "positioning_note": f"{name} makes vitamin C"}


def rec(name, hq="China", parent=None, ownership="independent", earns=True, verified=True, moves=(), domestic=False,
        division=""):
    self_ = parent is None
    prod = verified and earns is not False
    return {"name": name, "hq_country": hq if verified else "", "hq_verified": verified,
            "parent_group": name if self_ else parent, "parent_is_self": self_, "parent_verified": verified,
            "ownership": "independent" if self_ else ownership, "wholly_owned": not self_ and ownership == "wholly_owned",
            "business_type": "producer" if verified else "", "business_verified": verified,
            "division": division, "product_verified": prod, "product_phrase": "vitamin c" if prod else None,
            "role": f"Makes ascorbic acid ({name})" if prod else "",
            "earns_defined_revenue": True if prod else (False if earns is False else None),
            "excluded_why": "it sells finished vitamin C tablets at retail" if earns is False else None,
            "mainly_domestic": domestic,
            "subsidiaries_in_list": [], "ownership_note": "", "moves": [{"event": "x", "year": y, "quote": "q"}
                                                                            for y in moves],
            "verified": verified, "why": [] if verified else ["HQ not stated in the evidence"]}


def fleet(companies, check=None):
    f = seg_fleet()
    f["fields"]["competitive_landscape"] = {"companies": companies, "_sources": {}}
    if check is not None:
        f["fields"]["competitive_landscape"]["_sources"]["share_check"] = check
    f["fields_raw"]["competitive_landscape"] = copy.deepcopy(f["fields"]["competitive_landscape"])
    return f


GLOBAL = [row("DSM-Firmenich", 25), row("CSPC Pharmaceutical Group", 22, SNIPS[0]["text"]),
          row("Shandong Luwei Pharmaceutical", 14, SNIPS[1]["text"]),
          row("Northeast Pharmaceutical Group", 12, SNIPS[2]["text"]), row("BASF SE", 9, SNIPS[3]["text"])]
POSITIONING = [{"vendor": n, "role": f"{n} sells ascorbic acid to supplement makers", "mea_presence": p}
               for n, p in (("CSPC Pharmaceutical Group", "Strong"), ("Northeast Pharmaceutical Group", "Leading"),
                            ("Shandong Luwei Pharmaceutical", "Moderate"), ("DSM-Firmenich", "Moderate"),
                            ("BASF SE", "Limited"))]
RECORDS = {"CSPC Pharmaceutical Group": rec("CSPC Pharmaceutical Group"),
           "Shandong Luwei Pharmaceutical": rec("Shandong Luwei Pharmaceutical"),
           "Northeast Pharmaceutical Group": rec("Northeast Pharmaceutical Group", moves=(2024,)),
           "DSM-Firmenich": rec("DSM-Firmenich", hq="Switzerland", verified=False),
           "BASF SE": rec("BASF SE", hq="Germany", earns=False)}


def cmi(vendors=None, records=None, mea_check=None, entities_on=True):
    c = seg_cmi()
    c["fields"]["vendors"] = vendors or {
        "value_chain": [], "mea_shares": [], "positioning": copy.deepcopy(POSITIONING),
        "capability_dimensions": ["a", "b", "c", "MEA presence"],
        "capabilities": {n: [3, 3, 3, 3] for n in ("CSPC Pharmaceutical Group", "Northeast Pharmaceutical Group",
                                                   "Shandong Luwei Pharmaceutical", "BASF SE", "DSM-Firmenich")},
        "dashboard": [{"vendor": n, "hq": "Singapore", "core_strength": "Scale", "mea_footprint": "Exports to Egypt.",
                       "verified": True, "ownership_note": ""} for n in ("CSPC Pharmaceutical Group",
                                                                         "Northeast Pharmaceutical Group",
                                                                         "DSM-Firmenich", "BASF SE")],
        "company_classification": []}
    if entities_on:
        c["fields"]["entities"] = {"records": copy.deepcopy(records if records is not None else RECORDS),
                                   "not_checked": []}
    c["fields"]["vendor_review"] = {"problems": [], "reasked": False, "mea_shares": mea_check or {}}
    return c


def check_of(rows):
    return entities.check_shares(rows, SNIPS, MARKET, YEAR)


# --------------------------------------------------------------------------- #
# 1. shares: sourced or dropped
# --------------------------------------------------------------------------- #
def test_share_verification_rules():
    ok = entities.verify_share(row("CSPC Pharmaceutical Group", 22, SNIPS[0]["text"]), SNIPS, MARKET, YEAR)
    assert ok["status"] == "sourced" and ok["share_pct"] == 22 and ok["url"]
    assert entities.verify_share(row("DSM-Firmenich", 25), SNIPS, MARKET, YEAR)["status"] == "unsourced"
    invented = entities.verify_share(row("DSM-Firmenich", 23, "DSM-Firmenich leads the vitamin C market with 23%"),
                                     SNIPS, MARKET, YEAR)
    assert invented["status"] == "rejected" and "not found" in invented["why"]
    wrong_num = entities.verify_share(row("CSPC Pharmaceutical Group", 25, SNIPS[0]["text"]), SNIPS, MARKET, YEAR)
    assert wrong_num["status"] == "rejected" and "not stated" in wrong_num["why"]
    other_co = entities.verify_share(row("Zhejiang NHU", 22, SNIPS[0]["text"]), SNIPS, MARKET, YEAR)
    assert other_co["why"] == "quote does not name the company"
    old = entities.verify_share(row("CSPC Pharmaceutical Group", 22, SNIPS[0]["text"]), SNIPS, MARKET, YEAR + 1)
    assert old["status"] == "rejected" and f"not {YEAR + 1}" in old["why"]


def test_an_unsourced_share_is_dropped():
    m = model.build(fleet(GLOBAL, check_of(GLOBAL)), cmi())
    shown = dict(m["vendor_share_global"])
    assert "DSM-Firmenich" not in shown                                    # 25%, no quote
    assert shown["CSPC Pharmaceutical Group"] == 22 and shown["Northeast Pharmaceutical Group"] == 12
    assert any("DSM-Firmenich at 25% has no source" in w for w in m["vendor_review"]["warnings"])
    assert all(n != "DSM-Firmenich" for n, _ in m["vendor_chart_global"])
    reg_ids = [e["id"] for e in __import__("AiAgentsFleet.cmi_report.register", fromlist=["x"]).build_register(m, {})
               if e["id"].startswith("V-")]
    assert not any("DSMFIRMENI" in i for i in reg_ids)


def test_fewer_than_3_sourced_shares_gives_a_positioning_table_not_a_donut():
    two = GLOBAL[:3]                                                       # DSM (unsourced) + 2 sourced
    m = model.build(fleet(two, check_of(two)), cmi())
    assert not m["sections"]["vendor_global"]["show"] and not m["sections"]["vendor_mea"]["show"]
    assert m["vendor_chart_global"] == [] and m["vendor_share_global"] == []
    assert m["sections"]["positioning"]["show"]
    rows = m["vendor_positioning"]
    assert [r["vendor"] for r in rows] == ["CSPC Pharmaceutical Group", "Northeast Pharmaceutical Group",
                                           "Shandong Luwei Pharmaceutical"]           # alphabetical
    assert all("%" not in json.dumps(r) for r in rows)                     # never percentages
    from AiAgentsFleet.cmi_report.register import build_register
    assert not [e for e in build_register(m, {}) if e["id"].startswith("V-")]   # no vendor rows in A.2
    # the report: the positioning table where the donut was, no vendor image
    out = _vendor_section(m)
    assert "Figure" not in " ".join(out["texts"]) and out["tables"] == 1
    assert out["header"][:4] == ["Vendor", "HQ", "Parent group", "Business type"] and len(out["header"]) == 5      # no MEA column: chapter 7 is global
    assert "7.2 Vendor positioning (Global)" in out["texts"]


def test_three_sourced_shares_give_a_donut():
    m = model.build(fleet(GLOBAL[:4], check_of(GLOBAL[:4])), cmi())
    assert m["sections"]["vendor_global"]["show"]
    assert [n for n, _ in m["vendor_chart_global"]][:3] == ["CSPC Pharmaceutical Group",
                                                           "Shandong Luwei Pharmaceutical",
                                                           "Northeast Pharmaceutical Group"]
    assert m["vendor_sources"]["global"][0]["quote"] == SNIPS[0]["text"]


# --------------------------------------------------------------------------- #
# 2. entities, merges, exclusions
# --------------------------------------------------------------------------- #
def test_entity_hq_and_parent_must_be_in_the_evidence():
    shown = [snip("RYNAN Technologies Vietnam, headquartered in Tra Vinh, Vietnam, builds shrimp farm systems."),
             snip("RYNAN Aquaculture is a subsidiary of RYNAN Technologies, a Vietnamese technology company.")]
    good = {"hq_country": "Vietnam", "hq_quote": shown[0]["text"], "parent_group": "RYNAN Technologies",
            "parent_quote": shown[1]["text"], "ownership": "wholly_owned", "business_type": "technology provider",
            "business_quote": shown[0]["text"], "earns_defined_revenue": False}
    r = entities.verify_entity(good, "RYNAN Aquaculture (TOMGOXY)", shown)
    assert r["verified"] and r["hq_country"] == "Vietnam" and r["wholly_owned"]
    wrong_hq = entities.verify_entity({**good, "hq_country": "Singapore"}, "RYNAN Aquaculture (TOMGOXY)", shown)
    assert not wrong_hq["verified"] and wrong_hq["hq_country"] == ""        # never printed
    no_quote = entities.verify_entity({**good, "parent_quote": "RYNAN is owned by RYNAN Technologies (made up)"},
                                      "RYNAN Aquaculture (TOMGOXY)", shown)
    assert not no_quote["parent_verified"] and no_quote["parent_group"] == ""


def test_no_exclusion_without_evidence_and_no_facts_from_a_namesake():
    shown = [snip("New Hope Group is an Australian thermal coal mining company headquartered in Australia.")]
    namesake = {"same_company": False, "hq_country": "Australia", "hq_quote": shown[0]["text"],
                "business_type": "coal miner", "business_quote": shown[0]["text"], "earns_defined_revenue": False}
    r = entities.verify_entity(namesake, "New Hope Group", shown)
    assert not r["verified"] and r["hq_country"] == "" and r["earns_defined_revenue"] is None
    # no evidence on the business: unknown -- not excluded (and not printed until confirmed)
    blank = entities.verify_entity({"earns_defined_revenue": False, "earns_reason": "No web evidence was provided"},
                                   "Egyptian Shrimp Company", [])
    assert blank["earns_defined_revenue"] is None and blank["excluded_why"] is None
    assert entities.exclusions({"Egyptian Shrimp Company": blank, "New Hope Group": r}) == {}


def test_entity_check_sees_only_snippets_that_name_the_company(monkeypatch):
    monkeypatch.setattr(entities.time, "sleep", lambda s: None)
    junk = [snip("WhatsApp Web: Log in to WhatsApp Web for simple messaging."),
            snip("Welcome to Python.org: experienced programmers pick up Python quickly.")]
    good = snip("Shandong Luwei Pharmaceutical is a vitamin C maker headquartered in Zibo, China.")
    calls, prompts = [], []

    class L:
        def complete_json(self, system, user):
            prompts.append(user)
            return {"hq_country": "CN", "hq_quote": good["text"], "ownership": "independent",
                    "business_type": "producer", "business_quote": good["text"], "earns_defined_revenue": True}

    def search(qs):
        calls.append(qs)
        return junk + ([good, good] if len(calls) > 1 else [])               # degraded first, then a hit
    r = entities.check_entity("Shandong Luwei Pharmaceutical", MARKET, "", {}, [], L(), search, [])
    assert len(calls) == 2 and "WhatsApp" not in prompts[0] and "Python" not in prompts[0]
    assert r["verified"] and r["hq_country"] == "China"                    # 'CN' normalised


PRODUCTS_VC = entities.product_phrases(MARKET, {"measures": "Ex-factory sales of vitamin C (ascorbic acid and its "
                                                                  "derivatives)", "included_items": ["sodium ascorbate"]})
PRODUCTS_FEED = entities.product_phrases("Functional Poultry Feed Market", {"included_items": ["gut-health poultry feed"]})


def test_product_phrases_come_from_the_market_and_its_definition():
    assert PRODUCTS_VC == ["vitamin c", "sodium ascorbate", "ascorbic acid"]
    assert "poultry feed" in PRODUCTS_FEED and "feed" not in PRODUCTS_FEED
    assert entities.mentions_product("Zhejiang NHU is a leading vitamin producer (vitamins A and E).", PRODUCTS_VC) is None
    assert entities.mentions_product("CSPC makes Vitamin C tablets and bulk ascorbic acid.", PRODUCTS_VC) == "vitamin c"
    assert entities.mentions_product("NEPG is a major ascorbic acid exporter.", PRODUCTS_VC) == "ascorbic acid"
    assert entities.mentions_product("A vitamin complex with zinc.", PRODUCTS_VC) is None         # 'c' must follow


def _entity(name, quote, **over):
    raw = {"hq_country": "China", "hq_quote": quote, "business_type": "producer", "business_quote": quote,
           "product_quote": quote, "role": "Makes the product", "earns_defined_revenue": True}
    raw.update(over)
    return entities.verify_entity(raw, name, [snip(quote)], over.pop("_products", PRODUCTS_VC))


def test_a_generic_vitamin_producer_is_not_a_vitamin_c_vendor():
    generic = _entity("Zhejiang NHU Company", "Zhejiang NHU Company, headquartered in China, is a vitamin producer.")
    assert generic["verified"] and not generic["product_verified"] and generic["role"] == ""
    assert generic["excluded_why"] is None                          # not shown either way: unconfirmed, not excluded
    real = _entity("Zhejiang NHU Company", "Zhejiang NHU Company, headquartered in China, produces vitamin C.")
    assert real["product_verified"] and real["product_phrase"] == "vitamin c"
    acid = _entity("CSPC Pharmaceutical Group", "CSPC Pharmaceutical Group of China is a top ascorbic acid maker.")
    assert acid["product_verified"]
    names = ["Zhejiang NHU Company", "CSPC Pharmaceutical Group"]
    assert entities.included(names, {"Zhejiang NHU Company": generic, "CSPC Pharmaceutical Group": acid}) == \
        ["CSPC Pharmaceutical Group"]


def test_a_diversified_company_with_a_relevant_division_is_included():
    hq = "Cargill, headquartered in the United States, is a broad agricultural and food company."
    prod = "Cargill Animal Nutrition makes poultry feed and gut-health feed programmes for broilers."
    shown = [snip(hq), snip(prod)]
    raw = {"hq_country": "United States", "hq_quote": hq, "business_type": "agribusiness", "business_quote": hq,
           "division": "Cargill Animal Nutrition", "product_quote": prod, "role": "Makes functional poultry feed",
           "earns_defined_revenue": False, "earns_reason": "Cargill is a broad agricultural company, not specifically "
           "a functional poultry feed maker", "exclusion_quote": hq}
    r = entities.verify_entity(raw, "Cargill", shown, PRODUCTS_FEED)
    assert r["product_verified"] and r["excluded_why"] is None and r["division"] == "Cargill Animal Nutrition"
    # 'broad company' is never a reason to exclude, even without a product line
    vague = entities.verify_entity({**raw, "product_quote": ""}, "Cargill", shown, PRODUCTS_FEED)
    assert vague["excluded_why"] is None
    # in the report the company carries the division's name
    f = seg_fleet()
    others = ["De Heus Animal Nutrition", "ForFarmers"]                # a table needs 3 confirmed vendors
    f["fields"]["competitive_landscape"] = {"companies": [{"name": n, "share_pct": 8} for n in ["Cargill"] + others]}
    c = seg_cmi()
    c["fields"]["vendors"] = {"positioning": [{"vendor": "Cargill", "role": "x", "mea_presence": "Strong"}]}
    c["fields"]["entities"] = {"records": {"Cargill": r, **{n: rec(n, hq="Netherlands") for n in others}},
                               "not_checked": []}
    m = model.build(f, c)
    assert [x["vendor"] for x in m["vendor_positioning"]] == ["Cargill (Cargill Animal Nutrition)",
                                                              "De Heus Animal Nutrition", "ForFarmers"]
    assert m["vendor_positioning"][0]["role"] == "Makes functional poultry feed"


def test_a_parent_hq_is_not_printed_as_the_entitys_hq():
    q = "Japfa Comfeed Indonesia is a subsidiary of Singapore-based Japfa Ltd, Asia's agri-food group."
    raw = {"hq_country": "Singapore", "hq_quote": q, "parent_group": "Japfa Ltd", "parent_quote": q,
           "ownership": "majority", "business_type": "feed producer", "business_quote": q}
    r = entities.verify_entity(raw, "Japfa Comfeed", [snip(q)], PRODUCTS_FEED)
    assert r["hq_country"] == "" and not r["hq_verified"] and not r["verified"]
    assert r["parent_group"] == "Japfa Ltd"                          # the parent is recorded as the parent
    assert any("parent Japfa Ltd" in w for w in r["why"])
    own = "Japfa Comfeed is headquartered in Jakarta, Indonesia."
    r2 = entities.verify_entity({**raw, "hq_country": "Indonesia", "hq_quote": own}, "Japfa Comfeed",
                                [snip(q), snip(own)], PRODUCTS_FEED)
    assert r2["hq_country"] == "Indonesia"                          # a snippet states the entity's own HQ


def test_a_thin_vendor_list_is_reasked_once_with_the_exclusion_reasons():
    recs = {"Thai Union Group": {**rec("Thai Union Group", hq="Thailand", earns=False),
                                 "excluded_why": "a seafood processor, not a farm"},
            "Minh Phu Seafood": rec("Minh Phu Seafood", hq="Vietnam"),
            "CP Foods": rec("CP Foods", hq="Thailand", verified=False)}
    fields = {"vendors": {"positioning": [{"vendor": n, "role": "x"} for n in recs]},
              "entities": {"records": recs, "not_checked": []}}
    asks, checked, log = [], [], []
    again = {"companies": [{"vendor": "National Aquaculture Group (NAQUA)", "role": "farms shrimp"},
                           {"vendor": "Minh Phu Seafood", "role": "repeated"}]}

    def ask(system, body, queries):
        asks.append((system, body, queries))
        return again

    def check_new(names):
        checked.append(names)
        return {n: rec(n, hq="Saudi Arabia") for n in names}
    dfn = {"value_chain_level": "farm-gate output", "measures": "Farm-gate value of super-intensive shrimp"}
    out = entities.reask_if_thin(fields, {}, dfn, "Super-Intensive Shrimp Farming Market", ask, check_new, log)
    assert len(asks) == 1 and asks[0][0] == entities.THIN_SYSTEM
    body, queries = asks[0][1], asks[0][2]
    assert "farm-gate output" in body and "Farm-gate value of super-intensive shrimp" in body
    assert "Thai Union Group: a seafood processor, not a farm" in body
    assert "EXCLUDE (already checked -- do not list any of these): Thai Union Group; Minh Phu Seafood; CP Foods" in body
    assert queries[:3] == entities.THIN_QUERIES
    assert checked == [["National Aquaculture Group (NAQUA)"]]               # the repeated name is not re-checked
    assert out["reasked"] and out["included_before"] == ["Minh Phu Seafood"] and out["repeated"] == ["Minh Phu Seafood"]
    assert set(out["included_after"]) == {"Minh Phu Seafood", "National Aquaculture Group (NAQUA)"}
    assert len(fields["vendors"]["positioning"]) == 4                  # the first answer's companies are kept
    # enough vendors: no re-ask
    many = {f"Vendor {i} Shrimp Farms": rec(f"Vendor {i} Shrimp Farms") for i in range(5)}
    f2 = {"vendors": {"positioning": [{"vendor": n} for n in many]}, "entities": {"records": many}}
    assert entities.reask_if_thin(f2, {}, dfn, "x", lambda *a: 1 / 0, check_new, [])["reasked"] is False


def test_the_reask_prompt_has_no_top_companies_list_and_has_the_exclude_list(monkeypatch, tmp_path):
    """run_data with the LLM and search mocked: the thin-list re-ask is its own prompt."""
    from AiAgentsFleet.cmi_report import run_data
    d = tmp_path / "output" / "vc"
    d.mkdir(parents=True)
    (d / "whitepaper_fields.json").write_text(json.dumps(fleet(GLOBAL[:3])), encoding="utf-8")
    (d / "cmi_fields.json").write_text(json.dumps(seg_cmi()), encoding="utf-8")
    llm = _LLM()
    monkeypatch.setattr(entities.time, "sleep", lambda s: None)
    monkeypatch.setattr(run_data, "FLEET", tmp_path)
    monkeypatch.setattr(run_data.GenConfig, "load", staticmethod(lambda root: None))
    monkeypatch.setattr(run_data, "GenLLM", lambda cfg, cache_path=None: llm)
    monkeypatch.setattr(run_data, "search", lambda *a, **k: [snip("Vitamin C producers in China")])
    monkeypatch.setattr(run_data, "entity_search", lambda qs: [snip(
        f"{qs[0].replace(' company headquarters', '')} is a vitamin C producer headquartered in Shijiazhuang, China.")])
    run_data._SHOWN.clear()
    run_data.run("vc", {"vendors"})
    thin = [u for sys_, u in llm.prompts if sys_ == entities.THIN_SYSTEM]
    assert len(thin) == 1
    reask = thin[0]
    vendors_prompt = next(u for sys_, u in llm.prompts if sys_ not in (entities.THIN_SYSTEM, entities.ENTITY_SYSTEM))
    assert "top_companies" in vendors_prompt                               # the first vendors call has the context ...
    assert "top_companies" not in reask and "CONTEXT" not in reask         # ... the re-ask does not
    assert "position every company" not in reask.lower() and "CONTEXT.top_companies" not in entities.THIN_SYSTEM
    assert "EXCLUDE (already checked -- do not list any of these): DSM-Firmenich; CSPC Pharmaceutical Group" in reask
    assert "Value-chain level: manufacturer" in reask


def test_a_snippet_must_say_the_company_supplies_the_product():
    adisseo = ("Adisseo releases new vitamin nutrition guide - aquafeed.com: This new version of the Microvit "
               "Nutrition Guide presents minimum and maximum recommended levels of vitamin C for animal feed.")
    assert not entities.states_supply(adisseo, ["Adisseo"], "vitamin c")
    r = entities.verify_entity({"hq_country": "France", "hq_quote": "Adisseo is headquartered in Antony, France.",
                                "business_type": "feed additive maker",
                                "business_quote": "Adisseo is headquartered in Antony, France.",
                                "product_quote": adisseo, "role": "vitamin nutrition guidance",
                                "earns_defined_revenue": True}, "Adisseo",
                               [snip(adisseo), snip("Adisseo is headquartered in Antony, France.")], PRODUCTS_VC)
    assert not r["product_verified"] and r["role"] == ""
    assert any("does not say it makes, supplies or sells" in w for w in r["why"])
    cspc = "CSPC Pharmaceutical Group is a leading manufacturer of bulk drugs, including vitamin C."
    assert entities.states_supply(cspc, ["CSPC Pharmaceutical Group"], "vitamin c")
    assert entities.states_supply("CSPC Pharmaceutical Group Limited - Bulk Drugs: Major products: vitamin C",
                                  ["CSPC Pharmaceutical Group"], "vitamin c")
    # the same words, far apart, are not a statement that the company sells it
    far = ("CSPC Pharmaceutical Group reported results on Tuesday after a board meeting chaired by its chief "
           "executive in Shijiazhuang where analysts asked about margins and exports to Europe and Africa; separately "
           "the city also hosts several unrelated plants and a museum about vitamin C history")
    assert not entities.states_supply(far, ["CSPC Pharmaceutical Group"], "vitamin c")


def test_fewer_than_3_confirmed_vendors_print_one_sentence_not_a_table():
    recs = {"National Aquaculture Group (NAQUA)": rec("National Aquaculture Group (NAQUA)", hq="Saudi Arabia"),
            "Minh Phu Seafood": rec("Minh Phu Seafood", hq="Vietnam")}
    c = cmi(vendors={"positioning": [{"vendor": "National Aquaculture Group (NAQUA)", "role": "x",
                                      "mea_presence": "Leading"},
                                     {"vendor": "Minh Phu Seafood", "role": "x", "mea_presence": "Limited"}],
                     "dashboard": [{"vendor": n, "hq": "x", "core_strength": "y", "mea_footprint": "z",
                                    "verified": True} for n in recs]},
            records=recs)
    m = model.build(fleet([row(n, 5) for n in recs], check_of([])), c)
    assert m["vendor_positioning"] == [] and m["vendor_dashboard"] == [] and m["vendor_capabilities"] == {}
    assert not m["sections"]["positioning"]["show"] and not m["sections"]["dashboard"]["show"]
    assert m["vendor_unconfirmed"]["global"] == entities.UNCONFIRMED
    assert m["vendor_unconfirmed"]["mea"].startswith(entities.UNCONFIRMED)
    assert "National Aquaculture Group (NAQUA) (Saudi Arabia) has the strongest Middle East & Africa presence" in \
        m["vendor_unconfirmed"]["mea"]                                     # a confirmed MEA leader may be named
    assert any("only 2 confirmed vendor(s) (fewer than 3)" in w for w in m["vendor_review"]["warnings"])
    assert any("MEA leader National Aquaculture Group (NAQUA)" in w for w in m["vendor_review"]["warnings"])
    # the report: both sections are left out entirely (no "could not be confirmed" sentence, no table)
    out = _vendor_section(m)
    assert out["tables"] == 0
    assert out["texts"] == []
    from AiAgentsFleet.cmi_report import prose
    assert prose.mea_leader_named({"mea_share_para": "x"}, m) is None
    # Minh Phu (confirmed but in no table) may not be named in the text; the MEA leader may
    assert vendor_problem("Minh Phu Seafood farms shrimp.", m["vendor_text"])
    assert vendor_problem("National Aquaculture Group (NAQUA) farms shrimp.", m["vendor_text"]) is None


def test_there_is_no_tier_column():
    from AiAgentsFleet.cmi_report import build
    from AiAgentsFleet.cmi_report.groups import GROUPS
    m = model.build(fleet(GLOBAL[:3], check_of(GLOBAL[:3])), cmi())
    assert all("tier" not in r for r in m["vendor_positioning"])
    out = _vendor_section(m)
    assert out["header"] == build.POSITIONING_HEADER and not any("tier" in h.lower() for h in out["header"])
    assert not any(w in " ".join(out["texts"]) for w in ("Leader", "Challenger", "Niche"))
    g = next(x for x in GROUPS if x["key"] == "vendors")
    assert '"tier"' not in g["schema"] and "Leader|Challenger" not in g["schema"]
    from AiAgentsFleet.cmi_report import prose
    assert "tier" not in json.dumps(prose.vendor_facts(m))


def test_an_unverified_vendor_is_left_out_of_every_table():
    m = model.build(fleet(GLOBAL, check_of(GLOBAL)), cmi())
    names = [r["vendor"] for r in m["vendor_positioning"]] + [r["vendor"] for r in m["vendor_dashboard"]]
    assert "DSM-Firmenich" not in names                                    # HQ unverified
    assert all(r["hq"] == "China" for r in m["vendor_dashboard"])          # the entity's HQ, not the agent's
    assert any("DSM-Firmenich (HQ not stated" in w for w in m["vendor_review"]["warnings"])
    assert "DSM-Firmenich" in m["vendor_text"]["blocked"]


def test_a_joint_venture_is_not_merged():
    recs = {"Cargill": rec("Cargill", hq="United States"),
            "Sanderson Farms": rec("Sanderson Farms", hq="United States", parent="Wayne-Sanderson Farms",
                                   ownership="joint_venture"),
            "Valagro": rec("Valagro", hq="Italy", parent="Syngenta", ownership="wholly_owned"),
            "Syngenta": rec("Syngenta", hq="Switzerland")}
    out, notes, declined = model.merge_parents([("Cargill", 5.4), ("Sanderson Farms", 2.4), ("Syngenta", 8.0),
                                                ("Valagro", 3.0)], recs)
    names = dict(out)
    assert names["Cargill"] == 5.4 and names["Sanderson Farms"] == 2.4    # no "Cargill (incl. Sanderson Farms)"
    assert names["Syngenta (incl. Valagro)"] == 11.0
    assert any("Sanderson Farms" in d and "joint_venture" in d for d in declined)
    # an unconfirmed parent is never merged either
    recs["Valagro"]["wholly_owned"] = False
    assert "Valagro" in dict(model.merge_parents([("Syngenta", 8.0), ("Valagro", 3.0)], recs)[0])


def test_an_excluded_vendor_is_absent_from_both_charts():
    mea_rows = [{"vendor": "BASF SE", "share_pct": 9, "quote": SNIPS[3]["text"]}] + \
               [{"vendor": r["name"], "share_pct": r["share_pct"], "quote": r["share_quote"]} for r in GLOBAL[1:4]]
    mea_check = check_of(mea_rows)
    assert len(mea_check["verified"]) == 4
    m = model.build(fleet(GLOBAL, check_of(GLOBAL)), cmi(mea_check=mea_check))
    assert m["sections"]["vendor_global"]["show"] and m["sections"]["vendor_mea"]["show"]
    for key in ("vendor_chart_global", "vendor_chart_mea", "vendor_share_global", "vendor_share_mea"):
        assert not any("BASF" in n for n, _ in m[key]), key
    assert not any("BASF" in r["vendor"] for r in m["vendor_positioning"] + m["vendor_dashboard"])
    assert "BASF SE" not in m["vendor_capabilities"]
    assert "BASF SE" in m["vendor_review"]["excluded"] and "BASF SE" in m["vendor_text"]["blocked"]
    assert sum(1 for c in m["corrections"] if "Left out of every vendor list" in c) == 1    # logged once


def test_only_evidence_excludes():
    # an exclusion needs a quote showing the company does not sell the product, and a real reason
    shown = [snip("Tyson Foods is a meat company that processes chicken, beef and pork; it sells no feed.")]
    base = {"hq_country": "", "business_type": "meat processor", "business_quote": shown[0]["text"],
            "earns_defined_revenue": False}
    meat = entities.verify_entity({**base, "earns_reason": "Tyson sells meat, not feed",
                                   "exclusion_quote": shown[0]["text"]}, "Tyson Foods", shown, PRODUCTS_FEED)
    assert meat["excluded_why"] == "Tyson sells meat, not feed"
    assert entities.exclusions({"Tyson Foods": meat}) == {"Tyson Foods": "meat processor: Tyson sells meat, not feed"}
    no_quote = entities.verify_entity({**base, "earns_reason": "Tyson sells meat, not feed"}, "Tyson Foods", shown,
                                      PRODUCTS_FEED)
    assert no_quote["excluded_why"] is None and no_quote["earns_defined_revenue"] is None
    # the vendors agent's own classification never excludes on its own
    assert entities.exclusions({}) == {}


def test_plausibility_blocks_a_domestic_vendor_bigger_than_its_home_market():
    recs = {"Alibaba Cloud": rec("Alibaba Cloud", hq="China", domestic=True)}
    probs = entities.plausibility([{"vendor": "Alibaba Cloud", "share_pct": 15.4, "sourced": True}], 350.0,
                                  {"China": 41.0}.get, recs)
    assert [p["kind"] for p in probs] == ["over_home"]
    probs = entities.plausibility([{"vendor": "DSM", "share_pct": 23, "sourced": False}], 1600.0, {}.get, {})
    assert [p["kind"] for p in probs] == ["unsourced_over_20"]


def test_a_sourced_but_implausible_share_is_a_blocker_and_not_printed():
    big = [row("CSPC Pharmaceutical Group", 22, SNIPS[0]["text"])] + GLOBAL[2:4]
    f = fleet(big, check_of(big))
    m0 = model.build(f, cmi())                                             # an exporter: no home-market limit
    china = m0["countries"]["Asia Pacific"]["China"]["v26"]
    assert 0.22 * m0["global"]["v25"] > china                              # $220M of sales > China's market
    assert "CSPC Pharmaceutical Group" in dict(m0["vendor_share_global"]) and not m0["vendor_review"]["blockers"]
    recs = {**RECORDS, "CSPC Pharmaceutical Group": rec("CSPC Pharmaceutical Group", domestic=True)}
    m = model.build(f, cmi(records=recs))                                  # mainly domestic: impossible
    assert any("implausible sourced share" in b and "CSPC" in b for b in m["vendor_review"]["blockers"])
    assert "CSPC Pharmaceutical Group" not in dict(m["vendor_share_global"])


def test_old_data_without_entity_checks_is_a_blocker():
    m = model.build(fleet(GLOBAL), cmi(entities_on=False))
    assert any("predates PLAN-05" in b for b in m["vendor_review"]["blockers"])
    assert m["vendor_positioning"] == [] and m["vendor_dashboard"] == []
    assert not m["sections"]["positioning"]["show"]


# --------------------------------------------------------------------------- #
# 3. one vendor year; text uses only the final vendor data
# --------------------------------------------------------------------------- #
def test_the_vendor_year_is_the_same_in_chart_caption_register_and_text(monkeypatch, tmp_path):
    m = model.build(fleet(GLOBAL[:4], check_of(GLOBAL[:4])), cmi())
    from AiAgentsFleet.cmi_report import charts, prose
    from AiAgentsFleet.cmi_report.register import build_register
    seen = {}
    monkeypatch.setattr(charts, "_save", lambda fig, path: seen.setdefault(
        "texts", [t.get_text() for ax in fig.axes for t in ax.texts]) and path)
    charts.vendor_donut(m["vendor_chart_global"], "Global", tmp_path / "v.png", m["vendor_year"])
    assert f"{YEAR} value share (published)" in seen["texts"]
    caption = next(t for t in _vendor_section(m)["texts"] if t.startswith("Figure"))
    assert caption.endswith(f"{YEAR} (published figures)")
    ids = [e for e in build_register(m, {}) if e["id"].startswith("V-")]
    assert ids and all(e["id"].endswith(f"-{YEAR}") and e["fact"].endswith(str(YEAR)) for e in ids)
    assert prose.vendor_facts(m)["vendor_year"] == str(YEAR)
    assert vendor_problem(f"CSPC Pharmaceutical Group leads with 22.0% in {YEAR + 1}E.", m["vendor_text"])
    assert vendor_problem(f"CSPC Pharmaceutical Group leads with 22.0% in {YEAR}.", m["vendor_text"]) is None


def test_vendor_text_guard():
    m = model.build(fleet(GLOBAL[:4], check_of(GLOBAL[:4])), cmi())
    vt = m["vendor_text"]
    assert "not in the final vendor data" in vendor_problem("DSM-Firmenich remains a major producer.", vt)
    assert "not in the final vendor data" in vendor_problem("BASF SE supplies premixes.", vt)
    assert "23.0%" in vendor_problem("CSPC Pharmaceutical Group holds 23.0% of the market.", vt)
    assert vendor_problem("CSPC Pharmaceutical Group holds 22.0% and Northeast Pharmaceutical Group 12.0%.", vt) is None
    # "recent" deals: the year must be in the evidence (entity moves), else the claim goes
    assert vendor_problem("Northeast Pharmaceutical Group recently expanded capacity.", vt) == \
        "undated 'recent' deal or move"
    assert "2019" in vendor_problem("Northeast Pharmaceutical Group made a recent acquisition in 2019.", vt)
    # a descriptive company name does not block ordinary text ('Egyptian shrimp' vs 'Egyptian Shrimp Company')
    vt2 = {**vt, "blocked": vt["blocked"] + ["Egyptian Shrimp Company"]}
    assert vendor_problem("Egyptian shrimp farms supply Cairo.", vt2) is None
    assert vendor_problem("Egyptian Shrimp Company supplies Cairo.", vt2)
    dropped = []
    out = vendor_clean("NEPG exports to Egypt; recent moves include a new plant. Northeast Pharmaceutical Group "
                       "recently opened a plant in 2024.", vt, dropped)
    assert out == "NEPG exports to Egypt. Northeast Pharmaceutical Group opened a plant in 2024."
    assert len(dropped) == 1


def test_the_mea_paragraph_names_the_mea_leader():
    from AiAgentsFleet.cmi_report import prose
    m = model.build(fleet(GLOBAL[:3], check_of(GLOBAL[:3])), cmi())
    assert m["vendor_mea_leader"] == "Northeast Pharmaceutical Group"      # 'Leading' MEA presence
    slots = {"mea_share_para": "Chinese producers supply most MEA demand through distributors."}
    assert prose.mea_leader_named(slots, m)
    assert slots["mea_share_para"].startswith("Northeast Pharmaceutical Group is the leading vendor")
    assert prose.mea_leader_named(slots, m) is None                        # named now: nothing to add
    facts = prose.vendor_facts(m)
    assert facts["mea_leader"] == "Northeast Pharmaceutical Group" and "vendor_share_global_pct" not in facts
    assert "DSM-Firmenich" not in json.dumps(facts) and "BASF" not in json.dumps(facts)


# --------------------------------------------------------------------------- #
# 4. agents: never jittered, checked and re-asked once
# --------------------------------------------------------------------------- #
def test_vendor_data_is_never_randomised():
    from AiAgentsFleet.cmi_report.groups import GROUPS
    from AiAgentsFleet.cmi_report.run_data import _PARTIAL_RANDOMIZE
    assert "vendors" not in _PARTIAL_RANDOMIZE
    g = next(x for x in GROUPS if x["key"] == "vendors")
    assert g["randomize"] is False
    assert "value_chain_level" in g["system"].lower().replace("-", "_") or "VALUE-CHAIN LEVEL" in g["system"]
    assert '"quote"' in g["schema"] and '"positioning"' in g["schema"]
    from AiAgentsFleet.field_groups import FIELD_GROUPS
    cl = next(x for x in FIELD_GROUPS if x["key"] == "competitive_landscape")
    assert "share_quote" in cl["schema_hint"] and "one country's producers dominate" in cl["system"]


def test_fleet_shares_are_reasked_once_when_a_quote_fails():
    bad = {"companies": [row("DSM-Firmenich", 23, "DSM-Firmenich leads vitamin C with 23% in the market")]}
    good = {"companies": [row("DSM-Firmenich", 23), GLOBAL[1]]}
    notes, log = [], []
    out, chk = entities.check_shares_with_reask(bad, SNIPS, lambda n: notes.append(n) or copy.deepcopy(good), log,
                                                MARKET)
    assert len(notes) == 1 and "copied EXACTLY" in notes[0] and "DSM-Firmenich" in notes[0]
    assert out == good and chk["reasked"] and chk["first_rejected"][0]["vendor"] == "DSM-Firmenich"
    assert [v["vendor"] for v in chk["verified"]] == ["CSPC Pharmaceutical Group"] and chk["year"] == YEAR
    # nothing to fix: no re-ask
    _, chk2 = entities.check_shares_with_reask(good, SNIPS, lambda n: 1 / 0, [], MARKET)
    assert chk2["reasked"] is False


def test_vendors_answer_is_reasked_once_and_recorded():
    first = {"mea_shares": [{"vendor": "BASF SE", "share_pct": 30, "quote": "BASF leads MEA vitamin C with 30%"}],
             "positioning": [{"vendor": "BASF SE", "tier": "Leader", "role": "x", "mea_presence": "Leading"}],
             "company_classification": [{"company": "BASF SE", "earns_defined_revenue": False}]}
    second = {"mea_shares": [{"vendor": "CSPC Pharmaceutical Group", "share_pct": None, "quote": ""}],
              "positioning": POSITIONING[:2], "company_classification": []}
    fields, notes = {"vendors": copy.deepcopy(first)}, []
    rec = entities.review_vendor_answer(fields, SNIPS, MARKET, lambda k, n: notes.append(n) or copy.deepcopy(second),
                                        [])
    assert rec["reasked"] and len(notes) == 1 and rec["after"] == []
    assert any("not found" in p for p in rec["problems"]) and any("does not earn" in p for p in rec["problems"])
    assert fields["vendors"] == second and rec["mea_shares"]["verified"] == []


class _LLM:
    """vendors group, then one entity call per company (and the thin-list re-ask)."""
    def __init__(self):
        self.calls, self.prompts = [], []

    def complete_json(self, system, user):
        self.calls.append(system)
        self.prompts.append((system, user))
        if system == entities.THIN_SYSTEM:
            return {"companies": [{"vendor": "Zhejiang Tianli", "role": "Makes vitamin C"}]}
        if system == entities.ENTITY_SYSTEM:
            name = user.split("COMPANY: ", 1)[1].split("\n", 1)[0]
            q = f"{name} is a vitamin C producer headquartered in Shijiazhuang, China."
            return {"name": name, "hq_country": "China", "hq_quote": q, "parent_group": name, "ownership": "independent",
                    "business_type": "producer", "business_quote": q, "product_quote": q,
                    "role": "Makes vitamin C", "earns_defined_revenue": True, "mainly_domestic": False, "moves": []}
        return {"value_chain": [], "mea_shares": [], "positioning": POSITIONING[:3], "capabilities": {},
                "capability_dimensions": [], "dashboard": [], "company_classification": []}


def test_run_data_vendors_runs_the_checks_and_the_entity_calls(monkeypatch, tmp_path):
    from AiAgentsFleet.cmi_report import run_data
    d = tmp_path / "output" / "vc"
    d.mkdir(parents=True)
    (d / "whitepaper_fields.json").write_text(json.dumps(fleet(GLOBAL[:3])), encoding="utf-8")
    (d / "cmi_fields.json").write_text(json.dumps(seg_cmi()), encoding="utf-8")
    llm = _LLM()
    monkeypatch.setattr(entities.time, "sleep", lambda s: None)
    monkeypatch.setattr(run_data, "FLEET", tmp_path)
    monkeypatch.setattr(run_data.GenConfig, "load", staticmethod(lambda root: None))
    monkeypatch.setattr(run_data, "GenLLM", lambda cfg, cache_path=None: llm)
    monkeypatch.setattr(run_data, "search", lambda *a, **k: [snip("Vitamin C producers in China")])
    monkeypatch.setattr(run_data, "entity_search", lambda qs: [snip(
        f"{qs[0].replace(' company headquarters', '')} is a vitamin C producer headquartered in Shijiazhuang, China.")])
    run_data._SHOWN.clear()
    run_data.run("vc", {"vendors"})
    out = json.loads((d / "cmi_fields.json").read_text(encoding="utf-8"))["fields"]
    assert out["vendor_review"]["reasked"] is False and out["vendor_review"]["problems"] == []
    recs = out["entities"]["records"]
    assert set(recs) == {"DSM-Firmenich", "CSPC Pharmaceutical Group", "Shandong Luwei Pharmaceutical",
                         "Northeast Pharmaceutical Group", "Zhejiang Tianli"}   # + the re-ask's new company
    assert all(r["verified"] and r["product_verified"] and r["hq_country"] == "China" for r in recs.values())
    assert llm.calls.count(entities.ENTITY_SYSTEM) == 5
    # 4 confirmed vendors < 5: the vendor list was asked for once more, on the extra searches
    thin = out["vendor_review"]["thin_reask"]
    assert thin["reasked"] and len(thin["included_before"]) == 4 and thin["country"] == "China"
    assert thin["new_vendors"] == ["Zhejiang Tianli"] and len(thin["included_after"]) == 5
    assert "{market} key players list" in thin["queries"] and "{market} China manufacturers producers companies" in \
        thin["queries"]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _vendor_section(m):
    """Builds report elements 332-341 (sections 7.2 / 7.3) with the real template."""
    from pathlib import Path

    from docx.oxml.ns import qn

    from AiAgentsFleet.cmi_report import build
    tpl = Path(__file__).resolve().parents[2] / "templates" / "cmi_whitepaper_template.docx"
    b = build.Builder(tpl)
    slots = {"global_share_para": "Global text.", "mea_share_para": "MEA text."}
    charts = {"vendor_global": None, "vendor_mea": None}
    b.image = lambda i, path: b.out.append(b.el(i))                       # no chart files needed
    for i in range(332, 342):
        build.vendor_shares(b, i, m, slots.get, "Vitamin C", charts, lambda t: t or "")
    texts, tables, header = [], 0, []
    for el in b.out:
        if el.tag == qn("w:tbl"):
            tables += 1
            header = ["".join(t.text or "" for t in tc.iter(qn("w:t")))
                      for tc in el.findall(qn("w:tr"))[0].findall(qn("w:tc"))]
        else:
            texts.append("".join(t.text or "" for t in el.iter(qn("w:t"))))
    return {"texts": texts, "tables": tables, "header": header}

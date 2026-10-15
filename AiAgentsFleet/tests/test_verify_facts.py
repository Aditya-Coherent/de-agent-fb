"""PLAN-06: every regulator, requirement, milestone and vendor move is checked
claim by claim (search + a separate judge, both mocked here); unconfirmed
facts are left out, backed corrections replace the original, and nothing
carries a "[to verify]" marker."""
import copy
import json
from pathlib import Path

import pytest

from AiAgentsFleet.cmi_report import model, verify_facts as vf
from AiAgentsFleet.cmi_report.guard import unverified_fact

FIX = Path(__file__).parent / "fixtures"
TRAPS = json.loads((FIX / "fact_traps.json").read_text(encoding="utf-8"))["traps"]
REAL = Path(r"C:\Users\rajeev.ranjan\Desktop\Desktop\Desktop\de-agent-fb\AiAgentsFleet\output")

SFDA_SNIP = {"text": "The Saudi Food and Drug Authority (SFDA) registers food supplements, including vitamin C "
                     "products, before they are placed on the Saudi market.", "url": "https://sfda.gov.sa/x",
             "domain": "sfda.gov.sa"}
REQ_SNIP = {"text": "In Saudi Arabia, vitamin supplements need pre-market registration with GMP compliance and "
                    "Arabic labelling.", "url": "https://sfda.gov.sa/y", "domain": "sfda.gov.sa"}


class FakeLLM:
    """A judge that answers from a {claim id or text fragment: answer} map, and
    a GenLLM-like cache so claim-level caching can be checked."""

    def __init__(self, answers, fail=False):
        self.answers, self.fail, self.calls, self.store = answers, fail, [], {}

    def complete_json(self, system, user):
        assert system == vf.JUDGE_SYSTEM           # the judge has its own prompt, not the writer's
        self.calls.append(user)
        if self.fail:
            raise RuntimeError("provider down")
        claim = user.split("CLAIM", 1)[1].split("\n", 1)[0]
        return next((a for k, a in self.answers.items() if k in claim), {"verdict": "not_found"})

    def _cache_get(self, k):
        return self.store.get(k)

    def _cache_put(self, k, v):
        self.store[k] = v


def search_with(snippets):
    calls = []

    def fn(qs):
        calls.append(qs)
        return list(snippets)
    fn.calls = calls
    return fn


def fields(snapshot=None, milestones=None, dashboard=None, watchlist=None):
    return copy.deepcopy({"regulation": {"applicable": True, "category_terms": ["food supplement"],
                                         "snapshot": snapshot or [], "milestones": milestones or [],
                                         "scorecard": {}, "watchlist": watchlist or []},
                          "vendors": {"dashboard": dashboard or []}})


def run(f, answers, snippets, market="Global Vitamin C Market", **kw):
    llm = FakeLLM(answers, **kw)
    res = vf.verify(vf.claims(f), market, llm, search_with(snippets))
    f = {**f, "fact_checks": {"version": vf.VERSION, "results": res}}
    return f, vf.apply(f), llm


SAUDI = {"country": "Saudi Arabia", "regulator": "Saudi Food and Drug Authority (SFDA)",
         "requirements": "Vitamin supplements need pre-market registration with GMP compliance and Arabic labelling."}


# --------------------------------------------------------------------------- #
# each verdict branch
# --------------------------------------------------------------------------- #
def test_supported_is_printed_without_marker():
    f, a, _ = run(fields([SAUDI]), {
        "Saudi Food and Drug": {"verdict": "supported", "evidence": 1,
                                "quote": "The Saudi Food and Drug Authority (SFDA) registers food supplements"},
        "pre-market": {"verdict": "supported", "evidence": 2,
                       "quote": "vitamin supplements need pre-market registration with GMP compliance and Arabic labelling"},
    }, [SFDA_SNIP, REQ_SNIP])
    row = a["regulation"]["snapshot"][0]
    assert row["regulator"] == SAUDI["regulator"] and row["requirements"] == SAUDI["requirements"]
    assert "[to verify]" not in json.dumps(a)
    r = {x["id"]: x for x in f["fact_checks"]["results"]}["regulator:Saudi Arabia"]
    assert r["status"] == "supported" and r["url"] == "https://sfda.gov.sa/x"   # the URL comes from the snippet


def test_not_found_regulator_row_is_left_out():
    # user decision 2026-10-15: a country whose authority is not confirmed is left out of the table
    _, a, _ = run(fields([SAUDI]), {}, [SFDA_SNIP])
    assert a["regulation"]["snapshot"] == [] and "Saudi Arabia" in a["regulation"]["snapshot_unconfirmed"]
    assert "SFDA" not in json.dumps(a["regulation"]["snapshot"])


def test_only_supported_requirement_sentences_are_kept():
    s = dict(SAUDI, requirements="Vitamin supplements need pre-market registration with GMP compliance and Arabic "
                                 "labelling. Approval takes under 30 days.")
    _, a, _ = run(fields([s]), {"pre-market": {"verdict": "supported", "evidence": 1, "quote":
                  "vitamin supplements need pre-market registration with GMP compliance and Arabic labelling"},
                  "Saudi Food and Drug": {"verdict": "supported", "evidence": 1,
                                          "quote": "The Saudi Food and Drug Authority (SFDA) registers food supplements"}},
                  [REQ_SNIP, SFDA_SNIP])
    assert a["regulation"]["snapshot"][0]["requirements"] == SAUDI["requirements"]


@pytest.mark.parametrize("verdict", ["renamed", "contradicted"])
def test_corrected_value_replaces_the_original(verdict):
    t = next(x for x in TRAPS if x["name"] == "Israel ministry name")
    _, a, _ = run(fields([t["snapshot"]]), {"Rural Development": {**t["honest_judge"], "verdict": verdict}},
                  t["evidence"], t["market"])
    assert a["regulation"]["snapshot"][0]["regulator"] == "Ministry of Agriculture and Food Security"


def test_contradicted_without_correction_is_omitted():
    ms = [{"date": "2019", "jurisdiction": "UAE", "event": "Framework for intensive shrimp farm licensing launched"}]
    _, a, _ = run(fields(milestones=ms), {"Framework": {"verdict": "contradicted", "corrected": "", "evidence": 1,
                                                        "quote": "The UAE has no shrimp farm licensing framework yet"}},
                  [{"text": "The UAE has no shrimp farm licensing framework yet, officials said.", "url": "u",
                    "domain": "d"}])
    assert a["regulation"]["milestones"] == []


def test_a_quote_not_in_the_evidence_is_not_found():
    _, a, _ = run(fields([SAUDI]), {"Saudi Food and Drug": {
        "verdict": "supported", "evidence": 1, "quote": "SFDA is the competent authority for all supplements"}},
        [SFDA_SNIP])
    assert a["regulation"]["snapshot"] == [] and "Saudi Arabia" in a["regulation"]["snapshot_unconfirmed"]


def test_a_correction_not_stated_in_the_quote_is_not_printed():
    t = next(x for x in TRAPS if x["name"] == "Turkey TITCK")
    _, a, _ = run(fields([t["snapshot"]]), {"TITCK": {**t["honest_judge"], "corrected": "Ministry of Health"}},
                  t["evidence"], t["market"])
    assert a["regulation"]["snapshot"] == [] and "TITCK" not in _printed(a)


# --------------------------------------------------------------------------- #
# the known traps (FINDINGS B1, B8, C10, E4, F4)
# --------------------------------------------------------------------------- #
def _trap_fields(t):
    f = fields([t["snapshot"]]) if "snapshot" in t else fields(milestones=[t["milestone"]])
    if "Shrimp" in t["market"]:          # the category the regulation agent would name for this market
        f["regulation"]["category_terms"] = ["aquaculture", "fish farming"]
    return f


def _printed(a):
    return json.dumps({"snapshot": [{k: r[k] for k in ("regulator", "requirements")} for r in a["regulation"]["snapshot"]],
                       "milestones": [{k: m[k] for k in ("date", "event")} for m in a["regulation"]["milestones"]]})


@pytest.mark.parametrize("t", TRAPS, ids=[t["name"] for t in TRAPS])
def test_trap_with_honest_judge_prints_the_correction_or_nothing(t):
    key = (t.get("snapshot") or {}).get("regulator") or t["milestone"]["event"]
    _, a, _ = run(_trap_fields(t), {key[:20]: t["honest_judge"]}, t["evidence"], t["market"])
    out = _printed(a)
    assert t["must_not_print"] not in out
    if t["expected_printed"]:
        assert t["expected_printed"] in out
    elif "milestone" in t:
        assert a["regulation"]["milestones"] == []
    if t.get("expected_year"):
        assert a["regulation"]["milestones"][0]["date"] == t["expected_year"]


@pytest.mark.parametrize("t", TRAPS, ids=[t["name"] for t in TRAPS])
def test_trap_rubber_stamped_by_a_lazy_judge_is_still_not_printed(t):
    key = (t.get("snapshot") or {}).get("regulator") or t["milestone"]["event"]
    f, a, _ = run(_trap_fields(t), {key[:20]: t["lazy_judge"]}, t["evidence"], t["market"])
    assert t["must_not_print"] not in _printed(a)
    assert f["fact_checks"]["results"][0]["status"] == "omitted"


# --------------------------------------------------------------------------- #
# timeline, vendor moves, watchlist
# --------------------------------------------------------------------------- #
MS = [{"date": str(2015 + i), "jurisdiction": j, "event": f"{j} supplement registration rules entered into force"}
      for i, j in enumerate(["Saudi Arabia", "UAE", "Egypt", "Kenya", "Morocco"])]


def _ms_answers(n):
    return {MS[i]["jurisdiction"] + " supplement": {
        "verdict": "supported", "evidence": i + 1,
        "quote": f"In {MS[i]['date']}, {MS[i]['jurisdiction']} supplement registration rules entered into force"}
        for i in range(n)}


def _ms_snips():
    return [{"text": f"In {m['date']}, {m['jurisdiction']} supplement registration rules entered into force.",
             "url": f"https://x/{k}", "domain": "x"} for k, m in enumerate(MS)]


def test_fewer_than_three_confirmed_milestones_drop_the_timeline():
    f, a, _ = run(fields([SAUDI, dict(SAUDI, country="UAE")], milestones=MS), _ms_answers(2), _ms_snips())
    assert len(a["regulation"]["milestones"]) == 2
    sec = model.sections("Global Vitamin C Market", [], [], a)
    assert sec["milestones"]["show"] is False
    assert any("timeline" in w for w in vf.review(f)["warnings"])


def test_three_confirmed_milestones_keep_the_timeline():
    _, a, _ = run(fields([SAUDI, dict(SAUDI, country="UAE")], milestones=MS), _ms_answers(3), _ms_snips())
    assert model.sections("Global Vitamin C Market", [], [], a)["milestones"]["show"] is True
    assert all(m["verified"] for m in a["regulation"]["milestones"])


def test_unconfirmed_vendor_move_is_dropped_and_confirmed_one_kept():
    dash = [{"vendor": "CSPC", "hq": "China", "core_strength": "Scale", "mea_footprint": "Distributor in Egypt since 2020."},
            {"vendor": "Land O'Lakes", "hq": "United States", "core_strength": "Feed",
             "mea_footprint": "Recent deal in Saudi Arabia."}]
    snip = {"text": "CSPC has worked with a distributor in Egypt since 2020 for vitamin C.", "url": "u", "domain": "d"}
    _, a, _ = run(fields(dashboard=dash), {"CSPC": {"verdict": "supported", "evidence": 1,
                                                    "quote": "CSPC has worked with a distributor in Egypt since 2020"}},
                  [snip])
    assert [d["vendor"] for d in a["vendors"]["dashboard"]] == ["CSPC"]


def test_watchlist_row_naming_an_unconfirmed_law_is_dropped():
    wl = [{"theme": "AMA harmonisation", "what_to_watch": "Ratification of the African Medicines Agency Treaty",
           "implication": "Faster multi-country registration."},
          {"theme": "Claim scrutiny", "what_to_watch": "Tighter rules on immunity claims",
           "implication": "Labels may need changes."}]
    _, a, _ = run(fields(watchlist=wl), {}, [SFDA_SNIP])
    assert [w["theme"] for w in a["regulation"]["watchlist"]] == ["Claim scrutiny"]


# --------------------------------------------------------------------------- #
# running: cap, cache, failures, outputs
# --------------------------------------------------------------------------- #
def test_claims_are_ordered_by_visibility_and_capped():
    snap = [dict(SAUDI, country=c) for c in ["Saudi Arabia", "UAE", "Egypt"]]
    dash = [{"vendor": "V", "mea_footprint": "Office in Dubai."}]
    cl = vf.claims(fields(snap, MS, dash))
    assert [c["kind"] for c in cl] == ["regulator"] * 3 + ["policy"] * 3 + ["milestone"] * 5 + ["vendor_move"]
    res = vf.verify(cl, "Global Vitamin C Market", FakeLLM({}), search_with([SFDA_SNIP]), cap=4)
    assert [r["status"] for r in res].count("not_checked") == len(cl) - 4
    assert all(r["status"] == "not_checked" for r in res[4:])


def test_results_are_cached_by_claim_text():
    f = fields([SAUDI])
    llm = FakeLLM({"Saudi Food and Drug": {"verdict": "supported", "evidence": 1,
                                           "quote": "The Saudi Food and Drug Authority (SFDA) registers food supplements"}})
    s1 = search_with([SFDA_SNIP])
    vf.verify(vf.claims(f), "Global Vitamin C Market", llm, s1)
    n_llm, n_search = len(llm.calls), len(s1.calls)
    s2 = search_with([SFDA_SNIP])
    res = vf.verify(vf.claims(f), "Global Vitamin C Market", llm, s2)
    assert len(llm.calls) == n_llm and s2.calls == []           # no new search, no new judge call
    assert res[0]["status"] == "supported" and res[0]["cached"]
    assert n_search == 2 and len(s1.calls[0]) <= 2               # 1-2 targeted searches per claim


def test_judge_failure_is_omitted_and_a_blocker():
    f, a, _ = run(fields([SAUDI]), {}, [SFDA_SNIP], fail=True)
    assert a["regulation"]["snapshot"] == [] and "Saudi Arabia" in a["regulation"]["snapshot_unconfirmed"]
    assert any("could not be checked" in b for b in vf.review(f)["blockers"])


def test_no_checks_at_all_prints_nothing_unconfirmed_and_blocks():
    f = fields([SAUDI], MS)
    a = vf.apply(f)
    assert a["regulation"]["snapshot"] == [] and "Saudi Arabia" in a["regulation"]["snapshot_unconfirmed"] and a["regulation"]["milestones"] == []
    assert vf.review(f)["blockers"]


def test_changed_claim_after_the_check_is_left_out():
    f, _, _ = run(fields([SAUDI]), {"Saudi Food and Drug": {
        "verdict": "supported", "evidence": 1, "quote": "The Saudi Food and Drug Authority (SFDA) registers food supplements"}},
        [SFDA_SNIP])
    f["regulation"]["snapshot"][0]["regulator"] = "Ministry of Health"
    assert vf.apply(f)["regulation"]["snapshot"] == []
    assert not vf.current(f)


def test_ensure_writes_fact_checks_json_listing_everything_printed(tmp_path):
    f = fields([SAUDI, dict(SAUDI, country="UAE", regulator="Ministry of Health and Prevention (MOHAP)")], MS)
    llm = FakeLLM({"Saudi Food and Drug": {"verdict": "supported", "evidence": 1,
                                           "quote": "The Saudi Food and Drug Authority (SFDA) registers food supplements"},
                   **_ms_answers(3)})
    assert vf.ensure(tmp_path, "Global Vitamin C Market", f, llm, search_with([SFDA_SNIP] + _ms_snips()), log=None)
    out = json.loads((tmp_path / "fact_checks.json").read_text(encoding="utf-8"))
    printed = out["printed"]
    assert [r["regulator"] for r in printed["regulators"]] == [SAUDI["regulator"]]
    assert len(printed["milestones"]) == 3
    assert {r["id"] for r in out["results"]} == {c["id"] for c in vf.claims(f)}
    assert set(out["review"]) >= {"blockers", "warnings", "verified_facts", "corrections"}
    # unchanged data: nothing is re-checked
    n = len(llm.calls)
    assert vf.ensure(tmp_path, "Global Vitamin C Market", f, llm, search_with([]), log=None) is False
    assert len(llm.calls) == n


def test_judge_uses_its_own_model_when_configured(monkeypatch, tmp_path):
    from me_engine.generation.config import GenConfig
    cfg = GenConfig(api_key="k", base_url="http://x", model="writer", timeout=1, neon_url=None, enable_web=False,
                    max_loop_iters=1, min_loop_iters=1, reward_threshold=0.9, per_region_shares=True)
    monkeypatch.setenv("DEAGENT_VERIFY_MODEL", "judge-model")
    assert vf.judge_llm(cfg, None)._config.model == "judge-model"
    monkeypatch.delenv("DEAGENT_VERIFY_MODEL")
    assert vf.judge_llm(cfg, None)._config.model == "writer"


# --------------------------------------------------------------------------- #
# the prose guard: unverified_fact
# --------------------------------------------------------------------------- #
FACTS = ["Saudi Arabia: Saudi Food and Drug Authority (SFDA)", "Turkey: Ministry of Agriculture and Forestry",
         "2021 African Union: Treaty establishing the African Medicines Agency entered into force"]


@pytest.mark.parametrize("s", [
    "Turkey's TITCK registers vitamin C supplements.",
    "Supplements in Turkey are approved by the Turkish Medicines and Medical Devices Agency.",
    "South Africa's 2023 Animal Feed Act revision tightened additive approvals.",
    "Saudi Vision 2030 programmes support local supplement production.",
    "The African Medicines Agency began operating in 2023 under the AMA Treaty of 2023.",
    "Producers must comply with the Fertilizers Act 36 of 1947.",
    "Kenya's guidelines were expected in 2025 [to verify].",
])
def test_unverified_fact_drops(s):
    assert unverified_fact(s, FACTS)


@pytest.mark.parametrize("s", [
    "The Saudi Food and Drug Authority (SFDA) registers supplements before sale.",
    "In Turkey, the Ministry of Agriculture and Forestry approves food supplements.",
    "CMI estimates the GCC market at $17.4M in 2026E, growing to 2033F.",
    "Registration rules are tightening across the region by 2030.",
    "Demand from feed mills grows fastest in Egypt.",
    "DSM-Firmenich's registration dossiers and FAO-guided mycotoxin regulation raise the bar for imports.",
])
def test_unverified_fact_keeps(s):
    assert unverified_fact(s, FACTS) is None


def test_no_fact_list_means_no_check():
    assert unverified_fact("Turkey's TITCK registers vitamin C supplements.", None) is None


def test_prose_guard_drops_unverified_regulator_sentence():
    from AiAgentsFleet.cmi_report import prose
    from AiAgentsFleet.cmi_report.guard import ClaimGuard, NumberGuard
    cg = ClaimGuard([])
    cg.verified_facts = FACTS
    dropped = []
    out = prose._guard("Regulation shapes entry. Turkey's TITCK registers vitamin C supplements.",
                       NumberGuard({}), None, dropped, [])
    assert "TITCK" in out                                       # no fact list: nothing checked
    out = prose._guard("Regulation shapes entry. Turkey's TITCK registers vitamin C supplements.",
                       NumberGuard({}), cg, dropped, [])
    assert out == "Regulation shapes entry." and any("unverified fact" in d for d in dropped)


def test_no_to_verify_marker_left_in_the_code_paths():
    root = Path(vf.__file__).parent
    assert "[to verify]" not in (root / "charts.py").read_text(encoding="utf-8")
    assert "' [to verify]'" not in (root / "prose.py").read_text(encoding="utf-8")
    src = (root / "build.py").read_text(encoding="utf-8")
    assert '" [to verify]"' not in src
    assert "never write '[to verify]'" in (root / "prose.py").read_text(encoding="utf-8").lower()


def test_regulation_prompt_asks_for_instrument_authority_and_entry_into_force():
    from AiAgentsFleet.cmi_report.groups import GROUPS
    g = next(x for x in GROUPS if x["key"] == "regulation")
    assert "ENTERED INTO FORCE" in g["system"] and "product category" in g["system"].lower()
    assert '"instrument"' in g["schema"] and '"verified"' not in g["schema"]


# --------------------------------------------------------------------------- #
# real report data (read-only): without checks nothing unconfirmed is printed
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not (REAL / "global-vitamin-c-market" / "cmi_fields.json").exists(),
                    reason="vitamin C output not available")
def test_real_vitamin_c_data_prints_no_unchecked_regulator():
    f = json.loads((REAL / "global-vitamin-c-market" / "cmi_fields.json").read_text(encoding="utf-8"))["fields"]
    f = copy.deepcopy(f)
    f.pop("fact_checks", None)
    cl = vf.claims(f)
    assert 0 < len(cl) <= 60 and cl[0]["kind"] == "regulator"
    a = vf.apply(f)
    assert all(r["regulator"] == vf.NOT_CONFIRMED for r in a["regulation"]["snapshot"])
    assert "TITCK" not in json.dumps(a["regulation"]["snapshot"])


# --------------------------------------------------------------------------- #
# the model: m["fact_review"] and the applied data
# --------------------------------------------------------------------------- #
def test_model_carries_fact_review_and_applies_the_checks():
    from AiAgentsFleet.tests.test_segments import cmi as seg_cmi, fleet as seg_fleet
    c = seg_cmi()
    t = next(x for x in TRAPS if x["name"] == "Turkey TITCK")
    f, _, _ = run(fields([t["snapshot"], SAUDI], milestones=MS), {"TITCK": t["honest_judge"]}, t["evidence"],
                  t["market"])
    c["fields"] = {**c["fields"], **f}
    m = model.build(seg_fleet(), c)
    fr = m["fact_review"]
    assert fr["status"] == "checked" and fr["corrected"] == 1 and fr["blockers"] == []
    assert "Turkey: Ministry of Agriculture and Forestry" in fr["verified_facts"]
    assert any("Saudi Arabia" in w for w in fr["warnings"])          # SFDA not confirmed in this mock
    assert m["sections"]["milestones"]["show"] is False              # no milestone confirmed
    assert c["fields"]["regulation"]["snapshot"][0]["regulator"] == t["snapshot"]["regulator"]   # raw data kept

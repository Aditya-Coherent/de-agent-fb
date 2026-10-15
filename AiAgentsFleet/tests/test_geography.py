"""PLAN-03: country and region numbers are the agents' own -- no rescaling to a
plug, incoherent splits re-asked once (mocked LLM here), 'Rest of' members
built from data."""
from __future__ import annotations

import copy

from AiAgentsFleet.cmi_report import geo_check, model
from AiAgentsFleet.cmi_report.groups import DEFAULT_COUNTRIES, MEMBERS, misplaced, rest_members, validate_geography

GEO = {r: list(c) for r, c in DEFAULT_COUNTRIES.items()}


def cell(share, cagr=5.0):
    return {"share_pct_of_region": share, "cagr_2026_2033_pct": cagr}


def split(region, named, rest, cagr=5.0):
    """{country: cell} for the region's default countries, plus Rest of."""
    out = {c: cell(s, cagr + i * 0.3) for i, (c, s) in enumerate(zip(GEO[region], named))}
    if rest is not None:
        out[f"Rest of {region}"] = cell(rest, cagr)
    return out


def good_main():
    return {"North America": {"United States": cell(88, 5.1), "Canada": cell(12, 4.4)},
            "Europe": split("Europe", [25, 20, 18, 15, 10], 12),
            "Asia Pacific": split("Asia Pacific", [40, 20, 15, 10, 7], 8),
            "Latin America": split("Latin America", [45, 20, 10, 6, 5], 14)}


def good_mea():
    return {"middle_east_share_of_mea_pct": 55,
            "Middle East": split("Middle East", [30, 25, 20, 10], 15),
            "Africa": split("Africa", [30, 20, 15, 10, 5], 20)}


def fleet(regions=None):
    regions = regions or {"North America": 300, "Europe": 250, "Asia Pacific": 300, "Latin America": 90,
                          "Middle East & Africa": 60}
    series = {str(y): 1000 * 1.06 ** (y - 2025) for y in range(2025, 2034)}
    body = {"market_size": {"forecast_series_usd_mn": series},
            "regional": {"regions": {r: {"value_usd_mn": v, "cagr_2025_2030_pct": 6, "countries": []}
                                     for r, v in regions.items()}}}
    return {"market_name": "Test Market", "fields": body, "fields_raw": copy.deepcopy(body)}


def cmi(main=None, mea=None, review=None):
    return {"geography": {"countries": GEO}, "countries_main": main or good_main(),
            "countries_mea": mea or good_mea(), **({"geo_review": review} if review else {})}


# --------------------------------------------------------------------------- #
# no 88% rescale
# --------------------------------------------------------------------------- #
def test_rest_of_share_is_used_as_given():
    """95% named + 5% Rest of stays 5% (the old rule rescaled it to 12%)."""
    main = good_main()
    main["Europe"] = split("Europe", [30, 25, 20, 12, 8], 5)
    gg = model.geography(fleet(), cmi(main=main))
    eu = gg["countries"]["Europe"]
    total = sum(v["v26"] for v in eu.values())
    assert abs(eu["Rest of Europe"]["v26"] / total * 100 - 5.0) < 1e-9
    assert abs(eu["Germany"]["v26"] / total * 100 - 30.0) < 1e-9


def test_rest_of_shares_differ_by_region():
    gg = model.geography(fleet(), cmi())
    shares = {r: round(d[f"Rest of {r}"]["v26"] / sum(v["v26"] for v in d.values()) * 100, 1)
              for r, d in gg["countries"].items() if r != "North America"}
    assert shares == {"Europe": 12.0, "Asia Pacific": 8.0, "Latin America": 14.0, "Middle East": 15.0,
                      "Africa": 20.0}


def test_coherent_answers_have_no_blockers():
    m = model.geography(fleet(), cmi(review={"regional": {"problems": [], "reasked": False},
                                             "splits": {"countries_main": {"problems": [], "reasked": False},
                                                        "countries_mea": {"problems": [], "reasked": False}}}))
    rv = m["review"]
    assert rv["blockers"] == [] and rv["warnings"] == []
    assert {c["status"] for c in rv["splits"].values()} == {"ok"}
    assert rv["regional"]["status"] == "ok" and rv["regional"]["ratio"] == 1.0


# --------------------------------------------------------------------------- #
# check_split
# --------------------------------------------------------------------------- #
def test_sum_outside_99_101_is_flagged():
    p = geo_check.check_split("Europe", split("Europe", [40, 30, 25, 20, 10], 5), GEO["Europe"])
    assert [x["kind"] for x in p] == ["sum"]
    assert "130.0%" in p[0]["detail"]


def test_nonpositive_and_missing_rest_are_flagged():
    p = geo_check.check_split("Europe", split("Europe", [40, 30, 0, 20, 10], None), GEO["Europe"])
    assert {x["kind"] for x in p} == {"nonpositive", "rest_missing"}


def test_identical_rest_shares_across_regions_are_flagged():
    main = good_main()
    for r in ("Europe", "Asia Pacific", "Latin America"):
        named = [s for c, v in main[r].items() if not c.startswith("Rest of") for s in [v["share_pct_of_region"]]]
        k = 88 / sum(named)
        main[r] = split(r, [s * k for s in named], 12.0)
    probs = geo_check.check_splits(geo_check.splits_of({"countries_main": main, "countries_mea": good_mea()}), GEO)
    for r in ("Europe", "Asia Pacific", "Latin America"):
        assert [x["kind"] for x in probs[r]] == ["same_rest"]
    assert probs["Middle East"] == [] and probs["Africa"] == []


def test_rest_smaller_than_named_country_with_a_bigger_member_is_flagged():
    """Rest of Europe (5%) < Spain (8%), yet the desk research sizes the
    Netherlands -- a Rest-of member -- above Spain."""
    ans = split("Europe", [35, 25, 15, 12, 8], 5)
    p = geo_check.check_split("Europe", ans, GEO["Europe"], elsewhere={"Netherlands": 30, "Spain": 20},
                              region_value=250)
    assert [x["kind"] for x in p] == ["rest_too_small"]
    assert "Netherlands" in p[0]["detail"] and "Spain" in p[0]["detail"]
    # no member sized above Spain -> nothing to flag
    assert geo_check.check_split("Europe", ans, GEO["Europe"], elsewhere={"Netherlands": 10, "Spain": 20},
                                 region_value=250) == []


def test_north_america_same_cagr_is_flagged():
    ans = {"United States": cell(88, 5.04), "Canada": cell(12, 4.96)}
    assert [x["kind"] for x in geo_check.check_split("North America", ans, GEO["North America"])] == ["same_cagr"]


def test_regional_sum_and_convention_checked_against_global():
    reg = fleet({"North America": 2000, "Europe": 1500, "Asia Pacific": 900, "Latin America": 150,
                 "Middle East & Africa": 50})["fields_raw"]["regional"]
    reg["regions"]["North America"]["countries"] = [{"name": "Mexico", "value_usd_mn": 80}]
    p = geo_check.check_regions(reg, 1000)
    assert {x["kind"] for x in p} == {"sum", "convention"}
    assert "4.60x" in next(x["detail"] for x in p if x["kind"] == "sum")


# --------------------------------------------------------------------------- #
# re-ask (mocked LLM)
# --------------------------------------------------------------------------- #
def test_sum_of_130_triggers_one_reask_and_keeps_the_second_answer():
    bad = good_main()
    bad["Europe"] = split("Europe", [40, 30, 25, 20, 10], 5)        # 130%
    fields = {"countries_main": bad, "countries_mea": good_mea()}
    calls = []

    def ask(key, note):
        calls.append((key, note))
        return good_main()

    rec = geo_check.review_splits(fields, GEO, ask, [])
    assert [k for k, _ in calls] == ["countries_main"]               # once, and only the failing agent
    assert "130.0%" in calls[0][1] and "PREVIOUS ANSWER" in calls[0][1]
    assert rec["countries_main"]["reasked"] and rec["countries_main"]["after"] == []
    assert not rec["countries_mea"]["reasked"]
    assert fields["countries_main"] == good_main()
    assert any(c.startswith("Europe/Rest of Europe: 5") for c in rec["countries_main"]["changes"])


def test_still_failing_on_sum_only_is_normalised_and_logged():
    bad = good_main()
    bad["Europe"] = split("Europe", [40, 30, 25, 20, 10], 5)
    fields = {"countries_main": bad, "countries_mea": good_mea()}
    rec = geo_check.review_splits(fields, GEO, lambda k, n: copy.deepcopy(bad), [])
    rv = model.geography(fleet(), cmi(main=fields["countries_main"],
                                       review={"regional": {"problems": [], "reasked": False}, "splits": rec}))["review"]
    assert rv["splits"]["Europe"]["status"] == "normalised"
    assert rv["blockers"] == [] and any("normalised proportionally" in x for x in rv["normalised"])


def test_still_failing_for_another_reason_is_a_blocker():
    bad = good_main()
    bad["Europe"] = split("Europe", [40, 30, 0, 20, 10], None)       # zero share, no Rest of
    fields = {"countries_main": bad, "countries_mea": good_mea()}
    rec = geo_check.review_splits(fields, GEO, lambda k, n: copy.deepcopy(bad), [])
    rv = model.geography(fleet(), cmi(main=fields["countries_main"],
                                       review={"regional": {"problems": [], "reasked": False}, "splits": rec}))["review"]
    assert rv["splits"]["Europe"]["status"] == "blocker"
    assert any(b.startswith("Europe:") and "after one re-ask" in b for b in rv["blockers"])


def test_unreasked_failure_from_old_data_is_a_blocker():
    bad = good_main()
    bad["Europe"] = split("Europe", [40, 30, 25, 20, 10], None)
    rv = model.geography(fleet(), cmi(main=bad))["review"]
    assert any(b.startswith("Europe:") and "not re-asked" in b for b in rv["blockers"])


def test_regional_sum_triggers_reask_and_the_model_uses_the_second_answer():
    f = fleet({"North America": 2000, "Europe": 1500, "Asia Pacific": 900, "Latin America": 150,
               "Middle East & Africa": 50})
    second = fleet({"North America": 330, "Europe": 260, "Asia Pacific": 300, "Latin America": 70,
                    "Middle East & Africa": 40})["fields"]["regional"]
    notes = []
    rec = geo_check.reask_regions(f["fields_raw"]["regional"], 1000, lambda n: notes.append(n) or second, [])
    assert rec["reasked"] and rec["after"] == [] and len(notes) == 1
    assert "$1,000.0M" in notes[0]
    gg = model.geography(f, cmi(review={"regional": rec, "splits": {
        "countries_main": {"problems": [], "reasked": False}, "countries_mea": {"problems": [], "reasked": False}}}))
    assert abs(gg["reg"]["North America"]["v26"] / sum(v["v26"] for v in gg["reg"].values()) - 0.33) < 1e-9
    assert gg["review"]["regional"]["status"] == "ok"
    assert gg["review"]["regional"]["first_problems"][0]["kind"] == "sum"


# --------------------------------------------------------------------------- #
# sanity review
# --------------------------------------------------------------------------- #
def test_sanity_flag_reasks_the_region_and_keeps_only_a_passing_split():
    fields = {"countries_main": good_main(), "countries_mea": good_mea()}
    gg = model.geography(fleet(), cmi())
    tbl = geo_check.table(gg["countries"], rest_members(GEO))
    flags = {"flags": [{"country": "Turkey", "region": "Middle East", "problem": "too small",
                        "reason": "Turkey produces far more than Israel"},
                       {"country": "Atlantis", "region": "Europe", "problem": "too large", "reason": "x"}]}
    fixed = good_mea()
    fixed["Middle East"] = split("Middle East", [25, 20, 32, 8], 15)  # Turkey 20 -> 32, still sums to 100
    rec = geo_check.resolve_flags(flags, tbl, fields, GEO, lambda k, n: fixed, [])
    tr = next(f for f in rec["flags"] if f["country"] == "Turkey")
    assert tr["share_after"] == 32 and tr["open"] is False and tr["outcome"] == "re-asked split kept"
    assert fields["countries_mea"]["Middle East"]["Turkey"]["share_pct_of_region"] == 32
    assert next(f for f in rec["flags"] if f.get("country") == "Atlantis")["open"] is False

    # a re-asked split that fails check_split is rejected and the flag stays open (a warning)
    fields = {"countries_main": good_main(), "countries_mea": good_mea()}
    broken = good_mea()
    broken["Middle East"] = split("Middle East", [25, 20, 60, 8], 15)
    rec = geo_check.resolve_flags(flags, tbl, fields, GEO, lambda k, n: broken, [])
    tr = next(f for f in rec["flags"] if f["country"] == "Turkey")
    assert tr["open"] is True and tr["outcome"].startswith("re-asked split rejected")
    assert fields["countries_mea"]["Middle East"]["Turkey"]["share_pct_of_region"] == 20
    rv = model.geography(fleet(), cmi(review={"sanity": rec}))["review"]
    assert any("Turkey" in w for w in rv["warnings"])


# --------------------------------------------------------------------------- #
# Rest-of members and the region convention
# --------------------------------------------------------------------------- #
def test_iran_is_in_rest_of_middle_east_when_not_named():
    rm = rest_members(GEO)
    assert {"Iran", "Iraq", "Jordan"} <= set(rm["Middle East"])
    assert not set(rm["Middle East"]) & set(GEO["Middle East"])
    assert set(rm["Middle East"]) | set(GEO["Middle East"]) == set(MEMBERS["Middle East"])
    assert "North America" not in rm
    named_iran = {**GEO, "Middle East": ["Saudi Arabia", "Iran", "Turkey", "Israel"]}
    assert "Iran" not in rest_members(named_iran)["Middle East"]


def test_model_exposes_rest_members_and_geo_review():
    """On a real fleet output (read only): m["rest_members"] is MEMBERS minus
    the named countries, and m["geo_review"] is recorded."""
    import json
    from pathlib import Path
    d = Path(__file__).resolve().parents[1] / "output" / "functional-poultry-feed-market"
    if not (d / "cmi_fields.json").exists():
        import pytest
        pytest.skip("no poultry feed output")
    m = model.build(json.loads((d / "whitepaper_fields.json").read_text(encoding="utf-8")),
                    json.loads((d / "cmi_fields.json").read_text(encoding="utf-8")))
    for r, members in m["rest_members"].items():
        assert set(members) == set(MEMBERS[r]) - set(m["geography"][r])
    assert {"regional", "splits", "sanity", "blockers", "warnings", "normalised"} <= set(m["geo_review"])


def test_country_schema_asks_for_rest_of_with_its_members():
    from AiAgentsFleet.cmi_report.groups import GROUPS, schema_for
    g = next(x for x in GROUPS if x["key"] == "countries_mea")
    s = schema_for(g, GEO)
    assert '"Rest of Middle East": {"share_pct_of_region"' in s and "Iran, Iraq, Jordan" in s
    assert "Rest of North America" not in schema_for(next(x for x in GROUPS if x["key"] == "countries_main"), GEO)


def test_region_convention_is_enforced():
    raw = {"Europe": [{"country": "Turkey", "why": "x"}, "Germany"], "Latin America": ["Brazil"],
           "Africa": ["Egypt"], "Middle East": ["Egypt", "Israel"]}
    bad = misplaced(raw)
    assert "Turkey is Middle East, not Europe (report convention)" in bad
    assert "Egypt is Africa, not Middle East (report convention)" in bad
    geo, _, notes = validate_geography(raw)
    assert "Turkey" not in geo["Europe"] and "Egypt" not in geo["Middle East"]
    assert any("Turkey is Middle East, not Europe" in n for n in notes)


# --------------------------------------------------------------------------- #
# follow-up: single-region balancing, region sanity rows, region convention
# --------------------------------------------------------------------------- #
FIRST = {"North America": 4200, "Europe": 3500, "Asia Pacific": 6800, "Latin America": 1500,
         "Middle East & Africa": 500}                                          # 0.81x a 20,281 global
DUMPED = {**FIRST, "Middle East & Africa": 4281}                               # gap put into MEA alone
SPREAD = {"North America": 5000, "Europe": 4300, "Asia Pacific": 8400, "Latin America": 1700,
          "Middle East & Africa": 881}


def regional(values):
    return fleet(values)["fields"]["regional"]


def test_balancing_flags_a_gap_dumped_into_one_region_only():
    p = geo_check.balancing(regional(FIRST), regional(DUMPED))
    assert [(x["region"], x["kind"]) for x in p] == [("Middle East & Africa", "balancing")]
    assert geo_check.balancing(regional(FIRST), regional(SPREAD)) == []
    # every region rescaled together (GenAI's 4.3x answer) is not single-region balancing
    assert geo_check.balancing(regional(FIRST), regional({r: v * 0.23 for r, v in FIRST.items()})) == []


def test_a_reask_that_dumps_the_gap_into_one_region_is_reasked_again():
    answers = [regional(DUMPED), regional(SPREAD)]
    notes = []
    rec = geo_check.reask_regions(regional(FIRST), 20281, lambda n: notes.append(n) or answers.pop(0), [])
    assert len(notes) == 2 and rec["reasked_balancing"]
    assert "inflating Middle East & Africa alone" in notes[1] and "spread across the regions" in notes[1]
    assert rec["balancing"][0]["region"] == "Middle East & Africa"
    assert rec["after"] == [] and geo_check._values(rec["answer"]) == SPREAD


def test_balancing_still_present_after_the_second_reask_is_a_blocker():
    f = fleet(FIRST)
    f["fields"]["market_size"]["forecast_series_usd_mn"] = {str(y): 20281 * 1.05 ** (y - 2025)
                                                              for y in range(2025, 2034)}
    rec = geo_check.reask_regions(regional(FIRST), 20281, lambda n: regional(DUMPED), [])
    assert [p["kind"] for p in rec["after"]] == ["balancing"]
    rv = model.geography(f, cmi(review={"regional": rec, "splits": {
        "countries_main": {"problems": [], "reasked": False}, "countries_mea": {"problems": [], "reasked": False}}}))
    assert rv["review"]["regional"]["status"] == "blocker"
    assert any("single-region balancing" in b for b in rv["review"]["blockers"])


def test_region_level_sanity_flag_triggers_a_regional_reask():
    f = fleet(DUMPED)
    gg = model.geography(f, cmi())
    tbl = geo_check.table(gg["countries"], rest_members(GEO), gg["reg"])
    assert tbl["Global regions"]["Middle East & Africa"]["share_of_global_pct"] == 21.1
    flags = {"flags": [{"country": "Middle East & Africa", "region": "Global regions", "problem": "too large",
                        "reason": "MEA is about a tenth of world poultry feed"}]}
    notes = []
    rec = geo_check.resolve_flags(flags, tbl, {"countries_main": good_main(), "countries_mea": good_mea()}, GEO,
                                  lambda k, n: None, [], regional=regional(DUMPED),
                                  ask_regional=lambda n: notes.append(n) or regional(SPREAD), global_value=20281,
                                  regional_first=regional(FIRST))
    assert len(notes) == 1 and "Middle East & Africa: too large" in notes[0]
    fl = rec["flags"][0]
    assert fl["outcome"] == "re-asked regions kept" and fl["open"] is False and fl["share_after"] == 4.3
    assert geo_check._values(rec["regional_answer"]) == SPREAD
    # an answer that is still single-region balancing against the first answer is rejected
    rec = geo_check.resolve_flags(flags, tbl, {"countries_main": good_main(), "countries_mea": good_mea()}, GEO,
                                  lambda k, n: None, [], regional=regional(DUMPED),
                                  ask_regional=lambda n: regional({**FIRST, "Middle East & Africa": 4000}),
                                  global_value=20281, regional_first=regional(FIRST))
    assert "regional_answer" not in rec and rec["flags"][0]["open"] is True
    assert "single-region balancing" in rec["flags"][0]["outcome"]


def test_mexico_in_north_america_after_the_reask_is_a_blocker():
    with_mexico = fleet()["fields"]["regional"]
    with_mexico["regions"]["North America"]["countries"] = [{"name": "Mexico", "value_usd_mn": 40}]
    rec = geo_check.reask_regions(with_mexico, 1000, lambda n: copy.deepcopy(with_mexico), [])
    assert rec["reasked"] and [p["kind"] for p in rec["after"]] == ["convention"]
    rv = model.geography(fleet(), cmi(review={"regional": rec, "splits": {
        "countries_main": {"problems": [], "reasked": False}, "countries_mea": {"problems": [], "reasked": False}}}))
    assert rv["review"]["regional"]["status"] == "blocker"
    assert any("Mexico" in b and "after one re-ask" in b for b in rv["review"]["blockers"])
    assert not any("Mexico" in w for w in rv["review"]["warnings"])


def test_fleet_regional_prompt_states_the_region_convention():
    from AiAgentsFleet.field_groups import FIELD_GROUPS
    sysmsg = next(g for g in FIELD_GROUPS if g["key"] == "regional")["system"]
    assert "United States and Canada ONLY" in sysmsg and "Mexico is Latin America" in sysmsg

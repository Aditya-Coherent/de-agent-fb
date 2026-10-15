"""Writes every text slot of the CMI whitepaper with the LLM -- one call per
chapter -- from the numeric model's pre-formatted facts, in the tone of the
template's own paragraph for that slot. Each sentence then passes the
NumberGuard and ClaimGuard (guard.py); failing sentences are dropped and
logged, and data-only fallbacks fill slots that come back empty.

Writes output/<slug>/cmi_prose.json.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import docx

from AiAgentsFleet.cmi_report import fmt
from AiAgentsFleet.cmi_report.guard import (ClaimGuard, NumberGuard, clean_text, drop_size_comparison,
                                            vendor_clean)
from AiAgentsFleet.cmi_report.model import Y0, Y1

PROSE_VERSION = 9      # bump when prompts or guards change, so saved text is rewritten
ALIASES = {"KSA": "Saudi Arabia", "United Arab Emirates": "UAE", "US": "United States", "U.S.": "United States",
           "UK": "United Kingdom", "MEA": "Middle East & Africa", "Middle East and Africa": "Middle East & Africa",
           "APAC": "Asia Pacific", "Asia-Pacific": "Asia Pacific", "LATAM": "Latin America"}

_SYSTEM = (
    "You are a senior analyst at Coherent Market Insights (CMI) writing one chapter of a market whitepaper. "
    "Match the tone, density and length of each slot's STYLE EXAMPLE (taken from a CMI drone-market "
    "whitepaper) but write ONLY about the market named, never about drones unless that is the market.\n"
    "Rules:\n"
    "1. Every number you write must appear in FACTS exactly as formatted there (e.g. '$4.10B', '15.0%', "
    "'+$1.78B'). Never compute, round differently, estimate or invent a number; no other years than those in "
    "FACTS.\n"
    "2. Comparative words (largest, fastest, smallest, leads, dominates, anchors) only as FACTS.rankings states "
    "them, in the year it states (a 'largest_in_2026' ranking is about 2026 values); when rankings say "
    "'(tied)', say they are tied.\n"
    "3. Qualitative specifics (named regulations, programmes, companies, crops, technologies, country "
    "dynamics) may come from EVIDENCE or well-established knowledge of this market; if unsure, stay general.\n"
    "4. Plain prose, no markdown, no URLs, no 'according to'. Use 'CMI estimates' for our figures.\n"
    "5. Refer to segments with the dimension names in FACTS.dimensions.\n"
    "6. Mention only countries, regions, segments and categories that exist in FACTS -- e.g. never a 'Rest of' "
    "group that FACTS does not list.\n"
    "7. No caveats about drafts, templates, data refreshes or publication, and no disclaimers.\n"
    "8. Paragraphs at most 90 words, bullets at most 40 words. A STYLE EXAMPLE's heading or numbering is not "
    "part of the text: return lists as JSON arrays of plain strings.\n"
    "9. No repetition: every slot must add something the other slots do not say. Do not restate the global "
    "or MEA headline size, forecast or CAGR outside the executive summary and the sizing paragraph, and do "
    "not reuse a sentence pattern across slots.\n"
    "10. Label statement types: market sizes, shares and growth rates are 'CMI estimates' (to 2026) or "
    "'forecasts' (2027 onward) -- say so at least once in every paragraph that uses them; scores and ratings "
    "are 'CMI assessments'. A real-world fact you are not certain is current (regulator names, law dates, "
    "company moves, project names) gets ' [to verify]' right after it; never invent companies, projects or "
    "partnerships.\n"
    "11. Avoid vague filler ('considerable headroom', 'anchors', 'a volume story') unless a figure from FACTS "
    "backs it in the same sentence.\n"
    "13. Write in UK English (programme, localisation, defence, organisation).\n"
    "15. Every money value carries its own year right after it ('$75.7M in 2026E', '$290M by 2033F') -- never "
    "rely on a year stated earlier in the sentence.\n"
    "16. The STYLE EXAMPLE shows tone and length only. Never reuse its phrases or sentence frames ('A common "
    "theme:', 'is a ... story', 'depends less on ... than on', 'the largest enabler and the largest swing "
    "factor', 'smallest region in the study'); write this market's own sentences.\n"
    "17. Name the subject explicitly: never open a slot with 'The region', 'Within the region', 'This market' "
    "or 'It'.\n"
    "18. A published third-party figure always names its publisher in the same sentence.\n"
    "14. Storyline: the first slot of each chapter opens by linking to the previous chapter's subject; vary "
    "sentence structure (never open several paragraphs with 'X is the largest... while Y is the fastest'); "
    "explain the REASONS behind rankings with concrete local detail (named crops, industries, programmes, "
    "export sectors).\n"
    "12. When FACTS.body_findings is given (executive summary, recommendations), summarise or build only on "
    "those findings -- never introduce a finding the body does not contain.\n"
    "Return ONLY JSON with exactly the SLOTS keys."
)


# --------------------------------------------------------------------------- #
# facts
# --------------------------------------------------------------------------- #
def _nd(d: dict) -> dict:
    return {f"{Y0}E": fmt.usd(d["v26"]), f"{Y1}F": fmt.usd(d["v33"]), "CAGR": fmt.pct(d["cagr"]),
            "incremental": fmt.incr(d["incr"])}


def _rank(d: dict) -> dict:
    """Rankings register for one peer group: each entry names its metric and
    year, and a tie at printed precision is stated as a tie."""
    def pick(key: str, best: bool) -> str:
        order = sorted(d, key=lambda k: d[k][key], reverse=best)
        show = (lambda v: fmt.pct(v)) if key == "cagr" else (lambda v: fmt.usd(v))
        tied = [k for k in order if show(d[k][key]) == show(d[order[0]][key])]
        return order[0] if len(tied) == 1 else " and ".join(tied) + " (tied)"
    if len(d) < 2:
        return {}
    return {f"largest_in_{Y0}": pick("v26", True), f"largest_in_{Y1}": pick("v33", True),
            f"smallest_in_{Y0}": pick("v26", False),
            "fastest_growing": pick("cagr", True), "slowest_growing": pick("cagr", False),
            "largest_absolute_opportunity": pick("incr", True)}


def _block(d: dict, parent: dict | None = None) -> dict:
    out = {k: _nd(v) for k, v in d.items()}
    if parent:
        for k, v in d.items():
            out[k][f"share_of_parent_{Y0}"] = fmt.pct(v["v26"] / parent["v26"] * 100)
    return {"values": out, "rankings": _rank(d)}


def _segs(m: dict, geo: str) -> dict:
    """The geography's segment facts -- only for dimensions this report shows."""
    s, sec = m["segments"][geo], m["sections"]
    return {m["dimensions"][d]["plural"]: _block(s[d]) for d in ("type", "demand") if sec[d]["show"] and s[d]}


def base_facts(m: dict) -> dict:
    g, mea = m["global"], m["regions"]["Middle East & Africa"]
    return {
        "market": m["market"], "years": {"base": Y0 - 1, "estimate": f"{Y0}E", "forecast": f"{Y1}F"},
        "dimensions": {"type": m["dimensions"]["type"]["plural"], "demand": m["dimensions"]["demand"]["plural"]},
        "global": {**_nd(g), f"{Y0 - 1}": fmt.usd(g["v25"])},
        "mea": {**_nd(mea), f"{Y0 - 1}": fmt.usd(mea["v25"]),
                f"share_of_global_{Y0}": fmt.pct(mea["v26"] / g["v26"] * 100),
                f"share_of_global_{Y1}": fmt.pct(mea["v33"] / g["v33"] * 100),
                "cagr_premium_vs_global_pp": f"{mea['cagr'] - g['cagr']:.1f}"},
        "regions": _block(m["regions"], g),
        "subregions": _block(m["subregions"], mea),
        "size_check": _size_facts(m),
    }


def _size_facts(m: dict) -> dict:
    """Where the headline comes from (verify_size.py): a named source, or a CMI estimate."""
    chk = m.get("size_check") or {}
    if chk.get("status") == "sourced" and chk.get("source"):
        s = chk["source"]
        who = ("CMI's published estimate" if chk.get("source_is_cmi") else
               s["domain"] if s["publisher"] == s["domain"] else f"{s['publisher']} ({s['domain']})")
        return {"status": "sourced", "headline_source": who,
                "rule": "the global base is this source's figure: name the source; never explain why other "
                        "figures differ"}
    return {"status": "unsourced", "headline_label": "CMI estimate",
            "rule": "the headline is a CMI estimate: call it that; never mention published or third-party "
                    "estimates, ranges or triangulation"}


# --------------------------------------------------------------------------- #
# chapters: slot -> (instruction, template element giving the style example)
# --------------------------------------------------------------------------- #
def chapters(m: dict, ag: dict) -> dict[str, dict]:
    tdim, ddim = m["dimensions"]["type"]["singular"], m["dimensions"]["demand"]["singular"]
    me_units = list(m["countries"]["Middle East"])
    af_units = list(m["countries"]["Africa"])
    pub = m.get("published_2025") or []          # same-scope figures only
    chs = {
        "ch1": {"facts": {"published_2025_estimates_usd_bn": [f"{p['publisher']}: ${p['value_usd_bn']:g}B" for p in pub],
                          "segments_global": _segs(m, "Global"),
                          "middle_east_countries": _block(m["countries"]["Middle East"], m["subregions"]["Middle East"]),
                          "africa_countries": _block(m["countries"]["Africa"], m["subregions"]["Africa"])},
                "slots": {
                    "practice": ("CMI practice name for this market, 2-5 words, e.g. 'Agriculture & Food Practice'", None),
                    "thesis": ("ONE sentence: the report's central thesis, built from FACTS.body_findings (e.g. 'This "
                               "market is moving from niche to mainstream, and the Middle East & Africa is where the "
                               "relative opportunity is largest' -- only if the findings support it)", None),
                    "cover_subtitle": ("one line like the example, naming this market's themes", 3),
                    "about_scope": ("1-2 sentences: what this whitepaper sizes and why the Middle East and Africa are the focus", 20),
                    "exec_para": (("executive summary opener with the CMI global estimates, naming "
                                   "FACTS.size_check.headline_source as the source of the base-year figure")
                                  if m.get("size_check", {}).get("status") == "sourced" else
                                  ("executive summary opener with the global figures, explicitly called 'CMI "
                                   "estimates'; no comparison with any other figures"), 24),
                    "global_bullets": ("list of 3 bullets: largest region; fastest-growing region and its "
                                       "incremental value; one structural shift", 29),
                    "mea_bullets": ("list of 3 bullets: MEA size, share and CAGR premium; Middle East led by its two "
                                    "largest countries; Africa size, CAGR and fastest countries", 33),
                    "largest_demand_pool": ("object {global, mea}: short phrases naming the largest demand pools", 41),
                    "primary_catalyst": ("object {global, mea}: short phrases", 41),
                    "primary_constraint": ("object {global, mea}: short phrases", 41),
                    "key_conclusions": ("list of 3 numbered-style conclusions (without the number), the first with "
                                        "the Middle East's absolute opportunity and its largest country pools", 43)}},
        "ch2": {"facts": {"trends": (ag.get("dynamics") or {}).get("trends"),
                          "mea_drivers_pp": (ag.get("dynamics") or {}).get("mea_drivers"),
                          "mea_restraints_pp": (ag.get("dynamics") or {}).get("mea_restraints")},
                "slots": {
                    "trends_intro": ("1-2 sentences introducing six structural trends", 45),
                    "trend_paras": ("list of 6 objects, one for EACH of the six FACTS.trends in order {lead: <its "
                                    "title exactly>, text: <2-3 sentences explaining it, tied to countries or "
                                    "segments that appear later>}", 50),
                    "drivers_para": ("paragraph on MEA growth factors and restraints, impact in pp of CAGR", 54),
                    "matrix_para": ("paragraph introducing the impact matrix", 60)}},
        "ch3": {"facts": {"segments_global": _segs(m, "Global"),
                          "scenarios": {k: {"CAGR": fmt.pct(v["cagr"]), f"{Y1}F": fmt.usd(v["series"][Y1])}
                                        for k, v in m["scenarios"].items()},
                          "countries_by_region": {r: _block(m["countries"][r], (m["regions"].get(r) or m["subregions"].get(r)))
                                                  for r in m["countries"]}},
                "slots": {
                    "sizing_para": ("paragraph: base case 2026E to 2033F and why three scenarios", 65),
                    "scenario_conditions": ("object {Conservative, Base, Optimistic}: one sentence each on what has to be true", 74),
                    "type_para__Global": (f"2-3 sentences on the global {tdim} split shown in the chart", 87),
                    "demand_para__Global": (f"2-3 sentences on the global {ddim} split shown in the chart", 92),
                    "share_para": ("paragraph on how regional shares shift between 2026E and 2033F", 97),
                    "abs_para": ("paragraph on the absolute dollar opportunity by region", 102),
                    "highlights": ("object {North America, Europe, Asia Pacific, Latin America, Middle East & "
                                   "Africa}: 2 sentences each, starting with its 2026E size", 107),
                    "country_bridge": ("1-2 sentences introducing the country tables", 112),
                    "country_intro": ("object {North America, Latin America, Europe, Asia Pacific, Middle East, "
                                      "Africa}: 2 sentences each on that region's country split", 115)}},
        # B5: every country named in a table gets at least a short profile
        "ch3p": {"facts": {"countries_by_region": {r: _block(m["countries"][r], m["regions"][r])
                                                   for r in ("North America", "Europe", "Asia Pacific", "Latin America")},
                           "why_each_country_matters": m.get("geography_why") or {},
                           "rest_of_members": {r: (m.get("rest_members") or {}).get(r)
                                               for r in ("Europe", "Asia Pacific", "Latin America")}},
                 "slots": {"country_profiles": (
                     "object {<every country AND every 'Rest of' group named in FACTS.countries_by_region>: "
                     "<2-3 sentences: its 2026E size and CAGR from FACTS, then the REASONS behind its trajectory "
                     "(crops, industry, policy, climate, income); a 'Rest of' group covers exactly the countries in "
                     "FACTS.rest_of_members; no segment figures -- this report has no segment "
                     "breakdown for these countries>}", 115)}},
        "ch4": {"facts": {"middle_east": _nd(m["subregions"]["Middle East"]),
                          "countries": _block(m["countries"]["Middle East"], m["subregions"]["Middle East"]),
                          "segments": {u: _segs(m, u) for u in ["Middle East"] + me_units if u in m["segments"]},
                          "use_cases": (ag.get("use_cases") or {}).get("middle_east"),
                          "rest_of_members": (m.get("rest_members") or {}).get("Middle East")},
                "slots": {
                    "region_para": ("opening paragraph: Middle East size, forecast, CAGR, absolute opportunity", 147),
                    "type_para__Middle East": (f"2-3 sentences on the Middle East {tdim} split", 162),
                    "demand_para__Middle East": (f"2-3 sentences on the Middle East {ddim} split", 167),
                    **{f"profile__{u}": (f"profile of {u}: its size and growth from FACTS and the REASONS behind its "
                                         f"trajectory (crops, industry, policy, climate, income), 2-4 sentences"
                                         + (" -- it covers exactly the countries in FACTS.rest_of_members; "
                                            "describe only those" if u.startswith("Rest of") else ""), 170)
                       for u in m["profiled"]["Middle East"]},
                    **{f"type_para__{u}": (f"2 sentences on {u}'s {tdim} split", 175) for u in m["profiled"]["Middle East"]},
                    **{f"demand_para__{u}": (f"2 sentences on {u}'s {ddim} split", 180) for u in m["profiled"]["Middle East"]},
                    "where_to_play": ("list of 3 objects {lead: <2-4 words>, text: <1-2 sentences>}: largest pool, "
                                      "fastest-growing pool, adjacent upside", 225)}},
        "ch5": {"facts": {"africa": _nd(m["subregions"]["Africa"]),
                          "countries": _block(m["countries"]["Africa"], m["subregions"]["Africa"]),
                          "segments": {u: _segs(m, u) for u in ["Africa"] + af_units if u in m["segments"]},
                          "use_cases": (ag.get("use_cases") or {}).get("africa"),
                          "rest_of_members": (m.get("rest_members") or {}).get("Africa")},
                "slots": {
                    "region_para": ("opening paragraph: Africa size, forecast, CAGR and context", 227),
                    "type_para__Africa": (f"2-3 sentences on the Africa {tdim} split", 242),
                    "demand_para__Africa": (f"2-3 sentences on the Africa {ddim} split", 247),
                    **{f"profile__{u}": (f"profile of {u}: its size and growth from FACTS and the REASONS behind its "
                                         f"trajectory (crops, industry, policy, climate, income), 2-4 sentences"
                                         + (" -- it covers exactly the countries in FACTS.rest_of_members; "
                                            "describe only those" if u.startswith("Rest of") else ""), 250)
                       for u in m["profiled"]["Africa"]},
                    **{f"type_para__{u}": (f"2 sentences on {u}'s {tdim} split", 255) for u in m["profiled"]["Africa"]},
                    **{f"demand_para__{u}": (f"2 sentences on {u}'s {ddim} split", 260) for u in m["profiled"]["Africa"]},
                    "where_to_play": ("list of 3 objects {lead: <2-4 words>, text: <1-2 sentences>}: anchor market, "
                                      "growth markets, business model", 302)}},
        "ch6": {"facts": {"regulation": {k: v for k, v in (ag.get("regulation") or {}).items() if k != "_sources"}},
                "slots": {
                    "reg_intro": ("paragraph on regulation as enabler and swing factor in MEA", 304),
                    "policy_bullets": ("list of 4 bullets: policy outlook to 2030", 316),
                    "entry_implication": ("2 sentences: implication for market entry", 324)}},
        "ch7": {"facts": {"value_chain": m.get("value_chain"), **vendor_facts(m)},
                "slots": {
                    "vendor_intro": ("paragraph: the vendor tiers in this market, naming only FACTS.vendors", 326),
                    "value_chain_para": ("2 sentences on where value sits in the chain", 331),
                    "global_share_para": (("2 sentences on the published global vendor shares in "
                                           "FACTS.vendor_share_global_pct, for FACTS.vendor_year")
                                          if m["sections"]["vendor_global"]["show"] else
                                          ("2 sentences on how the leading vendors are positioned globally "
                                           "(FACTS.vendor_positioning; tiers are CMI assessments; no shares or %)"), 336),
                    "mea_share_para": (("2 sentences on the published MEA vendor shares in FACTS.vendor_share_mea_pct, "
                                        "naming FACTS.mea_leader as the leader")
                                       if m["sections"]["vendor_mea"]["show"] else
                                       ("2 sentences on vendor positioning in the Middle East & Africa, naming "
                                        "FACTS.mea_leader as the leading vendor on CMI's assessment (no shares or %)"),
                                       341),
                    "implications": ("list of 3 competitive implications", 351)}},
        "ch8": {"facts": {"strategy": {k: v for k, v in (ag.get("strategy") or {}).items()
                                       if k not in ("_sources", "priority_actions")}},
                "slots": {"reco_intro": ("paragraph introducing recommendations by stakeholder, with a common theme", 353),
                          # B9: every recommendation follows from a specific finding of the report
                          "priority_actions": ("list of 5 objects {lead: <one action for the next 12-18 months, "
                                               "8-15 words>, text: <'Finding: ' + the specific finding from "
                                               "FACTS.body_findings it follows from, with its figure and its "
                                               "context (e.g. 'In the optimistic scenario, ...')>} -- the finding "
                                               "must DIRECTLY justify the action, not merely be related", 361)}},
        # 9.1 (the basis of the headline) is written from m["size_check"] by build.py, not by the LLM
        "ch9": {"facts": {"cmi_base_2025": fmt.usd(m["global"]["v25"]),
                          "scope": (ag.get("_definition") or {})},
                "slots": {
                    "method_para": ("2 sentences on CMI's top-down and bottom-up method", 363),
                    "assumptions": ("list of 4 assumptions and limitations bullets for this market (no template or "
                                    "draft wording)", 381)}},
    }
    # each chapter closes with a sentence that sets up the next one (storyline transitions)
    nxt = {"ch1": "the market trends, drivers and restraints behind these numbers",
           "ch2": "the global market size, scenarios, segments and regions",
           "ch3": "the Middle East market opportunity", "ch4": "the Africa market opportunity",
           "ch5": "the regulatory and policy landscape" if m["sections"]["regulation"]["show"]
                  else "the vendor landscape",
           "ch6": "the vendor landscape", "ch7": "the strategic recommendations",
           "ch8": "the research methodology behind these estimates"}
    for c, topic in nxt.items():
        chs[c]["slots"][f"closing_{c}"] = (f"ONE sentence that closes this chapter and sets up the next one, on {topic} "
                                           f"-- an insight-led bridge with NO figures (the next chapter states them)",
                                           None)
    return chs


# --------------------------------------------------------------------------- #
# guards per slot
# --------------------------------------------------------------------------- #
def vendor_facts(m: dict) -> dict:
    """The vendor chapter's facts, from the final vendor data only (PLAN-05):
    sourced shares when there are enough, else the positioning table; never an
    excluded or unconfirmed company, never a share without a source."""
    sec = m["sections"]
    out = {"vendor_year": str(m["vendor_year"]),
           "vendors": [r["vendor"] for r in m["vendor_positioning"]] or list(m["vendor_text"]["names"]),
           "mea_leader": m["vendor_mea_leader"],
           "dashboard": [{k: r[k] for k in ("vendor", "hq", "core_strength")} for r in m["vendor_dashboard"]],
           "rule": "name only companies in these facts; a % next to a vendor only as listed in "
                   "vendor_share_*_pct; a deal or move only with its year"}
    if sec["vendor_global"]["show"]:
        out["vendor_share_global_pct"] = [[n, fmt.pct(s)] for n, s in m["vendor_chart_global"]]
    if sec["vendor_mea"]["show"]:
        out["vendor_share_mea_pct"] = [[n, fmt.pct(s)] for n, s in m["vendor_chart_mea"]]
    if not (sec["vendor_global"]["show"] and sec["vendor_mea"]["show"]):
        out["vendor_positioning"] = [{"vendor": r["vendor"], "hq": r["hq"], "parent_group": r["parent"],
                                      "business_type": r["business_type"],
                                      "tier_cmi_assessment": r["tier"], "role": r["role"],
                                      "mea_presence_cmi_assessment": r["mea_presence"]}
                                     for r in m["vendor_positioning"]]
    return out


def mea_leader_named(slots: dict, m: dict) -> str | None:
    """The MEA paragraph names the MEA leader in the vendor data (F3: NAQUA was
    dropped from it). When the guarded text does not, a sentence built from
    the data opens the paragraph. Returns what was done, or None."""
    from AiAgentsFleet.cmi_report.entities import named_in
    leader = m.get("vendor_mea_leader")
    sec = m["sections"]
    if not leader or not (sec["vendor_mea"]["show"] or sec["positioning"]["show"]):
        return None
    text = slots.get("mea_share_para") or ""
    if named_in(text, leader):
        return None
    if sec["vendor_mea"]["show"]:
        share = dict(m["vendor_chart_mea"]).get(leader)
        lead = (f"{leader} holds the largest published share of the Middle East & Africa market, at "
                f"{fmt.pct(share)} in {m['vendor_year']}.")
    else:
        lead = f"{leader} is the leading vendor in the Middle East & Africa on CMI's assessment of regional presence."
    slots["mea_share_para"] = (lead + " " + text).strip()
    return f"MEA paragraph did not name the MEA leader {leader}: a data sentence was added"


def claim_groups(m: dict, geo: str | None) -> list[dict]:
    gs = [{"names": m["regions"], "aliases": ALIASES}, {"names": m["subregions"]}]
    gs += [{"names": d, "aliases": ALIASES} for d in m["countries"].values()]
    if geo and geo in m["segments"]:
        gs += [{"names": m["segments"][geo]["type"]}, {"names": m["segments"][geo]["demand"]}]
    return gs


_TPL_SENT: list[set[str]] = []


def set_template(template: Path) -> None:
    """Loads the template's own example sentences once, so text copied from the
    (drone-market) template can be recognised and dropped."""
    from AiAgentsFleet.cmi_report.guard import sentences, word_set
    if _TPL_SENT:
        return
    for t in template_texts(template).values():
        for part in t.split(" / "):
            _TPL_SENT.extend(word_set(s) for s in sentences(part) if len(s.split()) >= 6)


def make_guard(m: dict, geo: str | None) -> ClaimGuard:
    g = ClaimGuard(claim_groups(m, geo), m["global"]["cagr"], _TPL_SENT)
    # segment data exists only for the Middle East & Africa units: no segment claims for other countries
    with_breakdown = {u for v in m["profiled"].values() for u in v}
    g.no_breakdown = [c for r, d in m["countries"].items() for c in d
                      if c not in with_breakdown and not c.startswith("Rest of")]
    g.segment_names = [n for dims in m["segments"].get("Global", {}).values() for n in dims
                       if not n.lower().startswith("other")]
    g.headline_unsourced = (m.get("size_check") or {}).get("status") != "sourced"
    g.definition = m.get("definition")       # no sentence may describe another value-chain level (PLAN-04)
    g.vendor_text = m.get("vendor_text")     # vendors named only from the final vendor data (PLAN-05)
    return g


def _geo_of(slot: str, ch: str) -> str | None:
    if "__" in slot:
        return slot.split("__", 1)[1]
    return {"ch1": "Global", "ch3": "Global", "ch4": "Middle East", "ch5": "Africa"}.get(ch)


def _guard(text, ng, cg, dropped, absent) -> str:
    out = clean_text(text, ng, cg, dropped, absent)
    out = vendor_clean(out, getattr(cg, "vendor_text", None), dropped)
    return drop_size_comparison(out, dropped, getattr(cg, "headline_unsourced", False))


def _clean(v, ng, cg, dropped, absent, limit=110):
    if isinstance(v, str):
        return cap_words(_guard(v, ng, cg, dropped, absent), limit) or None
    if isinstance(v, list):
        out = [_clean(x, ng, cg, dropped, absent, 45) for x in v]
        return [x for x in out if x]
    if isinstance(v, dict):
        if {"lead", "text"} <= v.keys():
            lead = v["lead"] if not ng.bad(v["lead"]) else None
            text = cap_words(_guard(v["text"], ng, cg, dropped, absent), 60)
            return {"lead": lead, "text": text} if lead and text else None
        out = {}
        for k, x in v.items():
            if isinstance(x, str):
                # {"North America": "Smallest region ..."}: the key is the sentence's subject,
                # so the claim check reads "North America: Smallest region ..."
                lead = f"{k}: "
                c = _guard(lead + x, ng, cg, dropped, absent)
                out[k] = cap_words(c[len(lead):] if c.startswith(lead) else c, 60) or None
            else:
                out[k] = _clean(x, ng, cg, dropped, absent, 60)
        return {k: x for k, x in out.items() if x}
    return None


def template_texts(template: Path) -> dict[int, str]:
    """Each body element's text as a style example; paragraphs of a box or
    table are separated (not glued into 'Key conclusions1. ...')."""
    from docx.oxml.ns import qn
    d = docx.Document(str(template))
    out = {}
    for i, el in enumerate(d.element.body.iterchildren()):
        ps = [p.xpath("string(.)").strip() for p in el.iter(qn("w:p"))]
        out[i] = " / ".join(p for p in ps if p)
    return out


def absent_names(m: dict) -> list[str]:
    """'Rest of <region>' groups that this report's data does not have."""
    have = {k for d in m["countries"].values() for k in d}
    regions = list(m["regions"]) + list(m["subregions"])
    return [f"Rest of {r}" for r in regions if f"Rest of {r}" not in have]


def _normalise(v, desc: str):
    """Lists sometimes come back as one numbered string; split them."""
    if isinstance(v, str) and desc.startswith("list"):
        parts = [p.strip() for p in re.split(r"(?:^|\s)(?:\d+[.)]|[-•])\s+", v) if p.strip()]
        return parts if len(parts) > 1 else [v]
    if isinstance(v, list):
        # list items carry no numbering of their own (the report numbers them)
        return [re.sub(r"^\s*(?:\d+[.)]\s*)+", "", x) if isinstance(x, str) else x for x in v]
    return v


def cap_words(text: str, limit: int) -> str:
    """Whole sentences up to `limit` words (at least one sentence)."""
    from AiAgentsFleet.cmi_report.guard import sentences
    out, n = [], 0
    for s in sentences(text):
        w = len(s.split())
        if out and n + w > limit:
            break
        out.append(s)
        n += w
    return " ".join(out)


def write(market_dir: Path, m: dict, ag: dict, llm, template: Path, evidence_fn=None, log=print) -> dict:
    tpl = template_texts(template)
    set_template(template)
    facts0 = base_facts(m)
    out = {"version": PROSE_VERSION, "slots": {}, "dropped": {}}
    sec = m["sections"]
    hidden = {"type_para": not sec["type"]["show"], "demand_para": not sec["demand"]["show"]}
    specs = chapters(m, ag)
    # every number must be a register value of an entity named in the same sentence
    from AiAgentsFleet.cmi_report.register import FactGuard, build_register
    fact_guard = FactGuard(build_register(m, ag))
    # The body is written first; the recommendations (ch8) and then the executive
    # summary (ch1) are written LAST, from the body's final findings.
    order = [c for c in specs if c not in ("ch1", "ch8")] + ["ch8", "ch1"]
    for ch in order:
        spec = specs[ch]
        if ch == "ch6" and not sec["regulation"]["show"]:
            continue
        facts = {**facts0, **spec["facts"]}
        if ch in ("ch1", "ch8"):
            facts["body_findings"] = body_findings(out["slots"])
        # no text for a section this report leaves out
        slots = {k: v for k, v in spec["slots"].items() if not hidden.get(k.split("__")[0])}
        evidence = evidence_fn(ch) if evidence_fn else []
        user = (f"MARKET: {m['market']}\n\nFACTS (the only allowed source of numbers):\n"
                f"{json.dumps(facts, ensure_ascii=False)}\n\nEVIDENCE (qualitative only):\n"
                + ("\n".join(f"- {e}" for e in evidence) or "(none)") + "\n\nSLOTS (key: what to write || STYLE EXAMPLE):\n"
                + "\n".join(f"- {k}: {desc} || {tpl.get(idx, '')[:600] if idx is not None else ''}"
                            for k, (desc, idx) in slots.items()))
        try:
            raw = llm.complete_json(_SYSTEM, user) or {}
        except Exception as exc:  # noqa: BLE001
            log(f"  [{ch}] LLM failed: {exc}")
            raw = {}
        ng = fact_guard
        dropped: list[str] = []
        absent = absent_names(m)
        for k, (desc, idx) in slots.items():
            cg = make_guard(m, _geo_of(k, ch))
            v = _normalise(raw.get(k), desc)
            # a 'Rest of' profile may not describe a separately profiled country
            geo_k = _geo_of(k, ch) or ""
            absent_k = absent + ([c for c in m["countries"].get(geo_k.replace("Rest of ", ""), {})
                                  if not c.startswith("Rest of")] if geo_k.startswith("Rest of") else [])
            # a box title the model copied from the style example is not text ('Competitive implications1. ...')
            title = (tpl.get(idx, "").split(" / ")[0] if idx is not None else "")
            if title and len(title.split()) <= 6:
                strip = lambda x: x[len(title):].lstrip(" :/") if isinstance(x, str) and x.startswith(title) else x  # noqa: E731
                v = [strip(x) for x in v] if isinstance(v, list) else strip(v)
            v = _clean(v, ng, cg, dropped, absent_k)
            if v:
                out["slots"][k if ch not in ("ch4", "ch5") or "__" in k else f"{ch}.{k}"] = v
        if dropped:
            out["dropped"][ch] = dropped
        log(f"  [{ch}] {sum(1 for k in slots if raw.get(k))}/{len(slots)} slots written"
            + (f", {len(dropped)} sentence(s) dropped by the guards" if dropped else ""))
    out["slots"] = in_document_order(out["slots"], specs)
    repeats = dedupe(out["slots"], m)
    if repeats:
        out["dropped"]["repetition"] = repeats
        log(f"  [repetition] {len(repeats)} repeated sentence(s) removed")
    fixed = mea_leader_named(out["slots"], m)
    if fixed:
        out["vendor_check"] = [fixed]
        log(f"  [vendors] {fixed}")
    (market_dir / "cmi_prose.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def body_findings(slots: dict, limit: int = 40) -> list[str]:
    """Number-bearing sentences of the finished body chapters -- what the
    executive summary and the recommendations must be built from."""
    from AiAgentsFleet.cmi_report.guard import sentences
    found = []
    context = {"scenario_conditions": "{k} scenario", "highlights": "{k}", "country_intro": "{k}",
               "country_profiles": "{k}"}

    def walk(v, prefix=""):
        # each finding keeps its context ('Optimistic scenario: ...'), so a quote can't change its meaning
        if isinstance(v, str):
            found.extend(f"{prefix}{s}" for s in sentences(v) if re.search(r"\d", s))
        elif isinstance(v, list):
            for x in v:
                walk(x, prefix)
        elif isinstance(v, dict):
            for k, x in v.items():
                walk(x, prefix + (context[slot].format(k=k) + ": " if slot in context else ""))
    for slot, v in slots.items():
        walk(v)
    return found[:limit]


def in_document_order(slots: dict, specs: dict) -> dict:
    """Slots re-ordered as they appear in the report (chapter 1 first)."""
    keys = []
    for ch, spec in specs.items():
        for k in spec["slots"]:
            keys.append(k if ch not in ("ch4", "ch5") or "__" in k else f"{ch}.{k}")
    return {k: slots[k] for k in keys if k in slots} | {k: v for k, v in slots.items() if k not in keys}


def reguard(pr: dict, m: dict, ag: dict) -> int:
    """Re-applies the current guards and the repetition pass to saved text
    (no LLM calls) -- so a guard improvement reaches reports whose text is
    reused. Returns the number of sentences removed."""
    slots = pr.get("slots") or {}
    before = json.dumps(slots, ensure_ascii=False)
    absent = absent_names(m)
    dropped: list[str] = []
    from AiAgentsFleet.cmi_report.register import FactGuard, build_register
    fact_guard = FactGuard(build_register(m, ag))
    for ch, spec in chapters(m, ag).items():
        ng = fact_guard
        for k in spec["slots"]:
            key = k if ch not in ("ch4", "ch5") or "__" in k else f"{ch}.{k}"
            if key in slots:
                v = _clean(slots[key], ng, make_guard(m, _geo_of(k, ch)), dropped, absent)
                if v:
                    slots[key] = v
                else:
                    del slots[key]
    dropped += dedupe(slots, m)
    fixed = mea_leader_named(slots, m)
    if fixed:
        pr["vendor_check"] = [fixed]
        dropped.append(f"[vendors] {fixed}")
    if dropped:
        pr.setdefault("dropped", {})["reguard"] = dropped
    return len(dropped) if json.dumps(slots, ensure_ascii=False) != before else 0


# --------------------------------------------------------------------------- #
# repetition
# --------------------------------------------------------------------------- #
# Executive summary and recommendations: written last, from the body; they restate findings by design.
_SUMMARY_SLOTS = {"thesis", "practice", "cover_subtitle", "about_scope", "exec_para", "global_bullets", "mea_bullets",
                  "largest_demand_pool", "primary_catalyst", "primary_constraint", "key_conclusions",
                  "priority_actions"}
# Slots allowed to state the global / MEA headline figures.
_HEADLINE_OWNERS = ("practice", "about_scope", "exec_para", "global_bullets", "mea_bullets", "key_conclusions",
                    "largest_demand_pool", "sizing_para", "highlights", "scenario_conditions")


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9$.%+]+", s.lower()) if len(w) > 2}


def dedupe(slots: dict, m: dict) -> list[str]:
    """In document order: drops a sentence that (a) nearly repeats an earlier
    one (>= 70% of words shared) or (b) restates two or more global/MEA
    headline figures outside the slots that own them."""
    from AiAgentsFleet.cmi_report.guard import sentences
    g, mea = m["global"], m["regions"]["Middle East & Africa"]
    headline = {fmt.usd(g["v26"]), fmt.usd(g["v33"]), fmt.pct(g["cagr"]),
                fmt.usd(mea["v26"]), fmt.usd(mea["v33"]), fmt.pct(mea["cagr"])}
    seen: list[tuple[set[str], set[str]]] = []
    removed: list[str] = []

    def nums(s: str) -> set[str]:
        return set(re.findall(r"\d[\d,.]*", s))

    def walk(v, owner: bool):
        if isinstance(v, str):
            keep = []
            for s in sentences(v):
                w, n = _words(s), nums(s)
                restates = not owner and sum(h in s for h in headline) >= 2
                # a repeat says nearly the same words AND no new figure ('largest type in Saudi
                # Arabia, $26.4M' vs '... in the UAE, $8.87M' are two different facts)
                repeat = len(w) >= 6 and any(len(w & x) / len(w | x) >= 0.7 and n <= xn for x, xn in seen)
                if restates or repeat:
                    removed.append(("[headline restated] " if restates else "[repeat] ") + s[:110])
                    continue
                seen.append((w, n))
                keep.append(s)
            return " ".join(keep)
        if isinstance(v, list):
            return [x for x in (walk(x, owner) for x in v) if x]
        if isinstance(v, dict):
            return {k: x for k, x in ((k, walk(x, owner)) for k, x in v.items()) if x}
        return v

    for k in list(slots):
        base = k.split(".")[-1].split("__")[0]
        if base in _SUMMARY_SLOTS:          # summaries restate body findings by design
            continue
        owner = base in _HEADLINE_OWNERS
        v = walk(slots[k], owner)
        if v:
            slots[k] = v
        else:
            del slots[k]
    return removed


# --------------------------------------------------------------------------- #
# data-only fallbacks (used when a slot is empty after the guards)
# --------------------------------------------------------------------------- #
def seg_fallback(seg: dict, dim: str, geo: str) -> str:
    big = max(seg, key=lambda n: seg[n]["v33"])
    fast = max(seg, key=lambda n: seg[n]["cagr"])
    where = "globally" if geo == "Global" else f"in {geo}"
    txt = f"{big} remains the largest {dim.lower()} {where} through {Y1}F ({fmt.usd(seg[big]['v33'])})"
    if fast != big:
        txt += (f", while {fast} grows fastest, from {fmt.usd(seg[fast]['v26'])} to {fmt.usd(seg[fast]['v33'])} "
                f"at a {fmt.pct(seg[fast]['cagr'])} CAGR.")
    else:
        txt += f" and also grows fastest, at a {fmt.pct(seg[big]['cagr'])} CAGR."
    return txt


def region_country_fallback(region: str, d: dict) -> str:
    big = max(d, key=lambda n: d[n]["v26"])
    fast = max(d, key=lambda n: d[n]["cagr"])
    txt = (f"{big} is the largest market in {region} at {fmt.usd(d[big]['v26'])} in {Y0}E, reaching "
           f"{fmt.usd(d[big]['v33'])} by {Y1}F.")
    if fast != big:
        txt += f" {fast} is the fastest-growing, at a {fmt.pct(d[fast]['cagr'])} CAGR."
    return txt


def slug_safe(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")

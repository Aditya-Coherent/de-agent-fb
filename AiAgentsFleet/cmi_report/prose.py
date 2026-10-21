"""Writes every text slot of the CMI whitepaper with the LLM -- one call per
chapter -- from the numeric model's pre-formatted facts, in the tone of the
template's own paragraph for that slot. Each sentence then passes the
NumberGuard and ClaimGuard (guard.py); failing sentences are dropped and
logged, and data-only fallbacks fill slots that come back empty.

After the chapters (PLAN-07): every priority action is judged against the
finding it cites (one LLM call each; regenerated once, then dropped), a phrase
repeated in 3+ sentences is rewritten, and a domain-sanity pass drops
sentences stating a real-world fact that is likely false. All drops are logged
in cmi_prose.json["dropped"].

Writes output/<slug>/cmi_prose.json.
"""
from __future__ import annotations

import json
import re
import zlib
from pathlib import Path

import docx

from AiAgentsFleet.cmi_report import fmt, rules
from AiAgentsFleet.cmi_report.guard import (ClaimGuard, NumberGuard, clean_text, contradiction_guard,
                                            drop_mea_basis, drop_size_comparison, fix_plurals, sentences,
                                            unverified_fact, vendor_clean)
from AiAgentsFleet.cmi_report.model import Y0, Y1, regional_chapters

PROSE_VERSION = 31    # bump when prompts or guards change, so saved text is rewritten
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
    "2. Comparative words (largest, fastest, smallest, second-largest, leads, dominates, anchors, momentum, "
    "faster than) only as FACTS.rankings and FACTS values state them, in the year they state (a "
    "'largest_in_2026' ranking is about 2026 values); when rankings say '(tied)' or '(similar)', name every "
    "party and say they are level or grow at a similar rate -- never call one of them the largest or fastest. "
    "A change in ranking names exactly what is overtaken and the result (e.g. '<segment A> overtakes <segment "
    "B> to become the largest by 2033F'); never vague phrases such as 'overtakes the field' or 'pulls ahead'.\n"
    "3. Qualitative specifics (named regulations, programmes, companies, crops, technologies, country "
    "dynamics) may come from EVIDENCE or well-established knowledge of this market; if unsure, stay general.\n"
    "4. Plain prose, no markdown, no URLs, no 'according to'. The figures are CMI's own: never tag them ('CMI "
    "estimates', 'CMI forecasts', 'CMI assessments', '(CMI estimate)'); only the executive summary's opening "
    "sentence says 'CMI estimates the market at ...'.\n"
    "5. Refer to segments with the dimension names in FACTS.dimensions.\n"
    "6. Mention only countries, regions, segments and categories that exist in FACTS -- e.g. never a 'Rest of' "
    "group that FACTS does not list.\n"
    "7. No caveats, disclaimers or hedges: nothing about drafts, templates, data refreshes, publication, "
    "data availability, what could or could not be confirmed, or where a figure comes from. State findings with "
    "confidence.\n"
    "8. Paragraphs at most 90 words, bullets at most 40 words. A STYLE EXAMPLE's heading or numbering is not "
    "part of the text: return lists as JSON arrays of plain strings.\n"
    "9. No repetition: every slot must add something the other slots do not say. Do not restate the global "
    "or MEA headline size, forecast or CAGR outside the executive summary and the sizing paragraph, and do "
    "not reuse a sentence pattern across slots.\n"
    "10. Name a regulator, ministry, agency, law, decree, 'Vision 20xx' programme or dated "
    "policy ONLY if it is in FACTS.verified_regulatory_facts, exactly as written there; otherwise describe it "
    "generically ('the national food safety authority', 'registration rules'). Never write '[to verify]' or any "
    "other verification marker; never invent companies, projects or partnerships.\n"
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
    "19. Never describe research steps (interviews, expert panels, surveys, bottom-up builds, triangulation); "
    "the methodology chapter states what was done.\n"
    "20. A causal or comparative claim ('X shapes share more than Y', 'tighter rules sustain demand', 'high "
    "mobile penetration') must follow from FACTS; never contradict a listed driver or restraint.\n"
    "21. A slot marked [CHART] sits next to a chart or table that already shows every value. Do NOT narrate the "
    "chart: no run of sizes, shares, CAGRs or increments, and at most ONE figure in the whole slot (none is "
    "fine), used only to anchor an explanation. Write the reasoning the chart cannot show: WHY the leader leads, "
    "WHY the fastest grows fastest, what holds the laggard back, and what it means for a supplier or investor -- "
    "with concrete local detail (crops, industries, programmes, buyers, climate, channels).\n"
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
    year. A tie at printed precision is '(tied)'; leaders within the
    materiality threshold (rules.TIE_PP / TIE_REL) are '(similar)'."""
    def pick(key: str, best: bool) -> str:
        order = sorted(d, key=lambda k: d[k][key], reverse=best)
        show = (lambda v: fmt.pct(v)) if key == "cagr" else (lambda v: fmt.usd(v))
        same = [k for k in order if show(d[k][key]) == show(d[order[0]][key])]
        if len(same) > 1:
            return " and ".join(same) + " (tied)"
        near = rules.top_tied(d, key, best)
        return order[0] if len(near) == 1 else " and ".join(near) + " (similar)"
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
                # signed: negative = MEA grows more slowly than the world
                "cagr_gap_vs_global_pp": fmt.pp(mea["cagr"] - g["cagr"])},
        "regions": _block(m["regions"], g),
        "subregions": _block(m["subregions"], mea),
        "size_check": _size_facts(m),
        # the only regulators, laws and dated policies the text may name (verify_facts.py, PLAN-06)
        "verified_regulatory_facts": (m.get("fact_review") or {}).get("verified_facts") or [],
    }


def _size_facts(m: dict) -> dict:
    """Where the headline comes from (verify_size.py): a named source, or a CMI estimate."""
    chk = m.get("size_check") or {}
    if chk.get("status") == "sourced" and chk.get("source") and chk.get("source_is_cmi"):
        return {"status": "sourced", "headline_source": "CMI's published estimate",
                "rule": "the global base is CMI's published estimate; never name another research publisher; "
                        "never explain why other figures differ"}
    return {"status": "unsourced", "headline_label": "CMI estimate",
            "rule": "state the headline once as 'CMI estimates the market at ...'; never comment on where it comes "
                    "from, and never mention published or third-party estimates, ranges or triangulation"}


def _impact(x: dict) -> float:
    return float(x.get("impact_pp") if x.get("impact_pp") is not None else x.get("impact_score") or 0)


def highlight_regions(m: dict) -> list[tuple[str, str]]:
    """Section 3.4's two regions as (label, region): the largest market (2026E value) and the highest
    CAGR. When one region is both, the second point is the next-highest CAGR (user decision 2026-10-18)."""
    regs = m["regions"]
    largest = max(regs, key=lambda r: regs[r]["v26"])
    by_cagr = sorted(regs, key=lambda r: -regs[r]["cagr"])
    if by_cagr[0] != largest:
        return [("Largest market", largest), ("Highest CAGR", by_cagr[0])]
    return [("Largest market and highest CAGR", largest), ("Second-highest CAGR", by_cagr[1])]


def top_factors(m: dict, ag: dict) -> dict:
    """The largest global driver / restraint (fleet desk research, in bps of the
    global CAGR) and the largest MEA ones (CMI agents), by name only."""
    fd = m.get("fleet_drivers") or {}
    dyn = ag.get("dynamics") or {}

    def top(xs, key):
        xs = [x for x in xs or [] if x.get("name")]
        return max(xs, key=key)["name"] if xs else None
    return {"global_driver": top(fd.get("drivers"), lambda x: abs(float(x.get("cagr_contribution_bps") or 0))),
            "global_restraint": top(fd.get("restraints"), lambda x: abs(float(x.get("cagr_drag_bps") or 0))),
            "mea_driver": top(dyn.get("mea_drivers"), lambda x: abs(_impact(x))),
            "mea_restraint": top(dyn.get("mea_restraints"), lambda x: abs(_impact(x)))}


def driver_facts(dyn: dict) -> dict:
    """The MEA growth factors as the prose may state them (after rules.reconcile_drivers)."""
    basis = dyn.get("impact_basis")
    rows = lambda xs, k: [{"name": x.get("name"),  # noqa: E731
                           k: fmt.pp(float(x.get("impact_pp") or 0)) if k == "impact" else x.get(k)}
                          for x in xs or []]
    if basis == "score":
        return {"impact_basis": "relative impact score 1-5 (CMI assessment); no percentage points",
                "mea_drivers_score": rows(dyn.get("mea_drivers"), "impact_score"),
                "mea_restraints_score": rows(dyn.get("mea_restraints"), "impact_score")}
    out = {"mea_drivers_pp": rows(dyn.get("mea_drivers"), "impact"),
           "mea_restraints_pp": rows(dyn.get("mea_restraints"), "impact")}
    if basis == "pp_vs_global":
        out["impact_basis"] = (f"pp of CAGR relative to the global CAGR baseline: global CAGR "
                               f"{fmt.pct(dyn['baseline_cagr_pct'])} + drivers + restraints = MEA CAGR "
                               f"{fmt.pct(dyn['mea_cagr_pct'])}")
    return out


def driver_para_spec(dyn: dict) -> str:
    """The chart beside this paragraph prints every factor's impact, so the text explains, not lists."""
    unit = ("never in percentage points" if dyn.get("impact_basis") == "score" else
            "the chart shows each impact in pp")
    return (f"[CHART] paragraph on WHY the MEA market grows at the pace it does: the two or three strongest "
            f"growth factors and the strongest restraint, and the mechanism behind each (who buys more and why, "
            f"what slows adoption) -- {unit}; do not list the factors' impacts")


def research_steps(m: dict, root: str) -> list[tuple[str, list[str]]]:
    """CMI's five-step research process (the VP's methodology, 2026-10-19: rescrh.png), as (title, bullets).
    The steps are the same for every market; a few bullets name this market's own segment dimensions,
    value chain and buyers, so the wording varies slightly from report to report."""
    dims = m.get("dimensions") or {}
    tdim = ((dims.get("type") or {}).get("singular") or "product type").lower()
    ddim = ((dims.get("demand") or {}).get("singular") or "end use").lower()
    stages = [s.get("stage") for s in m.get("value_chain") or [] if s.get("stage")]
    demand = (m.get("segments", {}).get("Global") or {}).get("demand") or {}
    buyers = [k for k in sorted(demand, key=lambda k: -demand[k]["v26"]) if not k.lower().startswith("other")]
    name = fmt.lower_name(root)
    # the VP's points (2026-10-19), word for word except where a market detail is added in brackets
    return [
        ("Defining Market\nScope & Taxonomy",
         ["Market definition",
          f"Value-chain mapping ({stages[0].lower()} to end users)" if stages else "Value-chain mapping",
          f"Segmentation framework ({tdim}, {ddim})", "Inclusions and exclusions"]),
        ("Primary Research /\nExpert Interviews",
         ["Industry expert interviews", "Supplier and customer discussions",
          f"Demand-side assessment ({buyers[0]})" if buyers and len(buyers[0]) <= 24 else "Demand-side assessment",
          "Assumption validation"]),
        ("Paid Database\nInputs",
         ["Company financial databases", "Import-export intelligence",
          f"Industry-specific datasets ({name})" if len(name) <= 28 else "Industry-specific datasets",
          "Historical market benchmarks"]),
        ("Secondary /\nDesk Research",
         ["Annual reports and company disclosures", "Government publications", "Industry associations",
          "Industry whitepapers/journals"]),
        ("Data Triangulation\n& Validation",
         ["Top-down and bottom-up estimation", "Supply-demand reconciliation", "Cross-source validation",
          "Forecast consistency and QA"]),
    ]


def method_text(m: dict, root: str) -> str:
    """The methodology paragraph: the VP's text (2026-10-19), with the market and its segment dimensions
    named so it reads slightly differently in each report. Where the headline comes from stays internal
    (market_size_check.json)."""
    dims = m.get("dimensions") or {}
    tdim = ((dims.get("type") or {}).get("plural") or "product types").lower()
    ddim = ((dims.get("demand") or {}).get("plural") or "end uses").lower()
    return (f"CMI follows a structured and integrated research methodology for the {fmt.lower_name(root)} market, "
            f"combining market scope definition, primary research, paid database intelligence, secondary research, "
            f"and data triangulation. This integrated approach ensures reliable market estimates, validated "
            f"industry insights, and consistent market forecasts across regions, countries, {tdim} and {ddim}.")


# --------------------------------------------------------------------------- #
# chapters: slot -> (instruction, template element giving the style example)
# --------------------------------------------------------------------------- #
def chapters(m: dict, ag: dict) -> dict[str, dict]:
    tdim, ddim = m["dimensions"]["type"]["singular"], m["dimensions"]["demand"]["singular"]
    me_units = list(m["countries"]["Middle East"])
    af_units = list(m["countries"]["Africa"])
    pub = m.get("published_2025") or []          # same-scope figures only
    dyn = ag.get("dynamics") or {}
    chs = {
        "ch1": {"facts": {"published_2025_estimates_usd_bn": [f"{p['publisher']}: ${p['value_usd_bn']:g}B" for p in pub],
                          # the scorecard's global column gets global factors only, the MEA column MEA ones (X14)
                          "top_factors": top_factors(m, ag),
                          "segments_global": _segs(m, "Global"),
                          "definition": {k: (m.get("definition") or {}).get(k) for k in
                                         ("definition", "measures", "value_chain_level", "included", "excluded")},
                          "middle_east_countries": _block(m["countries"]["Middle East"], m["subregions"]["Middle East"]),
                          "africa_countries": _block(m["countries"]["Africa"], m["subregions"]["Africa"])},
                "slots": {
                    "practice": ("CMI practice name for this market, 2-5 words, e.g. 'Agriculture & Food Practice'", None),
                    # the summary's opening paragraph is about the GLOBAL market only (CEO feedback 2026-10-20)
                    "thesis": ("ONE sentence: the report's central thesis about the GLOBAL market, built from "
                               "FACTS.body_findings (e.g. 'This market is moving from niche to mainstream as "
                               "biological inputs enter routine crop programmes') -- never mention the Middle East, "
                               "Africa or MEA", None),
                    "cover_subtitle": ("one line like the example, naming this market's themes", 3),
                    "about_scope": ("1-2 sentences: what this whitepaper sizes and why the Middle East and Africa are the focus", 20),
                    "exec_para": ((("executive summary opener with the CMI global estimates, naming "
                                   "FACTS.size_check.headline_source as the source of the base-year figure")
                                  if m.get("size_check", {}).get("status") == "sourced" else
                                  ("executive summary opener with the global figures, explicitly called 'CMI "
                                   "estimates'; no comparison with any other figures"))
                                 + "; GLOBAL scenario only -- what drives the world market -- and never mention the "
                                   "Middle East, Africa or MEA (they have their own snapshot)", 24),
                    # 1.1 carries the global TRENDS, not the region sizes the KPI panel shows (2026-10-18)
                    # the global snapshot carries global statistics as well as the trends (CEO feedback 2026-10-20)
                    "global_bullets": ("list of 4 bullets on the GLOBAL market. Bullet 1: the global market size in "
                                       f"{Y0 - 1}, {Y0}E and {Y1}F with its CAGR and incremental value "
                                       "(FACTS.global). Bullets 2-4: each one shift in how this product is made, "
                                       "bought, regulated or used and why it is happening, backed by ONE global "
                                       "statistic from FACTS (a segment's or region's size, share or CAGR from "
                                       "FACTS.segments_global or FACTS.regions) that the shift explains; global "
                                       "only, never the Middle East, Africa or MEA", 29),
                    # chapter 01 opens with what the market IS (CEO feedback 2026-10-20)
                    "definition_para": ("2-3 sentences defining this market for a client: what the product is and "
                                        "what it does, who buys and uses it and for what, and which value the "
                                        "market counts (FACTS.definition.measures, at FACTS.definition."
                                        "value_chain_level level). Plain definition, no figures, no trends, no "
                                        "regions", 20),
                    "mea_bullets": ("list of 3 bullets: MEA size, share and its CAGR gap to the global market "
                                    "(FACTS.mea.cagr_gap_vs_global_pp: a negative gap means MEA grows more slowly -- "
                                    "say 'below'); Middle East led by its two largest countries; Africa size, CAGR "
                                    "and fastest countries", 33),
                    "largest_demand_pool": ("object {global, mea}: short phrases naming the largest demand pools", 41),
                    "primary_catalyst": ("object {global: FACTS.top_factors.global_driver in a short phrase, global "
                                         "facts only; mea: FACTS.top_factors.mea_driver in a short phrase}. The global "
                                         "phrase never mentions MEA, pp or percentage points", 41),
                    "primary_constraint": ("object {global: FACTS.top_factors.global_restraint in a short phrase, "
                                           "global facts only; mea: FACTS.top_factors.mea_restraint in a short "
                                           "phrase}. The global phrase never mentions MEA, pp or percentage points",
                                           41),
                    "key_conclusions": ("list of 3 numbered-style conclusions (without the number), the first with "
                                        "the Middle East's absolute opportunity and its largest country pools", 43)}},
        "ch2": {"facts": {"trends": dyn.get("trends"), **driver_facts(dyn),
                          # 2.x.1 Global / 2.x.2 MEA (changes.txt 2026-10-19): the fleet's global factors
                          "global_drivers": [x.get("name") for x in (m.get("fleet_drivers") or {}).get("drivers") or []],
                          "global_restraints": [x.get("name") for x in (m.get("fleet_drivers") or {}).get("restraints") or []]},
                "slots": {
                    "trends_intro": ("1-2 sentences introducing six structural trends", 45),
                    "trend_paras": ("list of 6 objects, one for EACH of the six FACTS.trends in order {lead: <its "
                                    "title exactly>, text: <2-3 sentences explaining the mechanism, NO figures, tied to the specific "
                                    "countries or segments that appear later -- a different one per trend; never "
                                    "end trends with the same stock phrase such as where it is 'most visible'>}; "
                                    "this is the GLOBAL view: name global regions or segments, not MEA countries", 50),
                    "trend_paras_mea": ("list of 3 objects {lead: <2-5 words naming a trend from FACTS.trends>, "
                                        "text: <2 sentences on how that trend plays out in the Middle East & Africa, "
                                        "naming MEA countries or crops; NO figures>}", 50),
                    "drivers_para_global": ("[CHART] paragraph on WHY the GLOBAL market grows at the pace it does: "
                                            "the two strongest of FACTS.global_drivers and the strongest of "
                                            "FACTS.global_restraints, and the mechanism behind each; global view "
                                            "only, no MEA, no figures", 54),
                    "matrix_para_global": ("[CHART] 1-2 sentences on which global factors act soonest and which "
                                           "world regions are most exposed, no figures", 60),
                    "drivers_para": (driver_para_spec(dyn), 54),  # 2.2.2 MEA
                    "matrix_para": ("[CHART] 1-2 sentences on what the impact matrix reveals (which factors act soonest, which "
                                    "countries are most exposed and why), no figures", 60)}},
        "ch3": {"facts": {"segments_global": _segs(m, "Global"),
                          "scenarios": {k: {"CAGR": fmt.pct(v["cagr"]), f"{Y1}F": fmt.usd(v["series"][Y1])}
                                        for k, v in m["scenarios"].items()},
                          "countries_by_region": {r: _block(m["countries"][r], (m["regions"].get(r) or m["subregions"].get(r)))
                                                  for r in m["countries"]}},
                "slots": {
                    "sizing_para": ("paragraph: base case 2026E to 2033F and why three scenarios", 65),
                    "scenario_conditions": ("object {Conservative, Base, Optimistic}: one sentence each on what has to be true", 74),
                    "type_para__Global": (f"[CHART] 2-3 sentences on WHY the global {tdim} split looks as it does", 87),
                    "demand_para__Global": (f"[CHART] 2-3 sentences on WHY the global {ddim} split looks as it does", 92),
                    "share_para": (f"[CHART] paragraph on WHY the regional shares in the {Y0 - 1}A pie look as they "
                                   "do: what anchors the largest regions' share and what keeps the smaller ones "
                                   "small", 97),
                    "abs_para": ("[CHART] paragraph on what drives the absolute dollar opportunity by region and what "
                                 "it means for where suppliers should focus", 102),
                    "highlights": ("object {" + ", ".join(r for _, r in highlight_regions(m)) + "}: 2 sentences "
                                   "each. " + "; ".join(f"{r} = {lab.lower()}" for lab, r in highlight_regions(m))
                                   + ". Say WHY (crops, industries, policy, buyers), with at most one figure", 107),
                    # no country_bridge / country_intro: section 3.5 is left out, each region has its own
                    # chapter (2026-10-19)
                    }},
        # B5: every country named in a table gets at least a short profile
        "ch3p": {"facts": {"countries_by_region": {r: _block(m["countries"][r], m["regions"][r])
                                                   for r in ("North America", "Europe", "Asia Pacific", "Latin America")},
                           "why_each_country_matters": m.get("geography_why") or {},
                           "rest_of_members": {r: (m.get("rest_members") or {}).get(r)
                                               for r in ("Europe", "Asia Pacific", "Latin America")}},
                 "slots": {"country_profiles": (
                     "object {<every country AND every 'Rest of' group named in FACTS.countries_by_region>: "
                     "<[CHART] 2-3 sentences on the REASONS behind its position and trajectory (crops, industry, policy, "
                     "climate, income) -- the table above already shows its size and CAGR, so no figures; a 'Rest of' group covers exactly the countries in "
                     "FACTS.rest_of_members; no segment figures -- this report has no segment "
                     "breakdown for these countries>}", 115)}},
        "ch4": {"facts": {"middle_east": _nd(m["subregions"]["Middle East"]),
                          "countries": _block(m["countries"]["Middle East"], m["subregions"]["Middle East"]),
                          "segments": {u: _segs(m, u) for u in ["Middle East"] + me_units if u in m["segments"]},
                          "use_cases": (ag.get("use_cases") or {}).get("middle_east"),
                          "rest_of_members": (m.get("rest_members") or {}).get("Middle East")},
                "slots": {
                    "region_para": ("opening paragraph: Middle East size, forecast, CAGR, absolute opportunity", 147),
                    "type_para__Middle East": (f"[CHART] 2-3 sentences on WHY the Middle East {tdim} split looks as it does", 162),
                    "demand_para__Middle East": (f"[CHART] 2-3 sentences on WHY the Middle East {ddim} split looks as it does", 167),
                    **{f"profile__{u}": (f"[CHART] profile of {u}, 2-4 sentences: its position among its peers and the REASONS "
                                         f"behind its trajectory (crops, industry, policy, climate, income, buyers) "
                                         f"-- the table and charts already show its size and growth"
                                         + (" -- it covers exactly the countries in FACTS.rest_of_members; "
                                            "describe only those" if u.startswith("Rest of") else ""), 170)
                       for u in m["profiled"]["Middle East"]},
                    **{f"type_para__{u}": (f"[CHART] 2 sentences on WHY {u}'s {tdim} split looks as it does", 175) for u in m["profiled"]["Middle East"]},
                    **{f"demand_para__{u}": (f"[CHART] 2 sentences on WHY {u}'s {ddim} split looks as it does", 180) for u in m["profiled"]["Middle East"]},
                    "where_to_play": ("list of 3 objects {lead: <2-4 words>, text: <1-2 sentences>}: largest pool, "
                                      "fastest-growing pool, adjacent upside", 225)}},
        "ch5": {"facts": {"africa": _nd(m["subregions"]["Africa"]),
                          "countries": _block(m["countries"]["Africa"], m["subregions"]["Africa"]),
                          "segments": {u: _segs(m, u) for u in ["Africa"] + af_units if u in m["segments"]},
                          "use_cases": (ag.get("use_cases") or {}).get("africa"),
                          "rest_of_members": (m.get("rest_members") or {}).get("Africa")},
                "slots": {
                    "region_para": ("opening paragraph: Africa size, forecast, CAGR and context", 227),
                    "type_para__Africa": (f"[CHART] 2-3 sentences on WHY the Africa {tdim} split looks as it does", 242),
                    "demand_para__Africa": (f"[CHART] 2-3 sentences on WHY the Africa {ddim} split looks as it does", 247),
                    **{f"profile__{u}": (f"[CHART] profile of {u}, 2-4 sentences: its position among its peers and the REASONS "
                                         f"behind its trajectory (crops, industry, policy, climate, income, buyers) "
                                         f"-- the table and charts already show its size and growth"
                                         + (" -- it covers exactly the countries in FACTS.rest_of_members; "
                                            "describe only those" if u.startswith("Rest of") else ""), 250)
                       for u in m["profiled"]["Africa"]},
                    **{f"type_para__{u}": (f"[CHART] 2 sentences on WHY {u}'s {tdim} split looks as it does", 255) for u in m["profiled"]["Africa"]},
                    **{f"demand_para__{u}": (f"[CHART] 2 sentences on WHY {u}'s {ddim} split looks as it does", 260) for u in m["profiled"]["Africa"]},
                    "where_to_play": ("list of 3 objects {lead: <2-4 words>, text: <1-2 sentences>}: anchor market, "
                                      "growth markets, business model", 302)}},
        "ch6": {"facts": {"regulation": {k: v for k, v in (ag.get("regulation") or {}).items() if k != "_sources"}},
                "slots": {
                    # chapter 6 is about the Middle East & Africa only (changes.txt 2026-10-19)
                    "reg_intro": ("paragraph on regulation as enabler and swing factor in the Middle East & "
                                  "Africa, naming MEA authorities or countries only", 304),
                    "policy_bullets": ("list of 4 bullets: policy outlook to 2030 in the Middle East & Africa, each "
                                       "naming the MEA countries or blocs it concerns", 316),
                    "entry_implication": ("2 sentences: implication for market entry in the Middle East & Africa", 324)}},
        "ch7": {"facts": {**vendor_facts(m)},          # no value chain: 7.1 removed (2026-10-19)
                "slots": {
                    # chapter 7 is the GLOBAL vendor view (changes.txt 2026-10-19)
                    "vendor_intro": ("paragraph: the GLOBAL vendor landscape -- what kinds of company make this "
                                     "product worldwide, naming only FACTS.vendors; no tiers, rankings or leaders; "
                                     "no MEA focus", 326),
                    "global_share_para": (("2 sentences on the published global vendor shares in "
                                           "FACTS.vendor_share_global_pct, for FACTS.vendor_year")
                                          if m["sections"].get("vendor_global", {}).get("show") else
                                          ("2 sentences on what the vendors in FACTS.vendor_positioning make (their role), "
                                           "with no tiers, rankings, shares or %"), 336),
                    "mea_share_para": (("2 sentences on the published MEA vendor shares in FACTS.vendor_share_mea_pct, "
                                        "naming FACTS.mea_leader as the leader")
                                       if m["sections"].get("vendor_mea", {}).get("show") else
                                       ("2 sentences on vendor positioning in the Middle East & Africa, naming "
                                        "FACTS.mea_leader as the leading vendor on CMI's assessment (no shares or %)"),
                                       341),
                    "implications": ("list of 3 competitive implications for the GLOBAL market", 351)}},
        "ch8": {"facts": {"strategy": {k: v for k, v in (ag.get("strategy") or {}).items()
                                       if k not in ("_sources", "priority_actions")}},
                "slots": {"reco_intro": ("paragraph introducing recommendations by stakeholder, with a common theme", 353),
                          # B9: every recommendation follows from a specific finding of the report
                          "priority_actions": ("list of 5 objects {lead: <one action for the next 12-18 months, "
                                               "8-15 words>, text: <'Finding: ' + the specific finding from "
                                               "FACTS.body_findings it follows from, with its figure and its "
                                               "context (e.g. 'In the optimistic scenario, ...')>} -- the finding "
                                               "must DIRECTLY justify the action, not merely be related", 361)}},
        # one opportunity-landscape chapter per non-MEA region (changes.txt 2026-10-19)
        "chR": {"facts": {r: {"region": _nd(m["regions"][r]),
                              "countries": _block(m["countries"][r], m["regions"][r]),
                              **({"segments": _segs(m, r)} if r in m["segments"] else {})}
                          for r in regional_chapters(m)},
                "slots": {k: v for r in regional_chapters(m) for k, v in {
                    f"region_para__{r}": (f"opening paragraph for the {r} chapter: WHY the {r} market grows at "
                                          f"the pace it does (crops, policy, buyers, climate) and which countries "
                                          f"lead, with at most 3 figures from FACTS.{r}.region", 147),
                    **({f"type_para__{r}": (f"[CHART] 2-3 sentences on WHY the {r} {tdim} split looks as it does",
                                            162),
                        f"demand_para__{r}": (f"[CHART] 2-3 sentences on WHY the {r} {ddim} split looks as it does",
                                              167)} if r in m["segments"] else {})}.items()}},
        # the methodology paragraph is method_text(); the template's 'Assumptions and limitations' list and
        # the chapter-closing bridge sentences are left out: they read as disclaimers and filler (2026-10-16)
    }
    return chs


# --------------------------------------------------------------------------- #
# chart echo: text beside a chart explains it, it does not read it out (2026-10-16)
# --------------------------------------------------------------------------- #
# slot -> how many figures the slot (or each item of a dict / list slot) may state
FIGURE_BUDGET = {"type_para": 1, "demand_para": 1, "share_para": 1, "abs_para": 1, "drivers_para": 1,
                 "drivers_para_global": 0, "matrix_para_global": 0, "trend_paras_mea": 0,
                 "matrix_para": 0, "value_chain_para": 1, "country_intro": 1, "country_profiles": 1, "profile": 1,
                 "highlights": 1, "trend_paras": 0, "global_bullets": 2, "region_para": 3, "sizing_para": 3}
_FIGURE = re.compile(r"[+\-−]?\$\s?\d[\d.,]*\s?(?:[BMK]\b|bn\b|billion\b|million\b)?|"
                     r"(?<![\w$.])\d[\d.,]*\s?(?:%|pp\b|percentage points?\b|x\b)")


_LEANS_BACK = re.compile(r"^\W*(?:[\w&'-]+[\s,]+){0,7}?(?:also\b|too\b|follows?(?=[,.;]|\s+(?:closely|at|with|on)\b)|"
                         r"close behind|trails?\b(?=[,.;]|\s+(?:at|with|on)\b)|is behind|are behind|"
                         r"in second place)", re.I)
# a paragraph cannot open on a sentence ranked against one that is not there ('Food Grade follows, ...')
_OPENS_MID = re.compile(r"^\W*(?:[\w&'-]+[\s,]+){0,7}?(?:follows?(?=[,.;]|\s+(?:closely|at|with|on)\b)|"
                        r"is close behind|are close behind|trails?\b(?=[,.;]|\s+(?:at|with|on)\b)|in second place)",
                        re.I)


def figures(s: str) -> int:
    """Money values, percentages, pp and multiples in a sentence (years are not figures)."""
    return len(_FIGURE.findall(s))


def chart_echo(slots: dict) -> list[str]:
    """In every slot of FIGURE_BUDGET, keeps sentences in order while the slot's figure budget
    lasts and drops any sentence that would exceed it -- such a sentence reads out the chart or
    table beside it. A sentence leaning on a dropped one ('This ...') goes too. Returns the log."""
    from AiAgentsFleet.cmi_report.guard import _ORPHAN, sentences
    log: list[str] = []

    def trim(text: str, budget: int) -> str:
        keep, used, gone, any_gone = [], 0, False, False
        for s in sentences(text):
            n = figures(s)
            # 'X follows' / 'Y is close behind' / 'Z also ...' points back at a removed sentence -- after a
            # removal, or at the top of the paragraph once its opening sentence has gone
            leans = ((gone or (any_gone and not keep)) and (_ORPHAN.match(s) or _LEANS_BACK.match(s))) or \
                (not keep and _OPENS_MID.match(s))
            if leans or used + n > budget:
                log.append(f"[restates the chart] {s[:110]}")
                gone = any_gone = True
                continue
            used, gone = used + n, False
            keep.append(s)
        # what is left only restates a figure ('Europe is the second-largest region at $2.88B in 2026E.'):
        # no reasoning survived, so the item goes (chapter openers, budget > 1, may state their headline)
        if budget <= 1 and keep and all(figures(s) for s in keep) and sum(len(s.split()) for s in keep) < 18:
            log.append(f"[only restates the chart] {' '.join(keep)[:110]}")
            return ""
        return " ".join(keep)

    def walk(v, budget):
        if isinstance(v, str):
            return trim(v, budget)
        if isinstance(v, list):
            return [x for x in (walk(x, budget) for x in v) if x]
        if isinstance(v, dict):
            if {"lead", "text"} <= v.keys():
                t = trim(str(v["text"] or ""), budget)
                return {**v, "text": t} if t else None
            return {k: x for k, x in ((k, walk(x, budget)) for k, x in v.items()) if x}
        return v
    for k in list(slots):
        base = k.split(".")[-1].split("__")[0]
        if base in FIGURE_BUDGET:
            v = walk(slots[k], FIGURE_BUDGET[base])
            if v:
                slots[k] = v
            else:
                del slots[k]
    return log


# --------------------------------------------------------------------------- #
# guards per slot
# --------------------------------------------------------------------------- #
def vendor_facts(m: dict) -> dict:
    """The vendor chapter's facts, from the final vendor data only (PLAN-05):
    sourced shares when there are enough, else the positioning table; never an
    excluded or unconfirmed company, never a share without a source."""
    if "vendor_year" not in m:              # a model built before PLAN-05 has no vendor data
        return {}
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
                                      "role": r["role"],
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
    if not leader or (m.get("vendor_unconfirmed") or {}).get("mea") or not (sec["vendor_mea"]["show"]
                                                                          or sec["positioning"]["show"]):
        return None             # an unconfirmed MEA section prints its data sentence instead (build.vendor_shares)
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


def _node(m: dict, geo: str) -> dict | None:
    """The model node of a geography (global, region, sub-region or country)."""
    if geo == "Global":
        return m["global"]
    return m["regions"].get(geo) or m["subregions"].get(geo) or \
        next((d[geo] for d in m["countries"].values() if geo in d), None)


def claim_groups(m: dict, geo: str | None) -> list[dict]:
    """Peer groups for the claim checks; `parent_cagr` is the group's peer average
    (its parent's CAGR) for 'momentum' / 'high-growth' claims."""
    mea = m["regions"]["Middle East & Africa"]
    gs = [{"names": m["regions"], "aliases": ALIASES, "parent_cagr": m["global"]["cagr"]},
          {"names": m["subregions"], "parent_cagr": mea["cagr"]}]
    gs += [{"names": d, "aliases": ALIASES, "parent_cagr": (_node(m, r) or {}).get("cagr")}
           for r, d in m["countries"].items()]
    if geo and geo in m["segments"]:
        parent = (_node(m, geo) or {}).get("cagr")
        gs += [{"names": m["segments"][geo]["type"], "parent_cagr": parent},
               {"names": m["segments"][geo]["demand"], "parent_cagr": parent}]
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


def _other_share(rows) -> float | None:
    """The 'Regional & other' slice of a vendor share chart (None when the chart is empty)."""
    rows = list(rows or [])
    if not rows:
        return None
    return sum(float(s) for n, s in rows if "other" in str(n).lower())


def make_guard(m: dict, geo: str | None, ag: dict | None = None) -> ClaimGuard:
    g = ClaimGuard(claim_groups(m, geo), m["global"]["cagr"], _TPL_SENT)
    # contradiction_guard inputs: vendor concentration, MEA restraints, names outside the MEA
    g.other_share = {k: v for k, v in (("global", _other_share(m.get("vendor_chart_global"))),
                                       ("mea", _other_share(m.get("vendor_chart_mea")))) if v is not None}
    dyn = (ag or {}).get("dynamics") or {}
    g.restraints = [x.get("name", "") for x in dyn.get("mea_restraints") or [] if x.get("name")]
    g.non_mea = [r for r in m["regions"] if r != "Middle East & Africa"] + \
        [c for r, d in m["countries"].items() if r not in ("Middle East", "Africa") for c in d] + \
        ["global", "globally", "worldwide"]
    # segment data exists only for the Middle East & Africa units: no segment claims for other countries
    with_breakdown = {u for v in m["profiled"].values() for u in v}
    g.no_breakdown = [c for r, d in m["countries"].items() for c in d
                      if c not in with_breakdown and not c.startswith("Rest of")]
    g.segment_names = [n for dims in m["segments"].get("Global", {}).values() for n in dims
                       if not n.lower().startswith("other")]
    # only CMI's own published figure is presented as published; anything else is worded as a CMI estimate
    g.headline_unsourced = not ((m.get("size_check") or {}).get("status") == "sourced"
                                and (m.get("size_check") or {}).get("source_is_cmi"))
    g.definition = m.get("definition")       # no sentence may describe another value-chain level (PLAN-04)
    g.vendor_text = m.get("vendor_text")     # vendors named only from the final vendor data (PLAN-05)
    # regulators, laws and dated policies only from the verified fact list (PLAN-06); None = no check ran
    g.verified_facts = (m["fact_review"].get("verified_facts") or []) if m.get("fact_review") else None
    return g


def _geo_of(slot: str, ch: str) -> str | None:
    if "__" in slot:
        return slot.split("__", 1)[1]
    return {"ch1": "Global", "ch3": "Global", "ch4": "Middle East", "ch5": "Africa"}.get(ch)


def _guard(text, ng, cg, dropped, absent) -> str:
    out = clean_text(fix_plurals(text, getattr(cg, "segment_names", None)), ng, cg, dropped, absent)
    out = vendor_clean(out, getattr(cg, "vendor_text", None), dropped)
    out = drop_size_comparison(out, dropped, getattr(cg, "headline_unsourced", False))
    facts = getattr(cg, "verified_facts", None)
    if facts is None:
        return out
    keep = []
    for s in sentences(out):
        why = unverified_fact(s, facts)
        if why:
            dropped.append(f"[unverified fact: {why}] {s[:120]}")
        else:
            keep.append(s)
    return " ".join(keep)


def _clean(v, ng, cg, dropped, absent, limit=110):
    """Guards a slot value of any shape. In a {global, mea} object (the scorecard
    cells) the global cell may not carry MEA-basis figures (FINDINGS X14)."""
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
                # the key prefix goes in any case, and also one the model wrote itself ('Global: Global: ...',
                # 'Mea: ...' in the scorecard cells; user report 2026-10-18)
                c = re.sub(rf"^(?:\s*{re.escape(k)}\s*:\s*)+", "", c, flags=re.I)
                c = re.sub(r"\bMea\b", "MEA", c)
                if k == "global" and set(v) <= {"global", "mea"}:
                    c = drop_mea_basis(c, dropped)
                out[k] = cap_words(c, 60) or None
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


def prepare(m: dict, ag: dict) -> dict:
    """Ties the MEA drivers / restraints to the CAGRs (rules.reconcile_drivers), in
    place, so the register, the prose facts, the chart and the tables agree.
    Idempotent; write() and reguard() call it."""
    dyn = ag.get("dynamics")
    if isinstance(dyn, dict):
        rules.reconcile_drivers(dyn, m["global"]["cagr"], m["regions"]["Middle East & Africa"]["cagr"])
    return ag


def write(market_dir: Path, m: dict, ag: dict, llm, template: Path, evidence_fn=None, log=print,
          judge_llm=None) -> dict:
    """`judge_llm` (default: `llm`) answers the yes/no checks: action support and domain sanity."""
    prepare(m, ag)
    tpl = template_texts(template)
    set_template(template)
    facts0 = base_facts(m)
    out = {"version": PROSE_VERSION, "slots": {}, "dropped": {}}
    sec = m["sections"]
    hidden = {"type_para": not sec["type"]["show"], "demand_para": not sec["demand"]["show"],
              # a vendor section without confirmed vendors prints one data sentence, not generated text
              "global_share_para": bool((m.get("vendor_unconfirmed") or {}).get("global")),
              "mea_share_para": bool((m.get("vendor_unconfirmed") or {}).get("mea"))}
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
        if not slots:
            continue
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
            cg = make_guard(m, _geo_of(k, ch), ag)
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
        if ch == "ch8" and out["slots"].get("priority_actions"):
            # B9 / X13: every action must follow from the finding it cites (before the summary uses them)
            acts, log_a = check_actions(out["slots"]["priority_actions"], m, ag, judge_llm or llm, llm, fact_guard,
                                        out["slots"])
            out["slots"]["priority_actions"] = acts
            if log_a:
                out["dropped"]["priority_actions"] = log_a
                log(f"  [actions] {len(log_a)} action check note(s); {len(acts)} action(s) kept")
            if not acts:
                del out["slots"]["priority_actions"]
    out["slots"] = in_document_order(out["slots"], specs)
    echo = chart_echo(out["slots"])
    if echo:
        out["dropped"]["chart_echo"] = echo
        log(f"  [chart echo] {len(echo)} sentence(s) reading out a chart or table removed")
    repeats = dedupe(out["slots"], m)
    if repeats:
        out["dropped"]["repetition"] = repeats
        log(f"  [repetition] {len(repeats)} repeated sentence(s) removed")
    phrases = rewrite_repeated_phrases(out["slots"], m, ag, llm, fact_guard, specs)
    if phrases:
        out["dropped"]["repeated_phrases"] = phrases
        log(f"  [boilerplate] {len(phrases)} sentence(s) with a repeated phrase rewritten or removed")
    insane = domain_sanity(out["slots"], m, judge_llm or llm)
    if insane:
        out["dropped"]["domain_sanity"] = insane
        log(f"  [domain sanity] {len(insane)} sentence(s) with a likely false real-world claim removed")
    fixed = mea_leader_named(out["slots"], m)
    if fixed:
        out["vendor_check"] = [fixed]
        log(f"  [vendors] {fixed}")
    (market_dir / "cmi_prose.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def body_findings(slots: dict, limit: int = 40) -> list[str]:
    """Sentences of the finished body chapters -- what the executive summary and the
    recommendations must be built from. Number-bearing ones first; the body's reasoning
    sentences (which carry few figures, see chart_echo) follow."""
    from AiAgentsFleet.cmi_report.guard import sentences
    found, reasons = [], []
    context = {"scenario_conditions": "{k} scenario", "highlights": "{k}", "country_intro": "{k}",
               "country_profiles": "{k}"}

    def walk(v, prefix=""):
        # each finding keeps its context ('Optimistic scenario: ...'), so a quote can't change its meaning
        if isinstance(v, str):
            for s in sentences(v):
                (found if re.search(r"\d", s) else reasons).append(f"{prefix}{s}")
        elif isinstance(v, list):
            for x in v:
                walk(x, prefix)
        elif isinstance(v, dict):
            for k, x in v.items():
                walk(x, prefix + (context[slot].format(k=k) + ": " if slot in context else ""))
    for slot, v in slots.items():
        if slot.split(".")[-1].split("__")[0] not in _SUMMARY_SLOTS:
            walk(v)
    return (found + [r for r in reasons if len(r.split()) >= 8])[:limit]


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
    reused. Returns the number of sentences removed. A phrase repeated in 3+
    sentences is removed from all but the first (rewriting needs the LLM)."""
    prepare(m, ag)
    slots = pr.get("slots") or {}
    before = json.dumps(slots, ensure_ascii=False)
    absent = absent_names(m)
    dropped: list[str] = []
    from AiAgentsFleet.cmi_report.register import FactGuard, build_register
    fact_guard = FactGuard(build_register(m, ag))
    specs = chapters(m, ag)
    for ch, spec in specs.items():
        ng = fact_guard
        for k in spec["slots"]:
            key = k if ch not in ("ch4", "ch5") or "__" in k else f"{ch}.{k}"
            if key in slots:
                v = _clean(slots[key], ng, make_guard(m, _geo_of(k, ch), ag), dropped, absent)
                if v:
                    slots[key] = v
                else:
                    del slots[key]
    # text saved before the methodology was made deterministic (method_text)
    slots.pop("method_para", None)
    if isinstance(slots.get("priority_actions"), list):
        # the deterministic part of the action check (the judge needs the LLM)
        acts, notes = check_actions(slots["priority_actions"], m, ag, None, None, fact_guard, slots)
        dropped += notes
        if acts:
            slots["priority_actions"] = acts
        else:
            del slots["priority_actions"]
    # slots of the removed closing / assumptions text (PROSE_VERSION < 26)
    for k in [k for k in slots if re.search(r"(?:^|\.)closing_ch\d$", k) or k == "assumptions"]:
        del slots[k]
    dropped += chart_echo(slots)
    dropped += dedupe(slots, m)
    dropped += rewrite_repeated_phrases(slots, m, ag, None, fact_guard, specs)
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
_SUMMARY_SLOTS = {"thesis", "practice", "cover_subtitle", "about_scope", "definition_para", "exec_para", "global_bullets", "mea_bullets",
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
# post-passes (PLAN-07): action support, repeated phrases, domain sanity
# --------------------------------------------------------------------------- #
def _map_sentences(slots: dict, fn) -> None:
    """Applies fn(slot_key, sentence) -> sentence | None to every sentence of
    every slot, in place. A sentence that leans on a removed one ('This ...')
    goes with it; emptied strings, items and slots are removed."""
    from AiAgentsFleet.cmi_report.guard import _ORPHAN, sentences

    def walk(key, v):
        if isinstance(v, str):
            keep, gone = [], False
            for s in sentences(v):
                new = None if gone and _ORPHAN.match(s) else fn(key, s)
                gone = new is None
                if new:
                    keep.append(new)
            return " ".join(keep)
        if isinstance(v, list):
            return [x for x in (walk(key, x) for x in v) if x]
        if isinstance(v, dict):
            if {"lead", "text"} <= v.keys():
                t = walk(key, v["text"])
                return {**v, "text": t} if t else None
            return {k: x for k, x in ((k, walk(key, x)) for k, x in v.items()) if x}
        return v
    for k in list(slots):
        v = walk(k, slots[k])
        if v:
            slots[k] = v
        else:
            del slots[k]


def _slot_specs(specs: dict) -> dict[str, tuple[str, str]]:
    """slot key as saved -> (chapter, slot name)."""
    return {(k if ch not in ("ch4", "ch5") or "__" in k else f"{ch}.{k}"): (ch, k)
            for ch, spec in specs.items() for k in spec["slots"]}


_JUDGE_SYSTEM = (
    "You are a strict editor of a market whitepaper whose focus is the Middle East & Africa (MEA). You get one "
    "priority action and the finding it cites. Judge two things:\n"
    "1. support: 'yes' only if the finding DIRECTLY justifies the action; 'weak' if it is related but indirect; "
    "'no' if the finding is about another segment, country, region or topic, or points the other way (e.g. the "
    "slowest segment cited for an expansion, a restraint cited as the reason to invest, a market size cited for "
    "localising production in a tiny market).\n"
    "2. geography_matches_focus: false if the action targets regions outside the Middle East & Africa.\n"
    "Return ONLY JSON: {\"support\": \"yes\"|\"weak\"|\"no\", \"geography_matches_focus\": true|false, "
    "\"why\": <one sentence>}.")


def _action_text(a) -> tuple[str, str]:
    if isinstance(a, dict):
        return str(a.get("lead") or ""), str(a.get("text") or "")
    lead, _, text = str(a).partition(":")
    return lead, text


def action_problem(a, m: dict, ag: dict, judge) -> tuple[str | None, str | None]:
    """(why the action fails, a note for a weak one). Deterministic checks first:
    every entity the action names must fit its data (a 'high-growth' country that
    grows below its peer average fails); then one judge call."""
    lead, text = _action_text(a)
    finding = re.sub(r"^\s*Finding:\s*", "", text)
    cg = make_guard(m, None, ag)
    for label, part in (("action", lead), ("finding", finding)):
        why = contradiction_guard(part, cg) or cg.problem(part) or cg.ordinal(part)
        if why:
            return f"{label} contradicts the data: {why}", None
    if judge is None:
        return None, None
    try:
        v = judge.complete_json(_JUDGE_SYSTEM, f"MARKET: {m['market']}\nACTION: {lead}\nCITED FINDING: {finding}") or {}
    except Exception as exc:  # noqa: BLE001
        return None, f"judge unavailable: {exc}"
    support = str(v.get("support") or "").lower()
    if v.get("geography_matches_focus") is False:
        return f"geography outside the MEA focus: {v.get('why', '')}", None
    if support == "no":
        return f"finding does not support the action: {v.get('why', '')}", None
    return None, (f"weak support: {v.get('why', '')}" if support == "weak" else None)


def check_actions(acts: list, m: dict, ag: dict, judge, writer, ng, slots: dict) -> tuple[list, list[str]]:
    """Each priority action is checked (action_problem); a failing one is
    regenerated once by `writer` with the judge's reason and re-checked, and
    dropped if it still fails. Returns (kept actions, log)."""
    kept, notes = [], []
    absent = absent_names(m)
    for a in acts:
        why, note = action_problem(a, m, ag, judge)
        if why and writer is not None:
            lead, text = _action_text(a)
            user = (f"MARKET: {m['market']}\n\nFACTS (the only allowed source of numbers):\n"
                    f"{json.dumps({'body_findings': body_findings(slots)}, ensure_ascii=False)}\n\n"
                    f"This priority action was rejected: {lead} -- {text}\nReason: {why}\n\n"
                    "SLOTS (key: what to write):\n- action: object {lead: <one action for the next 12-18 months "
                    "in the Middle East & Africa, 8-15 words>, text: <'Finding: ' + the finding from "
                    "FACTS.body_findings that DIRECTLY justifies it, with its figure>}")
            try:
                new = (writer.complete_json(_SYSTEM, user) or {}).get("action")
            except Exception:  # noqa: BLE001
                new = None
            dropped: list[str] = []
            new = _clean(new, ng, make_guard(m, None, ag), dropped, absent) if isinstance(new, dict) else None
            if new and str(new.get("text", "")).startswith("Finding"):
                why2, note = action_problem(new, m, ag, judge)
                notes.append(f"[regenerated] {lead[:60]} -- {why}")
                if not why2:
                    kept.append(new)
                    continue
                why = why2
        if why:
            notes.append(f"[action dropped] {_action_text(a)[0][:80]} -- {why}")
            continue
        if note:
            notes.append(f"[kept, {note[:100]}] {_action_text(a)[0][:60]}")
        kept.append(a)
    return kept, notes


# words that make up the report's sizing sentences: a phrase of only these is data, not boilerplate
_DATA_WORDS = set("""cagr cagrs estimate estimates estimated forecast forecasts forecasted largest smallest fastest slowest
fastest-growing slowest-growing second-largest third-largest opportunity opportunities absolute incremental market markets
size sizes share shares valued value values reach reaches reaching rising rise rises grow grows growing growth type types
segment segments cmi e f by in at of to from and the a an is are with remains remain also its it while as for over between
indicate indicates show shows""".split())


def repeated_phrases(slots: dict, m: dict, n: int = 5, min_count: int = 3) -> dict[str, str]:
    """{sentence: phrase} for every body sentence but the first that shares a
    phrase of `n`+ words with `min_count`-1 other sentences ('This trend is most
    visible in the Middle East' x6). Names, figures and sizing vocabulary don't
    count: 'X is the largest Product Type in Y' is a data statement, not filler."""
    from AiAgentsFleet.cmi_report.guard import sentences
    names = set(m["regions"]) | set(m["subregions"]) | {c for d in m["countries"].values() for c in d} | \
        {s for g in m["segments"].values() for d in g.values() for s in d} | \
        {m["dimensions"][d][k] for d in ("type", "demand") for k in ("singular", "plural")} | {"MEA", "UAE", "KSA"}
    root = re.sub(r"(?i)^\s*global\s+|\s+market\s*$", "", m["market"])
    # the market's and the segments' own words ('farm advisory' in 'generative AI for farm advisory')
    words = {w for x in [root] + [s for g in m["segments"].values() for d in g.values() for s in d]
             for w in re.findall(r"[A-Za-z][A-Za-z-]+", x) if len(w) > 3}
    names |= {root} | words | {w.rstrip("s") for w in words}
    rx = re.compile("|".join(re.escape(x) for x in sorted(names, key=len, reverse=True) if x), re.I)
    order: list[str] = []

    def walk(v):
        if isinstance(v, str):
            order.extend(sentences(v))
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
    for k, v in slots.items():
        if k.split(".")[-1].split("__")[0] not in _SUMMARY_SLOTS:
            walk(v)
    where: dict[str, list[int]] = {}
    for i, s in enumerate(order):
        w = re.findall(r"\$?\d[\d.,]*[%bm]?|[a-z][a-z'-]*", rx.sub(" 0 ", s).lower())
        for j in range(len(w) - n + 1):
            g = w[j:j + n]
            if any(re.match(r"[\d$]", t) for t in g) or sum(t not in _DATA_WORDS for t in g) < 3:
                continue
            ix = where.setdefault(" ".join(g), [])
            if not ix or ix[-1] != i:
                ix.append(i)
    out: dict[str, str] = {}
    for phrase, ix in where.items():
        if len(ix) >= min_count:
            for i in ix[1:]:
                if i != ix[0] and order[i] != order[ix[0]]:
                    out.setdefault(order[i], phrase)
    return out


_REWRITE_SYSTEM = (
    "You edit a market whitepaper. Each sentence below repeats a stock phrase used elsewhere in the report. "
    "Rewrite each one so it no longer uses that phrase: keep every number, name and claim exactly as written, add "
    "nothing, one sentence each, UK English. Return ONLY JSON {\"rewrites\": [{\"id\": <id>, \"text\": <sentence>}]}.")


def rewrite_repeated_phrases(slots: dict, m: dict, ag: dict, llm, ng, specs: dict) -> list[str]:
    """Rewrites (one LLM call) every sentence but the first that repeats a 5+ word
    phrase; a rewrite that fails the guards or keeps the phrase -- or every such
    sentence when `llm` is None -- is removed. Returns the log."""
    hits = repeated_phrases(slots, m)
    if not hits:
        return []
    items = list(hits.items())
    new: dict[str, str] = {}
    if llm is not None:
        try:
            raw = llm.complete_json(_REWRITE_SYSTEM, json.dumps(
                [{"id": i, "phrase": p, "sentence": s} for i, (s, p) in enumerate(items)], ensure_ascii=False)) or {}
            for r in raw.get("rewrites") or []:
                if isinstance(r, dict) and isinstance(r.get("id"), int) and 0 <= r["id"] < len(items):
                    new[items[r["id"]][0]] = str(r.get("text") or "").strip()
        except Exception:  # noqa: BLE001
            new = {}
    where = _slot_specs(specs)
    absent = absent_names(m)
    log: list[str] = []

    def fix(key: str, s: str) -> str | None:
        if s not in hits:
            return s
        ch, k = where.get(key, (None, key))
        r = new.get(s)
        if r and hits[s] not in " ".join(re.findall(r"[a-z][a-z'-]*", r.lower())):
            dropped: list[str] = []
            r = _guard(r, ng, make_guard(m, _geo_of(k, ch) if ch else None, ag), dropped, absent)
            if r:
                log.append(f"[rewritten: '{hits[s]}'] {s[:90]} -> {r[:90]}")
                return r
        log.append(f"[repeated phrase '{hits[s]}' removed] {s[:110]}")
        return None
    _map_sentences(slots, fix)
    return log


_SANITY_SYSTEM = (
    "You are a domain fact-checker for a market-research whitepaper. You get numbered sentences from the report. "
    "List ONLY the sentences that state a real-world fact -- about an industry, technology, crop, country, "
    "company or policy -- that is likely false or unsupported. Examples: calling a country a key producer of "
    "something it imports; calling a technology that has been commercial for years 'pilot-scale'; the post-Brexit "
    "UK as a gateway to the EU market; a nutrient deficiency unrelated to the product; a market 'already mature' "
    "for a nascent technology; tighter rules said to sustain demand. Do NOT flag market sizes, shares, growth rates "
    "or rankings (CMI estimates, checked elsewhere), forecasts, or opinions. When unsure, do not flag. "
    "Return ONLY JSON {\"flagged\": [{\"id\": <int>, \"why\": <short reason>}]}.")


def domain_sanity(slots: dict, m: dict, llm, batch: int = 60) -> list[str]:
    """Drops sentences the domain-sanity judge flags as stating a likely false
    real-world fact (FINDINGS B7, D7, E7, F5). Returns the log."""
    if llm is None:
        return []
    from AiAgentsFleet.cmi_report.guard import sentences
    allsent: list[str] = []

    def walk(v):
        if isinstance(v, str):
            allsent.extend(s for s in sentences(v) if len(s.split()) >= 6)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
    for k, v in slots.items():
        if k not in ("practice", "cover_subtitle"):
            walk(v)
    allsent = list(dict.fromkeys(allsent))
    flagged: dict[str, str] = {}
    for start in range(0, len(allsent), batch):
        part = allsent[start:start + batch]
        user = f"MARKET: {m['market']}\n\nSENTENCES:\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(part))
        try:
            raw = llm.complete_json(_SANITY_SYSTEM, user) or {}
        except Exception:  # noqa: BLE001
            continue
        for f in raw.get("flagged") or []:
            if isinstance(f, dict) and isinstance(f.get("id"), int) and 0 <= f["id"] < len(part):
                flagged[part[f["id"]]] = str(f.get("why") or "")
    log: list[str] = []

    def fix(_key: str, s: str) -> str | None:
        if s in flagged:
            log.append(f"[domain sanity: {flagged[s][:80]}] {s[:110]}")
            return None
        return s
    if flagged:
        _map_sentences(slots, fix)
    return log


# --------------------------------------------------------------------------- #
# structure helpers for build.py (PLAN-07)
# --------------------------------------------------------------------------- #
def fill_conclusions(items: list, slots: dict, need: int = 3) -> list:
    """'Key conclusions' with at least `need` items: the prose's own, then the
    first body findings that are not already there. Fewer than `need` -> []
    (the box is left out)."""
    out = [x for x in items or [] if isinstance(x, str) and x.strip()]
    if len(out) >= need:
        return out
    # data-led slots first (where the opportunity is), then the rest of the body
    first = ("abs_para", "share_para", "highlights", "ch4.region_para", "ch5.region_para", "sizing_para",
             "country_intro")
    body = {k: slots[k] for k in first if k in slots} | \
        {k: v for k, v in slots.items() if k.split(".")[-1].split("__")[0] not in _SUMMARY_SLOTS}
    have = [_words(x) for x in out]
    for f in body_findings(body, limit=200):
        w = _words(f)
        # out of its paragraph, 'This trend ...' / '... these farms' points at nothing
        if re.search(r"\b(this|these|those|such|together|they|it|figure|chart|table)\b", f.split(": ", 1)[-1], re.I) \
                or len(w) < 6 \
                or any(len(w & h) / len(w | h) >= 0.5 for h in have):
            continue
        out.append(f)
        have.append(w)
        if len(out) >= need:
            return out
    return out if len(out) >= need else []


_ORDINAL = {1: "the largest", 2: "the second-largest", 3: "the third-largest"}
_ORD_WORD = {2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth",
             10: "tenth", 11: "eleventh", 12: "twelfth", 13: "thirteenth", 14: "fourteenth", 15: "fifteenth"}
_WITH_THE = {"United States", "United Kingdom", "UAE", "Netherlands", "Philippines", "Czech Republic",
             "Dominican Republic", "Middle East"}


def profile_lead(unit: str, d: dict, parent: dict | None, parent_name: str, peers: dict | None = None) -> str:
    """A country profile's lead sentence from the data, without restating the table beside it:
    its size rank among its peers and its growth against the parent's CAGR (near-ties, rules.TIE_PP,
    read 'in line with')."""
    peers = peers or {}
    who = f"The {unit}" if unit in _WITH_THE else unit
    mid = f"the {unit}" if unit in _WITH_THE else unit           # the same name inside a sentence
    if unit not in peers or not parent or parent.get("cagr") is None:
        return (f"{unit} groups the remaining markets of {parent_name}." if unit.startswith("Rest of")
                else f"{who} is a market in {parent_name}.")
    # each lead carries this unit's own size and growth rank among its peers, in one of three phrasings
    # (one fixed sentence with only the name changed read as a repeat: QA 2026-10-20)
    size = sorted(peers, key=lambda k: -peers[k]["v26"]).index(unit) + 1
    growth = sorted(peers, key=lambda k: -peers[k]["cagr"]).index(unit) + 1
    big = "the largest" if size == 1 else f"the {_ORD_WORD.get(size, 'smaller')}-largest"
    fast = "the fastest" if growth == 1 else f"the {_ORD_WORD.get(growth, 'slower')}-fastest"
    gap = d["cagr"] - parent["cagr"]
    pace = ("in line with" if abs(gap) < rules.TIE_PP else "faster than" if gap > 0 else "more slowly than")
    # its share of the parent in Y0 (a register figure the table beside it does not show): a figure of its own
    share = fmt.share(d["v26"], sum(v["v26"] for v in peers.values()))
    if unit.startswith("Rest of"):
        what = f"{unit}, which groups the remaining markets of {parent_name},"
        return [f"Taken together, {what} holds {share} of regional value in {Y0}E as {big} unit and grows "
                f"{pace} the regional total.",
                f"{what[0].upper() + what[1:]} accounts for {share} of the {Y0}E total and expands {pace} "
                f"{parent_name} as a whole.",
                f"As a group, {what} carries {share} of {Y0}E value and has {fast} growth rate among its peers "
                f"through {Y1}F."][(size - 1) % 3]
    return [f"{who} is {big} market in {parent_name}, with {share} of {Y0}E value and {fast} growth rate, "
            f"expanding {pace} the regional average through {Y1}F.",
            f"Holding {share} of {parent_name}'s {Y0}E value, {mid} is {big} market there and moves {pace} the "
            f"region with {fast} growth rate among its peers.",
            f"With {fast} growth rate in {parent_name} through {Y1}F, {mid} grows {pace} the region from a "
            f"{share} share of {Y0}E value, {big} by size."][(size - 1) % 3]


def with_lead(unit: str, text: str | None, d: dict, parent: dict | None, parent_name: str,
              peers: dict | None = None) -> str:
    """A profile's text as written when its opening sentence names the unit and is a real sentence (the
    table and charts carry the numbers); otherwise profile_lead() opens it."""
    from AiAgentsFleet.cmi_report.guard import sentences
    sents = sentences(text or "")
    names = {unit, ALIASES.get(unit, unit)} | {k for k, v in ALIASES.items() if v == unit} | \
        ({"Emirates"} if unit == "UAE" else set())
    if sents and len(sents[0].split()) >= 6 and any(re.search(rf"\b{re.escape(n)}\b", sents[0]) for n in names):
        return " ".join(sents)
    return " ".join([profile_lead(unit, d, parent, parent_name, peers)] + sents)


# --------------------------------------------------------------------------- #
# data-only fallbacks (used when a slot is empty after the guards)
# --------------------------------------------------------------------------- #
def _names(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]



def seg_insight(seg: dict, dim: str, geo: str) -> str | None:
    """A one-to-two-line key insight under a segment chart when the written paragraph did not survive the
    guards (user decision 2026-10-18: every segment chart has a line of analysis). It states the ranking the
    chart implies -- the largest segment, the fastest grower, how the mix shifts -- and repeats no figure.
    Leaders within the tie threshold (rules.TIE_PP / TIE_REL) are named together."""
    seg = {k: v for k, v in (seg or {}).items() if v.get("v33")}
    if len(seg) < 2:
        return None
    where = "globally" if geo == "Global" else f"in {geo}"
    bigs, fasts = rules.top_tied(seg, "v26"), rules.top_tied(seg, "cagr")
    plural = len(bigs) > 1 or bigs[0].endswith("s")
    # the leader's share of the Y0 mix (a register figure the value chart does not show), so the insight
    # under each chart carries a figure of its own and never reads as a copy of another (QA 2026-10-20)
    lead_share = fmt.share(seg[bigs[0]]["v26"], sum(v["v26"] for v in seg.values()))
    big = (f"{_names(bigs)} are level as the largest {dim.lower()} segments {where}" if len(bigs) > 1 else
           f"{bigs[0]} {'are' if plural else 'is'} the largest {dim.lower()} segment {where} at {lead_share} of "
           f"the {Y0}E mix")
    if set(fasts) == set(bigs):
        return big + " and also grow" + ("" if plural else "s") + " fastest, so the mix concentrates further " \
            f"on {'them' if plural else 'it'} through {Y1}F."
    fast = _names(fasts)
    verb = "grow" if len(fasts) > 1 or fasts[0].endswith("s") else "grows"
    if len(bigs) == 1 and len(fasts) == 1:
        # one fixed sentence with only the geography changed read as a repeat (QA 2026-10-20): three phrasings,
        # chosen by the geography so a report always reads the same way
        their = "their" if fasts[0].endswith("s") else "its"
        holds = "hold" if plural else "holds"
        k = zlib.crc32(geo.encode()) % 3
        if k == 1:
            return (f"{where[0].upper() + where[1:]}, {bigs[0]} {holds} the biggest slice of the {dim.lower()} mix "
                    f"({lead_share} in {Y0}E), but {fast} {verb} fastest and gain{'' if verb == 'grow' else 's'} "
                    f"share by {Y1}F.")
        if k == 2:
            return (f"{fast} {verb} fastest {where}, lifting {their} weight in the {dim.lower()} mix through "
                    f"{Y1}F, while {bigs[0]} remain{'' if plural else 's'} the largest segment at {lead_share} of "
                    f"{Y0}E value.")
    return (f"{big}, while {fast} {verb} fastest" + (" at a similar rate" if len(fasts) > 1 else "")
            + f", so {'their' if len(fasts) > 1 or fasts[0].endswith('s') else 'its'} share of the {dim.lower()} "
            f"mix rises through {Y1}F.")


def slug_safe(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")

"""Builds the full section-by-section CONTENT PLAN for one market's
whitepaper -- same 15-section structure as `vitamin_c_market_whitepaper_v2.docx`
-- as a single JSON file that `build_docx.mjs` (docx-js) turns into the
actual .docx. Keeping the content plan as data (not inline in the JS) means
the section text/numbers are entirely generated here, in Python, from each
market's (already consistency-reconciled, see `../consistency.py`)
`whitepaper_fields.json` -- the JS side is a pure, market-agnostic layout
renderer.

Usage:
    python build_content.py --market-slug super-intensive-shrimp-farming-market [--allow-warnings]
Writes: AiAgentsFleet/output/<slug>/content_plan.json
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from narrative import build_narrative, fmt_pct, fmt_usd_mn

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from AiAgentsFleet.consistency import concentration_label  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_ROOT = ROOT / "output"


def _section(num: int, title: str, confidence: tuple[str, str], paragraphs: list[str],
             figures: list[dict] | None = None, bullets: list[dict] | None = None,
             tables: list[dict] | None = None, subheads: list[dict] | None = None) -> dict:
    return {
        "num": num, "title": title,
        "confidence_label": confidence[0], "confidence_note": confidence[1],
        "paragraphs": paragraphs, "figures": figures or [], "bullets": bullets or [],
        "tables": tables or [], "subheads": subheads or [],
    }


# --------------------------------------------------------------------------- #
# Confidence: computed per section from evidence, NOT a fixed constant.
# --------------------------------------------------------------------------- #
def compute_confidence(group_fields: dict | None, changes: list[str] | None,
                       n_snippets: int | None = None) -> tuple[str, str]:
    sources = (group_fields or {}).get("_sources")
    n_changes = len(changes or [])
    if sources is None:
        # Generated before per-source capture existed: say so, rather than
        # implying a search ran and found zero sources.
        note = ("Source URLs were not recorded for this run"
                + (f" (synthesized from {n_snippets} web snippets)" if n_snippets else "")
                + "; figures are unverified")
        note += f", and {n_changes} required consistency correction." if n_changes else "."
        return ("LOW", note)
    n_domains = len([d for d in sources.get("domains") or [] if not _is_low_quality_domain(d)])
    if n_domains >= 3 and n_changes == 0:
        return ("HIGH", f"Cross-validated across {n_domains} independent source domains; no consistency adjustments needed.")
    if n_domains >= 2 and n_changes <= 2:
        note = f"Based on {n_domains} source domains"
        note += f"; {n_changes} figure(s) auto-corrected for internal consistency." if n_changes else "."
        return ("MEDIUM", note)
    note = f"Limited source coverage ({n_domains} domain(s) found)"
    note += f"; {n_changes} figure(s) required consistency correction." if n_changes else "."
    return ("LOW", note)


# User-generated / unrelated sites that web search sometimes returns. They
# are still listed in Section 16, but don't count toward confidence.
_LOW_QUALITY_DOMAINS = {
    "youtube.com", "quizlet.com", "slideshare.net", "studyx.ai", "medium.com", "teletype.in",
    "linkedin.com", "polymarket.com", "reddit.com", "quora.com", "pinterest.com", "facebook.com",
    "bloxfruitstradehub.com", "pillarofgaming.com", "mrguider.org", "globalpetrolprices.com",
}


def _is_low_quality_domain(domain: str) -> bool:
    d = (domain or "").lower()
    return d in _LOW_QUALITY_DOMAINS or d.endswith(".baby")


def _fmt_pct_range(lo: float, hi: float) -> str:
    lo, hi = float(lo or 0), float(hi or 0)
    fmt = (lambda v: f"{v:.1f}") if max(lo, hi) < 10 else (lambda v: f"{v:.0f}")
    return f"about {fmt(hi)}%" if fmt(lo) == fmt(hi) else f"{fmt(lo)}-{fmt(hi)}%"


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


_SNIPPETS_RE = re.compile(r"^\[(\w+)\].*?synthesized from (\d+) web snippets")


def _snippet_counts(log: list[str]) -> dict[str, int]:
    out = {}
    for line in log or []:
        m = _SNIPPETS_RE.match(str(line))
        if m:
            out[m.group(1)] = int(m.group(2))
    return out


def _phrase(name: str) -> str:
    """Mid-sentence form of a driver/restraint name: lowercase the first
    letter, but leave acronyms ("AI adoption", "EU rules") intact."""
    name = (name or "").strip()
    first = name.split(" ", 1)[0]
    if not name or (len(first) > 1 and first[1:2].isupper()):
        return name
    return name[:1].lower() + name[1:]


def _fmt_price(v: float) -> str:
    v = float(v or 0)
    if v == 0:
        return "0"
    return f"{v:,.2f}" if abs(v) < 10 else f"{v:,.0f}"


def _vs_market(seg_cagr: float, market_cagr: float) -> str:
    diff = (seg_cagr or 0) - (market_cagr or 0)
    if diff >= 2:
        return f" -- well above the overall market CAGR of {fmt_pct(market_cagr)}"
    if diff > 0.2:
        return f" -- above the overall market CAGR of {fmt_pct(market_cagr)}"
    return f", in line with the overall market CAGR of {fmt_pct(market_cagr)}"


def _definition_bullets(data: dict) -> list[dict]:
    """Scope box content from either the LLM-generated `market_definition`
    (run_fleet.py) or the client-supplied `definition_inclusion_exclusion`."""
    md = data.get("market_definition") or {}
    # A client-supplied definition is authoritative and reads cleaner, so
    # it wins over the fleet's scope-lock copy of it.
    if md and not data.get("definition_inclusion_exclusion"):
        return [b for b in (
            {"heading": "Included", "text": md.get("included", "")},
            {"heading": "Excluded", "text": md.get("excluded", "")},
            {"heading": "Value-chain level", "text": md.get("value_chain_level", "")},
            {"heading": "Unit of measure", "text": md.get("unit_of_measure", "")},
        ) if b["text"]]
    de = data.get("definition_inclusion_exclusion") or {}
    return [b for b in (
        {"heading": "Definition", "text": de.get("definition", "")},
        {"heading": "Included", "text": de.get("inclusions", "")},
        {"heading": "Excluded", "text": de.get("exclusions", "")},
        {"heading": "Unit of measure", "text": "USD million, market value" if de else ""},
    ) if b["text"]]


def build(slug: str, allow_warnings: bool = False) -> dict:
    market_dir = OUTPUT_ROOT / slug
    data = json.loads((market_dir / "whitepaper_fields.json").read_text(encoding="utf-8"))
    n = build_narrative(data)
    charts = market_dir / "charts"
    market = n["market"]
    root = n["root"]
    changes = data.get("consistency_changes", {}) or {}
    snippet_counts = _snippet_counts(data.get("log") or [])

    def conf(key: str) -> tuple[str, str]:
        return compute_confidence(n["fields"].get(key), changes.get(key), snippet_counts.get(key))

    top_driver_name = _phrase(n["top_driver"]["name"])
    top_restraint_name = _phrase(n["top_restraint"]["name"])

    sections: list[dict] = []

    # Warning box, if the consistency pass still has unresolved issues and
    # the run was rendered anyway with --allow-warnings.
    validation_path = market_dir / "validation_report.json"
    warning_paragraphs: list[str] = []
    if allow_warnings and validation_path.exists():
        vr = json.loads(validation_path.read_text(encoding="utf-8"))
        after_issues = (vr.get("after") or {}).get("issues") or []
        if after_issues:
            warning_paragraphs = [
                "WARNING: this report was rendered with unresolved data-consistency issues "
                "(--allow-warnings). Treat the figures below with caution:",
            ] + [f"- {i}" for i in after_issues]

    # Sec 1 -- Executive Summary
    top3_label = concentration_label(n["top3_share"])
    exec_paragraphs = []
    if warning_paragraphs:
        exec_paragraphs.extend(warning_paragraphs)
    exec_paragraphs.extend([
        f"The global {root} market remains an important and closely watched segment, with demand shaped by "
        f"a mix of structural drivers and near-term headwinds. We estimate the market at "
        f"{n['base_val_fmt']} in {n['base_year']}, projected to reach {n['fc_val_fmt']} by {n['forecast_year']}, "
        f"creating {n['incremental_fmt']} in incremental opportunity.",
        f"Long-term growth is expected to be driven primarily by {top_driver_name}, "
        f"contributing an estimated {n['top_driver'].get('cagr_contribution_bps', 0):+.0f} basis points to the "
        f"market CAGR. The single most important structural headwind is {top_restraint_name}, "
        f"with a drag of around {n['top_restraint'].get('cagr_drag_bps', 0):.0f} basis points.",
    ])
    sections.append(_section(1, "EXECUTIVE SUMMARY", conf("market_size"), exec_paragraphs))

    largest_fastest_app = (
        f"{n['top_app']['name']} is both the largest and the fastest-growing end-use segment, at "
        f"{fmt_pct(n['top_app'].get('share_pct_of_market', 0))} of market value and "
        f"{fmt_pct(n['fastest_app'].get('cagr_2025_2030_pct', 0))} annual growth."
        if n["top_app"]["name"] == n["fastest_app"]["name"] else
        f"{n['top_app']['name']} accounts for {fmt_pct(n['top_app'].get('share_pct_of_market', 0))} "
        f"of market value, while {n['fastest_app']['name']} is the fastest-growing application at "
        f"{fmt_pct(n['fastest_app'].get('cagr_2025_2030_pct', 0))} annually."
    )
    sections[-1]["bullets"] = [
        {"heading": f"{n['leader_region'][0] or 'The leading region'} dominates global demand",
         "text": (f"{n['leader_region'][0]} accounts for "
                   f"{fmt_pct((n['leader_region'][1] or {}).get('share_pct_of_global', 0))} of global market value, "
                   f"making regional concentration a key factor shaping the {root} market.")},
        {"heading": (f"{n['top_app']['name']} is the largest and fastest-growing end-use segment"
                     if n["top_app"]["name"] == n["fastest_app"]["name"]
                     else f"{n['top_app']['name']} is the largest end-use segment"),
         "text": largest_fastest_app},
        {"heading": f"Competitive landscape is {top3_label}",
         "text": (f"The top three named players account for approximately {fmt_pct(n['top3_share'])} of global "
                   f"revenue, a {top3_label} market structure by standard concentration thresholds "
                   f"(<25% fragmented, 25-50% moderately concentrated, >50% highly concentrated).")},
    ]

    # Sec 2 -- Market Size Outlook (+ Market Definition & Scope box)
    scope_bullets = _definition_bullets(data)
    leader_name, leader_data = n["leader_region"][0], (n["leader_region"][1] or {})
    leader_incr = (leader_data.get("forecast_value_usd_mn", 0) or 0) - (leader_data.get("value_usd_mn", 0) or 0)
    growth_mix = (
        f"{leader_name} is expected to add the most absolute value, about {fmt_usd_mn(leader_incr)} between "
        f"{n['base_year']} and 2030, while {n['fastest_app']['name']} is the fastest-growing application "
        f"at {fmt_pct(n['fastest_app'].get('cagr_2025_2030_pct', 0))} a year."
        if leader_name else ""
    )
    sections.append(_section(2, f"Global {root} Market Size Outlook ({n['base_year']}-{n['forecast_year']})", conf("market_size"), [
        f"We estimate the global {root} market at {n['base_val_fmt']} in {n['base_year']}. The market is "
        f"projected to reach {n['fc_val_fmt']} by {n['forecast_year']}, growing at a CAGR of {fmt_pct(n['cagr'])}.",
    ] + ([growth_mix] if growth_mix else []), figures=[{"path": str(charts / "fig1_1_market_size.png"),
                  "caption": f"Figure 1.1 - Global {root} market: "
                              + ("historical and " if (n["fields"].get("market_size", {}).get("history_check") or {}).get("ok") else "")
                              + "base-case forecast "
                              f"{n['base_year']} to {n['forecast_year']}, in USD million."}],
       subheads=([{"heading": "Market Definition & Scope", "text": "", "bullets_inline": scope_bullets}] if scope_bullets else [])))

    # Sec 3 -- Drivers & Challenges
    d_list = sorted(n["fields"].get("drivers_restraints", {}).get("drivers", []) or [],
                    key=lambda d: abs(d.get("cagr_contribution_bps", 0)), reverse=True)
    r_list = sorted(n["fields"].get("drivers_restraints", {}).get("restraints", []) or [],
                    key=lambda r: abs(r.get("cagr_drag_bps", 0)), reverse=True)
    shown_note = (f" The four largest of each are listed below; Figure 3.1 shows all "
                  f"{len(d_list)} drivers and {len(r_list)} restraints."
                  if len(d_list) > 4 or len(r_list) > 4 else "")
    sections.append(_section(3, "Market Dynamics - Drivers & Challenges", conf("drivers_restraints"), [
        f"The market's growth fundamentals reflect a balance of positive and negative forces. We estimate that "
        f"the {len(d_list)} growth drivers identified contribute about {n['total_driver_bps']:.0f} basis points "
        f"to the market CAGR, while the {len(r_list)} structural restraints reduce growth by about "
        f"{n['total_restraint_bps']:.0f} basis points, netting out to the projected {fmt_pct(n['cagr'])} "
        f"long-term growth outlook.{shown_note}",
    ], figures=[{"path": str(charts / "fig2_1_drivers_restraints.png"),
                  "caption": "Figure 2.1 - What pushes growth up, and what pulls it down, in basis points of annual CAGR."}],
       bullets=[
        {"heading": "What is driving market growth", "text": " ".join(
            f"{d.get('name')} (+{d.get('cagr_contribution_bps', 0):.0f} bps)." for d in d_list[:4])},
        {"heading": "What continues to challenge growth", "text": " ".join(
            f"{r.get('name')} (-{abs(r.get('cagr_drag_bps', 0)):.0f} bps)." for r in r_list[:4])},
    ]))

    # Sec 4 -- Porter's Five Forces
    pf = n["fields"].get("porters_five_forces", {}) or {}
    forces = pf.get("forces", {}) or {}

    def _flabel(key):
        return (forces.get(key) or {}).get("label", "Medium")

    force_names = {"supplier_power": "supplier power", "competitive_rivalry": "competitive rivalry",
                   "buyer_power": "buyer power", "threat_of_new_entrants": "the threat of new entrants",
                   "threat_of_substitutes": "the threat of substitutes"}
    scored = sorted(((forces.get(k) or {}).get("score_1_5", 3), v) for k, v in force_names.items() if k in forces)
    porter_paras = [
        f"Based on Porter's Five Forces analysis, the global {root} market has an average competitive-force "
        f"intensity of {pf.get('overall_score_out_of_5', 3):.1f} out of 5 (higher scores mean more pressure on "
        f"industry profitability).",
    ]
    if scored and len({sc for sc, _ in scored}) == 1:
        porter_paras.append(
            "The available sources did not differentiate between the five forces, so every force carries the "
            "same neutral score; treat this section as a placeholder until better evidence is gathered.")
    elif scored:
        top_two = [nm for _, nm in sorted(scored, reverse=True)[:2]]
        porter_paras.append(
            f"{top_two[0][:1].upper() + top_two[0][1:]} and {top_two[1]} are the strongest forces, while "
            f"{scored[0][1]} is the weakest ({scored[0][0]:.1f}/5).")
    sections.append(_section(4, "Industry Attractiveness Assessment - Porter's Five Forces", conf("porters_five_forces"),
        porter_paras, figures=[{"path": str(charts / "fig3_1_porters.png"),
                  "caption": "Figure 3.1 - Porter's Five Forces, scored 1 to 5."}],
       bullets=[
        {"heading": f"Supplier Power - {_flabel('supplier_power')}",
         "text": f"Score {forces.get('supplier_power', {}).get('score_1_5', 3)}/5."},
        {"heading": f"Competitive Rivalry - {_flabel('competitive_rivalry')}",
         "text": f"Score {forces.get('competitive_rivalry', {}).get('score_1_5', 3)}/5."},
        {"heading": f"Buyer Power - {_flabel('buyer_power')}",
         "text": f"Score {forces.get('buyer_power', {}).get('score_1_5', 3)}/5."},
        {"heading": f"Threat of New Entrants - {_flabel('threat_of_new_entrants')}",
         "text": f"Score {forces.get('threat_of_new_entrants', {}).get('score_1_5', 3)}/5."},
        {"heading": f"Threat of Substitutes - {_flabel('threat_of_substitutes')}",
         "text": f"Score {forces.get('threat_of_substitutes', {}).get('score_1_5', 3)}/5."},
    ]))

    # Sec 5 -- Segmentation by Grade/Type and Form
    sections.append(_section(5, "Market Segmentation by Grade and Product Form", conf("segmentation_grade_form"), [
        f"The {root} market is segmented across multiple grades/types, each with distinct standards, "
        f"regulatory requirements, and pricing dynamics. {n['top_grade']['name']} remains the largest segment, "
        f"accounting for {fmt_pct(n['top_grade'].get('share_pct_of_market', 0))} of {n['base_year']} market value, "
        f"followed by {n['second_grade']['name']} at {fmt_pct(n['second_grade'].get('share_pct_of_market', 0))}.",
        (f"By physical form, {n['top_form']['name']} is both the largest form, at "
         f"{fmt_pct(n['top_form'].get('share_pct_of_market', 0))} of total value, and the fastest-growing, "
         f"expanding at {fmt_pct(n['fastest_form'].get('fastest_growing_cagr_pct', 0))} annually."
         if n["top_form"]["name"] == n["fastest_form"]["name"] else
         f"By physical form, {n['top_form']['name']} dominates the market at "
         f"{fmt_pct(n['top_form'].get('share_pct_of_market', 0))} of total value, while "
         f"{n['fastest_form']['name']} is the fastest-growing form, expanding at "
         f"{fmt_pct(n['fastest_form'].get('fastest_growing_cagr_pct', 0))} annually."),
    ], figures=[
        {"path": str(charts / "fig4_1_grade.png"), "caption": f"Figure 4.1 - {root} market value by grade/type, {n['base_year']} estimate."},
        {"path": str(charts / "fig4_2_form.png"), "caption": f"Figure 4.2 - {root} market value by physical form, {n['base_year']} estimate."},
    ]))

    # Sec 6 -- End-Use Industry
    sections.append(_section(6, "End-Use Industry Analysis", conf("end_use_industry"), [
        f"{n['top_app']['name']} remains the largest {root} application segment, accounting for "
        f"{fmt_pct(n['top_app'].get('share_pct_of_market', 0))} of market value. "
        f"{n['fastest_app']['name']} is the fastest-growing application, expanding at "
        f"{fmt_pct(n['fastest_app'].get('cagr_2025_2030_pct', 0))} annually"
        f"{_vs_market(n['fastest_app'].get('cagr_2025_2030_pct', 0), n['cagr'])}.",
    ], figures=[
        {"path": str(charts / "fig5_1_end_use_share.png"), "caption": f"Figure 5.1 - End-use application share, {n['base_year']} estimate."},
        {"path": str(charts / "fig5_2_end_use_cagr.png"), "caption": f"Figure 5.2 - Compound annual growth rates by end-use application, {n['base_year']} to 2030."},
    ]))

    # Sec 7 -- Sub-Segments (now with specific facts, not one generic sentence)
    sub_groups = n["fields"].get("sub_segments", {}).get("by_top_application", []) or []
    sub_facts = []
    for grp in sub_groups[:2]:
        subs = grp.get("sub_segments") or []
        if not subs:
            continue
        top_sub = max(subs, key=lambda s: s.get("cagr_pct", 0))
        sub_facts.append(
            f"Within {grp.get('application', 'this application')}, {top_sub.get('name')} is the standout, "
            f"growing at {fmt_pct(top_sub.get('cagr_pct', 0))} annually"
            + (f" and reaching {fmt_usd_mn(top_sub['value_usd_mn'])}" if top_sub.get("value_usd_mn") else "") + "."
        )
    sections.append(_section(7, "High-Growth Sub-Segment Opportunities", conf("sub_segments"), [
        "Overall market averages do not fully capture where growth is truly concentrated. Within each major "
        "application segment, select high-growth niches are expanding well above the market average.",
    ] + sub_facts, figures=[{"path": str(charts / "fig6_1_sub_segments.png"),
                  "caption": "Figure 6.1 - Sub-segment sizes and growth rates within top applications."}]))

    # Sec 8 -- Derivatives & Pricing
    tiers = n["price_tiers"]
    units = {t.get("unit") for t in tiers if t.get("unit")}
    if len(units) == 1:
        price_header = f"Price ({next(iter(units))})"
    elif units:
        price_header = "Price"
    else:
        price_header = "Price (USD; unit not specified by source)"
    price_rows = [[t.get("tier", ""),
                   f"{_fmt_price(t.get('price_low_usd_per_unit', 0))}-{_fmt_price(t.get('price_high_usd_per_unit', 0))}"
                   + (f" {t['unit']}" if t.get("unit") and len(units) > 1 else ""),
                   t.get("grade", ""), t.get("principal_buyers", "")] for t in tiers]
    derivs = n["fields"].get("derivatives_pricing", {}).get("derivatives", []) or []
    if derivs:
        deriv_paras = [
            f"Beyond the core market, value also accrues to adjacent products and services across the {root} "
            f"value chain. {n['top_derivative']['name']} is the largest of these, at "
            f"{fmt_usd_mn(n['top_derivative'].get('value_usd_mn', 0))}.",
        ]
        deriv_figs = [{"path": str(charts / "fig7_1_derivatives.png"),
                       "caption": f"Figure 7.1 - Adjacent/derivative product market sizes, {n['base_year']} estimate."}]
    else:
        deriv_paras = [
            f"The available sources did not size any adjacent or derivative product segments separately from the "
            f"core {root} market, so this section focuses on pricing.",
        ]
        deriv_figs = []
    if tiers:
        deriv_paras.append(
            f"Prices span {len(tiers)} tiers, from {tiers[0].get('tier', 'the entry tier')} to "
            f"{tiers[-1].get('tier', 'the top tier')}, reflecting differences in specification, quality and "
            f"certification standards, and the buyer segment served.")
    sections.append(_section(8, "Derivatives and Pricing Structure Analysis", conf("derivatives_pricing"),
        deriv_paras, figures=deriv_figs,
       tables=([{"title": "The Multi-Tier Pricing Structure",
                 "headers": ["Tier", price_header, "Grade", "Principal buyers"],
                 "rows": price_rows}] if tiers else [])))

    # Sec 9 -- Regional Outlook
    regions = n["fields"].get("regional", {}).get("regions", {}) or {}
    region_paras = [
        f"The global {root} market reflects a clear regional divide across production, consumption, and demand "
        f"patterns. {n['leader_region'][0]} remains the leading region, accounting for "
        f"{fmt_usd_mn((n['leader_region'][1] or {}).get('value_usd_mn', 0))} or "
        f"{fmt_pct((n['leader_region'][1] or {}).get('share_pct_of_global', 0))} of global market value, "
        + (f"and is also projected to grow fastest, at "
           f"{fmt_pct((n['fastest_region'][1] or {}).get('cagr_2025_2030_pct', 0))} annually."
           if n["leader_region"][0] == n["fastest_region"][0] else
           f"while {n['fastest_region'][0]} is projected to grow fastest at "
           f"{fmt_pct((n['fastest_region'][1] or {}).get('cagr_2025_2030_pct', 0))} annually."),
    ]
    region_figs = [{"path": str(charts / "fig8_1_regional.png"),
                     "caption": f"Figure 8.1 - Regional market size {n['base_year']} and projected CAGR through 2030."}]
    region_subheads = []
    for i, rname in enumerate(n["region_order"], start=1):
        rdata = regions.get(rname, {})
        region_subheads.append({
            "heading": f"{rname} - Regional Snapshot",
            "text": (f"{rname} represents {fmt_usd_mn(rdata.get('value_usd_mn', 0))} in {n['base_year']}, "
                     f"accounting for {fmt_pct(rdata.get('share_pct_of_global', 0))} of global demand, and is "
                     f"projected to grow at {fmt_pct(rdata.get('cagr_2025_2030_pct', 0))} annually, reaching "
                     f"{fmt_usd_mn(rdata.get('forecast_value_usd_mn', 0))} by 2030."),
            "figure": {"path": str(charts / f"fig{8 + i}_1_region_{i}.png"),
                        "caption": f"Figure {8 + i}.1 - {rname}: country market sizes and application mix, {n['base_year']} estimate."},
        })
    sections.append(_section(9, "Regional Market Dynamics and Outlook", conf("regional"), region_paras,
                               figures=region_figs, subheads=region_subheads))

    # Sec 10 -- Competitive Landscape
    companies = n["fields"].get("competitive_landscape", {}).get("companies", []) or []
    sections.append(_section(10, "Competitive Landscape and Market Share Analysis", conf("competitive_landscape"), [
        f"The global {root} market is {top3_label}, with the top three named players controlling "
        f"approximately {fmt_pct(n['top3_share'])} of total market value.",
    ], figures=[{"path": str(charts / "fig14_1_competitive.png"),
                  "caption": f"Figure 14.1 - Estimated market share by company, {n['base_year']}."}],
       bullets=[{"heading": f"{c.get('name')} (~{c.get('share_pct', 0):.0f}% share)",
                  "text": c.get("positioning_note", "")} for c in companies[:5]]))

    # Sec 11 -- Regulation (specific facts: costliest vs cheapest)
    comp_areas = sorted(n["fields"].get("regulation", {}).get("compliance_areas", []) or [],
                          key=lambda a: a.get("cost_pct_of_cogs_high", 0))
    cheapest = comp_areas[0] if comp_areas else None
    reg_paras = [
        f"Regulation acts as a built-in cost layer across the {root} industry, directly influencing margins, "
        f"pricing, and market entry dynamics. {n['top_compliance']['name']} is the costliest compliance "
        f"pathway, adding an estimated "
        f"{_fmt_pct_range(n['top_compliance'].get('cost_pct_of_cogs_low', 0), n['top_compliance'].get('cost_pct_of_cogs_high', 0))} "
        f"to cost of goods sold.",
    ]
    if cheapest and cheapest.get("name") != n["top_compliance"].get("name"):
        reg_paras.append(
            f"By contrast, {cheapest.get('name')} is the least burdensome area, adding only "
            f"{_fmt_pct_range(cheapest.get('cost_pct_of_cogs_low', 0), cheapest.get('cost_pct_of_cogs_high', 0))} to COGS."
        )
    sections.append(_section(11, "Regulation and Compliance Overview", conf("regulation"), reg_paras,
       figures=[{"path": str(charts / "fig15_1_regulation.png"),
                  "caption": "Figure 15.1 - Compliance cost burden by regulatory area, as a percentage of COGS."}]))

    # Sec 12 -- End-User Behavior (specific: highest WTP buyer profile)
    wtp_segs = n["fields"].get("willingness_to_pay", {}).get("consumer_segments", []) or []
    wtp_paras = [
        f"Consumer/buyer behavior in the {root} market is increasingly segmented by willingness to pay, "
        f"ranging from price-sensitive commodity buyers to premium, quality- and evidence-driven purchasers.",
    ]
    wtp_keys = ("with_clinical_evidence_usd", "with_premium_positioning_usd", "with_top_tier_format_usd")

    def _best_premium(seg: dict) -> float:
        lo = seg.get("baseline_usd", 0) or 0
        return max((seg.get(k, 0) or 0) for k in wtp_keys) / lo if lo else 0.0

    priced = [s for s in wtp_segs if (s.get("baseline_usd") or 0) > 0]
    if priced:
        top_wtp = max(priced, key=_best_premium)
        lo = top_wtp["baseline_usd"]
        hi = max((top_wtp.get(k, 0) or 0) for k in wtp_keys)
        if hi > lo * 1.05 and _fmt_price(hi) != _fmt_price(lo):
            wtp_paras.append(
                f"The \"{top_wtp.get('name')}\" segment is willing to pay the largest premium: up to "
                f"USD {_fmt_price(hi)} for its most valued attribute, versus USD {_fmt_price(lo)} at baseline "
                f"(+{hi / lo - 1:.0%})."
            )
    sections.append(_section(12, "End User Behavior Outlook", conf("willingness_to_pay"), wtp_paras,
       figures=[{"path": str(charts / "fig16_1_wtp.png"),
                  "caption": "Figure 16.1 - Willingness to pay by consumer/buyer profile and product attribute."}]))

    # Sec 13 -- Investment Priorities (specific: best ROI opportunity)
    opps = n["fields"].get("investment_priorities", {}).get("opportunities", []) or []
    opp_rows = [[o.get("opportunity", ""), o.get("target_segment", ""), o.get("time_to_market", ""),
                  f"{o.get('roi_low_pct', 0):.0f}-{o.get('roi_high_pct', 0):.0f}%", o.get("capital_intensity", "")]
                 for o in opps]
    inv_paras = [
        f"The {root} market offers multiple investment pathways, but returns vary significantly depending on "
        f"where participants operate within the value chain.",
    ]
    if opps:
        best = max(opps, key=lambda o: o.get("roi_high_pct", 0))
        inv_paras.append(
            f"{best.get('opportunity')} offers the strongest return profile, at "
            f"{best.get('roi_low_pct', 0):.0f}-{best.get('roi_high_pct', 0):.0f}% ROI within "
            f"{best.get('time_to_market', 'the near term')}, targeting {best.get('target_segment', 'the market')}."
        )
    sections.append(_section(13, "Strategic Investment Priorities", conf("investment_priorities"), inv_paras,
        tables=[{"title": "Go-to-Market Strategy Matrix, Ranked by Expected ROI and Time-to-Market",
                 "headers": ["Opportunity", "Target segment", "Time", "ROI", "Capital intensity"],
                 "rows": opp_rows}]))

    # Sec 14 -- Scenario Analysis -- rationale drawn from THIS market's own
    # drivers/restraints, not a shared boilerplate sentence.
    scen = n["fields"].get("scenarios", {}) or {}
    bear, base, bull = scen.get("bear", {}), scen.get("base", {}), scen.get("bull", {})
    sections.append(_section(14, "Scenario Analysis and Future Outlook", conf("scenarios"), [
        f"Our base-case forecast projects the global {root} market to grow at a {fmt_pct(base.get('cagr_pct', n['cagr']))} "
        f"CAGR, reaching {fmt_usd_mn(base.get('terminal_value_usd_mn', n['fc_val']))} by {n['forecast_year']}. "
        f"Long-term outcomes remain sensitive chiefly to {top_driver_name} on the upside and "
        f"{top_restraint_name} on the downside.",
    ], figures=[{"path": str(charts / "fig18_1_scenarios.png"),
                  "caption": f"Figure 18.1 - Bear, base, and bull case forecasts, {n['base_year']} to {n['forecast_year']}."}],
       bullets=[
        {"heading": f"Bear Case - {fmt_usd_mn(bear.get('terminal_value_usd_mn', 0))} by {n['forecast_year']} ({fmt_pct(bear.get('cagr_pct', 0))} CAGR)",
         "text": f"Assumes weaker momentum from {top_driver_name} and a heavier-than-expected drag from {top_restraint_name}."},
        {"heading": f"Base Case - {fmt_usd_mn(base.get('terminal_value_usd_mn', 0))} by {n['forecast_year']} ({fmt_pct(base.get('cagr_pct', 0))} CAGR)",
         "text": f"Assumes current momentum from {top_driver_name} is sustained and the drag from {top_restraint_name} stays contained."},
        {"heading": f"Bull Case - {fmt_usd_mn(bull.get('terminal_value_usd_mn', 0))} by {n['forecast_year']} ({fmt_pct(bull.get('cagr_pct', 0))} CAGR)",
         "text": f"Assumes faster momentum from {top_driver_name} and a material easing of the drag from {top_restraint_name}."},
    ]))

    # Sec 15 -- Investment Thesis -- conclusion drawn from this market's own
    # numbers, not a shared sentence.
    sections.append(_section(15, "Investment Thesis and Strategic Conclusions", conf("market_size"), [
        f"The {root} market presents a {('structurally attractive' if n['cagr'] >= 5 else 'steady, moderate-growth')} "
        f"long-term opportunity, anchored by {top_driver_name} and tempered by {top_restraint_name}.",
        f"Steady Structural Growth: the global {root} market is projected to grow at a "
        f"{fmt_pct(n['cagr'])} CAGR through {n['forecast_year']}, creating nearly {n['incremental_fmt']} in "
        f"incremental market opportunity, led by {n['leader_region'][0]} and {n['top_app']['name']}.",
        f"Concluding View: participants best positioned to capture disproportionate growth are those aligned "
        f"with {top_driver_name}, with a credible plan to manage exposure to {top_restraint_name}.",
    ]))

    # Sec 16 -- Sources & Methodology (new -- replaces the removed
    # fixed-boilerplate disclaimer with an honest, per-section source list)
    source_rows = []
    for s in sections:
        key_for_section = {
            1: "market_size", 2: "market_size", 3: "drivers_restraints", 4: "porters_five_forces",
            5: "segmentation_grade_form", 6: "end_use_industry", 7: "sub_segments", 8: "derivatives_pricing",
            9: "regional", 10: "competitive_landscape", 11: "regulation", 12: "willingness_to_pay",
            13: "investment_priorities", 14: "scenarios", 15: "market_size",
        }.get(s["num"])
        group = n["fields"].get(key_for_section) if key_for_section else None
        src = (group or {}).get("_sources")
        domains = (src or {}).get("domains") or []
        if domains:
            src_cell = ", ".join(domains)
        elif src is None and snippet_counts.get(key_for_section or ""):
            src_cell = f"Not recorded ({snippet_counts[key_for_section]} web snippets)"
        else:
            src_cell = "Not recorded"
        source_rows.append([str(s["num"]), s["title"], src_cell, s["confidence_label"]])
    sections.append(_section(16, "Sources & Methodology", ("N/A", ""), [
        "Every figure in this report is synthesized by an LLM from live, multi-backend web search results "
        "(Google/Bing/DuckDuckGo/Mojeek)"
        + (", scoped to the market definition shown in Section 2" if data.get("market_definition") else
           ", then checked against the market definition shown in Section 2 (segments it explicitly excludes "
           "were removed)" if scope_bullets else "")
        + ". Every numeric value is then randomized by +/-5-10% to "
        "avoid presenting a false sense of precision, and passed through an automated consistency pass that "
        "reconciles shares, regional totals, scenario ordering, and growth-rate math against each other before "
        "this report is rendered -- see validation_report.json for the full before/after audit trail. "
        "Confidence labels per section (High/Medium/Low) reflect the number of distinct source domains found "
        "(user-generated and unrelated sites are listed but not counted) and whether the consistency pass had to adjust that section's figures.",
    ], tables=[{"title": "Per-Section Source Domains & Confidence",
                 "headers": ["Sec", "Section", "Source Domains", "Confidence"], "rows": source_rows}]))

    plan = {
        "market_name": market,
        "root_name": root,
        "article": _article(root),
        "base_year": n["base_year"],
        "forecast_year": n["forecast_year"],
        "generated_at": data.get("generated_at"),
        # Reader-facing cover note -- the raw run note names code files.
        "source_note": ("Figures are AI-assisted estimates synthesized from public web sources and "
                        "reconciled for internal consistency; see Section 16 for sources and method."),
        "sections": sections,
    }
    _renumber_figures(plan)
    out_path = market_dir / "content_plan.json"
    out_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    return plan


_FIG_CAPTION_RE = re.compile(r"^Figure\s+[\d.]+\s*-\s*")


def _renumber_figures(plan: dict) -> None:
    """Figure numbers must go in order with the section numbers, no gaps --
    the per-section hardcoded "Figure 8.1"/"Figure 14.1" strings above drift
    out of sequence whenever a market has fewer than 5 regions (fewer
    regional figures shift everything after Section 9). This walks every
    figure in true document order and rewrites its "Figure N.M -" prefix."""
    for s in plan["sections"]:
        idx = 0
        all_figs = list(s.get("figures") or [])
        for sh in s.get("subheads") or []:
            if sh.get("figure"):
                all_figs.append(sh["figure"])
        for fig in all_figs:
            idx += 1
            fig["caption"] = _FIG_CAPTION_RE.sub(f"Figure {s['num']}.{idx} - ", fig.get("caption", ""))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--market-slug", required=True)
    p.add_argument("--allow-warnings", action="store_true")
    args = p.parse_args()
    build(args.market_slug, allow_warnings=args.allow_warnings)
    print(f"Wrote {OUTPUT_ROOT / args.market_slug / 'content_plan.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

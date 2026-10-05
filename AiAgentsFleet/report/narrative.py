"""Turns one market's whitepaper_fields.json into the exact set of narrative
strings/numbers the docx builder (build_docx.mjs, via build_content.py) drops
into each section -- mirrors the sentence patterns used in
`vitamin_c_market_whitepaper_v2.docx` ("We estimate the global X market at
USD Y in 2025...", etc.), but data-driven per market instead of hand-written.
"""
from __future__ import annotations

from typing import Any


# Single source of truth for regional ordering -- the regional chart file
# names (make_charts.py) and the regional snapshot text (build_content.py)
# must both index regions in this order, or a snapshot embeds another
# region's chart.
REGION_ORDER = ["Asia Pacific", "North America", "Europe", "Latin America", "Middle East & Africa"]


def ordered_regions(regions: dict) -> list[str]:
    return [r for r in REGION_ORDER if r in regions] + [r for r in regions if r not in REGION_ORDER]


def fmt_usd_mn(v: float) -> str:
    if v is None:
        return "n/a"
    if v >= 1000:
        return f"USD {v / 1000:,.2f} billion"
    return f"USD {v:,.1f} million"


def fmt_pct(v: float, decimals: int = 1) -> str:
    if v is None:
        return "n/a"
    return f"{v:.{decimals}f}%"


def market_root_name(market_name: str) -> str:
    # Templates already say "Global {root}", so drop a leading "Global".
    root = market_name.replace(" Market", "").strip()
    return root[len("Global "):].strip() if root.lower().startswith("global ") else root


def build_narrative(data: dict[str, Any]) -> dict[str, Any]:
    market = data["market_name"]
    root = market_root_name(market)
    f = data.get("fields", {})

    ms = f.get("market_size", {}) or {}
    dr = f.get("drivers_restraints", {}) or {}
    pf = f.get("porters_five_forces", {}) or {}
    seg = f.get("segmentation_grade_form", {}) or {}
    eu = f.get("end_use_industry", {}) or {}
    sub = f.get("sub_segments", {}) or {}
    deriv = f.get("derivatives_pricing", {}) or {}
    reg = f.get("regional", {}) or {}
    comp = f.get("competitive_landscape", {}) or {}
    rules = f.get("regulation", {}) or {}
    wtp = f.get("willingness_to_pay", {}) or {}
    inv = f.get("investment_priorities", {}) or {}
    scen = f.get("scenarios", {}) or {}

    base_year = ms.get("base_year", 2025)
    forecast_year = ms.get("forecast_year", 2035)
    base_val = ms.get("base_year_value_usd_mn", 0)
    fc_val = ms.get("forecast_value_usd_mn", 0)
    cagr = ms.get("cagr_pct", 0)
    incremental = fc_val - base_val

    top_driver = max(dr.get("drivers", []) or [{"name": "n/a", "cagr_contribution_bps": 0}],
                       key=lambda d: d.get("cagr_contribution_bps", 0))
    top_restraint = max(dr.get("restraints", []) or [{"name": "n/a", "cagr_drag_bps": 0}],
                          key=lambda d: d.get("cagr_drag_bps", 0))
    total_driver_bps = sum(d.get("cagr_contribution_bps", 0) for d in dr.get("drivers", []) or [])
    total_restraint_bps = sum(d.get("cagr_drag_bps", 0) for d in dr.get("restraints", []) or [])

    grade_list = sorted(seg.get("by_grade_or_type", []) or [], key=lambda g: g.get("share_pct_of_market", 0), reverse=True)
    top_grade = grade_list[0] if grade_list else {"name": "n/a", "share_pct_of_market": 0}
    second_grade = grade_list[1] if len(grade_list) > 1 else {"name": "n/a", "share_pct_of_market": 0}

    form_list = sorted(seg.get("by_form", []) or [], key=lambda g: g.get("share_pct_of_market", 0), reverse=True)
    top_form = form_list[0] if form_list else {"name": "n/a", "share_pct_of_market": 0}
    fastest_form = max(seg.get("by_form", []) or [{"name": "n/a", "fastest_growing_cagr_pct": 0}],
                         key=lambda g: g.get("fastest_growing_cagr_pct") or 0)

    apps = sorted(eu.get("applications", []) or [], key=lambda a: a.get("share_pct_of_market", 0), reverse=True)
    top_app = apps[0] if apps else {"name": "n/a", "share_pct_of_market": 0}
    fastest_app = max(eu.get("applications", []) or [{"name": "n/a", "cagr_2025_2030_pct": 0}],
                        key=lambda a: a.get("cagr_2025_2030_pct", 0))

    regions = reg.get("regions", {}) or {}
    region_order = ordered_regions(regions)
    leader_region = max(regions.items(), key=lambda kv: kv[1].get("share_pct_of_global", 0)) if regions else (None, {})
    fastest_region = max(regions.items(), key=lambda kv: kv[1].get("cagr_2025_2030_pct", 0)) if regions else (None, {})

    companies = comp.get("companies", []) or []
    top3_share = sum(c.get("share_pct", 0) for c in companies[:3])

    top_derivative = max(deriv.get("derivatives", []) or [{"name": "n/a", "value_usd_mn": 0}],
                           key=lambda d: d.get("value_usd_mn", 0))
    price_tiers = deriv.get("price_tiers", []) or []

    top_compliance = max(rules.get("compliance_areas", []) or [{"name": "n/a", "cost_pct_of_cogs_high": 0}],
                           key=lambda a: a.get("cost_pct_of_cogs_high", 0))

    return {
        "market": market,
        "root": root,
        "base_year": base_year,
        "forecast_year": forecast_year,
        "base_val": base_val,
        "fc_val": fc_val,
        "cagr": cagr,
        "incremental": incremental,
        "base_val_fmt": fmt_usd_mn(base_val),
        "fc_val_fmt": fmt_usd_mn(fc_val),
        "incremental_fmt": fmt_usd_mn(incremental),
        "top_driver": top_driver,
        "top_restraint": top_restraint,
        "total_driver_bps": total_driver_bps,
        "total_restraint_bps": total_restraint_bps,
        "top_grade": top_grade,
        "second_grade": second_grade,
        "top_form": top_form,
        "fastest_form": fastest_form,
        "top_app": top_app,
        "fastest_app": fastest_app,
        "leader_region": leader_region,
        "fastest_region": fastest_region,
        "region_order": region_order,
        "top3_share": top3_share,
        "top_derivative": top_derivative,
        "price_tiers": price_tiers,
        "top_compliance": top_compliance,
        "fields": f,
    }

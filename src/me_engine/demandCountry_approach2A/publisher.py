"""Publisher: turns a 2A signal-model run into a publishable, documented
market estimate.

    1. Bottom-up build (signal model, run_1): units per country from customs
       data / web evidence / estimator, price from the pricing agent.
    2. Reconciliation (src/newinfo/system.md §12): P = w*B + (1-w)*C, with C the
       median of the published global sizes that survive the consensus outlier
       filter and w = `publisher.weight_bottom_up` (0.5); `method="inverse_variance"`
       keeps the earlier log-space inverse-variance blend. The adjustment k = P/B
       is applied to the PRICE LEVEL, not to units -- units are physical
       measurements with their own sources. B (raw bottom-up) and k are always
       reported beside P.
    3. Regions from the World Bank's own country classification (all ~210
       countries, including every Rest-of-World country).
    4. Segments: the top-down segment module over the final country values.
    5. Respectability: where the final figure sits in the published range
       (criteria fixed in advance: inside the interquartile range = in line
       with the market; inside min-max = within the published range; else outlier).
    6. Documentation pack: Markdown + Excel with every number's basis and source.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
from openpyxl import Workbook

from me_engine.consensus import adapters, combine, published
from me_engine.generation.config import GenConfig
from me_engine.generation.llm import GenLLM

from . import checkpoints, design_config
from .signal_model import _search, page_windows, signal_dir
from .topdown import regions as R
from .topdown import segments as S
from .topdown import writer as W

REPORTS = Path("output") / "reports"
CONSENSUS = Path("output") / "consensus"
DISPLAY = {"Korea, Rep.": "South Korea", "Russian Federation": "Russia", "Egypt, Arab Rep.": "Egypt",
           "Viet Nam": "Vietnam", "Turkiye": "Turkey", "Iran, Islamic Rep.": "Iran", "Czechia": "Czech Republic",
           "Hong Kong SAR, China": "Hong Kong", "Venezuela, RB": "Venezuela", "Slovak Republic": "Slovakia"}
METHODOLOGY = (
    "Bottom-up demand by country (units from UN Comtrade net imports, quote-verified web evidence and a "
    "fact-checked estimator; a sigmoid saturation model for the rest), valued with a customs-based price curve "
    "and an evidence-based price level, reconciled with the consensus of published global sizes by "
    "inverse-variance weighting (applied to the price level). Segments split top-down from country totals."
)


def display(name: str) -> str:
    return DISPLAY.get(name, name)


def wb_meta() -> dict[str, dict[str, str]]:
    """{ISO3: {name, region}} with the World Bank's regions mapped to the report's five regions."""
    data = httpx.get("https://api.worldbank.org/v2/country", params={"format": "json", "per_page": 400}, timeout=60).json()[1]
    out = {}
    for c in data:
        wb_region = str((c.get("region") or {}).get("value") or "").strip().lower()
        if wb_region == "aggregates":
            continue
        if c["id"] in ("PAK", "AFG"):
            region = R.ASIA_PACIFIC
        elif "north america" in wb_region:
            region = R.NORTH_AMERICA
        elif "latin" in wb_region:
            region = R.LATIN_AMERICA
        elif "europe" in wb_region:
            region = R.EUROPE
        elif "asia" in wb_region or "pacific" in wb_region:
            region = R.ASIA_PACIFIC
        elif "middle east" in wb_region or "africa" in wb_region:
            region = R.MIDDLE_EAST_AFRICA
        else:
            region = R.REST_OF_WORLD
        out[c["id"]] = {"name": display(c["name"]), "region": region}
    return out


def _cached(path: Path, build: Callable[[], Any]) -> Any:
    data = checkpoints.read_json(path)
    if data is None:
        data = build()
        checkpoints.write_json(path, data)
    return data


def respectability(final: float, figures: list[float]) -> dict[str, Any]:
    if not figures:
        return {"verdict": "no published sizes to compare", "n": 0}
    fs = sorted(figures)
    q = statistics.quantiles(fs, n=4) if len(fs) >= 4 else [fs[0], statistics.median(fs), fs[-1]]
    if q[0] <= final <= q[2]:
        verdict = "IN LINE with the market (inside the published interquartile range)"
    elif fs[0] <= final <= fs[-1]:
        verdict = "WITHIN the published range (outside the interquartile range)"
    else:
        verdict = "OUTLIER -- outside every published figure; needs an explanation before publishing"
    return {"verdict": verdict, "n": len(fs), "min": fs[0], "p25": q[0], "median": statistics.median(fs),
            "p75": q[2], "max": fs[-1]}


def publish(market: str, llm: GenLLM, config: GenConfig, base_year: int = 2025, root: Path | None = None,
            segments: bool = True, say: Callable[[str], None] = print, method: str = "design") -> dict[str, Any]:
    slug = checkpoints.slugify(market)
    result_path = signal_dir(market, root) / "run_1" / "07_result.json"
    r = checkpoints.read_json(result_path)
    if r is None:
        raise FileNotFoundError(f"no signal-model run for {market} -- run demand_country_2a_signal.py first")
    if "countries" not in (r.get("rest_of_world") or {}):
        raise RuntimeError(f"{result_path} predates per-country Rest of World values -- rerun the signal model")
    out_dir = (Path(root) if root else Path(".")) / REPORTS / slug
    meta = _cached((Path(root) if root else Path(".")) / CONSENSUS / "wb_country_meta.json", wb_meta)

    # 1-2. reconcile with the published consensus
    pub = _cached((Path(root) if root else Path(".")) / CONSENSUS / f"{slug}.json", lambda: published.gather(
        llm, market, base_year, search=lambda q: _search(q, max_results=6), pages=page_windows))
    cons = published.consensus(pub["figures"], base_year)
    sigma_b, sigma_detail = adapters.sigma_signal_model(r)
    if method == "design":
        rec = combine.design_blend(r["global_usd_mn"], [f["value_base_year_usd_mn"] for f in cons["used"]] if cons else [],
                                   design_config.load()["publisher"]["weight_bottom_up"])
        rec["sigma"] = sigma_b  # informational: the bottom-up log-spread
    else:
        rec = combine.blend(r["global_usd_mn"], sigma_b, cons)
    factor = rec["value_usd_mn"] / r["global_usd_mn"]
    say(f"[reconcile] bottom-up ${r['global_usd_mn']:,.0f}M (sigma {sigma_b:.2f}) + published "
        + (f"${cons['value_usd_mn']:,.0f}M from {cons['n_publishers']} publishers (sigma {cons['sigma']:.2f})" if cons else "none")
        + f" -> ${rec['value_usd_mn']:,.0f}M (price-level factor x{factor:.2f})")

    # 3. countries + regions
    rows = []
    for c in r["countries"]:
        est = c.get("estimate") or {}
        rows.append({"country": display(c["country"]), "iso3": c["iso3"],
                     "region": meta.get(c["iso3"], {}).get("region", R.REST_OF_WORLD),
                     "units": c["units"], "price_usd": c["price_usd"] * factor,
                     "value_usd_mn": c["value_usd_mn"] * factor, "basis": c["basis"],
                     "source": c.get("anchor_source") or "", "quote": c.get("anchor_quote") or "",
                     "derivation": (f"{est.get('formula')} with {est.get('inputs')} (facts: {'; '.join(est.get('facts_used') or [])})"
                                    if est else "")})
    rest = []
    for c in r["rest_of_world"]["countries"]:
        m = meta.get(c["iso3"], {"name": c["iso3"], "region": R.REST_OF_WORLD})
        rest.append({"country": m["name"], "iso3": c["iso3"], "region": m["region"], "units": c["units"],
                     "price_usd": c["price_usd"] * factor, "value_usd_mn": c["value_usd_mn"] * factor})
    region_totals: dict[str, dict[str, float]] = {}
    for row in rows + rest:
        t = region_totals.setdefault(row["region"], {"units": 0.0, "value_usd_mn": 0.0})
        t["units"] += row["units"]
        t["value_usd_mn"] += row["value_usd_mn"]
    total_value = sum(t["value_usd_mn"] for t in region_totals.values())
    total_units = sum(t["units"] for t in region_totals.values())

    # 5. respectability against the published figures that survived the consensus outlier filter
    # (a figure >2x away from the median is a misread or a different market, e.g. "$595B" TVs)
    pub_values = [f["value_base_year_usd_mn"] for f in cons["used"]] if cons else []
    resp = respectability(total_value, pub_values)
    hard_value = sum(x["value_usd_mn"] for x in rows if x["basis"].endswith("anchor"))
    documentation = {"value_share_on_sourced_units": hard_value / total_value,
                     "price_basis": r["price"]["basis"], "sigma_bottom_up": sigma_b, **sigma_detail}

    report = {
        "market": market, "base_year": base_year, "generated_at": datetime.now(timezone.utc).isoformat(),
        "methodology": METHODOLOGY, "global_usd_mn": total_value, "global_units": total_units,
        "avg_price_usd": total_value * 1e6 / total_units, "bottom_up_usd_mn": r["global_usd_mn"],
        "reconciliation": {**rec, "price_level_factor": factor, "published": cons and {
            k: cons[k] for k in ("value_usd_mn", "sigma", "n_publishers")}},
        "respectability": resp, "documentation": documentation,
        "regions": region_totals, "countries": rows, "rest_of_world_countries": rest,
        "pricing": r["price"], "ceiling": r["ceiling"], "hs_code": r.get("hs_code"),
        "worldwide_units_crosscheck": r.get("worldwide_units_crosscheck"),
        "published_sources": (cons["used"] + cons["dropped"]) if cons else [],
        "flags": [f for f in [rec.get("flag")] if f] + ([resp["verdict"]] if resp.get("verdict", "").startswith("OUTLIER") else []),
    }

    # 4. segments over the final country values (top countries + Rest of World)
    if segments:
        totals = {x["country"]: x["value_usd_mn"] for x in rows}
        totals["Rest of World"] = sum(x["value_usd_mn"] for x in rest)
        seg_path = out_dir / "segments_approved.json"
        approved = checkpoints.read_json(seg_path)
        if approved is None:
            draft = S.build_segments_draft(llm=llm, config=config, approach="2A", market_name=market,
                                           scope=f"Global {market} market, {base_year}", base_year=base_year,
                                           country_totals=totals, bottom_up_dimensions=[])
            approved = S.approve_segments(draft, approved_by="publisher (auto)")
            checkpoints.write_json(seg_path, approved)
        seg_report = S.finalize_report(approved, METHODOLOGY)
        W.write_report_workbook(seg_report, out_dir / f"{market} - Segments.xlsx")
        report["segments"] = [{"dimension": t["dimension"], "global_values_usd_mn": t.get("global_values"),
                               "evidence_status": t.get("evidence_status")} for t in seg_report["segmentation"]]

    checkpoints.write_json(out_dir / f"{market} - Market Report.json", report)
    write_markdown(report, out_dir / f"{market} - Methodology & Sources.md")
    write_workbook(report, out_dir / f"{market} - Market Report.xlsx")
    return report


# --------------------------------------------------------------------------- #
# documentation pack
# --------------------------------------------------------------------------- #
def _usd(v: float) -> str:
    return f"${v / 1000:,.1f}B" if v >= 1000 else f"${v:,.0f}M"


def write_markdown(rep: dict[str, Any], path: Path) -> Path:
    rec, resp, doc = rep["reconciliation"], rep["respectability"], rep["documentation"]
    L = [f"# {rep['market']} — Global Market Estimate {rep['base_year']}", "",
         f"**Market size: {_usd(rep['global_usd_mn'])}** · {rep['global_units'] / 1e6:,.1f}M units · "
         f"average {rep['avg_price_usd']:,.0f} USD per unit", "",
         f"**Respectability:** {resp['verdict']}" + (f" (published: n={resp['n']}, min {_usd(resp['min'])}, "
         f"IQR {_usd(resp['p25'])}–{_usd(resp['p75'])}, max {_usd(resp['max'])})" if resp.get("n") else ""), "",
         f"**Documentation:** {doc['value_share_on_sourced_units']:.0%} of the value rests on sourced country units "
         f"(customs data or quoted evidence); price basis: {doc['price_basis']}.", ""]
    if rep["flags"]:
        L += ["**Flags:** " + " · ".join(rep["flags"]), ""]
    L += ["## Methodology", rep["methodology"], "",
          "## Reconciliation", "| Component | Value | Uncertainty (log σ) | Weight |", "|---|---|---|---|",
          f"| Bottom-up (this model) | {_usd(rep['bottom_up_usd_mn'])} | {doc['sigma_bottom_up']:.2f} | {rec['weight_bottom_up']:.0%} |"]
    if rec.get("published"):
        p = rec["published"]
        L.append(f"| Published consensus ({p['n_publishers']} publishers) | {_usd(p['value_usd_mn'])} | {p['sigma']:.2f} | {rec['weight_published']:.0%} |")
    L += [f"| **Final** | **{_usd(rec['value_usd_mn'])}** | {rec['sigma']:.2f} | price-level factor ×{rec['price_level_factor']:.2f} |", ""]
    w = rep.get("worldwide_units_crosscheck")
    if w:
        L += [f"Units cross-check: {rep['global_units'] / 1e6:,.1f}M vs a published worldwide total of "
              f"{w['units'] / 1e6:,.1f}M ({w['gap_pct']:+.1f}%, {w.get('year')}) — [source]({w.get('url')}).", ""]
    L += ["## Regions", "| Region | Units (M) | Value |", "|---|---|---|"]
    for reg in R.REGION_ORDER:
        if reg in rep["regions"]:
            t = rep["regions"][reg]
            L.append(f"| {reg} | {t['units'] / 1e6:,.1f} | {_usd(t['value_usd_mn'])} |")
    L += ["", "## Countries (top 40)", "| Country | Units (M) | Price (USD) | Value | Basis | Source |", "|---|---|---|---|---|---|"]
    for c in rep["countries"]:
        src = f"[link]({c['source']})" if c["source"].startswith("http") else (c["source"] or "—")
        L.append(f"| {c['country']} | {c['units'] / 1e6:,.2f} | {c['price_usd']:,.0f} | {_usd(c['value_usd_mn'])} | {c['basis']} | {src} |")
    L += ["", f"Rest of world: {len(rep['rest_of_world_countries'])} countries modelled from World Bank data "
          "(population, income, urbanisation, electricity, internet) under the same saturation curve.", "",
          "## Evidence for each sourced country", ""]
    for c in rep["countries"]:
        if c["quote"]:
            L.append(f"- **{c['country']}** ({c['basis']}): “{c['quote'][:240]}” — {c['source'] or 'n/a'}")
    L += ["", "## Estimator derivations (countries without a sourced figure)", ""]
    for c in rep["countries"]:
        if c["derivation"]:
            L.append(f"- **{c['country']}**: {c['derivation'][:400]}")
    pr = rep["pricing"]
    L += ["", "## Pricing", f"- {pr['basis']}"]
    lv = pr.get("level") or {}
    if lv:
        L.append(f"- Market definition evidence: {lv.get('definition', {}).get('level')} — {lv.get('definition', {}).get('reason', '')[:200]}")
        if lv.get("vendor_asp_points"):
            L.append(f"- Vendor ASP evidence: {', '.join(f'${v:,.0f}' for v in lv['vendor_asp_points'])}")
    L.append(f"- Reconciliation price-level factor ×{rec['price_level_factor']:.2f} → average {rep['avg_price_usd']:,.0f} USD per unit")
    ce = rep["ceiling"]
    L += ["", "## Physical constants",
          f"- Units per household at saturation: {ce['items_per_household']['value']:g} "
          f"({'sourced' if ce['items_per_household']['verified_quote'] else 'estimate'}) {ce['items_per_household'].get('url') or ''}",
          f"- Replacement cycle: {ce['replacement_years']['value']:g} years "
          f"({'sourced' if ce['replacement_years']['verified_quote'] else 'estimate'}) {ce['replacement_years'].get('url') or ''}"]
    if rep.get("segments"):
        L += ["", "## Segments (top-down from country totals)"]
        for s in rep["segments"]:
            vals = ", ".join(f"{k} {_usd(v)}" for k, v in (s["global_values_usd_mn"] or {}).items())
            L.append(f"- **{s['dimension']}** ({s.get('evidence_status')}): {vals}")
    L += ["", "## Published estimates consulted (for reconciliation and the respectability check)",
          "| Publisher | Year | Reported | At base year | Source |", "|---|---|---|---|---|"]
    for f in sorted(rep["published_sources"], key=lambda f: f["value_base_year_usd_mn"]):
        L.append(f"| {f['publisher']} | {f['year']} | {_usd(f['value_usd_mn'])} | {_usd(f['value_base_year_usd_mn'])} | "
                 + (f"[link]({f['url']})" if f.get("url") else "—") + " |")
    L += ["", "## Limitations",
          "- Countries without customs data or a quoted figure rely on the saturation model and fact-checked estimates.",
          "- Customs unit values are landed prices; the published consensus sets the price level through the reconciliation factor.",
          "- Segment shares are estimated top-down from web evidence and are less certain than country totals."]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L), encoding="utf-8")
    return path


def write_workbook(rep: dict[str, Any], path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    for row in [("Market", rep["market"]), ("Base year", rep["base_year"]), ("Market size (USD Mn)", round(rep["global_usd_mn"], 1)),
                ("Units", round(rep["global_units"])), ("Average price (USD)", round(rep["avg_price_usd"], 1)),
                ("Bottom-up (USD Mn)", round(rep["bottom_up_usd_mn"], 1)),
                ("Price-level factor", round(rep["reconciliation"]["price_level_factor"], 3)),
                ("Respectability", rep["respectability"]["verdict"]),
                ("Value on sourced units", f"{rep['documentation']['value_share_on_sourced_units']:.0%}"),
                ("Methodology", rep["methodology"])]:
        ws.append(row)
    ws = wb.create_sheet("Regions")
    ws.append(["Region", "Units", "Value (USD Mn)"])
    for reg, t in rep["regions"].items():
        ws.append([reg, round(t["units"]), round(t["value_usd_mn"], 1)])
    ws = wb.create_sheet("Countries")
    ws.append(["Country", "ISO3", "Region", "Units", "Price (USD)", "Value (USD Mn)", "Basis", "Source", "Quote", "Derivation"])
    for c in rep["countries"]:
        ws.append([c["country"], c["iso3"], c["region"], round(c["units"]), round(c["price_usd"], 1),
                   round(c["value_usd_mn"], 1), c["basis"], c["source"], c["quote"], c["derivation"][:1000]])
    for c in rep["rest_of_world_countries"]:
        ws.append([c["country"], c["iso3"], c["region"], round(c["units"]), round(c["price_usd"], 1),
                   round(c["value_usd_mn"], 2), "model (World Bank data)", "", "", ""])
    ws = wb.create_sheet("Published")
    ws.append(["Publisher", "Year", "Reported (USD Mn)", "At base year (USD Mn)", "CAGR %", "Quote", "URL"])
    for f in rep["published_sources"]:
        ws.append([f["publisher"], f["year"], round(f["value_usd_mn"]), round(f["value_base_year_usd_mn"]),
                   f.get("cagr_pct"), f["quote"], f.get("url")])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path

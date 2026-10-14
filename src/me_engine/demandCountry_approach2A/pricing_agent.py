"""Pricing agent: studies a market's price level and price curve from
evidence, then hands the signal model a per-country price function.

Three questions, each answered from sourced numbers:

1. Price LEVEL the market is defined at -- vendor/manufacturer revenue
   (sell-in) or consumer spending (sell-out)? The LLM decides from how
   market-size reports for this product describe themselves (evidence).
2. Price CURVE -- how price scales with income. Robust (soft-L1) fit of
   log(customs import unit value) on log(GDP per capita PPP), import-dependent
   countries only, with a bootstrap 90% interval on the elasticity.
3. LEVEL FACTOR -- from the landed (customs) curve to the chosen level:
     vendor   = median vendor ASP (brand revenue / brand units, tracker ASPs)
                / the landed curve's average at the same weights
     consumer = median over countries of (retail average price / landed curve price)
   Every input is a quote-verified fact; with none, the factor is 1.0 and
   the result says so.

The study (evidence + LLM extraction + level decision) is cached once per
market and shared by every run, so it never adds run-to-run variance.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Callable

import numpy as np
from scipy.optimize import least_squares

from me_engine.generation.llm import GenLLM

FACTOR_BOUNDS = (0.8, 2.5)
VENDOR_BAND = (0.7, 2.0)   # plausible vendor ASP / landed customs value
RETAIL_BAND = (0.9, 3.0)   # plausible retail price / landed customs value
MIN_VENDOR_SOURCES = 2
MIN_RETAIL_COUNTRIES = 3
_BOOTSTRAPS = 300


def _robust_curve(points: list[tuple[float, float, float]]) -> tuple[float, float]:
    """log(price) = a + e * log(gdp), soft-L1, weighted by log10(import units)."""
    x = np.array([math.log(g) for g, _, _ in points])
    y = np.array([math.log(p) for _, p, _ in points])
    sw = np.sqrt(np.array([math.log10(max(u, 10)) for *_, u in points]))
    e0, a0 = np.polyfit(x, y, 1)
    res = least_squares(lambda p: sw * (p[0] + p[1] * x - y), [a0, e0], loss="soft_l1", f_scale=0.3)
    return float(res.x[0]), float(res.x[1])


def study_curve(points: list[tuple[float, float, float]], seed: int = 0) -> dict[str, Any]:
    a, e = _robust_curve(points)
    rng = np.random.default_rng(seed)
    boot = []
    for _ in range(_BOOTSTRAPS):
        sample = [points[i] for i in rng.integers(0, len(points), len(points))]
        if len({g for g, _, _ in sample}) >= 3:
            boot.append(_robust_curve(sample)[1])
    lo, hi = (float(np.percentile(boot, 5)), float(np.percentile(boot, 95))) if boot else (e, e)
    return {"a": a, "elasticity": e, "elasticity_90ci": [lo, hi], "n_points": len(points)}


def gather_price_facts(llm: GenLLM, market: str, base_year: int, search: Callable[[str], list[dict[str, str]]],
                       pages: Callable[[str], list[str]], verify: Callable[[float, str], bool]) -> dict[str, Any]:
    """Quote-verified vendor ASPs, retail prices by country, and the market-definition evidence."""
    meta = llm.complete_json(
        "You know the consumer goods industry and how it is reported.",
        f"Product/market: {market}. Give (1) the 6 largest brands worldwide by units, (2) the everyday term "
        "people and the trade press use for one unit in search queries (e.g. 'TV' for Televisions), and (3) the "
        "industry trackers that publish its unit shipments and average selling prices.\n"
        'Return JSON: {"brands": ["..."], "term": "<term>", "trackers": ["..."]}',
    )
    brands, term = meta.get("brands") or [], str(meta.get("term") or market)
    trackers = " ".join((meta.get("trackers") or [])[:2])
    y = base_year - 1
    queries = ([f"{b} {term} shipments {y} million units revenue" for b in brands[:6]]
               + [f"global {term} average selling price {y} {trackers}", f"{term} ASP {y} average selling price fell rose",
                  f"{term} shipments {y} revenue billion units million average price"]
               + [f"average selling price of a {term} in {c} {y}" for c in
                  ("United States", "Germany", "United Kingdom", "Japan", "Brazil", "India", "China", "Indonesia")])
    def_queries = [f"{market} market size {y} report revenue methodology", f"{term} market size {y} billion"]
    evidence: list[dict[str, str]] = []
    for q in queries + def_queries:
        evidence += search(q)
    for url in list(dict.fromkeys(e["url"] for e in evidence))[:16]:
        evidence += [{"url": url, "text": f"[full page] {w}"[:1500]} for w in pages(url)]
    block = "\n".join(f"[{i}] ({e['url']}) {e['text']}" for i, e in enumerate(evidence))

    data = llm.complete_json(
        "You extract pricing facts from web evidence. Use only the snippets; copy each sentence verbatim. "
        "Never compute or invent a number.",
        f"Market: {market}, years {y - 1}-{base_year}.\n"
        "1) Every brand/vendor figure: annual units shipped and/or annual revenue for this product line (same brand, same year).\n"
        "2) Every worldwide average selling price (ASP) per unit stated by a tracker or vendor, with its scope: "
        "'all_units' only if it is the average over ALL units of the product sold (not a size class, a premium "
        "segment, one model or one brand).\n"
        "3) Every average retail/consumer price per unit for a named country (all units sold, not one model).\n"
        "4) From the market-size reports in the snippets: are such market sizes defined at vendor/manufacturer "
        "revenue (sell-in) or at consumer spending (sell-out)? Quote the sentence that shows it, if any.\n\n"
        f"Snippets:\n{block}\n\n"
        'Return JSON: {"vendor": [{"brand": "", "year": 0, "units_million": null, "revenue_usd_billion": null, '
        '"quote": "", "snippet_index": 0}], "asp": [{"usd": 0, "year": 0, "scope": "<all_units|segment>", '
        '"quote": "", "snippet_index": 0}], '
        '"retail": [{"country": "", "usd": 0, "year": 0, "quote": "", "snippet_index": 0}], '
        '"definition": {"level": "<vendor|consumer|unclear>", "quote": "", "snippet_index": null, "reason": ""}}',
    )

    def url(i: Any) -> str:
        return evidence[i]["url"] if isinstance(i, int) and 0 <= i < len(evidence) else ""

    vendor_asp = []
    for v in data.get("vendor") or []:
        try:
            units, revenue = float(v.get("units_million")), float(v.get("revenue_usd_billion"))
        except (TypeError, ValueError):
            continue
        q = str(v.get("quote") or "")
        if units > 0 and revenue > 0 and verify(units, q) and verify(revenue, q):
            vendor_asp.append({"usd": revenue * 1e9 / (units * 1e6), "source": f"{v.get('brand')} {v.get('year')}",
                               "quote": q[:300], "url": url(v.get("snippet_index")), "kind": "brand revenue / units"})
    for v in data.get("asp") or []:
        try:
            usd = float(v.get("usd"))
        except (TypeError, ValueError):
            continue
        if usd > 0 and v.get("scope") == "all_units" and verify(usd, str(v.get("quote") or "")):
            vendor_asp.append({"usd": usd, "source": f"tracker {v.get('year')}", "quote": str(v["quote"])[:300],
                               "url": url(v.get("snippet_index")), "kind": "tracker ASP"})
    retail = []
    for v in data.get("retail") or []:
        try:
            usd = float(v.get("usd"))
        except (TypeError, ValueError):
            continue
        if usd > 0 and v.get("country") and verify(usd, str(v.get("quote") or "")):
            retail.append({"country": str(v["country"]), "usd": usd, "year": v.get("year"),
                           "quote": str(v["quote"])[:300], "url": url(v.get("snippet_index"))})
    definition = data.get("definition") or {}
    level = str(definition.get("level") or "unclear")
    return {"brands": brands, "term": term, "vendor_asp": vendor_asp, "retail": retail,
            "definition": {"level": level if level in ("vendor", "consumer") else "unclear",
                           "quote": str(definition.get("quote") or "")[:300], "url": url(definition.get("snippet_index")),
                           "reason": str(definition.get("reason") or "")[:300]}}


def level_factor(study: dict[str, Any], curve: dict[str, Any], landed_avg: float,
                 gdp_of_country: Callable[[str], float | None]) -> dict[str, Any]:
    """Factor from the landed curve to the market's price level, with its basis."""
    def landed(gdp: float) -> float:
        return math.exp(curve["a"]) * gdp ** curve["elasticity"]

    level = study["definition"]["level"]
    # One number syndicated across press releases is ONE source; and a vendor ASP
    # outside a plausible band around the customs value can't be the all-units
    # average (it's a segment, or a misread) -- the price chain from landed to
    # vendor revenue doesn't span more than that.
    vendor, rejected = [], []
    for usd in sorted({round(v["usd"], 0) for v in study["vendor_asp"]}):
        (vendor if VENDOR_BAND[0] * landed_avg <= usd <= VENDOR_BAND[1] * landed_avg else rejected).append(usd)
    ratios = []
    for r in study["retail"]:
        gdp = gdp_of_country(r["country"])
        if gdp:
            ratio = r["usd"] / landed(gdp)
            if RETAIL_BAND[0] <= ratio <= RETAIL_BAND[1]:
                ratios.append({"country": r["country"], "retail": r["usd"], "landed": landed(gdp), "ratio": ratio})
    out: dict[str, Any] = {"level": level, "vendor_asp_points": vendor, "vendor_asp_rejected": rejected,
                           "retail_ratios": ratios}
    # A level change needs corroboration: >= 2 independent vendor ASPs agreeing
    # within 25%, or retail prices for >= 3 countries. One source (seen: a
    # single-country report's ASP read as worldwide) is not enough to move
    # the whole market's value.
    # Corroboration = a cluster of >= 2 sources within -20%/+25% of the median;
    # values outside the cluster are dropped rather than vetoing the rest.
    med = statistics.median(vendor) if vendor else 0.0
    cluster = [v for v in vendor if 0.8 * med <= v <= 1.25 * med]
    vendor_ok = len(cluster) >= MIN_VENDOR_SOURCES
    retail_ok = len(ratios) >= MIN_RETAIL_COUNTRIES
    out["vendor_asp_cluster"] = cluster
    chosen = level if level != "unclear" else ("vendor" if vendor_ok else "consumer")
    if chosen == "vendor" and vendor_ok:
        factor = statistics.median(cluster) / landed_avg
        basis = (f"median of {len(cluster)} corroborating vendor ASPs ${statistics.median(cluster):,.0f} / "
                 f"landed avg ${landed_avg:,.0f}")
    elif chosen == "consumer" and retail_ok:
        factor, basis = statistics.median(r["ratio"] for r in ratios), f"median retail/landed over {len(ratios)} countries"
    else:
        chosen = "landed"
        factor, basis = 1.0, (f"not enough corroborated level evidence ({len(vendor)} vendor ASP(s), "
                              f"{len(ratios)} retail countries) -- landed price level kept")
    clipped = float(np.clip(factor, *FACTOR_BOUNDS))
    out.update(chosen_level=chosen, factor=clipped, raw_factor=factor, basis=basis,
               clipped=not math.isclose(clipped, factor))
    return out

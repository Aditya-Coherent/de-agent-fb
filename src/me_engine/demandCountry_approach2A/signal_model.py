"""2A signal model (experiment): estimate every country's annual demand from
a feature vector of hard data + LLM-scored qualitative evidence, squashed
through a sigmoid under a physical ceiling, calibrated on the countries
where a figure exists.

    ceiling(c)          = items_per_household_at_saturation
                          / (household_size(c) * replacement_years)
    units_per_person(c) = ceiling(c) * sigmoid(w . z_c + b)
    units(c)            = population(c) * units_per_person(c)
    value(c)            = units(c) * price

The ceiling is physics, not fitted: how many units a fully-saturated country
buys per person per year. The sigmoid only says how far up that ceiling a
country sits (income, electrification, momentum, ...).

Steps (each cached to `output/demand_country_2a/<slug>/signal_model/`):

1. Countries  -- the LLM lists the top N consuming countries (ISO3, checked
   against the World Bank list) with their average household size.
2. Hard data  -- World Bank, latest year per country: population, GDP per
   capita (PPP), urban share, electricity access, internet use.
2b. Trade     -- UN Comtrade (official customs data) for the market's HS6
   code: imports/exports in units and USD per country. Net imports are a
   consumption anchor where exports are < 25% of imports (no big domestic
   production or re-export hub); import unit values give the price curve.
3. Evidence   -- per top-N country, 4 web searches (+ Google AI Overview when
   the local scraper answers) + passages around figures in the top 3 full
   pages (snippets cut figures off); the LLM converts the evidence into
   (a) an evidence anchor: annual units IN MILLIONS with a verbatim quote,
       accepted only when the quote contains the number, is about THIS
       product, and is domestic sales / shipments into the country, 2022-2025;
   (b) rubric scores -2..+2 (qualitative -> quantitative), and
   (c) the LLM's own best estimate + confidence (a "knowledge anchor") --
       free web snippets rarely state per-country unit sales.
   Plus an embedding signal: where the country's snippets sit between a
   "strong demand" and a "weak demand" reference statement.
4. Ceiling    -- items per household at saturation and replacement years,
   from evidence (verified quotes) else LLM knowledge.
5. Fit        -- b, w by robust (soft-L1) weighted least squares on
   log(units per person): evidence anchors weight 1, knowledge anchors
   0.5 x confidence; ridge-penalised. Leave-one-out error on the calibration
   points measures how well it predicts an unseen country.
6. Mix        -- a country keeps its trade / web anchor when within 3x of the
   leave-one-out prediction; otherwise the model, blended in log space with
   the LLM's estimate (weighted by its confidence) when there is one. Every
   other World Bank country is modelled from hard data only (Rest of World).
7. Price      -- per country, from a curve of customs import unit value vs
   GDP per capita (landed, pre-retail price level). Fallback when trade data
   is too thin: median of evidence-stated prices, else an LLM estimate.
8. Cross-check -- worldwide unit total from evidence, reported against the
   model's global units. Information only; never adjusts anything.

A published market VALUE is never an input.
"""
from __future__ import annotations

import json
import math
import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import httpx
import numpy as np
from scipy.optimize import least_squares

from me_engine.generation.llm import GenLLM

from . import checkpoints, estimator, pricing_agent
from .engine import web_search

WB_INDICATORS = {
    "population": "SP.POP.TOTL",
    "gdp_pc_ppp": "NY.GDP.PCAP.PP.CD",
    "urban_pct": "SP.URB.TOTL.IN.ZS",
    "electricity_pct": "EG.ELC.ACCS.ZS",
    "internet_pct": "IT.NET.USER.ZS",
}
HARD_FEATURES = ["log_gdp_pc_ppp", "urban_pct", "electricity_pct", "internet_pct"]
RUBRIC = {
    "market_momentum": "-2 sales clearly falling ... +2 sales clearly growing",
    "replacement_cycle": "-2 old sets kept for many years ... +2 frequent upgrades/replacements",
    "affordability": "-2 product unaffordable for most households ... +2 cheap relative to incomes, easy credit",
    "adoption_maturity": "-2 most households lack the product ... +2 near-universal ownership, multiple per household",
}
QUAL_FEATURES = list(RUBRIC) + ["embedding_signal"]
FEATURES = HARD_FEATURES + QUAL_FEATURES
ANCHOR_YEARS = range(2022, 2026)
ANCHOR_MEASURES = ("domestic_sales", "shipments_into_country")
ANCHOR_TRUST_RATIO = 3.0
KNOWLEDGE_WEIGHT = 0.5
EVIDENCE_VERSION = 5  # bump when the evidence queries, page reading or scoring prompt change
RIDGE = 0.5
MODEL_BLEND_WEIGHT = 0.5  # model's weight when blending with an LLM-knowledge estimate (log space)
TRADE_YEARS = (2023, 2022)
TRADE_MAX_EXPORT_RATIO = 0.25  # net imports ~ consumption only where the country makes/re-exports little
TRADE_MIN_PRICE_UNITS = 50_000  # import volume needed for a usable unit value
MASS_BALANCE_WEIGHT = 0.7  # production statistics are noisier than customs flows
OFFICIAL_CAP_SLACK = 1.5  # the saturation ceiling is approximate; official data may exceed it a little
PAGES_PER_COUNTRY = 3
_WB = "https://api.worldbank.org/v2"
_COMTRADE = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"


def signal_dir(market_name: str, root: Path | None = None) -> Path:
    return checkpoints.run_dir(market_name, root) / "signal_model"


def _cached(path: Path, build: Callable[[], Any]) -> Any:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    data = build()
    checkpoints.write_json(path, data)
    return data


def _number_in_quote(value: float, quote: str) -> bool:
    """The quote states the figure (as given, spelled out x1e3 / x1e6, or in
    East Asian units: 万 = 1e4, so N万 units = N/100 million; 亿 = 1e8 = N*100 million)."""
    nums = [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*\.?\d*", quote or "")]
    scales = (1.0, 1e-6, 1e-3, 1e-2, 1e2)
    return any(abs(n * s - value) <= 0.051 * max(value, 1e-9) for n in nums if n > 0 for s in scales)


def _search(query: str, max_results: int = 6) -> list[dict[str, str]]:
    for attempt in range(3):  # ddgs returns nothing when throttled
        results = web_search.search(query, max_results=max_results)
        if results:
            return [{"url": r.url, "text": r.snippet[:1500]} for r in results]
        time.sleep(5 * (attempt + 1))
    return []


def _snippet_block(evidence: list[dict[str, str]]) -> str:
    return "\n".join(f"[{i}] ({e['url']}) {e['text']}" for i, e in enumerate(evidence)) or "(none)"


def _url_at(evidence: list[dict[str, str]], idx: Any) -> str:
    return evidence[idx]["url"] if isinstance(idx, int) and 0 <= idx < len(evidence) else ""


# --------------------------------------------------------------------------- #
# 1-2. countries + World Bank hard data
# --------------------------------------------------------------------------- #
def world_bank_countries() -> dict[str, str]:
    """{ISO3: name} for real countries (aggregates like 'World' dropped)."""
    data = httpx.get(f"{_WB}/country", params={"format": "json", "per_page": 400}, timeout=60).json()[1]
    return {c["id"]: c["name"] for c in data if (c.get("region") or {}).get("value") != "Aggregates"}


def world_bank_indicators(iso3s: set[str]) -> dict[str, dict[str, float]]:
    """{ISO3: {feature: latest value 2015-2024}}."""
    out: dict[str, dict[str, float]] = {c: {} for c in iso3s}
    for feature, code in WB_INDICATORS.items():
        rows = httpx.get(f"{_WB}/country/all/indicator/{code}",
                         params={"format": "json", "per_page": 20000, "date": "2015:2024"}, timeout=120).json()[1]
        latest: dict[str, tuple[int, float]] = {}
        for r in rows:
            iso, val = r.get("countryiso3code"), r.get("value")
            if iso in out and val is not None and (iso not in latest or int(r["date"]) > latest[iso][0]):
                latest[iso] = (int(r["date"]), float(val))
        for iso, (_, val) in latest.items():
            out[iso][feature] = val
    return out


def top_countries(llm: GenLLM, market: str, n: int, wb: dict[str, str]) -> list[dict[str, Any]]:
    data = llm.complete_json(
        "You are a market analyst. List countries by how many units of a product their residents BUY per year "
        "(domestic consumption, not production or exports).",
        f"Product/market: {market}\nList the top {n} countries, largest first. Use ISO 3166-1 alpha-3 codes.\n"
        'Return JSON: {"countries": [{"name": "<country>", "iso3": "<XXX>", "population_million": <number>}, ...]}',
    )
    seen, out = set(), []
    for c in data.get("countries") or []:
        iso = str(c.get("iso3") or "").upper().strip()
        if iso in seen or not re.fullmatch(r"[A-Z]{3}", iso):
            continue
        seen.add(iso)
        out.append({"name": wb.get(iso) or str(c.get("name")), "iso3": iso, "in_world_bank": iso in wb,
                    "llm_population_million": c.get("population_million")})
    return out[:n]


def household_sizes(llm: GenLLM, countries: list[dict[str, Any]]) -> dict[str, float]:
    """{ISO3: average persons per household} (UN household statistics, from LLM knowledge)."""
    names = ", ".join(f"{c['name']} ({c['iso3']})" for c in countries)
    data = llm.complete_json(
        "You know UN household-size statistics.",
        f"Average number of persons per household, latest available, for: {names}\n"
        'Return JSON: {"household_size": {"<ISO3>": <number>, ...}}',
    )
    out = {}
    for iso, v in (data.get("household_size") or {}).items():
        try:
            if 1.0 <= float(v) <= 10.0:
                out[str(iso).upper()] = float(v)
        except (TypeError, ValueError):
            continue
    return out


# --------------------------------------------------------------------------- #
# 2b. official trade data (UN Comtrade, public preview endpoint, no key)
# --------------------------------------------------------------------------- #
def hs_code(llm: GenLLM, market: str) -> dict[str, str]:
    data = llm.complete_json(
        "You know the Harmonized System (HS) tariff nomenclature.",
        f"Which single 6-digit HS code covers finished {market} (the product itself, not parts or accessories)? "
        "Also say whether that code is SPECIFIC to this product, or also covers other finished products that "
        "sell in comparable volumes (e.g. 847130 covers both laptops and tablets).\n"
        'Return JSON: {"hs6": "<6 digits>", "description": "<official description>", '
        '"specific_to_product": <true|false>, "also_covers": "<other products, if any>"}',
    )
    hs6 = re.sub(r"\D", "", str(data.get("hs6") or ""))
    if len(hs6) != 6:
        raise RuntimeError(f"no valid HS6 code for {market}: {data}")
    return {"hs6": hs6, "description": str(data.get("description") or ""),
            "specific_to_product": data.get("specific_to_product") is True,
            "also_covers": str(data.get("also_covers") or "")}


def comtrade_reporters() -> dict[str, int]:
    """{ISO3: Comtrade numeric reporter code}."""
    data = httpx.get("https://comtradeapi.un.org/files/v1/app/reference/Reporters.json", timeout=60).json()["results"]
    # Current entities only: an ISO3 also maps to expired historical reporters
    # (USA -> 841 "USA and Puerto Rico (...1980)", India -> 356 "(...1974)"),
    # which return no data for recent years.
    return {r["reporterCodeIsoAlpha3"]: int(r["reporterCode"]) for r in data
            if r.get("reporterCodeIsoAlpha3") and not r.get("entryExpiredDate") and not r.get("isGroup")}


def trade_flows(hs6: str, reporter: int) -> dict[str, Any] | None:
    """Imports/exports (units + USD) for the latest available year, or None."""
    for year in TRADE_YEARS:
        params = {"reporterCode": reporter, "period": year, "cmdCode": hs6, "flowCode": "M,X", "partnerCode": 0}
        for attempt in range(4):
            try:
                resp = httpx.get(_COMTRADE, params=params, timeout=60)
            except httpx.HTTPError:
                time.sleep(3 * (attempt + 1))
                continue
            if resp.status_code == 429:  # the public endpoint is aggressively rate-limited
                time.sleep(5 * (attempt + 1))
                continue
            break
        else:
            return None
        rows = (resp.json() or {}).get("data") or []
        flows = {r["flowCode"]: r for r in rows}
        imp = flows.get("M")
        if imp and imp.get("qty"):
            exp = flows.get("X") or {}
            time.sleep(1.2)
            return {"year": year, "imports_units": float(imp["qty"]), "imports_usd": float(imp.get("primaryValue") or 0),
                    "exports_units": float(exp.get("qty") or 0.0),
                    "url": str(resp.request.url)}
        time.sleep(1.2)
    return None


def trade_anchor(flows: dict[str, Any] | None) -> dict[str, Any] | None:
    """Net imports as consumption, only where exports are small next to imports
    (no big domestic production / re-export hub)."""
    if not flows or flows["imports_units"] <= 0:
        return None
    if flows["exports_units"] > TRADE_MAX_EXPORT_RATIO * flows["imports_units"]:
        return None
    net = flows["imports_units"] - flows["exports_units"]
    return {"units": net, "year": flows["year"], "url": flows["url"],
            "quote": (f"UN Comtrade {flows['year']}: imports {flows['imports_units']:,.0f} units, exports "
                      f"{flows['exports_units']:,.0f} units -> net imports {net:,.0f}")}


def production_anchor(llm: GenLLM, market: str, country: str, flows: dict[str, Any] | None,
                      ceiling_units: float) -> dict[str, Any]:
    """Mass balance for producer countries (DeepSeek second-opinion #1):
    consumption = domestic production of the FINISHED product + imports - exports.
    Production must be a quote-verified figure for the finished product (not
    panels/parts), within one year of the trade data; the result must be
    positive and below the country's saturation ceiling. Returns
    {"anchor": ... or None, "rejected": reason, ...}."""
    if not flows:
        return {"anchor": None, "rejected": "no trade data"}
    y = flows["year"]
    evidence = []
    for q in [f"{country} {market} production {y} million units", f"{country} {market} output {y + 1} units statistics"]:
        evidence += _search(q, max_results=6)
    for url in list(dict.fromkeys(e["url"] for e in evidence))[:3]:
        evidence += [{"url": url, "text": f"[full page] {w}"[:1500]} for w in page_windows(url)]
    data = llm.complete_json(
        "You extract one production figure from web evidence. Use only the snippets; copy the sentence verbatim.",
        f"Annual domestic PRODUCTION of finished {market} in {country} (units made in the country, not components, "
        f"panels or modules), in MILLIONS of units, for {y - 1}-{y + 1}.\n\nSnippets:\n{_snippet_block(evidence)}\n\n"
        'Return JSON: {"production_million": <number or null>, "year": <int or null>, "quote": "<verbatim>", '
        '"finished_product_only": <true|false>, "snippet_index": <int or null>}',
    )
    try:
        prod = float(data.get("production_million"))
    except (TypeError, ValueError):
        return {"anchor": None, "rejected": "no production figure in the evidence"}
    quote = str(data.get("quote") or "")
    if prod <= 0 or not _number_in_quote(prod, quote):
        return {"anchor": None, "rejected": "quote does not contain the figure"}
    if data.get("finished_product_only") is not True:
        return {"anchor": None, "rejected": "figure includes components/panels"}
    if not isinstance(data.get("year"), int) or abs(data["year"] - y) > 1:
        return {"anchor": None, "rejected": f"production year {data.get('year')} too far from trade year {y}"}
    apparent = prod * 1e6 + flows["imports_units"] - flows["exports_units"]
    if apparent <= 0 or apparent > ceiling_units:
        return {"anchor": None, "rejected": f"apparent consumption {apparent / 1e6:,.1f}M outside (0, ceiling)",
                "production_units": prod * 1e6}
    return {"anchor": {"units": apparent, "year": data["year"], "url": _url_at(evidence, data.get("snippet_index")),
                       "quote": (f"production {prod:,.2f}M ({quote[:160]}) + imports {flows['imports_units'] / 1e6:,.2f}M "
                                 f"- exports {flows['exports_units'] / 1e6:,.2f}M (UN Comtrade {y})")},
            "rejected": None, "production_units": prod * 1e6}


def fit_price_curve(points: list[tuple[float, float, float]]) -> tuple[float, float]:
    """log(price) = a + e * log(GDP per capita PPP), weighted by log import units.
    points = [(gdp_pc, unit_value_usd, import_units)]."""
    x = np.array([math.log(g) for g, _, _ in points])
    y = np.array([math.log(p) for _, p, _ in points])
    w = np.array([math.log10(u) for *_, u in points])
    e, a = np.polyfit(x, y, 1, w=np.sqrt(w))
    return float(a), float(e)


# --------------------------------------------------------------------------- #
# 3. evidence -> anchor + rubric scores + knowledge estimate (+ embedding)
# --------------------------------------------------------------------------- #
_FIGURE_RE = re.compile(r"\d[\d,.]*\s*(?:million|mn|m\b|billion|bn|units|sets)", re.IGNORECASE)


def page_windows(url: str, max_windows: int = 5, width: int = 300) -> list[str]:
    """Passages around unit/currency figures in the full page (search
    snippets are ~150 characters and usually cut the figure off)."""
    from .engine.web_acquirer import _fetch_page_text

    if not url or url.startswith("https://www.google.com/search"):
        return []
    try:
        text = _fetch_page_text(url) or ""
    except Exception:  # noqa: BLE001
        return []
    out: list[str] = []
    for m in _FIGURE_RE.finditer(text):
        start = max(0, m.start() - width)
        if out and start < out[-1][1]:
            continue  # overlaps the previous passage
        out.append((start, m.end() + width))
        if len(out) >= max_windows:
            break
    return [" ".join(text[s:e].split()) for s, e in out]
def country_queries(market: str, country: str) -> list[str]:
    return [f"{country} {market} sales 2024 million units",
            f"{country} {market} market demand trends 2025",
            f"{country} {market} shipments 2024",
            f"{market} sold in {country} per year units 2025"]


def local_query(llm: GenLLM, market: str, country: str) -> str | None:
    """A search query in the country's main language, where that isn't
    English -- national sales statistics (e.g. China's AVC, in 万台) are
    published in the local language and invisible to English queries."""
    data = llm.complete_json(
        "You write web search queries in the local language.",
        f"Country: {country}. If its main language is NOT English, write a search query in that language for "
        f"'annual {market} retail sales volume in {country} 2024 (number of units)', using the terms local "
        "industry statistics use. If English is the main language, return null.\n"
        'Return JSON: {"language": "<language>", "query": "<query or null>"}',
    )
    q = data.get("query")
    return str(q).strip() if q and str(data.get("language", "")).lower() != "english" else None


def country_evidence(market: str, country: str, done: list[str] | None = None,
                     evidence: list[dict[str, str]] | None = None,
                     extra: list[str] | None = None) -> tuple[list[dict[str, str]], list[str]]:
    """Runs the queries not in `done`, appending to `evidence` (deduplicated)."""
    out = list(evidence or [])
    seen = {e["text"] for e in out}
    done = list(done or [])
    for q in country_queries(market, country) + list(extra or []):
        if q in done:
            continue
        for e in _search(q):
            if e["text"] not in seen:
                seen.add(e["text"])
                out.append(e)
        done.append(q)
    return out, done


def score_country(llm: GenLLM, market: str, country: str, evidence: list[dict[str, str]]) -> dict[str, Any]:
    rubric = "\n".join(f"- {k}: {v}" for k, v in RUBRIC.items())
    data = llm.complete_json(
        "You turn web evidence about one country's market into numbers. Use ONLY the snippets given; if a "
        "snippet doesn't support a score, score 0. Never invent a figure.",
        f"Market: {market}\nCountry: {country}\n\nSnippets:\n{_snippet_block(evidence)}\n\n"
        f"1) Annual units of {market} bought in {country} (domestic sales, or shipments INTO the country -- NOT "
        "production or exports), in MILLIONS of units, for 2022-2025, only if a snippet states it. Copy the exact "
        f"sentence, and say whether that sentence is about {market} itself (not a broader or different category).\n"
        f"2) Score each item from -2 to +2:\n{rubric}\n"
        f"3) Separately, from your own knowledge: your best estimate of annual units bought in {country} in "
        "MILLIONS, and your confidence 0-1.\n\n"
        'Return JSON: {"anchor": {"annual_units_million": <number or null>, "year": <int or null>, '
        '"measure": "<domestic_sales|shipments_into_country|production|exports|installed_base|other>", '
        '"quote": "<verbatim>", "quote_is_about_this_product": <true|false>, "snippet_index": <int or null>}, '
        '"scores": {' + ", ".join(f'"{k}": <-2..2>' for k in RUBRIC)
        + '}, "score_evidence": {"<item>": "<short reason citing a snippet index>"}, '
        '"knowledge_estimate": {"annual_units_million": <number>, "confidence": <0..1>}}',
    )
    scores = {}
    for k in RUBRIC:
        try:
            scores[k] = max(-2.0, min(2.0, float((data.get("scores") or {}).get(k) or 0.0)))
        except (TypeError, ValueError):
            scores[k] = 0.0
    a = data.get("anchor") or {}
    anchor, reject = None, None
    try:
        value = float(a.get("annual_units_million")) if a.get("annual_units_million") is not None else None
    except (TypeError, ValueError):
        value = None
    if value is None or value <= 0:
        reject = "no figure in the evidence"
    elif str(a.get("measure")) not in ANCHOR_MEASURES:
        reject = f"measures {a.get('measure')}, not domestic sales"
    elif a.get("quote_is_about_this_product") is not True:
        reject = "quote is about a different or broader product"
    elif a.get("year") not in ANCHOR_YEARS:
        reject = f"year {a.get('year')} outside {ANCHOR_YEARS.start}-{ANCHOR_YEARS.stop - 1}"
    elif not _number_in_quote(value, str(a.get("quote") or "")):
        reject = "quote does not contain the figure"
    else:
        anchor = {"units": value * 1e6, "year": a.get("year"), "quote": str(a.get("quote"))[:300],
                  "url": _url_at(evidence, a.get("snippet_index"))}
    k = data.get("knowledge_estimate") or {}
    try:
        knowledge = {"units": float(k["annual_units_million"]) * 1e6,
                     "confidence": max(0.0, min(1.0, float(k.get("confidence") or 0.0)))}
        if knowledge["units"] <= 0:
            knowledge = None
    except (KeyError, TypeError, ValueError):
        knowledge = None
    return {"scores": scores, "score_evidence": data.get("score_evidence") or {}, "anchor": anchor,
            "anchor_rejected": reject, "anchor_raw": a, "knowledge": knowledge}


def embedding_available() -> bool:
    from me_engine.generation import semantic_search
    return semantic_search.available()


def embedding_signal(market: str, evidence: list[dict[str, str]]) -> float:
    """0.0 (neutral) without evidence or without the local embedding model."""
    if not evidence or not embedding_available():
        return 0.0
    from me_engine.generation.semantic_search import embed, embed_many

    pos = np.array(embed(f"{market} sales are growing strongly with high demand"))
    neg = np.array(embed(f"{market} sales are declining with weak demand"))
    vecs = np.array(embed_many([e["text"] for e in evidence]))
    return float(np.mean(vecs @ pos - vecs @ neg))


# --------------------------------------------------------------------------- #
# 4. physical ceiling
# --------------------------------------------------------------------------- #
def ceiling_constants(llm: GenLLM, market: str, samples: int = 3) -> dict[str, Any]:
    """Items per household at saturation and replacement cycle (years): the
    median of `samples` LLM reads of the same evidence (self-consistency), so
    one noisy read can't move the ceiling. A sourced (quote-verified) value
    wins over estimates."""
    evidence = []
    for q in [f"average number of {market} per household United States", f"{market} per household statistics",
              f"average {market} replacement cycle years", f"how often do people replace their {market} years"]:
        evidence += _search(q, max_results=5)
    for url in list(dict.fromkeys(e["url"] for e in evidence))[:4]:
        evidence += [{"url": url, "text": f"[full page] {w}"[:1500]} for w in page_windows(url)]
    reads: dict[str, list[dict[str, Any]]] = {"items_per_household": [], "replacement_years": []}
    for k in range(samples):
        data = llm.complete_json(
            "You extract physical constants from web evidence. Prefer the snippets; if none states a value, "
            "give your best estimate and mark it as not from evidence.",
            f"Market: {market}\n1) Items per household in a SATURATED market (e.g. United States).\n"
            f"2) Average replacement cycle in years.\n\nSnippets:\n{_snippet_block(evidence)}\n\n"
            'Return JSON: {"items_per_household": {"value": <number>, "from_evidence": <bool>, "quote": "<verbatim>", '
            '"snippet_index": <int or null>}, "replacement_years": {"value": <number>, "from_evidence": <bool>, '
            '"quote": "<verbatim>", "snippet_index": <int or null>}}' + (f"\n(independent read #{k + 1})" if k else ""),
        )
        for key, lo, hi in (("items_per_household", 0.2, 6.0), ("replacement_years", 1.0, 20.0)):
            item = data.get(key) or {}
            try:
                value = float(item.get("value"))
            except (TypeError, ValueError):
                continue
            if lo <= value <= hi:
                verified = bool(item.get("from_evidence")) and _number_in_quote(value, str(item.get("quote") or ""))
                reads[key].append({"value": value, "verified_quote": verified, "quote": str(item.get("quote") or "")[:300],
                                   "url": _url_at(evidence, item.get("snippet_index"))})
    out: dict[str, Any] = {}
    for key, items in reads.items():
        if not items:
            raise RuntimeError(f"no usable '{key}' for the ceiling")
        pool = [i for i in items if i["verified_quote"]] or items
        med = statistics.median(i["value"] for i in pool)
        best = min(pool, key=lambda i: abs(i["value"] - med))
        out[key] = {**best, "value": med, "reads": [i["value"] for i in items]}
    return out


def margin_facts(llm: GenLLM, market: str) -> list[dict[str, Any]]:
    """Sourced price-chain facts (retail/distribution margins, retail vs
    import/wholesale prices) for the consumer-price markup -- quote-verified only."""
    evidence = []
    for q in [f"{market} retail gross margin percent", f"consumer electronics retailer gross margin {market}",
              f"{market} distribution margin wholesale to retail markup", f"{market} average retail price 2024 USD"]:
        evidence += _search(q, max_results=5)
    for url in list(dict.fromkeys(e["url"] for e in evidence))[:4]:
        evidence += [{"url": url, "text": f"[full page] {w}"[:1500]} for w in page_windows(url)]
    data = llm.complete_json(
        "You extract price-chain facts from web evidence. Use only the snippets; copy each sentence verbatim.",
        f"Market: {market}\nList every figure the snippets state about: retailer gross margin (%), distributor/"
        "wholesale margin (%), markup from import/factory price to shelf price (%), or average retail price "
        f"per unit (USD, with country).\n\nSnippets:\n{_snippet_block(evidence)}\n\n"
        'Return JSON: {"facts": [{"metric": "<retail_gross_margin_pct|distribution_margin_pct|import_to_retail_markup_pct|'
        'avg_retail_price_usd>", "value": <number>, "country": "<country or null>", "year": <int or null>, '
        '"product_scope": "<what it applies to>", "quote": "<verbatim>", "snippet_index": <int>}]}',
    )
    out = []
    for f in data.get("facts") or []:
        try:
            value = float(f.get("value"))
        except (TypeError, ValueError):
            continue
        if value > 0 and _number_in_quote(value, str(f.get("quote") or "")):
            out.append({"market": market, "country": f.get("country") or None, "metric": str(f.get("metric")),
                        "value": value, "unit": "USD" if "usd" in str(f.get("metric")) else "%",
                        "year": f.get("year"), "kind": "evidence",
                        "source": _url_at(evidence, f.get("snippet_index")) or "web",
                        "quote": str(f.get("quote"))[:300], "scope": str(f.get("product_scope") or "")[:120]})
    return out


# --------------------------------------------------------------------------- #
# 5. sigmoid model under the ceiling
# --------------------------------------------------------------------------- #
def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def predict_per_person(params: np.ndarray, z: np.ndarray, ceiling: np.ndarray) -> np.ndarray:
    """params = [b, w...]; ceiling = units/person/year at saturation, per row."""
    return ceiling * _sigmoid(z @ params[1:] + params[0])


def fit(z: np.ndarray, y_per_person: np.ndarray, ceiling: np.ndarray,
        weights: np.ndarray | None = None) -> np.ndarray:
    """[b, w...] by weighted soft-L1 least squares on log(units/person)."""
    sw = np.sqrt(weights) if weights is not None else np.ones(len(y_per_person))
    # A target above the ceiling can't be reached; cap it just below so it
    # pulls the country to saturation instead of distorting the weights.
    y = np.minimum(y_per_person, 0.99 * ceiling)

    def residuals(p: np.ndarray) -> np.ndarray:
        fit_res = sw * (np.log(predict_per_person(p, z, ceiling)) - np.log(y))
        return np.concatenate([fit_res, math.sqrt(RIDGE) * p[1:]])

    return least_squares(residuals, np.zeros(1 + z.shape[1]), loss="soft_l1", f_scale=0.5).x


def _standardize(x: np.ndarray, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    return np.nan_to_num((x - mean) / std, nan=0.0)


def _feature_row(hard: dict[str, float], qual: dict[str, float]) -> list[float]:
    gdp = hard.get("gdp_pc_ppp")
    row = {"log_gdp_pc_ppp": math.log(gdp) if gdp else np.nan,
           "urban_pct": hard.get("urban_pct", np.nan),
           "electricity_pct": hard.get("electricity_pct", np.nan),
           "internet_pct": hard.get("internet_pct", np.nan), **qual}
    return [float(row.get(f, np.nan)) for f in FEATURES]


# --------------------------------------------------------------------------- #
# 7-8. price + worldwide cross-check
# --------------------------------------------------------------------------- #
def global_price(llm: GenLLM, market: str, year: int) -> dict[str, Any]:
    """Median of the evidence-stated average prices; LLM estimate only as a fallback."""
    evidence = []
    for q in [f"global average selling price {market} {year - 1} USD", f"{market} ASP {year - 1} Omdia",
              f"{market} average selling price {year} TrendForce", f"worldwide {market} average price per unit {year - 1}"]:
        evidence += _search(q, max_results=5)
    data = llm.complete_json(
        "You extract prices from web evidence. List every worldwide AVERAGE price per unit (USD) the snippets "
        "state for all units sold (not a premium segment, not one model). Then give your own best estimate.",
        f"Market: {market}, years {year - 2}-{year}\n\nSnippets:\n{_snippet_block(evidence)}\n\n"
        'Return JSON: {"prices": [{"usd_per_unit": <number>, "year": <int>, "quote": "<verbatim>", '
        '"snippet_index": <int>}], "estimate_usd_per_unit": <number>}',
    )
    verified = []
    for p in data.get("prices") or []:
        try:
            v = float(p.get("usd_per_unit"))
        except (TypeError, ValueError):
            continue
        if v > 0 and _number_in_quote(v, str(p.get("quote") or "")):
            verified.append({"usd_per_unit": v, "year": p.get("year"), "quote": str(p.get("quote"))[:300],
                             "url": _url_at(evidence, p.get("snippet_index"))})
    if verified:
        return {"usd_per_unit": statistics.median(v["usd_per_unit"] for v in verified),
                "from_evidence": True, "points": verified}
    return {"usd_per_unit": float(data.get("estimate_usd_per_unit") or 0.0), "from_evidence": False, "points": []}


def worldwide_units(llm: GenLLM, market: str, year: int) -> dict[str, Any] | None:
    """Published worldwide annual unit total -- cross-check only."""
    evidence = []
    for q in [f"global {market} shipments {year - 1} million units", f"worldwide {market} shipments {year} forecast"]:
        evidence += _search(q, max_results=6)
    data = llm.complete_json(
        "You extract one figure from web evidence. Use only the snippets.",
        f"Worldwide annual {market} shipments or unit sales, {year - 2}-{year}, in MILLIONS of units.\n\n"
        f"Snippets:\n{_snippet_block(evidence)}\n\n"
        'Return JSON: {"units_million": <number or null>, "year": <int or null>, "quote": "<verbatim>", '
        '"snippet_index": <int or null>}',
    )
    try:
        value = float(data.get("units_million"))
    except (TypeError, ValueError):
        return None
    if value <= 0 or not _number_in_quote(value, str(data.get("quote") or "")):
        return None
    return {"units": value * 1e6, "year": data.get("year"), "quote": str(data.get("quote"))[:300],
            "url": _url_at(evidence, data.get("snippet_index"))}


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #
def run_signal_model(market: str, llm: GenLLM, n_countries: int = 40, base_year: int = 2025,
                     root: Path | None = None, say: Callable[[str], None] = print, workers: int = 4,
                     run_id: str = "run_1", exclude_features: tuple[str, ...] = (),
                     use_knowledge: bool = True, use_trade: bool = True, use_estimator: bool = True,
                     world_constraint: bool = False, vertical: str | None = None,
                     price_level: str = "auto") -> dict[str, Any]:
    """One model run. Shared across runs (cached once): country list, World
    Bank data, household sizes, web snippets, worldwide cross-check. Per run
    (`run_id`): everything the LLM judges -- scores, anchors, knowledge
    estimates, ceiling constants, price -- so repeat runs with an uncached LLM
    sample those judgments afresh. `exclude_features` / `use_knowledge`
    are for ablations: they change only the fit, never the inputs."""
    d = signal_dir(market, root)
    rd = d / run_id
    wb_names = _cached(d / "wb_countries.json", world_bank_countries)
    countries = _cached(d / "01_countries.json", lambda: top_countries(llm, market, n_countries, wb_names))
    say(f"[countries] {len(countries)} listed by the LLM ({sum(c['in_world_bank'] for c in countries)} in World Bank)")
    hard = _cached(d / "02_world_bank.json", lambda: world_bank_indicators(set(wb_names)))
    hh = _cached(d / "01_household_size.json", lambda: household_sizes(llm, countries))

    def gather(c: dict[str, Any]) -> dict[str, Any]:
        # Snippets: shared across runs. Scores: per run. Both cached per
        # country, so a crash or rerun only redoes what's missing.
        snip_path = d / "03_evidence" / f"{c['iso3']}.json"
        snip = checkpoints.read_json(snip_path) or {}
        if snip.get("version") != EVIDENCE_VERSION:
            done = snip.get("queries_done") or (country_queries(market, c["name"])[:2] if snip.get("evidence") else [])
            lq = snip.get("local_query") if "local_query" in snip else local_query(llm, market, c["name"])
            ev, done = country_evidence(market, c["name"], done=done, evidence=snip.get("evidence"),
                                        extra=[lq] if lq else [])
            # Read the full pages behind the top results: snippets usually cut the figure off.
            read = {e["url"] for e in ev if e["text"].startswith("[full page]")}
            urls = [u for u in dict.fromkeys(e["url"] for e in ev if not e["text"].startswith("[full page]")) if u not in read]
            for url in urls[:PAGES_PER_COUNTRY]:
                ev += [{"url": url, "text": f"[full page] {w}"[:1500]} for w in page_windows(url)]
            snip = {"iso3": c["iso3"], "evidence": ev, "queries_done": done, "version": EVIDENCE_VERSION,
                    "local_query": lq}
            checkpoints.write_json(snip_path, snip)
        ev = snip["evidence"]
        path = rd / "03_scores" / f"{c['iso3']}.json"
        cached = checkpoints.read_json(path) or {}
        if cached and not cached.get("error") and cached.get("evidence_version") == EVIDENCE_VERSION:
            return {"evidence": ev, **cached}
        out: dict[str, Any] = {}
        for attempt in range(3):
            try:
                out = score_country(llm, market, c["name"], ev)
                break
            except Exception as exc:  # noqa: BLE001 -- one country must not sink the run
                out = {"scores": {k: 0.0 for k in RUBRIC}, "score_evidence": {}, "anchor": None,
                       "anchor_rejected": "LLM call failed", "knowledge": None, "error": str(exc)}
                time.sleep(10 * (attempt + 1))
        out = {"iso3": c["iso3"], "evidence_version": EVIDENCE_VERSION, **out}
        checkpoints.write_json(path, out)
        return {"evidence": ev, **out}

    with ThreadPoolExecutor(workers) as pool:
        evidence = list(pool.map(gather, countries))
    if not embedding_available():
        say("[evidence] WARNING: no embedding backend (sentence-transformers / fastembed) -- embedding signal neutral (0)")
    for e in evidence:  # embedding signal: local model, kept out of the threads
        e["embedding_signal"] = embedding_signal(market, e["evidence"])
    checkpoints.write_json(rd / "03_evidence.json", evidence)
    by_iso = {e["iso3"]: e for e in evidence}
    failed = [e["iso3"] for e in evidence if e.get("error")]
    if failed:
        say(f"[evidence] WARNING: scoring failed for {failed} -- neutral scores, no anchor")
    ai_hits = sum(1 for e in evidence for s in e["evidence"] if s["url"].startswith("https://www.google.com/search?q="))
    say(f"[evidence] {sum(len(e['evidence']) for e in evidence)} snippets ({ai_hits} AI Overviews); "
        f"anchors accepted: {sum(1 for e in evidence if e['anchor'])} of {len(evidence)}")

    constants = _cached(d / "04_ceiling_consensus.json", lambda: ceiling_constants(llm, market))  # shared across runs
    items_hh, repl = constants["items_per_household"]["value"], constants["replacement_years"]["value"]
    say(f"[ceiling] {items_hh:g} per household at saturation ({'evidence' if constants['items_per_household']['verified_quote'] else 'estimate'}), "
        f"replaced every {repl:g} years ({'evidence' if constants['replacement_years']['verified_quote'] else 'estimate'})")

    def population(c: dict[str, Any]) -> float | None:
        pop = hard.get(c["iso3"], {}).get("population")
        if pop:
            return pop
        llm_pop = c.get("llm_population_million")
        return float(llm_pop) * 1e6 if llm_pop else None

    top = [c for c in countries if population(c)]
    x_top = np.array([_feature_row(hard.get(c["iso3"], {}),
                                   {**by_iso[c["iso3"]]["scores"], "embedding_signal": by_iso[c["iso3"]]["embedding_signal"]})
                      for c in top])
    mean, std = np.nanmean(x_top, axis=0), np.nanstd(x_top, axis=0)
    std[std == 0] = 1.0
    z_top = _standardize(x_top, mean, std)
    dropped = [FEATURES.index(f) for f in exclude_features]
    z_top[:, dropped] = 0.0  # a constant-zero column gets weight 0 under the ridge

    # Household size: LLM for the top countries; for the rest, a fit on log GDP per capita.
    gdp_top = x_top[:, 0]
    hh_known = [(g, hh[c["iso3"]]) for g, c in zip(gdp_top, top) if c["iso3"] in hh and not np.isnan(g)]
    slope, intercept = np.polyfit([g for g, _ in hh_known], [h for _, h in hh_known], 1)

    def hh_size(iso: str, log_gdp: float) -> float:
        if iso in hh:
            return hh[iso]
        return float(np.clip(intercept + slope * (log_gdp if not np.isnan(log_gdp) else np.nanmean(gdp_top)), 1.8, 7.0))

    ceil_top = np.array([items_hh / (hh_size(c["iso3"], g) * repl) for c, g in zip(top, gdp_top)])

    # Official trade data (shared across runs): HS code, then imports/exports per country.
    hs = _cached(d / "02_hs_code.json", lambda: hs_code(llm, market))
    if "specific_to_product" not in hs:  # cached before the specificity check existed
        hs = hs_code(llm, market)
        checkpoints.write_json(d / "02_hs_code.json", hs)
    reporters = _cached(d / "comtrade_reporters.json", comtrade_reporters)

    def flows_for(iso: str) -> dict[str, Any] | None:
        path = d / "02_trade" / f"{iso}.json"
        cached = checkpoints.read_json(path)
        if cached is not None:
            return cached.get("flows")
        flows = trade_flows(hs["hs6"], reporters[iso]) if iso in reporters else None
        checkpoints.write_json(path, {"flows": flows})
        return flows

    trade_ok = use_trade and hs["specific_to_product"]
    if use_trade and not hs["specific_to_product"]:
        say(f"[trade] HS {hs['hs6']} also covers {hs['also_covers'] or 'other products'} -- trade data can't "
            "isolate this market; not used")
    trade = {c["iso3"]: flows_for(c["iso3"]) for c in top} if trade_ok else {}
    trade_anchors = {iso: a for iso, f in trade.items() if (a := trade_anchor(f))}

    # Mass balance for producer countries (exports too large for net imports): production + imports - exports.
    def production_for(i: int, c: dict[str, Any]) -> dict[str, Any]:
        path = d / "02c_production" / f"{c['iso3']}.json"
        cached = checkpoints.read_json(path)
        if cached is not None:
            return cached
        try:
            out = production_anchor(llm, market, c["name"], trade.get(c["iso3"]),
                                    ceiling_units=float(ceil_top[i]) * population(c))
        except Exception as exc:  # noqa: BLE001
            out = {"anchor": None, "rejected": f"failed: {exc}"}
        checkpoints.write_json(path, out)
        return out

    producers = [(i, c) for i, c in enumerate(top) if trade.get(c["iso3"]) and c["iso3"] not in trade_anchors]
    with ThreadPoolExecutor(workers) as pool:
        production = dict(zip([c["iso3"] for _, c in producers], pool.map(lambda ic: production_for(*ic), producers)))
    mass_anchors = {iso: p["anchor"] for iso, p in production.items() if p.get("anchor")}
    if producers:
        say(f"[mass balance] {len(mass_anchors)} of {len(producers)} producer countries anchored "
            f"(production + imports - exports): {', '.join(name for iso, name in ((c['iso3'], c['name']) for _, c in producers) if iso in mass_anchors) or '-'}")
    if trade_ok:
        say(f"[trade] HS {hs['hs6']} ({hs['description'][:60]}): flows for {sum(1 for f in trade.values() if f)} of "
            f"{len(top)} countries; {len(trade_anchors)} usable as consumption (exports < "
            f"{TRADE_MAX_EXPORT_RATIO:.0%} of imports)")

    def hard_anchor(iso: str) -> tuple[dict[str, Any], str] | None:
        if iso in trade_anchors:
            return trade_anchors[iso], "trade"
        if iso in mass_anchors:
            return mass_anchors[iso], "mass_balance"
        if by_iso[iso]["anchor"]:
            return by_iso[iso]["anchor"], "evidence"
        return None

    # Facts library: record this market's hard facts, so the estimator (here and
    # in other markets of the same vertical) can reason from them.
    name_of = {c["iso3"]: c["name"] for c in top}
    gdp_by_name = {c["name"]: hard.get(c["iso3"], {}).get("gdp_pc_ppp") for c in top}
    if vertical:
        new_facts = []
        for iso in name_of:
            ha = hard_anchor(iso)
            if ha:
                pop = population(next(c for c in top if c["iso3"] == iso))
                new_facts.append({"market": market, "country": name_of[iso], "metric": "annual_units_consumed",
                                  "value": ha[0]["units"], "unit": "units", "year": ha[0].get("year"), "kind": ha[1],
                                  "source": ha[0].get("url") or ha[1], "quote": ha[0].get("quote", "")})
                new_facts.append({"market": market, "country": name_of[iso], "metric": "annual_units_per_person",
                                  "value": ha[0]["units"] / pop, "unit": "units/person/yr", "year": ha[0].get("year"),
                                  "kind": f"derived ({ha[1]} / World Bank population)", "source": ha[0].get("url") or ha[1]})
            f = trade.get(iso)
            if f and f["imports_units"] >= TRADE_MIN_PRICE_UNITS and f["imports_usd"] > 0:
                new_facts.append({"market": market, "country": name_of[iso], "metric": "import_unit_value_usd",
                                  "value": f["imports_usd"] / f["imports_units"], "unit": "USD/unit", "year": f["year"],
                                  "kind": "trade", "source": f["url"]})
        for key, unit in (("items_per_household", "units/household"), ("replacement_years", "years")):
            if constants[key]["verified_quote"]:
                new_facts.append({"market": market, "country": None, "metric": key, "value": constants[key]["value"],
                                  "unit": unit, "year": None, "kind": "evidence", "source": constants[key]["url"] or "web",
                                  "quote": constants[key]["quote"]})
        world_fact = _cached(d / "06_worldwide_units.json", lambda: worldwide_units(llm, market, base_year))
        if world_fact:
            new_facts.append({"market": market, "country": None, "metric": "annual_units_consumed",
                              "value": world_fact["units"], "unit": "units", "year": world_fact.get("year"),
                              "kind": "evidence", "source": world_fact.get("url") or "web", "quote": world_fact.get("quote", "")})
        new_facts += _cached(d / "06b_margin_facts.json", lambda: margin_facts(llm, market))
        estimator.add_facts(vertical, new_facts)
        library = estimator.load_facts(vertical)
        say(f"[facts] {len(new_facts)} facts recorded; '{vertical}' library holds {len(library)} facts "
            f"across {len({f['market'] for f in library})} market(s)")
    else:
        library = []

    # Estimator agent: a fact-grounded estimate for every country without a hard anchor.
    def estimate_for(c: dict[str, Any]) -> dict[str, Any] | None:
        path = rd / "04b_estimates" / f"{c['iso3']}.json"
        cached = checkpoints.read_json(path)
        if cached is not None:
            return cached.get("estimate")
        h = hard.get(c["iso3"], {})
        context = {"population": population(c), "gdp_pc_ppp": h.get("gdp_pc_ppp"), "urban_pct": h.get("urban_pct"),
                   "electricity_pct": h.get("electricity_pct"), "household_size": hh.get(c["iso3"]),
                   "trade": trade.get(c["iso3"]), "evidence_scores": by_iso[c["iso3"]]["scores"]}
        try:
            est = estimator.estimate_consensus(llm, library, market=market, country=c["name"], metric="annual_units_consumed",
                                     unit="units per year", context=context, gdp_of=gdp_by_name)
        except Exception as exc:  # noqa: BLE001
            est = None
            say(f"[estimator] {c['name']}: failed ({exc})")
        checkpoints.write_json(path, {"estimate": est})
        return est

    estimates: dict[str, dict[str, Any]] = {}
    if use_estimator and library:
        todo = [c for c in top if not hard_anchor(c["iso3"])]
        with ThreadPoolExecutor(workers) as pool:
            for c, est in zip(todo, pool.map(estimate_for, todo)):
                if est:
                    estimates[c["iso3"]] = est
        say(f"[estimator] fact-based estimates for {len(estimates)} of {len(todo)} countries without a hard anchor "
            f"(median {statistics.median([len(e['fact_ids']) for e in estimates.values()]) if estimates else 0:.0f} facts each)")

    # Physical cap per country (population x saturation ceiling): the model can't
    # exceed it, and neither may a soft estimate (seen: Argentina at 526M
    # smartphones, 11.5 per person, from an estimate with a scale error).
    cap_of = {c["iso3"]: float(ceil_top[i]) * population(c) for i, c in enumerate(top)}

    hard_pc = {c["iso3"]: ha[0]["units"] / population(c) for c in top if (ha := hard_anchor(c["iso3"]))}
    log_gdp = {c["iso3"]: g for c, g in zip(top, gdp_top) if not np.isnan(g)}

    def peer_ratio(iso: str, per_person: float) -> float | None:
        """This country's per-person rate / median of the 5 income-nearest other hard anchors."""
        if iso not in log_gdp:
            return None
        peers = sorted((p for p in hard_pc if p != iso and p in log_gdp), key=lambda p: abs(log_gdp[p] - log_gdp[iso]))[:5]
        if len(peers) < 3:
            return None
        return per_person / statistics.median(hard_pc[p] for p in peers)

    def soft_point(iso: str) -> tuple[float, float, str] | None:
        """(units, confidence, kind) for a country without a hard anchor; None when implausible."""
        if iso in estimates and 0 < estimates[iso]["value"] <= cap_of[iso]:
            return estimates[iso]["value"], estimates[iso]["confidence"], "estimator"
        k = by_iso[iso].get("knowledge")
        if use_knowledge and k and k["confidence"] > 0 and 0 < k["units"] <= cap_of[iso]:
            return k["units"], k["confidence"], "knowledge"
        return None

    # Calibration points: trade / web evidence (weight 1), else estimator or LLM knowledge (0.5 x confidence).
    cal: list[tuple[int, float, float, str]] = []
    for i, c in enumerate(top):
        ha, sp = hard_anchor(c["iso3"]), soft_point(c["iso3"])
        if ha:
            cal.append((i, ha[0]["units"], MASS_BALANCE_WEIGHT if ha[1] == "mass_balance" else 1.0, ha[1]))
        elif sp and sp[1] > 0:
            cal.append((i, sp[0], KNOWLEDGE_WEIGHT * sp[1], sp[2]))
    if len(cal) < 5:
        raise RuntimeError(f"only {len(cal)} calibration points -- too few to fit the sigmoid")
    idx = [i for i, *_ in cal]
    y = np.array([u / population(top[i]) for i, u, *_ in cal])
    wts = np.array([w for *_, w, _ in cal])
    params = fit(z_top[idx], y, ceil_top[idx], wts)
    kinds = [kind for *_, kind in cal]
    say(f"[fit] {len(cal)} calibration points ({kinds.count('trade')} trade, {kinds.count('mass_balance')} mass balance, "
        f"{kinds.count('evidence')} web evidence, "
        f"{kinds.count('estimator')} estimator, {kinds.count('knowledge')} LLM knowledge)")

    loo: dict[str, float] = {}
    for k, i in enumerate(idx):
        keep = [j for j in range(len(idx)) if j != k]
        p_k = fit(z_top[[idx[j] for j in keep]], y[keep], ceil_top[[idx[j] for j in keep]], wts[keep])
        loo[top[i]["iso3"]] = float(predict_per_person(p_k, z_top[[i]], ceil_top[[i]])[0] * population(top[i]))

    model_units = predict_per_person(params, z_top, ceil_top) * np.array([population(c) for c in top])
    rows = []
    for i, c in enumerate(top):
        e, ha, sp = by_iso[c["iso3"]], hard_anchor(c["iso3"]), soft_point(c["iso3"])
        anchor_units = ha[0]["units"] if ha else None
        pred = loo.get(c["iso3"], float(model_units[i]))
        if anchor_units is not None and ha[1] in ("trade", "mass_balance"):
            # Official statistics: judged against income peers' hard anchors, not
            # the model -- a weak model must not overrule customs data (seen: US
            # smartphones 118.9M from Comtrade vs a 12.5M model prediction) --
            # plus the physical ceiling with slack (the ceiling is approximate).
            peer = peer_ratio(c["iso3"], anchor_units / population(c))
            trusted = anchor_units <= OFFICIAL_CAP_SLACK * cap_of[c["iso3"]] and (
                peer is None or 1 / ANCHOR_TRUST_RATIO <= peer <= ANCHOR_TRUST_RATIO)
            off = (f" (official anchor rejected: {peer:.1f}x income peers per person)" if peer and not trusted
                   else " (official anchor above physical ceiling)" if not trusted else "")
        else:
            trusted = anchor_units is not None and 1 / ANCHOR_TRUST_RATIO <= anchor_units / pred <= ANCHOR_TRUST_RATIO
            off = " (anchor off by >3x)" if anchor_units and not trusted else ""
        if trusted:
            units, basis = anchor_units, f"{ha[1]} anchor"
        elif sp and sp[1] > 0:
            # Blend model and the soft estimate in log space, weighted by its confidence.
            ws = KNOWLEDGE_WEIGHT * sp[1]
            units = math.exp((ws * math.log(sp[0]) + MODEL_BLEND_WEIGHT * math.log(model_units[i]))
                             / (ws + MODEL_BLEND_WEIGHT))
            basis = f"model + {sp[2]} blend{off}"
        else:
            units, basis = float(model_units[i]), f"model{off}"
        rows.append({
            "country": c["name"], "iso3": c["iso3"], "population": population(c),
            "household_size": hh_size(c["iso3"], gdp_top[i]), "ceiling_per_person": float(ceil_top[i]),
            "features": dict(zip(FEATURES, x_top[i].round(3).tolist())),
            "model_units": float(model_units[i]), "loo_units": loo.get(c["iso3"]),
            "anchor_units": anchor_units, "anchor_kind": ha[1] if ha else None,
            "anchor_rejected": e["anchor_rejected"],
            "anchor_source": (ha[0] if ha else {}).get("url"), "anchor_quote": (ha[0] if ha else {}).get("quote"),
            "trade": trade.get(c["iso3"]),
            "estimate": estimates.get(c["iso3"]),
            "knowledge_units": (e.get("knowledge") or {}).get("units"),
            "knowledge_confidence": (e.get("knowledge") or {}).get("confidence"),
            "units": units, "basis": basis,
        })

    # Rest of World: every other World Bank country, hard data only (qualitative = neutral).
    top_iso = {c["iso3"] for c in top}
    rest = [(iso, h) for iso, h in hard.items() if iso not in top_iso and h.get("population")]
    x_rest = np.array([_feature_row(h, {k: 0.0 for k in QUAL_FEATURES}) for _, h in rest])
    z_rest = _standardize(x_rest, mean, std)
    z_rest[:, len(HARD_FEATURES):] = 0.0
    z_rest[:, dropped] = 0.0
    ceil_rest = np.array([items_hh / (hh_size(iso, x[0]) * repl) for (iso, _), x in zip(rest, x_rest)])
    rest_units_each = predict_per_person(params, z_rest, ceil_rest) * np.array([h["population"] for _, h in rest])

    # Optional soft constraint: scale the MODELLED countries (never the anchors)
    # toward the published worldwide unit total, at most +-50%.
    world = _cached(d / "06_worldwide_units.json", lambda: worldwide_units(llm, market, base_year))
    world_scale = 1.0
    if world_constraint and world:
        anchored = sum(r["units"] for r in rows if r["basis"].endswith("anchor"))
        modelled = sum(r["units"] for r in rows if not r["basis"].endswith("anchor")) + float(rest_units_each.sum())
        if modelled > 0:
            world_scale = float(np.clip((world["units"] - anchored) / modelled, 2 / 3, 1.5))
            for r in rows:
                if not r["basis"].endswith("anchor"):
                    r["units"] *= world_scale
            rest_units_each = rest_units_each * world_scale
            say(f"[world] modelled countries scaled x{world_scale:.2f} toward the published worldwide total")
    rest_units = float(np.sum(rest_units_each))

    # Price per country: customs import unit value vs income, fitted on countries that IMPORT most
    # of what they consume (elsewhere import values are kits/parts for local assembly).
    units_of = {r["iso3"]: r["units"] for r in rows}

    def price_point(c: dict[str, Any], strict: bool) -> tuple[float, float, float] | None:
        f, gdp = trade.get(c["iso3"]), hard.get(c["iso3"], {}).get("gdp_pc_ppp")
        if not (f and gdp and f["imports_units"] >= TRADE_MIN_PRICE_UNITS and f["imports_usd"] > 0):
            return None
        if strict and (f["imports_units"] < 0.5 * units_of[c["iso3"]]
                       or f["exports_units"] > TRADE_MAX_EXPORT_RATIO * f["imports_units"]):
            return None
        return gdp, f["imports_usd"] / f["imports_units"], f["imports_units"]

    price_points = [p for c in top if (p := price_point(c, strict=True))]
    curve_scope = "import-dependent countries"
    if len(price_points) < 5:
        price_points, curve_scope = [p for c in top if (p := price_point(c, strict=False))], "all importing countries"
    # Pricing agent: robust landed curve (+ bootstrap CI), then the factor to the market's price level.
    global_fallback, curve, level = None, None, None
    gdp_iso = {c["iso3"]: hard.get(c["iso3"], {}).get("gdp_pc_ppp") for c in top}
    if len(price_points) >= 5:
        curve = pricing_agent.study_curve(price_points)
        median_gdp = statistics.median(g for g, _, _ in price_points)

        def landed_of(gdp: float | None) -> float:
            return math.exp(curve["a"]) * (gdp or median_gdp) ** curve["elasticity"]

        landed_units = [(r["units"], landed_of(gdp_iso.get(r["iso3"]))) for r in rows]
        landed_units += [(u, landed_of(h.get("gdp_pc_ppp"))) for u, (_, h) in zip(rest_units_each, rest)]
        landed_avg = sum(u * p for u, p in landed_units) / sum(u for u, _ in landed_units)
        study = _cached(d / "05_pricing_study.json", lambda: pricing_agent.gather_price_facts(
            llm, market, base_year, search=lambda q: _search(q, max_results=5), pages=page_windows,
            verify=_number_in_quote))
        if vertical:
            estimator.add_facts(vertical, [
                {"market": market, "country": None, "metric": "vendor_asp_usd", "value": v["usd"], "unit": "USD/unit",
                 "year": None, "kind": v["kind"], "source": v["url"] or v["source"], "quote": v["quote"]}
                for v in study["vendor_asp"]] + [
                {"market": market, "country": r["country"], "metric": "avg_retail_price_usd", "value": r["usd"],
                 "unit": "USD/unit", "year": r.get("year"), "kind": "evidence", "source": r["url"] or "web",
                 "quote": r["quote"]} for r in study["retail"]])
        name_gdp = {name_of[iso].lower(): g for iso, g in gdp_iso.items()}
        level = pricing_agent.level_factor(study, curve, landed_avg,
                                           gdp_of_country=lambda name: name_gdp.get(str(name).lower()))
        level["definition"] = study["definition"]
        factor = level["factor"] if price_level == "auto" else 1.0

        def price_of(gdp: float | None) -> float:
            return factor * landed_of(gdp)

        price_basis = (f"pricing agent: landed curve on {curve['n_points']} {curve_scope} (elasticity "
                       f"{curve['elasticity']:+.2f}, 90% CI {curve['elasticity_90ci'][0]:+.2f}..{curve['elasticity_90ci'][1]:+.2f})"
                       + (f" x {factor:.2f} to the {level['chosen_level']} price level ({level['basis']})"
                          if price_level == "auto" else " at the landed level"))
        say(f"[pricing] {price_basis}")
    else:
        global_fallback = _cached(rd / "05_price.json", lambda: global_price(llm, market, base_year))

        def landed_of(gdp: float | None) -> float:
            return global_fallback["usd_per_unit"]

        price_of = landed_of
        price_basis = "global price (" + ("evidence" if global_fallback["from_evidence"] else "LLM estimate") + ")"
    for r in rows:
        r["price_usd"] = price_of(gdp_iso.get(r["iso3"]))
        r["value_usd_mn"] = r["units"] * r["price_usd"] / 1e6
    rest_value = float(sum(u * price_of(h.get("gdp_pc_ppp")) for u, (_, h) in zip(rest_units_each, rest))) / 1e6
    landed_value = (sum(r["units"] * landed_of(gdp_iso.get(r["iso3"])) for r in rows)
                    + sum(u * landed_of(h.get("gdp_pc_ppp")) for u, (_, h) in zip(rest_units_each, rest))) / 1e6

    total_units = sum(r["units"] for r in rows) + rest_units
    total_value = sum(r["value_usd_mn"] for r in rows) + rest_value
    avg_price = total_value * 1e6 / total_units

    loo_err = [abs(loo[top[i]["iso3"]] - u) / u for i, u, *_ in cal]
    loo_hard = [abs(loo[top[i]["iso3"]] - u) / u for i, u, _, kind in cal if kind in ("trade", "evidence")]
    result = {
        "market": market, "base_year": base_year, "version": 5, "run_id": run_id, "vertical": vertical,
        "exclude_features": list(exclude_features), "use_knowledge": use_knowledge, "use_trade": use_trade,
        "use_estimator": use_estimator, "world_constraint": world_constraint, "world_scale": world_scale,
        "method": "2A signal model v5 (sigmoid under a physical ceiling; trade, mass-balance and web anchors; "
                  "estimator agent (3-sample consensus) over a domain facts library; pricing agent: robust landed "
                  "curve + evidence-based price level)",
        "hs_code": hs, "ceiling": constants,
        "params": {"b": float(params[0]), "weights": dict(zip(FEATURES, params[1:].round(4).tolist()))},
        "n_trade_anchors": kinds.count("trade"), "n_mass_balance_anchors": kinds.count("mass_balance"),
        "n_anchors": kinds.count("evidence"),
        "mass_balance": production,
        "n_estimator_points": kinds.count("estimator"), "n_knowledge_points": kinds.count("knowledge"),
        "embedding_signal_used": embedding_available(),
        "loo_median_abs_pct_error": round(100 * float(np.median(loo_err)), 1),
        "loo_median_abs_pct_error_hard_anchors": round(100 * float(np.median(loo_hard)), 1) if loo_hard else None,
        "price": {"basis": price_basis, "avg_usd_per_unit": avg_price, "price_level": price_level,
                  "points": [{"gdp_pc_ppp": g, "unit_value": p, "import_units": u} for g, p, u in price_points],
                  "curve": curve, "level": level, "global_fallback": global_fallback},
        "countries": sorted(rows, key=lambda r: -r["units"]),
        "rest_of_world": {"n_countries": len(rest), "units": rest_units, "value_usd_mn": rest_value,
                          "countries": [{"iso3": iso, "units": float(u), "price_usd": price_of(h.get("gdp_pc_ppp")),
                                         "value_usd_mn": float(u) * price_of(h.get("gdp_pc_ppp")) / 1e6}
                                        for u, (iso, h) in zip(rest_units_each, rest)]},
        "global_units": total_units,
        "global_usd_mn": total_value,
        "global_usd_mn_landed": landed_value,
        "worldwide_units_crosscheck": world | {"gap_pct": round(100 * (total_units - world["units"]) / world["units"], 1)}
        if world else None,
    }
    if not exclude_features and use_knowledge and use_trade and use_estimator and not world_constraint and price_level == "auto":
        checkpoints.write_json(rd / "07_result.json", result)
    return result

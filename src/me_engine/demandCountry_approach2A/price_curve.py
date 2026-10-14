"""Per-country price for mass-measured markets (`src/newinfo/system.md` §10).

1. Value basis -- one per run (config `pricing.value_basis`, default
   manufacturer). Customs unit values are border prices (CIF imports / FOB
   exports), the closest published proxy for a manufacturer / first-sale
   price of a bulk commodity; no markup chain is applied.
2. Observed price per country -- UN Comtrade unit value (USD / net weight)
   over the HS headings that cover the market's scope:
     import-dependent country (imports >= exports): import unit value (CIF)
     producer / exporter:                          export unit value (FOB)
   Only flows of at least `pricing.min_import_tonnes` count. Label
   `verified_observed`, tier 3 (official customs data).
3. Price curve -- ln(P) = a + b * ln(GDP per capita PPP), fitted on the
   import unit values after removing outliers (> 3x IQR). Countries without an
   observed price use the curve (`derived`). Rest of World uses the median of
   the observed prices (`derived`).
4. Unit traps -- only runs when the run's final demand unit is a mass unit
   (tonnes / kg); anything else keeps the sizing engine's price.

Everything is cached under `<run dir>/price_curve/`, so a redo pass never
re-fetches.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
import time
from pathlib import Path
from typing import Any, Callable

import httpx
import numpy as np

from me_engine.generation.llm import GenLLM

from .ledger import geo_code

_COMTRADE = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
_WB = "https://api.worldbank.org/v2"
_MASS_TO_TONNES = {"tonnes": 1.0, "tonne": 1.0, "t": 1.0, "metric tons": 1.0, "metric tonnes": 1.0, "tons": 1.0,
                   "kg": 1e-3, "kilograms": 1e-3, "kilogram": 1e-3}
_REST = re.compile(r"^rest of (the )?world$", re.IGNORECASE)


def tonnes_factor(unit: str) -> float | None:
    """Multiplier from `unit` to tonnes, or None when it isn't a mass unit."""
    return _MASS_TO_TONNES.get(str(unit or "").strip().lower())


def _cached(path: Path, build: Callable[[], Any]) -> Any:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    data = build()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data


def hs_headings(llm: GenLLM, market: str, scope: str) -> dict[str, Any]:
    data = llm.complete_json(
        "You know the Harmonized System (HS) tariff nomenclature. Return json.",
        f"Market: {market}\nScope: {scope}\n\nList the HS headings (4-digit, or 6-digit only when the 4-digit "
        "heading also covers other products in large volume) whose goods are exactly the finished product in "
        "this scope -- include every grade the scope includes, exclude raw inputs and by-products the scope "
        "excludes.\n"
        'Return JSON: {"codes": ["<4 or 6 digits>"], "descriptions": ["..."], "excluded": "<what the codes leave out>"}',
    )
    codes = [c for c in (re.sub(r"\D", "", str(x)) for x in data.get("codes") or []) if len(c) in (4, 6)]
    if not codes:
        raise RuntimeError(f"no HS heading for {market}: {data}")
    return {"codes": sorted(set(codes)), "descriptions": [str(d) for d in data.get("descriptions") or []],
            "excluded": str(data.get("excluded") or "")}


def collapse_headings(codes: list[str]) -> list[str]:
    """Use the 4-digit heading where the 6-digit codes span it (two or more
    subheadings, or an 'xx00' code that is the whole heading). Subheadings
    change between HS editions (1509.10 -> 1509.20/30/40 in HS 2022), so a
    reporter on a newer edition would otherwise be missed."""
    by_heading: dict[str, list[str]] = {}
    for c in codes:
        by_heading.setdefault(c[:4], []).append(c)
    out = []
    for heading, subs in by_heading.items():
        if len(subs) >= 2 or any(len(s) == 4 or s.endswith("00") for s in subs):
            out.append(heading)
        else:
            out.extend(subs)
    return sorted(set(out))


def comtrade_reporters() -> dict[str, int]:
    data = httpx.get("https://comtradeapi.un.org/files/v1/app/reference/Reporters.json", timeout=60).json()["results"]
    return {r["reporterCodeIsoAlpha3"]: int(r["reporterCode"]) for r in data
            if r.get("reporterCodeIsoAlpha3") and not r.get("entryExpiredDate") and not r.get("isGroup")}


def trade(codes: list[str], reporter: int, years: list[int]) -> dict[str, Any] | None:
    """Imports/exports summed over `codes` for the latest year with data:
    {year, import_t, import_usd, export_t, export_usd, url}."""
    for year in years:
        params = {"reporterCode": reporter, "period": year, "cmdCode": ",".join(codes), "flowCode": "M,X",
                  "partnerCode": 0}
        resp = None
        for attempt in range(4):
            try:
                resp = httpx.get(_COMTRADE, params=params, timeout=60)
            except httpx.HTTPError:
                time.sleep(3 * (attempt + 1))
                continue
            if resp.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            break
        if resp is None or resp.status_code != 200:
            continue
        rows = (resp.json() or {}).get("data") or []
        time.sleep(1.2)
        out = {"year": year, "import_t": 0.0, "import_usd": 0.0, "export_t": 0.0, "export_usd": 0.0,
               "url": str(resp.request.url)}
        for r in rows:
            kg = float(r.get("netWgt") or 0.0)
            usd = float(r.get("primaryValue") or 0.0)
            side = "import" if r.get("flowCode") == "M" else "export" if r.get("flowCode") == "X" else None
            if side and kg > 0:
                out[f"{side}_t"] += kg / 1000.0
                out[f"{side}_usd"] += usd
        if out["import_t"] > 0 or out["export_t"] > 0:
            return out
    return None


def wb_latest(iso3s: list[str], indicator: str) -> dict[str, dict[str, Any]]:
    """{ISO3: {value, year}} -- latest non-null value 2015-2024."""
    if not iso3s:
        return {}
    url = f"{_WB}/country/{';'.join(iso3s)}/indicator/{indicator}"
    rows = httpx.get(url, params={"format": "json", "per_page": 2000, "date": "2015:2024"}, timeout=60).json()
    out: dict[str, dict[str, Any]] = {}
    for r in (rows[1] if isinstance(rows, list) and len(rows) > 1 and rows[1] else []):
        iso, val = r.get("countryiso3code"), r.get("value")
        if val is None:
            continue
        if iso not in out or int(r["date"]) > out[iso]["year"]:
            out[iso] = {"value": float(val), "year": int(r["date"]),
                        "url": f"https://data.worldbank.org/indicator/{indicator}?locations={iso}"}
    return out


def drop_outliers(values: list[float], iqr_mult: float = 3.0) -> tuple[list[int], list[int]]:
    """(kept indices, dropped indices) -- drop values > iqr_mult x IQR from the median."""
    if len(values) < 4:
        return list(range(len(values))), []
    q1, med, q3 = statistics.quantiles(values, n=4, method="inclusive")
    iqr = q3 - q1
    keep, drop = [], []
    for i, v in enumerate(values):
        (drop if iqr > 0 and abs(v - med) > iqr_mult * iqr else keep).append(i)
    return keep, drop


def fit_curve(points: list[tuple[float, float]]) -> dict[str, float]:
    """ln(P) = a + b ln(GDP pc); points = [(gdp_pc, price)]."""
    x = np.array([math.log(g) for g, _ in points])
    y = np.array([math.log(p) for _, p in points])
    b, a = np.polyfit(x, y, 1)
    resid = y - (a + b * x)
    return {"a": float(a), "b": float(b), "n": len(points),
            "rmse_log": float(math.sqrt(float(np.mean(resid ** 2)))) if len(points) > 2 else 0.0}


def country_prices(llm: GenLLM, market: str, scope: str, countries: list[str], base_year: int, cache_dir: Path,
                   cfg: dict[str, Any], say: Callable[[str], None] = print) -> dict[str, Any] | None:
    """Per-country price table in USD per tonne, or None when it can't be built."""
    pc = cfg["pricing"]
    iqr_mult = cfg["checks"]["price_outlier_iqr"]
    try:
        scope_key = hashlib.sha256(f"{market}|{scope}".encode()).hexdigest()[:10]  # scope decides pomace in/out
        hs = _cached(cache_dir / f"hs_headings_{scope_key}.json", lambda: hs_headings(llm, market, scope))
        hs = {**hs, "llm_codes": hs.get("llm_codes", hs["codes"]), "codes": collapse_headings(hs["codes"])}
        reporters = _cached(cache_dir.parent.parent / "_comtrade_reporters.json", comtrade_reporters)
    except Exception as exc:  # noqa: BLE001 -- no price curve; the engine price stands
        say(f"[pricing] price curve skipped: {exc}")
        return None
    listed = [c for c in countries if not _REST.match(c.strip())]
    iso = {c: geo_code(c) for c in listed}
    # Base year first: a price from another year is a year mismatch (system.md §10 unit traps) --
    # e.g. olive oil customs values fell ~35% from 2024 to 2025.
    years = [base_year, base_year - 1, base_year - 2]
    flows: dict[str, Any] = {}
    for c in listed:
        rep = reporters.get(iso[c])
        if rep is None:
            continue
        flows[c] = _cached(cache_dir / f"comtrade_{iso[c]}_{'-'.join(hs['codes'])}_{years[0]}.json",
                           lambda rep=rep: trade(hs["codes"], rep, years))
    gdp = _cached(cache_dir / "wb_gdp_pc_ppp.json", lambda: wb_latest(sorted(set(iso.values())), "NY.GDP.PCAP.PP.CD"))
    pop = _cached(cache_dir / "wb_population.json", lambda: wb_latest(sorted(set(iso.values())), "SP.POP.TOTL"))

    observed: dict[str, dict[str, Any]] = {}
    curve_pts: list[tuple[str, float, float]] = []
    for c, f in flows.items():
        if not f:
            continue
        imp_ok = f["import_t"] >= pc["min_import_tonnes"] and f["import_usd"] > 0
        exp_ok = f["export_t"] >= pc["min_import_tonnes"] and f["export_usd"] > 0
        if imp_ok and f["import_t"] >= f["export_t"]:
            p, side, t, usd = f["import_usd"] / f["import_t"], "import (CIF)", f["import_t"], f["import_usd"]
        elif exp_ok:
            p, side, t, usd = f["export_usd"] / f["export_t"], "export (FOB)", f["export_t"], f["export_usd"]
        else:
            continue
        observed[c] = {"usd_per_t": p, "side": side, "tonnes": t, "usd": usd, "year": f["year"], "url": f["url"]}
        g = (gdp.get(iso[c]) or {}).get("value")
        if imp_ok and g:
            curve_pts.append((c, g, f["import_usd"] / f["import_t"]))

    keep, drop = drop_outliers([p for _, _, p in curve_pts], iqr_mult)
    dropped = [curve_pts[i][0] for i in drop]
    for c in dropped:  # an outlier unit value is not trusted as the country's own price either
        observed.pop(c, None)
    pts = [curve_pts[i] for i in keep]
    same_year = [p for p in pts if (observed.get(p[0]) or {}).get("year") == base_year]
    if len(same_year) >= pc["curve_min_points"]:
        pts = same_year  # don't mix price years in one curve
    curve = fit_curve([(g, p) for _, g, p in pts]) if len(pts) >= pc["curve_min_points"] else None

    prices: dict[str, dict[str, Any]] = {}
    codes = ", ".join(hs["codes"])
    for c in countries:
        if c in observed:
            o = observed[c]
            prices[c] = {
                "usd_per_t": o["usd_per_t"], "label": "verified_observed", "tier": 3, "method": f"customs_{o['side'][:6]}",
                "year": o["year"], "source_url": o["url"], "publisher": "UN Comtrade",
                "quote": f"UN Comtrade {o['year']}, HS {codes}, {c} {o['side']}: {o['tonnes']:,.0f} t, "
                         f"US${o['usd']:,.0f} -> {o['usd_per_t']:,.0f} USD/t",
                "inputs": {"tonnes": o["tonnes"], "usd": o["usd"]},
            }
        elif not _REST.match(c.strip()) and curve and (gdp.get(iso.get(c, "")) or {}).get("value"):
            g = gdp[iso[c]]["value"]
            prices[c] = {"usd_per_t": math.exp(curve["a"]) * g ** curve["b"], "label": "derived", "tier": None,
                         "method": "curve", "year": gdp[iso[c]]["year"], "gdp_pc_ppp": g,
                         "quote": f"price curve ln P = {curve['a']:.3f} + {curve['b']:.3f} ln GDPpc at GDPpc {g:,.0f}"}
    known = [p["usd_per_t"] for p in prices.values() if p["method"].startswith("customs")]
    if not known:
        say("[pricing] price curve skipped: no customs unit values for the listed countries")
        return None
    med = statistics.median(known)
    for c in countries:
        if c not in prices:
            prices[c] = {"usd_per_t": med, "label": "derived", "tier": None, "method": "median_observed",
                         "year": None, "quote": f"median of {len(known)} observed customs unit values"}
    say(f"[pricing] HS {codes}: {len(observed)} observed customs prices, curve on {len(pts)} importers"
        + (f" (b={curve['b']:+.2f})" if curve else " (too few points, no curve)")
        + (f", outliers dropped: {', '.join(dropped)}" if dropped else "")
        + f", median {med:,.0f} USD/t")
    off_year = {c: p["year"] for c, p in prices.items() if p["method"].startswith("customs") and p["year"] != base_year}
    if off_year:
        say(f"[pricing] price year != base year {base_year}: " + ", ".join(f"{c} {y}" for c, y in off_year.items()))
    return {"basis": pc["value_basis"], "basis_note": "customs unit value (border price) as the manufacturer / "
            "first-sale price proxy; no markup chain", "hs": hs, "curve": curve, "outliers_dropped": dropped,
            "base_year": base_year, "off_year_prices": off_year,
            "prices": prices, "gdp_pc_ppp": gdp, "population": pop, "iso3": iso}


def apply_country_prices(state: dict[str, Any], table: dict[str, Any]) -> dict[str, Any]:
    """Recompute value = quantity x country price for the single 2A bucket.
    Pure arithmetic on the sizing state (`quantities` -> `usage_values`,
    `country_totals`, `total_market_value`); returns a new state."""
    out = dict(state)
    bucket = next(iter(state.get("drivers") or {}), None)
    driver = (state.get("drivers") or {}).get(bucket) or {}
    f = tonnes_factor(driver.get("final_demand_unit") or driver.get("unit") or "")
    if bucket is None or f is None:
        return out
    prices = table["prices"]
    values = dict((state.get("usage_values") or {}).get(bucket) or {})
    for c, q in ((state.get("quantities") or {}).get(bucket) or {}).items():
        p = prices.get(c)
        if p and isinstance(q.get("value"), (int, float)):
            values[c] = round(float(q["value"]) * f * p["usd_per_t"] / 1e6, 4)
    out["usage_values"] = {**(state.get("usage_values") or {}), bucket: values}
    totals = dict(state.get("country_totals") or {})
    totals.update(values)
    out["country_totals"] = totals
    out["total_market_value"] = round(math.fsum(totals.values()), 4)
    out["country_prices"] = table
    out["triangulation"] = [
        {**t, "demand_side_estimate_usd_mn": out["total_market_value"],
         "gap_pct": round(100 * (out["total_market_value"] - t["value"]) / t["value"], 2) if t.get("value") else None}
        for t in state.get("triangulation") or []
    ]
    out["log"] = list(state.get("log") or []) + [
        f"[pricing] per-country prices applied ({table['basis']} basis, {table['basis_note']}): global "
        f"${state.get('total_market_value', 0):,.1f}M -> ${out['total_market_value']:,.1f}M"]
    return out


def patch_report_rows(report: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Write the per-country price into the build-up rows of `02_sizing`
    (the engine's report builder only knows one price per bucket)."""
    table = state.get("country_prices")
    if not table:
        return report
    f = None
    for row in report.get("build_up_rows") or []:
        p = table["prices"].get(row.get("country"))
        f = tonnes_factor(row.get("final_demand_unit") or row.get("driver_unit") or "")
        if not p or f is None:
            continue
        row.update(engine_asp_usd_per_unit=row.get("asp_usd_per_unit"), asp_usd_per_unit=p["usd_per_t"] * f,
                   normalized_asp_per_base_unit=p["usd_per_t"] * f, asp_evidence_type=p["label"],
                   asp_source=p.get("publisher") or p["method"], asp_source_url=p.get("source_url", ""),
                   asp_citation=p["quote"], asp_method=p["method"], price_basis=table["basis"])
    report["country_prices"] = {k: v for k, v in table.items() if k not in ("gdp_pc_ppp", "population")}
    return report

"""Apparent-consumption market model for any country / any market.

    Apparent consumption = Domestic Production + Imports - Exports

Imports and exports are REAL customs data (UN Comtrade, via `trade_evidence`).
Production is the one figure customs records cannot see, so it is retrieved
separately and always labelled as such -- never presented as verified.

The resulting volume is then split Product -> Application, so a leaf reads as
"Italy / Balsamic Vinegar / Food & Beverages", carrying volume, price and
revenue. Splits come from the LLM as SHARES only; every litre and every dollar
is computed here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from me_engine.generation.config import GenConfig
from me_engine.generation.llm import GenLLM
from me_engine.trade_evidence import COUNTRY_CODES, KG_PER_LITRE, fetch_country_trade_evidence

# Population for the per-capita sanity check (World Bank, millions).
POPULATION_MN: dict[str, float] = {
    "India": 1428.6, "China": 1410.7, "Japan": 124.5, "South Korea": 51.7, "Australia": 26.6,
    "U.S.": 334.9, "Canada": 40.1, "Mexico": 128.5, "Germany": 84.5, "U.K.": 68.3,
    "France": 68.2, "Italy": 58.9, "Spain": 48.4, "Brazil": 216.4, "Argentina": 46.7,
    "South Africa": 60.4, "Turkey": 85.3,
}

SUPPORTED_COUNTRIES = sorted(set(COUNTRY_CODES) & set(POPULATION_MN))


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, float(v))
    except (TypeError, ValueError):
        return default


def _shares(raw: Any, keys: list[str]) -> dict[str, float]:
    """Normalise whatever came back to shares summing to exactly 1.0."""
    vals = {k: _f((raw or {}).get(k)) for k in keys}
    total = sum(vals.values())
    if total <= 0:
        return {k: 1.0 / len(keys) for k in keys}
    return {k: v / total for k, v in vals.items()}


def fetch_production_and_structure(
    llm: GenLLM, market: str, country: str, year: int,
) -> tuple[str, str, dict[str, Any]]:
    """One call for the three things customs data cannot provide: domestic
    production volume, the product mix, and each product's application mix.
    All are labelled `estimated`/`assumed` downstream -- never `verified`."""
    system = (
        "You are a market-sizing analyst. For the given market and country, provide: (1) domestic PRODUCTION "
        "volume in litres for the base year -- your best realistic estimate of what is manufactured inside "
        "that country, excluding imports; (2) the product-type mix of that market as percentage shares; "
        "(3) for EACH product type, how its volume splits across end-use applications, as percentage shares. "
        "Shares need not sum to exactly 100 -- they are normalised afterwards. Give a one-line basis for the "
        "production figure. Return ONLY JSON, no commentary."
    )
    user = (
        f"Market: {market}\n"
        f"Country: {country}\n"
        f"Base year: {year}\n\n"
        'Return JSON: {"production_litres": <number>, "production_basis": "<one line on what this is based on>", '
        '"products": ["<product>", ...], "applications": ["<application>", ...], '
        '"product_mix_pct": {"<product>": <pct>, ...}, '
        '"application_mix_pct": {"<product>": {"<application>": <pct>, ...}, ...}, '
        '"avg_price_usd_per_litre": {"<product>": <number>, ...}}'
    )
    return system, user, llm.complete_json(system, user)


def build_apparent_consumption(
    market: str, country: str, config: GenConfig, year: int = 2023,
    cache_path: Any = None, root: Path | None = None,
) -> dict[str, Any]:
    """Full model for one country: the identity, then the Product ->
    Application tree underneath it."""
    if country not in POPULATION_MN:
        raise ValueError(f"No reference data for '{country}'")

    trade = fetch_country_trade_evidence(country, year=year, root=root)
    llm = GenLLM(config, cache_path=cache_path)
    log: list[str] = []

    try:
        sys_p, usr_p, raw = fetch_production_and_structure(llm, market, country, year)
    except Exception as exc:  # noqa: BLE001
        log.append(f"[apparent_consumption] production/structure call failed: {exc}")
        sys_p, usr_p, raw = "", "", {}

    imports_kg = _f((trade.get("imports") or {}).get("weight_kg")) if trade.get("available") else 0.0
    exports_kg = _f((trade.get("exports") or {}).get("weight_kg")) if trade.get("available") else 0.0
    imports_l = imports_kg / KG_PER_LITRE
    exports_l = exports_kg / KG_PER_LITRE

    production_l = _f(raw.get("production_litres"))
    apparent_l = production_l + imports_l - exports_l
    if apparent_l <= 0:
        log.append(
            "[apparent_consumption] identity produced a non-positive volume -- production estimate is "
            "likely too low relative to recorded exports."
        )

    products = [str(p).strip() for p in (raw.get("products") or []) if str(p).strip()]
    applications = [str(a).strip() for a in (raw.get("applications") or []) if str(a).strip()]
    if not products or not applications:
        log.append("[apparent_consumption] no product/application structure returned")
        products, applications = products or ["Total"], applications or ["Total"]

    product_mix = _shares(raw.get("product_mix_pct"), products)
    app_mix_raw = raw.get("application_mix_pct") or {}
    prices = raw.get("avg_price_usd_per_litre") or {}

    # Fall back to the customs-derived unit value when no per-product price
    # was returned -- that at least keeps the price side evidence-anchored.
    customs_price = None
    if trade.get("available"):
        customs_price = (trade.get("imports") or {}).get("unit_value_usd_per_l")

    tree = []
    for p in products:
        p_vol = max(0.0, apparent_l) * product_mix[p]
        p_price = _f(prices.get(p)) or _f(customs_price) or 0.0
        app_shares = _shares(app_mix_raw.get(p), applications)
        children = []
        for a in applications:
            a_vol = p_vol * app_shares[a]
            children.append({
                "application": a,
                "volume_l": round(a_vol, 2),
                "volume_mn_l": round(a_vol / 1e6, 4),
                "share_of_product": round(app_shares[a], 6),
                "price_usd_per_l": round(p_price, 3),
                "revenue_usd": round(a_vol * p_price, 2),
                "revenue_usd_mn": round(a_vol * p_price / 1e6, 4),
            })
        tree.append({
            "product": p,
            "volume_l": round(p_vol, 2),
            "volume_mn_l": round(p_vol / 1e6, 4),
            "share_of_market": round(product_mix[p], 6),
            "price_usd_per_l": round(p_price, 3),
            "revenue_usd": round(p_vol * p_price, 2),
            "revenue_usd_mn": round(p_vol * p_price / 1e6, 4),
            "applications": children,
        })

    total_revenue = sum(t["revenue_usd"] for t in tree)
    pop = POPULATION_MN[country] * 1e6
    per_capita = apparent_l / pop if pop else 0.0

    # Plausibility band. Published per-capita vinegar intake sits roughly in
    # 0.3-3.0 L/person/yr across developed and emerging markets. Landing
    # outside it means the production estimate is the suspect input, since
    # trade is measured. Surfaced, never silently corrected.
    PC_LO, PC_HI = 0.3, 3.0
    if per_capita > PC_HI:
        verdict, detail = "too_high", (
            f"{per_capita:.2f} L/person exceeds the {PC_LO}-{PC_HI} L plausible band. Imports and exports are "
            "measured customs data, so the production estimate is the input to challenge — it is likely "
            "overstated."
        )
    elif per_capita < PC_LO:
        verdict, detail = "too_low", (
            f"{per_capita:.2f} L/person falls below the {PC_LO}-{PC_HI} L plausible band. Production is likely "
            "understated or covers only part of the category."
        )
    else:
        verdict, detail = "plausible", (
            f"{per_capita:.2f} L/person sits inside the {PC_LO}-{PC_HI} L band observed across comparable markets."
        )
    plausibility = {"verdict": verdict, "detail": detail, "band_lo": PC_LO, "band_hi": PC_HI,
                    "per_capita_l": round(per_capita, 3)}

    return {
        "market": market,
        "country": country,
        "year": year,
        "identity": {
            "production_l": round(production_l, 2),
            "production_mn_l": round(production_l / 1e6, 3),
            "production_basis": str(raw.get("production_basis") or "").strip(),
            "production_provenance": "estimated",
            "imports_l": round(imports_l, 2),
            "imports_mn_l": round(imports_l / 1e6, 3),
            "imports_kg": round(imports_kg, 1),
            "exports_l": round(exports_l, 2),
            "exports_mn_l": round(exports_l / 1e6, 3),
            "exports_kg": round(exports_kg, 1),
            "trade_provenance": "verified" if trade.get("available") else "unavailable",
            "trade_source": trade.get("source"),
            "trade_urls": trade.get("source_urls"),
            "apparent_consumption_l": round(apparent_l, 2),
            "apparent_consumption_mn_l": round(apparent_l / 1e6, 3),
            "per_capita_l": round(per_capita, 3) if pop else None,
            "formula": "Apparent consumption = Domestic Production + Imports − Exports",
        },
        "plausibility": plausibility,
        "products": products,
        "applications": applications,
        "tree": tree,
        "total_revenue_usd_mn": round(total_revenue / 1e6, 3),
        "customs_unit_value_usd_per_l": customs_price,
        "prompt": {"system": sys_p, "user": usr_p},
        "raw_response": raw,
        "log": log,
        "supported_countries": SUPPORTED_COUNTRIES,
    }

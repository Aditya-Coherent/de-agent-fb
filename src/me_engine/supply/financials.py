"""Real financial data lookup: yfinance (existence + ticker + total revenue),
then InsightSentry (RapidAPI) for a richer real region/business breakdown
once yfinance has confirmed the company is genuinely publicly listed.

Two-source design, not redundant: yfinance's search returns a relevance
score and reliably comes back EMPTY for private companies (verified) --
InsightSentry's own search has no relevance score and returns unrelated
fuzzy matches even for private companies (verified: searching "Bostik"
returns Boston Scientific; searching "Chosen Foods" returns Patanjali
Foods). So InsightSentry search is NEVER used as the existence check --
only to enrich a company yfinance already confirmed, and only when a
result's description plausibly matches the confirmed company name.

Replaces LLM-guessed company revenue with actual filed figures for publicly
listed companies. Returns None (never a guess) when no match is found, so
callers know to fall back to real web-evidence search instead of inventing
a number -- per the design principle: the LLM never estimates a number from
nothing, it only derives insight from data that was actually fetched.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from .companies import name_similarity

# Minimum token similarity between the searched company name and a Yahoo
# result's long/short name. Yahoo's search is fuzzy and its first EQUITY hit
# was confirmed wrong in real runs ("Borges" -> Borgestad ASA, a Norwegian
# refractories firm; "Xingyu" -> Changzhou Xingyu Automotive Lighting) --
# those then got capped/estimated as if they were olive-oil/glove makers.
_MIN_NAME_SIMILARITY = 0.6

# Fallback USD-per-unit rates (approximate, 2025-26 levels) for when the live
# yfinance FX quote is unavailable. A rough rate is fine here: it only feeds
# a revenue CAP and the LLM's grounding text, and being off by a few percent
# is nothing next to the 4.5x error of treating MYR 2,188 Mn as USD 2,188 Mn.
_STATIC_USD_PER_UNIT = {
    "USD": 1.0, "EUR": 1.08, "GBP": 1.27, "CHF": 1.12, "SEK": 0.094, "DKK": 0.145,
    "JPY": 0.0067, "CNY": 0.138, "KRW": 0.00073, "INR": 0.012, "MYR": 0.22, "THB": 0.028,
    "IDR": 0.000062, "SGD": 0.74, "HKD": 0.128, "TWD": 0.031, "AUD": 0.66, "CAD": 0.73,
    "BRL": 0.18, "MXN": 0.055, "ZAR": 0.054,
}
# Yahoo quotes some listings in minor units (pence, cents).
_MINOR_UNITS = {"GBP": ("GBP", 0.01), "GBX": ("GBP", 0.01), "ZAC": ("ZAR", 0.01), "ILA": ("ILS", 0.01)}


@dataclass(frozen=True, slots=True)
class CompanyFinancials:
    ticker: str
    matched_name: str
    total_revenue: float
    currency: str | None
    sector: str | None
    industry: str | None
    region_segments: dict | None = None    # {"date": year, "segments": [{"label": region, "value": revenue, "value_usd_mn": ...}, ...]}
    business_segments: dict | None = None  # same shape, by product/business line
    usd_rate: float | None = None          # USD per 1 unit of `currency`; None = unknown currency, do not cap

    @property
    def total_revenue_usd_mn(self) -> float | None:
        if self.usd_rate is None:
            return None
        return self.total_revenue * self.usd_rate / 1_000_000


def _live_fx_rate(currency: str) -> float | None:
    """USD per 1 unit of `currency` from Yahoo's "<CUR>USD=X" quote."""
    import yfinance as yf

    try:
        rate = yf.Ticker(f"{currency}USD=X").fast_info["last_price"]
    except Exception:  # noqa: BLE001 - any lookup failure means "use the fallback"
        return None
    return float(rate) if rate and rate > 0 else None


@lru_cache(maxsize=64)
def usd_rate(currency: str | None) -> float | None:
    """USD per 1 unit of a reported currency: live yfinance quote, else the
    static fallback table. None when the currency is missing/unknown -- the
    caller must then NOT use the figure as a USD cap (and should flag it)."""
    if not currency:
        return None
    cur = currency.strip()
    factor = 1.0
    if cur == "GBp" or cur.upper() in ("GBX", "ZAC", "ILA"):
        cur, factor = _MINOR_UNITS["GBP" if cur == "GBp" else cur.upper()]
    cur = cur.upper()
    if cur == "USD":
        return factor
    static = _STATIC_USD_PER_UNIT.get(cur)
    live = _live_fx_rate(cur)
    # Reject a live quote wildly off the static level (bad/garbled symbol).
    if live is not None and (static is None or 0.5 * static <= live <= 2 * static):
        return live * factor
    return static * factor if static is not None else None


def _to_usd_segments(segments: dict | None, rate: float | None) -> dict | None:
    """Copy of an InsightSentry segment block with a `value_usd_mn` per row
    (None when the currency is unknown). Values are in the same raw reporting
    currency/units as yfinance's totalRevenue."""
    if not segments:
        return segments
    rows = []
    for seg in segments.get("segments", []) or []:
        try:
            value = float(seg.get("value"))
        except (TypeError, ValueError):
            continue
        rows.append({**seg, "value": value, "value_usd_mn": value * rate / 1_000_000 if rate else None})
    return {**segments, "segments": rows}


def _lookup_ticker(company_name: str) -> dict | None:
    """Best-match ticker for a company name via Yahoo Finance search --
    only accepted when the listed name actually matches the company (see
    `_MIN_NAME_SIMILARITY`); otherwise None, i.e. "not publicly listed"."""
    import yfinance as yf

    try:
        quotes = yf.Search(company_name, max_results=5).quotes
    except Exception:  # noqa: BLE001 - any lookup failure means "no match"
        return None
    for q in quotes:
        if q.get("quoteType") != "EQUITY":
            continue
        names = [q.get("longname"), q.get("shortname")]
        if max((name_similarity(company_name, n) for n in names if n), default=0.0) >= _MIN_NAME_SIMILARITY:
            return q
    return None


def _insightsentry_headers() -> dict | None:
    key = os.environ.get("RAPIDAPI_KEY")
    if not key:
        return None
    host = os.environ.get("RAPIDAPI_INSIGHTSENTRY_HOST", "insightsentry.p.rapidapi.com")
    return {"x-rapidapi-key": key, "x-rapidapi-host": host}


def _insightsentry_search(query: str) -> list[dict]:
    headers = _insightsentry_headers()
    if not headers:
        return []
    import httpx

    try:
        r = httpx.get(
            "https://insightsentry.p.rapidapi.com/v3/symbols/search",
            params={"query": query}, headers=headers, timeout=15,
        )
        r.raise_for_status()
        return r.json().get("symbols", []) or []
    except Exception:  # noqa: BLE001
        return []


def _insightsentry_fundamentals(code: str) -> dict | None:
    headers = _insightsentry_headers()
    if not headers:
        return None
    import httpx

    try:
        r = httpx.get(
            f"https://insightsentry.p.rapidapi.com/v3/symbols/{code}/fundamentals",
            headers=headers, timeout=20,
        )
        r.raise_for_status()
        return r.json()
    except Exception:  # noqa: BLE001
        return None


def _fetch_segments(matched_name: str) -> tuple[dict | None, dict | None]:
    """Real geographic + business-line revenue segments, via InsightSentry --
    only called after yfinance already confirmed the company is publicly
    listed. Requires the first word of the confirmed name to appear in the
    candidate's description, to reject InsightSentry's unrelated fuzzy
    matches (see module docstring)."""
    key_word = matched_name.split()[0].lower()
    for sym in _insightsentry_search(matched_name)[:5]:
        code = sym.get("code")
        desc = f"{sym.get('description', '')} {sym.get('name', '')}".lower()
        if not code or key_word not in desc:
            continue
        data = _insightsentry_fundamentals(code)
        if not data:
            continue
        items = {i.get("id"): i for i in (data.get("data") or [])}
        region_h = (items.get("revenue_seg_by_region_h") or {}).get("value")
        business_h = (items.get("revenue_seg_by_business_h") or {}).get("value")
        region = region_h[0] if region_h else None
        business = business_h[0] if business_h else None
        if region or business:
            return region, business
    return None, None


def fetch_company_revenue(company_name: str) -> CompanyFinancials | None:
    """Real total revenue (and, when available, real region/business
    segments) for a company, if it's publicly listed.

    None means "not found via this source" -- NOT zero revenue. Only
    covers publicly listed companies; private companies (most of a
    discovered list) will not match here and need the web-evidence
    fallback instead.
    """
    match = _lookup_ticker(company_name)
    if not match:
        return None
    ticker = match.get("symbol")
    if not ticker:
        return None

    import yfinance as yf

    try:
        info = yf.Ticker(ticker).info
    except Exception:  # noqa: BLE001
        return None
    revenue = info.get("totalRevenue")
    if not revenue:
        return None

    matched_name = match.get("longname") or match.get("shortname") or company_name
    region_segments, business_segments = _fetch_segments(matched_name)
    currency = info.get("financialCurrency")
    rate = usd_rate(currency)

    return CompanyFinancials(
        ticker=ticker,
        matched_name=matched_name,
        total_revenue=float(revenue),
        currency=currency,
        sector=info.get("sector"),
        industry=info.get("industry"),
        region_segments=_to_usd_segments(region_segments, rate),
        business_segments=_to_usd_segments(business_segments, rate),
        usd_rate=rate,
    )

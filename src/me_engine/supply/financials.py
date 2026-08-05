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


@dataclass(frozen=True, slots=True)
class CompanyFinancials:
    ticker: str
    matched_name: str
    total_revenue: float
    currency: str | None
    sector: str | None
    industry: str | None
    region_segments: dict | None = None    # {"date": year, "segments": [{"label": region, "value": revenue}, ...]}
    business_segments: dict | None = None  # same shape, by product/business line


def _lookup_ticker(company_name: str) -> dict | None:
    """Best-match ticker for a company name via Yahoo Finance search."""
    import yfinance as yf

    try:
        quotes = yf.Search(company_name, max_results=5).quotes
    except Exception:  # noqa: BLE001 - any lookup failure means "no match"
        return None
    equities = [q for q in quotes if q.get("quoteType") == "EQUITY"]
    return equities[0] if equities else None


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

    return CompanyFinancials(
        ticker=ticker,
        matched_name=matched_name,
        total_revenue=float(revenue),
        currency=info.get("financialCurrency"),
        sector=info.get("sector"),
        industry=info.get("industry"),
        region_segments=region_segments,
        business_segments=business_segments,
    )

"""Supply-side financial grounding: ticker-match rejection, USD conversion of
real revenue before it is used as a cap, the LLM market-slice rescale, and
the coverage floor. No network/LLM calls -- yfinance and the LLM are faked."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from me_engine.supply import estimator, financials
from me_engine.supply.companies import Company, name_similarity
from me_engine.supply.estimator import (
    COVERAGE_FLOOR,
    estimate_company_countries,
    estimate_coverage_by_country,
    reconcile_market_slice,
)
from me_engine.supply.financials import CompanyFinancials


class _FakeLLM:
    def __init__(self, response: dict):
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str) -> dict:
        self.calls.append((system, user))
        return self.response


@pytest.fixture(autouse=True)
def _clear_fx_cache():
    financials.usd_rate.cache_clear()
    yield
    financials.usd_rate.cache_clear()


def _fake_yf_search(monkeypatch, quotes: list[dict]):
    fake = SimpleNamespace(Search=lambda name, max_results=5: SimpleNamespace(quotes=quotes))
    monkeypatch.setitem(sys.modules, "yfinance", fake)


# --- Fix 5: wrong ticker matches -----------------------------------------------

def test_ticker_rejects_borgestad_for_borges(monkeypatch):
    _fake_yf_search(monkeypatch, [
        {"symbol": "BOR.OL", "quoteType": "EQUITY", "longname": "Borgestad ASA", "shortname": "BORGESTAD"},
    ])
    assert financials._lookup_ticker("Borges") is None
    assert financials._lookup_ticker("Borges International Group") is None


def test_ticker_rejects_unrelated_xingyu(monkeypatch):
    _fake_yf_search(monkeypatch, [
        {"symbol": "601799.SS", "quoteType": "EQUITY",
         "longname": "Changzhou Xingyu Automotive Lighting Systems Co.,Ltd."},
    ])
    assert financials._lookup_ticker("Xingyu") is None


def test_ticker_accepts_real_match_and_skips_bad_first_hit(monkeypatch):
    _fake_yf_search(monkeypatch, [
        {"symbol": "X", "quoteType": "ETF", "longname": "Hartalega ETF"},
        {"symbol": "BAD", "quoteType": "EQUITY", "longname": "Hart Industries Inc"},
        {"symbol": "5168.KL", "quoteType": "EQUITY", "longname": "Hartalega Holdings Berhad"},
    ])
    assert financials._lookup_ticker("Hartalega")["symbol"] == "5168.KL"


@pytest.mark.parametrize("a,b,ok", [
    ("Top Glove", "Top Glove Corporation Bhd", True),
    ("TopGlove", "Top Glove Corporation Bhd", True),
    ("Kossan", "Kossan Rubber Industries Bhd", True),
    ("Ansell", "Ansell Limited", True),
    ("Borges", "Borgestad ASA", False),
    ("Xingyu", "Changzhou Xingyu Automotive Lighting Systems Co.,Ltd.", False),
])
def test_name_similarity_threshold(a, b, ok):
    assert (name_similarity(a, b) >= financials._MIN_NAME_SIMILARITY) is ok


# --- Fix 3: currency conversion --------------------------------------------------

def test_usd_rate_live_then_static_fallback(monkeypatch):
    monkeypatch.setattr(financials, "_live_fx_rate", lambda cur: 0.2195 if cur == "MYR" else None)
    assert financials.usd_rate("MYR") == pytest.approx(0.2195)
    assert financials.usd_rate("USD") == 1.0
    assert financials.usd_rate("THB") == financials._STATIC_USD_PER_UNIT["THB"]  # live failed
    assert financials.usd_rate("GBp") == pytest.approx(0.01 * financials._STATIC_USD_PER_UNIT["GBP"])
    assert financials.usd_rate("XYZ") is None
    assert financials.usd_rate(None) is None


def test_usd_rate_rejects_garbled_live_quote(monkeypatch):
    monkeypatch.setattr(financials, "_live_fx_rate", lambda cur: 4.5)  # MYR-per-USD, inverted
    assert financials.usd_rate("MYR") == financials._STATIC_USD_PER_UNIT["MYR"]


def test_fetch_company_revenue_converts_total_and_segments(monkeypatch):
    monkeypatch.setattr(financials, "_lookup_ticker",
                        lambda name: {"symbol": "5168.KL", "longname": "Hartalega Holdings Berhad"})
    monkeypatch.setattr(financials, "_fetch_segments", lambda name: (
        {"date": 2025, "segments": [{"label": "North America", "value": 1_000_000_000}]}, None,
    ))
    monkeypatch.setattr(financials, "_live_fx_rate", lambda cur: 0.22)
    import pandas as pd
    stmt = pd.DataFrame(
        {pd.Timestamp("2026-03-31"): [9_999_000_000], pd.Timestamp("2025-03-31"): [2_188_000_000],
         pd.Timestamp("2024-03-31"): [1_800_000_000]},
        index=["Total Revenue"],
    )
    fake_yf = SimpleNamespace(Ticker=lambda t: SimpleNamespace(income_stmt=stmt, info={
        "totalRevenue": 7_777_000_000, "financialCurrency": "MYR", "sector": "Healthcare", "industry": "x",
    }))
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)
    fin = financials.fetch_company_revenue("Hartalega")
    # FY2026 and the trailing-12-month total are never used; the latest year <= 2025 is.
    assert fin.revenue_year == 2025
    assert fin.total_revenue_usd_mn == pytest.approx(481.36)
    assert fin.region_segments["segments"][0]["value_usd_mn"] == pytest.approx(220.0)


def test_segments_skip_years_after_base():
    history = [{"date": "2026"}, {"date": 2025, "segments": []}]
    assert financials._latest_allowed(history) == {"date": 2025, "segments": []}
    assert financials._latest_allowed([{"date": 2026}]) is None


def test_web_revenue_for_2026_is_rejected(monkeypatch):
    monkeypatch.setattr(estimator, "fetch_company_revenue", lambda name: None)
    monkeypatch.setattr(estimator.web_search, "gather", lambda *a, **k: ["Acme revenue 2026: $900M"])
    llm = _FakeLLM({"country_revenue_usd_mn": {"US": 900.0}, "revenue_year": 2026})
    revenue, source, notes = estimate_company_countries(llm, "Widgets", Company("Acme"), ["US"], True)
    assert source == "no_real_data" and revenue == {"US": 0.0}
    assert "revenue_year_after_base:2026" in notes


def test_web_revenue_for_2025_is_kept(monkeypatch):
    monkeypatch.setattr(estimator, "fetch_company_revenue", lambda name: None)
    monkeypatch.setattr(estimator.web_search, "gather", lambda *a, **k: ["Acme revenue 2025: $900M"])
    llm = _FakeLLM({"country_revenue_usd_mn": {"US": 900.0}, "revenue_year": 2025})
    revenue, source, notes = estimate_company_countries(llm, "Widgets", Company("Acme"), ["US"], True)
    assert source == "web_evidence" and revenue["US"] == pytest.approx(900.0)
    assert "revenue_year:2025" in notes
    assert "2026" in llm.calls[0][0]  # prompt tells the LLM to ignore 2026+ figures


def _fin(currency: str | None, rate: float | None, revenue: float = 2_188_000_000) -> CompanyFinancials:
    return CompanyFinancials(
        ticker="5168.KL", matched_name="Hartalega Holdings Berhad", total_revenue=revenue,
        currency=currency, sector="Healthcare", industry="Medical Instruments & Supplies", usd_rate=rate,
    )


def test_myr_revenue_capped_in_usd_not_raw(monkeypatch):
    # MYR 2,188 Mn at a stubbed 0.22 USD/MYR -> cap ~USD 481 Mn, not 2,188.
    monkeypatch.setattr(estimator, "fetch_company_revenue", lambda name: _fin("MYR", 0.22))
    llm = _FakeLLM({"country_revenue_usd_mn": {"United States": 1500.0, "Germany": 500.0}})
    revenue, source, notes = estimate_company_countries(
        llm, "Disposable Medical Gloves", Company("Hartalega"), ["United States", "Germany"], False,
    )
    assert source == "financial_api"
    assert sum(revenue.values()) == pytest.approx(481.36)
    assert revenue["United States"] == pytest.approx(481.36 * 0.75)
    grounding = llm.calls[0][1]
    assert "2,188,000,000 MYR" in grounding and "USD 481.4 Mn" in grounding
    assert "USD millions" in grounding


def test_unknown_currency_is_not_capped_and_flagged(monkeypatch):
    monkeypatch.setattr(estimator, "fetch_company_revenue", lambda name: _fin("XYZ", None))
    llm = _FakeLLM({"country_revenue_usd_mn": {"US": 5000.0}})
    revenue, _, notes = estimate_company_countries(llm, "Gloves", Company("Odd Co"), ["US"], False)
    assert revenue["US"] == 5000.0
    assert "fx_unknown:XYZ" in notes


# --- Fix 4: per-country numbers vs the LLM's own stated slice --------------------

def test_rescale_to_stated_market_revenue_and_clamp_to_fraction():
    # "~2% ~= $600M" but countries summed to $6,450M (real audit case).
    revenue = {"US": 4300.0, "DE": 2150.0}
    out, notes = reconcile_market_slice(
        revenue, {"market_fraction": 0.02, "market_revenue_usd_mn": 600.0}, cap=30_000.0)
    assert sum(out.values()) == pytest.approx(600.0)
    assert out["US"] == pytest.approx(400.0)
    assert "rescaled_to_market_revenue" in notes and "missing_market_slice" not in notes

    # Stated market revenue above fraction x real total -> clamped to it.
    out, notes = reconcile_market_slice(
        {"US": 900.0}, {"market_fraction": 0.02, "market_revenue_usd_mn": 900.0}, cap=30_000.0)
    assert sum(out.values()) == pytest.approx(600.0)
    assert "market_revenue_clamped" in notes


def test_rescale_applies_to_web_evidence_without_cap():
    out, notes = reconcile_market_slice({"US": 30.0, "IN": 10.0}, {"market_revenue_usd_mn": 80.0}, cap=None)
    assert out == {"US": pytest.approx(60.0), "IN": pytest.approx(20.0)}
    assert "missing_market_slice" in notes  # fraction absent


def test_missing_slice_fields_backward_compatible():
    out, notes = reconcile_market_slice({"US": 300.0, "IN": 300.0}, {}, cap=400.0)
    assert sum(out.values()) == pytest.approx(400.0)  # old hard cap still applies
    assert notes == ["missing_market_slice"]
    out, _ = reconcile_market_slice({"US": 100.0}, {}, cap=None)
    assert out == {"US": 100.0}


def test_web_evidence_company_rescaled_end_to_end(monkeypatch):
    monkeypatch.setattr(estimator, "fetch_company_revenue", lambda name: None)
    monkeypatch.setattr(estimator.web_search, "gather", lambda queries, per_query, cap: ["Acme sells gloves"])
    llm = _FakeLLM({"market_fraction": 0.5, "market_revenue_usd_mn": 50.0,
                    "country_revenue_usd_mn": {"US": 300.0, "IN": 100.0}})
    revenue, source, notes = estimate_company_countries(llm, "Gloves", Company("Acme"), ["US", "IN"], True)
    assert source == "web_evidence"
    assert revenue == {"US": pytest.approx(37.5), "IN": pytest.approx(12.5)}
    assert "market_fraction" in llm.calls[0][0] and "market_revenue_usd_mn" in llm.calls[0][0]


# --- Fix 6: coverage floor --------------------------------------------------------

def test_coverage_floor_is_030():
    assert COVERAGE_FLOOR == 0.30
    llm = _FakeLLM({"coverage_fraction": {"US": 0.05, "IN": 0.6, "DE": 1.2}})
    cov = estimate_coverage_by_country(llm, "Gloves", ["US", "IN", "DE", "BR"], {})
    assert cov == {"US": 0.30, "IN": 0.6, "DE": 0.95, "BR": 0.6}

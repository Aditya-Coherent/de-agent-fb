"""Country-wise estimation tools — the LLM-calling building blocks used by
this package's agent graph (`graph.py`). Bottom-up (per-company, per-country
revenue, summed and grossed-up per country) is the technique this package
uses to actually produce the supply-side estimate; the single global total
is just `Σ(country_total)` over this same breakdown, not a separate method.

Real-data-grounded design (per mentor review): the LLM never invents a
company's revenue. For each company, real data is fetched FIRST —
`financials.py` (yfinance, for publicly listed companies) or `web_search.py`
(ddgs, multi-backend real search) as a fallback for private companies — and
the LLM's only job is to derive the country breakdown FROM that real data,
the same way a regression model infers an output from real input features.
If neither source finds anything real for a company, that is recorded
explicitly (`no_real_data`) rather than silently blended in as if grounded.

Every country's grossed-up total is summed into one grand supply-side
figure — directly comparable to the demand-side pipeline's global total,
with a per-country breakdown comparable to its regional split.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import web_search
from .companies import Company
from .financials import fetch_company_revenue
from ..generation.llm import GenLLM

_GROUNDED_SYSTEM_TMPL = (
    "You are a market-research analyst. You will be given REAL data about a company -- either its "
    "verified total revenue from financial records, or real web-search evidence snippets about it. "
    "Your job is to DERIVE an estimate from that real data, the way a regression model infers an "
    "output from real input features -- not to invent a number with no real anchor at all. "
    "\n\nIMPORTANT: if you are given a verified TOTAL revenue figure, you are expected to estimate "
    "what FRACTION of it is attributable to the specific market '{market}' -- using the company's "
    "known sector/industry/business mix as your basis for that fraction. This counts as deriving "
    "insight from real data, NOT as inventing a number -- do not zero everything out just because "
    "you weren't given an exact market-specific breakdown; deriving that breakdown IS the task. Your "
    "final country figures must be consistent with a plausible fraction of the real total given -- "
    "do not output something disconnected from it, and do not exceed it. "
    "\n\nOnly set \"no_real_data\" to true when you were told literally NO real data (no total revenue "
    "AND no web evidence) was found for this company -- in that case, and ONLY that case, set every "
    "country to 0. "
    "\n\nBreak the result down by country for exactly these countries: {countries}. It is fine (and "
    "expected) for revenue to be 0 or near-0 in countries where the company has negligible presence. "
    "Respond ONLY with a JSON object: {{\"country_revenue_usd_mn\": {{\"<country>\": <number>, ...}}, "
    "\"no_real_data\": <true|false>, \"reasoning\": \"<one or two sentences>\"}}"
)

COVERAGE_SYSTEM = (
    "You are a market-research analyst. Given a market, a list of countries, and named suppliers' "
    "combined revenue in EACH country, estimate what FRACTION of each country's TOTAL market these "
    "named suppliers represent (0-1 per country). Fragmented/many-small-player countries get a "
    "lower fraction (0.3-0.5); countries dominated by a handful of the named players get a higher "
    "fraction (0.7-0.9). Respond ONLY with a JSON object: "
    '{"coverage_fraction": {"<country>": <0-1 number>, ...}, "reasoning": "<one or two sentences>"}'
)


@dataclass(frozen=True, slots=True)
class CountryBreakdown:
    country: str
    named_total_usd_mn: float
    coverage_fraction: float
    country_total_usd_mn: float
    company_shares: dict[str, float]  # company name -> share of this country's total (0-1)


def _ground_company(market_name: str, company: Company, enable_web: bool) -> tuple[str, str, float | None]:
    """Fetch real data for a company. Returns (grounding_text_for_the_LLM, source_tag, cap)
    where source_tag is one of "financial_api", "web_evidence", "no_real_data", and `cap`
    is the company's real total revenue when known (for enforcing in code, not just by
    asking the LLM nicely, that its derived figure never exceeds the real number)."""
    financials = fetch_company_revenue(company.name)
    if financials is not None:
        text = (
            f"VERIFIED FINANCIAL DATA (source: Yahoo Finance, ticker {financials.ticker}): "
            f"{financials.matched_name} total annual revenue = "
            f"{financials.total_revenue:,.0f} {financials.currency or ''} "
            f"(sector: {financials.sector}, industry: {financials.industry}). "
            "This is the company's TOTAL revenue across all markets/products, not just this one."
        )
        if financials.region_segments:
            rows = "; ".join(
                f"{s['label']}: {s['value']:,.0f}" for s in financials.region_segments.get("segments", [])
            )
            text += (
                f"\n\nVERIFIED REAL revenue BY REGION (source: InsightSentry, FY "
                f"{financials.region_segments.get('date')}): {rows}. Use these REAL regional figures "
                "directly wherever a target country falls within one of these regions, instead of "
                "guessing a split -- only derive/interpolate for countries not covered by them."
            )
        if financials.business_segments:
            rows = "; ".join(
                f"{s['label']}: {s['value']:,.0f}" for s in financials.business_segments.get("segments", [])
            )
            text += (
                f"\n\nVERIFIED REAL revenue BY PRODUCT/BUSINESS LINE (source: InsightSentry, FY "
                f"{financials.business_segments.get('date')}): {rows}. Use this to judge which "
                f"business line(s) actually correspond to the market '{market_name}', instead of "
                "guessing what fraction of the company is in this market."
            )
        # Company_revenue is in USD Mn (per the prompt below); yfinance's totalRevenue
        # is a raw amount, so convert to the same "millions" scale before using it as
        # a cap. No currency conversion yet -- treated as a same-order-of-magnitude cap,
        # fine for EUR/GBP/USD; not precise for currencies like JPY/INR with a very
        # different numeric scale. Flagging rather than silently overclaiming precision.
        cap_mn = financials.total_revenue / 1_000_000
        return text, "financial_api", cap_mn

    if enable_web:
        snippets = web_search.gather(
            [f"{company.name} {market_name} revenue", f"{company.name} annual revenue"],
            per_query=4, cap=8,
        )
        if snippets:
            text = "REAL WEB-SEARCH EVIDENCE:\n" + "\n".join(f"- {s}" for s in snippets)
            return text, "web_evidence", None

    return "NO REAL DATA was found for this company from any source.", "no_real_data", None


def estimate_company_countries(
    llm: GenLLM, market_name: str, company: Company, countries: list[str], enable_web: bool
) -> tuple[dict[str, float], str]:
    """Returns (revenue_by_country, source_tag) -- source_tag tells you whether this
    was grounded in real financial data, real web evidence, or neither.

    Two guardrails enforced in CODE, not just by asking the LLM nicely:
    - No real data at all -> the LLM is never called; zeros are returned directly.
    - Real total revenue known -> the LLM's derived country-sum is clamped so it can
      never exceed that real number, no matter what the LLM outputs.
    """
    grounding_text, source, cap = _ground_company(market_name, company, enable_web)

    if source == "no_real_data":
        return {c: 0.0 for c in countries}, "no_real_data"

    facts_line = ", ".join(f"{k}: {v}" for k, v in company.facts.items()) or "none"
    system = _GROUNDED_SYSTEM_TMPL.format(market=market_name, countries=", ".join(countries))
    user = (
        f"Market: {market_name}\nCompany: {company.name}\nKnown facts: {facts_line}\n\n{grounding_text}"
    )
    data = llm.complete_json(system, user)
    if data.get("no_real_data"):
        return {c: 0.0 for c in countries}, "no_real_data"

    raw = data.get("country_revenue_usd_mn") or {}
    revenue = {c: max(0.0, float(raw.get(c, 0.0) or 0.0)) for c in countries}

    if cap is not None:
        total = sum(revenue.values())
        if total > cap:
            scale = cap / total if total > 0 else 0.0
            revenue = {c: v * scale for c, v in revenue.items()}

    return revenue, source


def estimate_coverage_by_country(
    llm: GenLLM, market_name: str, countries: list[str], named_totals: dict[str, float]
) -> dict[str, float]:
    """One LLM call covering every country's coverage fraction at once."""
    lines = "\n".join(f"- {c}: {named_totals.get(c, 0.0):.1f} USD Mn" for c in countries)
    user = f"Market: {market_name}\nCountries and named-supplier combined revenue:\n{lines}"
    data = llm.complete_json(COVERAGE_SYSTEM, user)
    raw = data.get("coverage_fraction") or {}
    return {c: min(0.95, max(0.05, float(raw.get(c, 0.6) or 0.6))) for c in countries}

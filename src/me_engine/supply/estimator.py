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
figure — directly comparable to the Desk Research pipeline's global total,
with a per-country breakdown comparable to its regional split.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import web_search
from .companies import Company
from .financials import MAX_REVENUE_YEAR, fetch_company_revenue
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
    "\n\nAlso state the market slice explicitly: \"market_fraction\" = the fraction (0-1) of the "
    "company's TOTAL revenue attributable to '{market}', and \"market_revenue_usd_mn\" = the company's "
    "revenue in this market summed over the listed countries, in USD millions. Your country figures "
    "MUST add up to market_revenue_usd_mn (they will be rescaled to it). "
    "Respond ONLY with a JSON object: {{\"market_fraction\": <0-1 number>, \"market_revenue_usd_mn\": "
    "<number>, \"country_revenue_usd_mn\": {{\"<country>\": <number>, ...}}, "
    "\"no_real_data\": <true|false>, \"revenue_year\": <fiscal year of the revenue you used>, "
    "\"reasoning\": \"<one or two sentences>\"}}"
    "\n\nREVENUE YEAR: use only revenue for fiscal year {max_year} or {prev_year} (the latest of the "
    "two that the data gives). IGNORE any figure for {next_year} or later, any forecast or projection, "
    "and any trailing-12-month figure running into {next_year}. Report the year you used in "
    "\"revenue_year\"."
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


# Coverage below this would gross named suppliers up >3.3x -- recorded runs used
# the old 0.05 floor (up to 20x gross-up) and inflated Disposable Medical Gloves
# to ~4x the published size. A list that really covers <30% of a market isn't a
# usable supply-side basis anyway; quality.py flags any gross-up above 2x.
COVERAGE_FLOOR = 0.30
COVERAGE_CEILING = 0.95


def _fmt_segment(seg: dict, currency: str) -> str:
    usd = seg.get("value_usd_mn")
    return f"{seg['label']}: {seg['value']:,.0f} {currency}" + (f" (= USD {usd:,.1f} Mn)" if usd is not None else "")


def _ground_company(
    market_name: str, company: Company, enable_web: bool
) -> tuple[str, str, float | None, list[str]]:
    """Fetch real data for a company. Returns (grounding_text_for_the_LLM, source_tag, cap, notes)
    where source_tag is one of "financial_api", "web_evidence", "no_real_data", `cap` is the
    company's real total revenue in USD Mn when known (for enforcing in code, not just by
    asking the LLM nicely, that its derived figure never exceeds the real number), and `notes`
    are quality-note codes (e.g. "fx_unknown:XYZ" when the reporting currency couldn't be
    converted to USD, so no cap was applied)."""
    financials = fetch_company_revenue(company.name)
    if financials is not None:
        notes: list[str] = []
        cur = financials.currency or "(currency not reported)"
        cap_mn = financials.total_revenue_usd_mn
        usd_part = (
            f" = USD {cap_mn:,.1f} Mn (converted at {financials.usd_rate:.6g} USD per {cur})"
            if cap_mn is not None else
            " (USD conversion unavailable for this currency -- convert it yourself)"
        )
        text = (
            f"VERIFIED FINANCIAL DATA (source: Yahoo Finance, ticker {financials.ticker}): "
            f"{financials.matched_name} total annual revenue for fiscal year {financials.revenue_year} = "
            f"{financials.total_revenue:,.0f} {cur} (raw units, not millions){usd_part} "
            f"(sector: {financials.sector}, industry: {financials.industry}). "
            "This is the company's TOTAL revenue across all markets/products, not just this one. "
            "All your output figures must be in USD millions."
        )
        if financials.region_segments:
            rows = "; ".join(
                _fmt_segment(s, cur) for s in financials.region_segments.get("segments", [])
            )
            text += (
                f"\n\nVERIFIED REAL revenue BY REGION (source: InsightSentry, FY "
                f"{financials.region_segments.get('date')}): {rows}. Use these REAL regional figures "
                "directly wherever a target country falls within one of these regions, instead of "
                "guessing a split -- only derive/interpolate for countries not covered by them."
            )
        if financials.business_segments:
            rows = "; ".join(
                _fmt_segment(s, cur) for s in financials.business_segments.get("segments", [])
            )
            text += (
                f"\n\nVERIFIED REAL revenue BY PRODUCT/BUSINESS LINE (source: InsightSentry, FY "
                f"{financials.business_segments.get('date')}): {rows}. Use this to judge which "
                f"business line(s) actually correspond to the market '{market_name}', instead of "
                "guessing what fraction of the company is in this market."
            )
        # The cap is in USD Mn, like the LLM's output; yfinance's totalRevenue is a raw
        # amount in the company's reporting currency, converted by financials.py.
        # Unknown currency -> no cap at all (a wrong-currency cap is worse than none:
        # MYR 2,188 Mn read as USD 2,188 Mn let Hartalega's estimate run ~4.5x high).
        if cap_mn is None:
            notes.append(f"fx_unknown:{financials.currency or 'none'}")
        return text, "financial_api", cap_mn, notes

    if enable_web:
        snippets = web_search.gather(
            [f"{company.name} {market_name} revenue {MAX_REVENUE_YEAR}",
             f"{company.name} annual revenue {MAX_REVENUE_YEAR}",
             f"{company.name} annual revenue {MAX_REVENUE_YEAR - 1}"],
            per_query=4, cap=8,
        )
        if snippets:
            text = "REAL WEB-SEARCH EVIDENCE:\n" + "\n".join(f"- {s}" for s in snippets)
            return text, "web_evidence", None, []

    return "NO REAL DATA was found for this company from any source.", "no_real_data", None, []


def _float_or_none(value) -> float | None:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def reconcile_market_slice(
    revenue: dict[str, float], data: dict, cap: float | None
) -> tuple[dict[str, float], list[str]]:
    """Make the per-country figures agree with the LLM's own stated market slice.

    Real failure: the LLM reasoned "~2% of revenue ~= $600M" and then output
    country figures summing to $6,450M. So the stated `market_revenue_usd_mn`
    is the anchor -- clamped to `market_fraction x total revenue` (and to the
    total itself) when the real USD total is known -- and the country split is
    rescaled to sum to it. Missing fields fall back to the old behaviour (raw
    country figures, still capped at the real total) with a note."""
    notes: list[str] = []
    fraction = _float_or_none(data.get("market_fraction"))
    if fraction is not None:
        fraction = min(1.0, max(0.0, fraction))
    stated = _float_or_none(data.get("market_revenue_usd_mn"))
    market_rev = max(0.0, stated) if stated is not None else None
    if fraction is None or stated is None:
        notes.append("missing_market_slice")

    if cap is not None:
        bound = cap * fraction if fraction is not None else cap
        if market_rev is None and fraction is not None:
            market_rev = bound
        elif market_rev is not None and market_rev > bound:
            market_rev = bound
            notes.append("market_revenue_clamped")

    total = sum(revenue.values())
    if market_rev is not None and total > 0 and abs(total - market_rev) > 1e-6:
        scale = market_rev / total
        revenue = {c: v * scale for c, v in revenue.items()}
        notes.append("rescaled_to_market_revenue")

    # Final hard guardrail, whatever the LLM said: never above the real total.
    if cap is not None:
        total = sum(revenue.values())
        if total > cap:
            scale = cap / total if total > 0 else 0.0
            revenue = {c: v * scale for c, v in revenue.items()}
    return revenue, notes


def estimate_company_countries(
    llm: GenLLM, market_name: str, company: Company, countries: list[str], enable_web: bool
) -> tuple[dict[str, float], str, list[str]]:
    """Returns (revenue_by_country, source_tag, notes) -- source_tag tells you whether
    this was grounded in real financial data, real web evidence, or neither; notes are
    quality-note codes for `quality.compute_company_quality_flags`.

    Guardrails enforced in CODE, not just by asking the LLM nicely:
    - No real data at all -> the LLM is never called; zeros are returned directly.
    - The country split is rescaled to the LLM's own stated market revenue, which is
      clamped to market_fraction x the real USD total when that is known
      (`reconcile_market_slice`) -- and never exceeds the real total, no matter what.
    """
    grounding_text, source, cap, notes = _ground_company(market_name, company, enable_web)

    if source == "no_real_data":
        return {c: 0.0 for c in countries}, "no_real_data", notes

    facts_line = ", ".join(f"{k}: {v}" for k, v in company.facts.items()) or "none"
    system = _GROUNDED_SYSTEM_TMPL.format(
        market=market_name, countries=", ".join(countries),
        max_year=MAX_REVENUE_YEAR, prev_year=MAX_REVENUE_YEAR - 1, next_year=MAX_REVENUE_YEAR + 1,
    )
    user = (
        f"Market: {market_name}\nCompany: {company.name}\nKnown facts: {facts_line}\n\n{grounding_text}"
    )
    data = llm.complete_json(system, user)
    if data.get("no_real_data"):
        return {c: 0.0 for c in countries}, "no_real_data", notes

    year = _float_or_none(data.get("revenue_year"))
    if source == "web_evidence" and year is not None and year > MAX_REVENUE_YEAR:
        # A 2026+ figure slipped through despite the prompt -- never use it.
        return {c: 0.0 for c in countries}, "no_real_data", notes + [f"revenue_year_after_base:{int(year)}"]
    if year is not None:
        notes = notes + [f"revenue_year:{int(year)}"]

    raw = data.get("country_revenue_usd_mn") or {}
    revenue = {c: max(0.0, _float_or_none(raw.get(c)) or 0.0) for c in countries}
    revenue, slice_notes = reconcile_market_slice(revenue, data, cap)
    return revenue, source, notes + slice_notes


def estimate_coverage_by_country(
    llm: GenLLM, market_name: str, countries: list[str], named_totals: dict[str, float]
) -> dict[str, float]:
    """One LLM call covering every country's coverage fraction at once."""
    lines = "\n".join(f"- {c}: {named_totals.get(c, 0.0):.1f} USD Mn" for c in countries)
    user = f"Market: {market_name}\nCountries and named-supplier combined revenue:\n{lines}"
    data = llm.complete_json(COVERAGE_SYSTEM, user)
    raw = data.get("coverage_fraction") or {}
    return {
        c: min(COVERAGE_CEILING, max(COVERAGE_FLOOR, _float_or_none(raw.get(c)) or 0.6))
        for c in countries
    }

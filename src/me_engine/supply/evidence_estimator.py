"""Evidence-based bottom-up estimation — a second technique alongside the
company-based one (`estimator.py`/`graph.py`), per mentor's plan:

    1. Real content gathering (ddgs) — news/journal/trade-publication text
       about the market. Each snippet is a real data point, not an LLM guess.
    2. Embedding-based relevance filtering (sentence-transformers, already
       used for Neon DB semantic search) — reject snippets that are only
       loosely related (fixes the "Popping Candy vs. whole Candy Market"
       scope-mismatch failure mode seen in the demand-side pipeline).
    3. Real-number extraction with regex, not the LLM — pull out explicitly
       stated market-size/CAGR figures directly from the real text.
    4. Google Trends as a real, quantifiable public-attention signal — not
       text sentiment (no free/working text-sentiment source was found:
       podcasts and Reddit were both tested and ruled out for these niche
       B2B markets) — used to nudge CAGR, not invent the base number.
    5. Deterministic aggregation (median across sources) into one bottom-up
       total, with every source's contribution logged for transparency.

No LLM call anywhere in this path. If no real figures are found, that is
returned honestly (empty result) rather than an invented number.
"""
from __future__ import annotations

import re
import statistics
import time
from dataclasses import dataclass, field

from ..generation.semantic_search import embed
from . import web_search

_VALUE_RE = re.compile(
    r"(?:USD|US\$|\$)\s*([\d,]+(?:\.\d+)?)\s*(trillion|billion|million|bn|mn)\b", re.IGNORECASE
)
_UNIT_TO_MN = {"trillion": 1_000_000, "billion": 1_000, "million": 1, "bn": 1_000, "mn": 1}
_CAGR_RE = re.compile(
    r"CAGR[^%\n]{0,40}?([\d]+(?:\.\d+)?)\s*%|([\d]+(?:\.\d+)?)\s*%[^%\n]{0,20}?CAGR", re.IGNORECASE
)

_RELEVANCE_THRESHOLD = 0.35  # cosine similarity, MiniLM embeddings, short snippets


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))  # already L2-normalised by embed()


@dataclass(frozen=True, slots=True)
class ExtractedFigure:
    snippet: str
    value_usd_mn: float | None
    cagr_pct: float | None


@dataclass(frozen=True, slots=True)
class EvidenceEstimate:
    market_name: str
    relevant_snippets: list[str]
    figures: list[ExtractedFigure]
    total_usd_mn: float | None
    cagr: float | None
    trends_note: str
    has_real_data: bool


def gather_relevant_evidence(market_name: str, cap: int = 20) -> list[str]:
    """Step 1 + 2: real web search, then embedding-based relevance filter."""
    queries = [
        f"{market_name} size forecast",
        f"{market_name} CAGR growth",
        f"{market_name} industry report",
    ]
    snippets = web_search.gather(queries, per_query=6, cap=cap)
    if not snippets:
        return []

    market_vec = embed(market_name)
    relevant = []
    for s in snippets:
        try:
            sim = _cosine(market_vec, embed(s))
        except Exception:  # noqa: BLE001
            continue
        if sim >= _RELEVANCE_THRESHOLD:
            relevant.append(s)
    return relevant


def extract_real_figures(snippets: list[str]) -> list[ExtractedFigure]:
    """Step 3: deterministic regex extraction, no LLM call."""
    out = []
    for s in snippets:
        vm = _VALUE_RE.search(s)
        value = float(vm.group(1).replace(",", "")) * _UNIT_TO_MN[vm.group(2).lower()] if vm else None
        cm = _CAGR_RE.search(s)
        cagr = None
        if cm:
            raw = cm.group(1) or cm.group(2)
            cagr = float(raw) / 100.0
        if value is not None or cagr is not None:
            out.append(ExtractedFigure(snippet=s, value_usd_mn=value, cagr_pct=cagr))
    return out


def google_trends_modifier(market_name: str, retries: int = 2) -> tuple[float, str]:
    """Step 4: real public search-interest trend -> small CAGR nudge (not a
    market-size source). Returns (delta, note); delta is added to the CAGR."""
    try:
        from pytrends.request import TrendReq
    except Exception:  # noqa: BLE001
        return 0.0, "pytrends not available"

    for attempt in range(retries):
        try:
            pytrends = TrendReq(hl="en-US", tz=360)
            pytrends.build_payload([market_name], timeframe="today 12-m")
            df = pytrends.interest_over_time()
            if df.empty or market_name not in df.columns:
                return 0.0, "no Google Trends data found"
            series = df[market_name].tolist()
            if len(series) < 8:
                return 0.0, "insufficient Google Trends history"
            recent = statistics.mean(series[-4:])
            prior = statistics.mean(series[-8:-4])
            if prior <= 0:
                return 0.0, "flat/zero prior trend baseline"
            pct_change = (recent - prior) / prior
            delta = max(-0.02, min(0.02, pct_change * 0.05))
            note = f"Google Trends interest {'up' if delta >= 0 else 'down'} {abs(pct_change):.0%} recently -> CAGR {delta:+.3f}"
            return delta, note
        except Exception as exc:  # noqa: BLE001
            if attempt < retries - 1:
                time.sleep(15)
                continue
            return 0.0, f"Google Trends failed: {exc}"
    return 0.0, "Google Trends failed"


def estimate_from_evidence(market_name: str) -> EvidenceEstimate:
    """Step 5: gather -> filter -> extract -> aggregate deterministically."""
    relevant = gather_relevant_evidence(market_name)
    figures = extract_real_figures(relevant)

    values = [f.value_usd_mn for f in figures if f.value_usd_mn is not None]
    cagrs = [f.cagr_pct for f in figures if f.cagr_pct is not None]

    total = statistics.median(values) if values else None
    cagr = statistics.median(cagrs) if cagrs else None

    trends_delta, trends_note = google_trends_modifier(market_name)
    if cagr is not None:
        cagr = round(cagr + trends_delta, 4)

    return EvidenceEstimate(
        market_name=market_name,
        relevant_snippets=relevant,
        figures=figures,
        total_usd_mn=total,
        cagr=cagr,
        trends_note=trends_note,
        has_real_data=total is not None,
    )

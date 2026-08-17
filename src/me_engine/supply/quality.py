"""Sanity flags for the two supply-side/bottom-up techniques.

Same purpose as generation/quality.py on the Desk Research side: surface a known
failure mode as a visible warning instead of a confident-looking number that
happens to be wrong. Each flag maps to a real case we hit and diagnosed
while testing this pipeline (see the message text for which one).
"""
from __future__ import annotations

from typing import Any

_LOW_EXTRACTION_RATIO = 0.4  # figures extracted / relevant snippets found
_MIN_FIGURES = 5

# Thresholds were empirically raised from an initial 0.3/3 after batch-testing:
# "Global Cloud Computing Market" hit ratio 0.33 (4/12 figures) and slipped through
# unflagged at the old 0.3 cutoff, yet its total ($515.2B) was ~2x too low against
# real published estimates ($906B-$1.2T). Same failure family as the Smartphone bug
# (3/15, $105-421B vs real $556-758B) which the old thresholds did catch. Raising
# both thresholds trades a few false-positive flags on genuinely fine runs for not
# missing this failure mode again.


def compute_evidence_quality_flags(est: Any) -> list[dict[str, str]]:
    """`est` is an evidence_estimator.EvidenceEstimate."""
    flags: list[dict[str, str]] = []

    if not est.has_real_data:
        flags.append({
            "code": "no_real_data",
            "severity": "critical",
            "message": "No explicitly cited market-size figure was found anywhere in the gathered "
                       "evidence -- there is no evidence-based estimate for this market.",
        })
        return flags

    n_snippets = len(est.relevant_snippets)
    n_figures = len(est.figures)
    if n_snippets > 0 and (n_figures / n_snippets) < _LOW_EXTRACTION_RATIO:
        flags.append({
            "code": "low_extraction_yield",
            "severity": "critical",
            "message": f"Only {n_figures} of {n_snippets} relevant snippets yielded a real figure -- "
                       "the regex extractor likely missed figures phrased differently than expected "
                       "(confirmed on 'Global Smartphone Market': 3/15 extracted, total came out 5-7x "
                       "too low vs published estimates). Treat this total with real caution.",
        })
    if n_figures < _MIN_FIGURES:
        flags.append({
            "code": "small_sample",
            "severity": "warning",
            "message": f"Only {n_figures} real figures went into the median -- a small sample can be "
                       "skewed by a single outlier or mis-scoped report.",
        })
    if est.trends_note.startswith("Google Trends failed") or "not available" in est.trends_note:
        flags.append({
            "code": "trends_unavailable",
            "severity": "info",
            "message": "The Google Trends CAGR nudge didn't run -- CAGR is based on cited figures alone.",
        })
    return flags


def compute_company_quality_flags(per_source: dict[str, str], grand_total: float) -> list[dict[str, str]]:
    n = len(per_source)
    if n == 0:
        return []
    n_financial = sum(1 for s in per_source.values() if s == "financial_api")
    if n_financial == 0:
        return [{
            "code": "no_hard_capped_data",
            "severity": "critical",
            "message": "None of the named companies had real financial-API data -- every revenue "
                       "number is an LLM estimate from web snippets with no real ceiling. Confirmed to "
                       "swing more than 2x between identical runs for markets like this (avocado oil: "
                       "318.6M vs 737.0M on back-to-back --discover runs). Do not trust a single run.",
        }]
    if n_financial / n < 0.3:
        return [{
            "code": "mostly_soft_estimates",
            "severity": "warning",
            "message": f"Only {n_financial}/{n} companies had real financial data -- most of this "
                       "total is still LLM-estimated from web snippets, not hard-capped.",
        }]
    return []

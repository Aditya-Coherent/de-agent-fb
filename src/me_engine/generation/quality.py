"""Sanity flags for a finished Desk Research (secondary-research) generation run.

Every flag here corresponds to a real failure mode we've actually hit and
diagnosed (see the specific bugs referenced in each check) -- this is not a
speculative checklist. The point is to surface the same warning signs a human
would have to notice by reading the log line-by-line, so a bad run looks
visibly bad instead of just looking like a normal, confident number.
"""
from __future__ import annotations

from typing import Any

from .cited_values import outside_size_range

# A "global market size" this small or this large is implausible for almost
# any "Global X Market" report -- catches the class of bug where the LLM
# returns a nonsensical anchor (e.g. 3.0) that isn't caught by the grounded/
# ungrounded check because it's *labelled* grounded but is still wrong.
_MIN_PLAUSIBLE_USD_MN = 10.0
_MAX_PLAUSIBLE_USD_MN = 5_000_000.0
_LOW_CONFIDENCE_REWARD = 0.70


def compute_demand_quality_flags(state: dict[str, Any]) -> list[dict[str, str]]:
    """Return [{"code", "severity", "message"}, ...] for a finished run's state.
    Empty list means nothing suspicious was detected -- not a guarantee of
    correctness, just the absence of a known red flag."""
    flags: list[dict[str, str]] = []

    anchor = state.get("global_anchor_2025")
    if anchor is not None and not (_MIN_PLAUSIBLE_USD_MN <= float(anchor) <= _MAX_PLAUSIBLE_USD_MN):
        flags.append({
            "code": "implausible_market_size",
            "severity": "critical",
            "message": f"Global 2025 value (${float(anchor):,.0f}M) is outside a plausible range for "
                       "a global market -- verify before trusting this run.",
        })

    if state.get("market_size_grounded") is False:
        flags.append({
            "code": "ungrounded_market_size",
            "severity": "warning",
            "message": "No explicitly cited market-size figure was found in any evidence -- this "
                       "total is the LLM's own best estimate, not grounded in a real citation.",
        })

    unstable = state.get("market_size_unstable")
    if unstable:
        values = ", ".join(f"${float(v):,.0f}M" for v in unstable.get("values", []))
        flags.append({
            "code": "market_size_unstable",
            "severity": "warning",
            "message": f"The uncited market size changed by more than 2x between passes ({values}); "
                       f"the median ${float(unstable.get('median') or 0):,.0f}M was used -- verify it.",
        })

    cited = state.get("cited_anchor") or {}
    if cited.get("disputed") and cited.get("value_2025_mn"):
        values = ", ".join(f"${float(v['value_2025_mn']):,.0f}M" for v in cited.get("accepted") or [])
        flags.append({
            "code": "cited_values_disputed",
            "severity": "warning",
            "message": f"The cited market sizes do not agree (only one value, or a spread above 2x: {values}); "
                       f"anchor chosen by: {cited.get('method')}.",
        })

    size_range = cited.get("size_range")
    if size_range and anchor is not None and outside_size_range(float(anchor), size_range):
        flags.append({
            "code": "final_value_outside_size_range",
            "severity": "warning",
            "message": f"Global 2025 value (${float(anchor):,.0f}M) is more than 3x outside the independent "
                       f"units x price range (${size_range['low_mn']:,.0f}M-${size_range['high_mn']:,.0f}M).",
        })

    reward = state.get("reward")
    if reward is not None and float(reward) < _LOW_CONFIDENCE_REWARD:
        flags.append({
            "code": "low_confidence",
            "severity": "warning",
            "message": f"Reviewer confidence ({float(reward):.0%}) is below the usual bar -- "
                       "the refine loop didn't converge on a strong pass.",
        })

    db_evidence_used = state.get("db_evidence") is not None
    web_snippets = state.get("web_snippets") or []
    if not db_evidence_used and not web_snippets:
        flags.append({
            "code": "no_evidence_found",
            "severity": "warning",
            "message": "No database match and no live web results were found for this market -- "
                       "every number in this run is the LLM's unaided estimate.",
        })

    return flags

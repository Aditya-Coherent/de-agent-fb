"""Keep driver evidence on an annual, base-year footing.

Two real failures this guards against (Smartphones 2A, 2026-10-07):
  - "China's smartphone shipments came in at roughly 66 million units in the
    second quarter of 2026" was used as China's 2025 ANNUAL demand -- one
    quarter, from the wrong year, so the country came out ~4x too low.

Rules, applied to every driver fact before it is used:
  1. A figure for a year AFTER the base year is rejected (never used).
  2. A figure for one quarter / half-year / month is annualized (x4 / x2 /
     x12) and tagged `derived`, with the reason written into the fact.
Deterministic, no LLM call.
"""
from __future__ import annotations

import re
from typing import Any

from . import evidence

_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
_ANNUAL_RE = re.compile(r"\b(full[- ]year|annual(ly)?|per year|a year|for the year|fy\s?\d{2,4}|year[- ]on[- ]year total)\b", re.I)
# "Quarterly" is how often a source publishes (IDC's "Worldwide Quarterly
# Mobile Phone Tracker"), not the period of the figure -- it once made full-
# year shipments look quarterly and get multiplied by 4. Only a named quarter
# counts.
_PERIODS: tuple[tuple[re.Pattern[str], int, str], ...] = (
    (re.compile(r"\b(q[1-4]|(first|second|third|fourth|1st|2nd|3rd|4th|last|latest|this|the|same|"
                r"september|june|march|december) quarter|three months|3 months)\b", re.I), 4, "one quarter"),
    (re.compile(r"\b(h[12]|first half|second half|half[- ]year|six months|6 months)\b", re.I), 2, "half a year"),
    (re.compile(r"\b(per month|monthly|a month|in (january|february|march|april|may|june|july|august|"
                r"september|october|november|december))\b", re.I), 12, "one month"),
)
# A driver whose own unit already carries a time base ("litres per month")
# is a rate, not a period total -- leave it alone.
_UNIT_PERIOD_RE = re.compile(r"\b(per|/)\s*(month|quarter|week|day|year|annum)\b|\bannual\b", re.I)


def fact_year(fact: dict[str, Any]) -> int | None:
    """The year a fact is for: its own `year` field, else the latest year
    named in its citation."""
    try:
        if fact.get("year") not in (None, ""):
            return int(float(fact["year"]))
    except (TypeError, ValueError):
        pass
    years = [int(y) for y in _YEAR_RE.findall(str(fact.get("citation") or ""))]
    return max(years) if years else None


_N_MONTHS_RE = re.compile(
    r"\b(?:past|last|first|over|in|for)\s+(?:the\s+)?(?:(\d{1,2})|(two|three|four|five|six|seven|eight|nine|ten|eleven))"
    r"\s+months\b", re.I)
_NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                 "ten": 10, "eleven": 11}


def sub_annual_period(citation: str, unit: str = "") -> tuple[float, str] | None:
    """(multiplier, label) when the citation states a figure for less than a
    year, else None."""
    if not citation or _UNIT_PERIOD_RE.search(unit or "") or _ANNUAL_RE.search(citation):
        return None
    m = _N_MONTHS_RE.search(citation)
    if m:
        n = int(m.group(1)) if m.group(1) else _NUMBER_WORDS[m.group(2).lower()]
        if 1 <= n < 12:
            return 12 / n, f"{n} months"
    for pattern, factor, label in _PERIODS:
        if pattern.search(citation):
            return factor, label
    return None


def check_driver_fact(
    fact: dict[str, Any] | None, base_year: int | None, unit: str = "",
) -> tuple[dict[str, Any] | None, str | None]:
    """(fact to use or None if rejected, note for the log or None)."""
    if not fact:
        return fact, None
    year = fact_year(fact)
    if base_year and year and year > int(base_year):
        return None, (
            f"rejected a {year} figure ({fact.get('value')} {unit}) -- only figures up to the "
            f"base year {base_year} are used"
        )
    citation = str(fact.get("citation") or "")
    period = sub_annual_period(citation, unit)
    if period is None:
        return fact, None
    factor, label = period
    try:
        value = float(fact.get("value") or 0.0) * factor
    except (TypeError, ValueError):
        return fact, None
    status = evidence.worse_of(fact.get("evidence_type") or "assumption", "derived")
    basis = f"annualized: the source figure covers {label} ('{citation[:120]}'), x{factor:g} to a full year"
    out = {
        **fact,
        "value": value,
        "evidence_type": status,
        "verification_status": status,
        "derivation_basis": (str(fact.get("derivation_basis") or "") + " " + basis).strip(),
        "annualized_from": {"value": fact.get("value"), "period": label, "factor": factor},
    }
    return out, f"{basis} -> {value:,.0f} {unit}"

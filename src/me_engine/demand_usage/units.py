"""Deterministic unit-contract parser/validator for the calculation layer.

No LLM call anywhere in this module. The LLM proposes unit strings freely
upstream (`demand_driver_node`, `pricing_node`, etc.) -- this module is the
code-level gate that decides whether a proposed unit string is trustworthy
enough to enter arithmetic at all. If it can't be unambiguously parsed, the
caller must reject the bucket, never invent a fallback interpretation.

Deliberately out of scope (Tier 1): converting between genuinely different
physical bases (e.g. barrels <-> tonnes via density). This module only
validates CONSISTENCY of a unit string against itself and against a sibling
unit string in the same bucket's chain -- it never performs cross-dimensional
conversion.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_SCALE_WORDS: dict[str, float] = {
    "trillion": 1e12, "billion": 1e9, "million": 1e6, "thousand": 1e3,
    "hundred": 1e2, "kilo": 1e3, "mega": 1e6, "giga": 1e9, "tera": 1e12,
    "bn": 1e9, "mn": 1e6,
}

_PERIOD_WORDS = {
    "day", "days", "daily", "month", "months", "monthly",
    "year", "years", "yearly", "annum", "annual", "annually",
}
_PERIOD_NORMALIZE = {
    "day": "day", "days": "day", "daily": "day",
    "month": "month", "months": "month", "monthly": "month",
    "year": "year", "years": "year", "yearly": "year",
    "annum": "year", "annual": "year", "annually": "year",
}

# Word-bounded tokens use \b normally; the symbol form ($) is matched without
# a trailing \b -- $ is a non-word character, so a \b right after it never
# matches when followed by another non-word character (e.g. a space), which
# silently let "US$ per barrel" slip through undetected.
_CURRENCY_RE = re.compile(r"\b(usd|currency|dollars?|eur|gbp|inr)\b|us\$|\$", re.IGNORECASE)
_COMPOUND_AND_RE = re.compile(r"\band\b", re.IGNORECASE)
_TRAILING_PER_RE = re.compile(r"\bper\s+([a-zA-Z]+)\s*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ParsedUnit:
    scale: float          # e.g. 1e6 for a leading "million"; 1.0 if no scale word
    base_unit: str        # normalized noun phrase, e.g. "tonnes", "barrels", "gw"
    per_period: str | None  # "day" | "month" | "year" | None (only if EXPLICITLY stated)
    raw: str               # original string, kept for the audit trail


def _normalize_base(text: str) -> str:
    """Lowercase, strip punctuation noise, collapse whitespace -- for equality
    comparison only, not physical-dimension analysis."""
    t = text.strip().lower()
    t = re.sub(r"[^a-z0-9\s\-]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def parse_unit_string(raw: str | None) -> ParsedUnit | None:
    """Deterministic grammar: [scale-word] <base-unit-noun> [per <period>].

    Returns None (unparseable / must be rejected by the caller) when the
    string is empty, currency-tainted, or shows a compound/ambiguous-unit
    signal -- multiple distinct concepts joined by 'and', a comma, a
    semicolon, or more than one '/'. Examples:

        "million tonnes per year"        -> ParsedUnit(1e6, "tonnes", "year", ...)
        "barrels per year"                -> ParsedUnit(1.0, "barrels", "year", ...)
        "vehicle-kilometers per year"      -> ParsedUnit(1.0, "vehicle-kilometers", "year", ...)
        "liters per vehicle"               -> ParsedUnit(1.0, "liters per vehicle", None, ...)
        "GW and million units"             -> None (compound signal: 'and')
        "USD per tonne"                    -> None (currency)
        ""                                  -> None
    """
    if not raw or not raw.strip():
        return None
    text = raw.strip()

    if _CURRENCY_RE.search(text):
        return None
    if _COMPOUND_AND_RE.search(text):
        return None
    if text.count(",") > 0 or text.count(";") > 0:
        return None
    if text.count("/") > 1:
        return None

    # Split off a trailing "per <period>" clause -- a TIME BASIS, never part
    # of the base-unit noun, and must be recognized explicitly (never
    # inferred) so a day/year-type mismatch can actually be detected later.
    per_period: str | None = None
    body = text
    m = _TRAILING_PER_RE.search(text)
    if m:
        candidate = m.group(1).lower()
        if candidate in _PERIOD_WORDS:
            per_period = _PERIOD_NORMALIZE[candidate]
            body = text[: m.start()].strip()
        # else: "per X" where X isn't a recognized period word (e.g. "per
        # vehicle", "per household") -- that's an ENTITY basis, legitimately
        # part of a rate driver's own unit (e.g. "liters per vehicle"), so
        # leave it inside the base-unit noun rather than stripping it.

    if not body:
        return None

    tokens = body.split()
    scale = 1.0
    if tokens and tokens[0].lower() in _SCALE_WORDS:
        scale = _SCALE_WORDS[tokens[0].lower()]
        tokens = tokens[1:]
    if not tokens:
        return None  # a bare scale word with nothing left isn't a real unit

    base_unit = _normalize_base(" ".join(tokens))
    if not base_unit:
        return None
    return ParsedUnit(scale=scale, base_unit=base_unit, per_period=per_period, raw=raw)


def units_share_base(a: ParsedUnit, b: ParsedUnit) -> bool:
    """Same normalized base-unit noun (ignoring scale/period) -- a light,
    string-level equivalence check, NOT a physical-dimension conversion
    (deliberately does NOT know barrels == tonnes)."""
    return a.base_unit == b.base_unit


def periods_conflict(a: ParsedUnit, b: ParsedUnit) -> bool:
    """True only when BOTH sides state an explicit, different period.
    Absence of a stated period on either side is never treated as a
    conflict -- that would be inferring, not validating."""
    return bool(a.per_period and b.per_period and a.per_period != b.per_period)

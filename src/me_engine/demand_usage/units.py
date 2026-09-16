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


# --------------------------------------------------------------------------- #
# Correctness-audit addendum (2026-08-26, `CEMENT_FORENSIC_AUDIT.md` finding
# A-3/C): a driver's `final_demand_unit` sometimes stays labeled as the RAW
# driver's own unit (e.g. "housing units") even after `consumption_unit`
# (e.g. "metric tons of cement per housing unit") has already converted the
# quantity into a DIFFERENT physical unit (tons). Downstream, `pricing_node`
# trusts that stale label and invents a SECOND, redundant conversion factor
# to bridge back to the same target unit the first conversion already
# reached -- a silent double-conversion. This is a deterministic, code-level
# reconciliation of the LABEL only -- it changes no arithmetic, no evidence,
# no driver/intensity VALUE, only which unit string is attached to the
# already-computed quantity.
# --------------------------------------------------------------------------- #
_PER_SPLIT_RE = re.compile(r"\s+per\s+", re.IGNORECASE)


def intensity_target_unit(consumption_unit: str | None) -> str | None:
    """Extracts the NUMERATOR of a rate-shaped `consumption_unit` string
    (e.g. "metric tons of cement per housing unit" -> "metric tons of
    cement"). Returns None for a non-rate-shaped or empty string -- callers
    must then leave `final_demand_unit` exactly as given (never guess)."""
    if not consumption_unit or not consumption_unit.strip():
        return None
    parts = _PER_SPLIT_RE.split(consumption_unit.strip(), maxsplit=1)
    if len(parts) != 2:
        return None
    numerator = parts[0].strip()
    return numerator or None


def reconcile_final_demand_unit(
    final_demand_unit: str, driver_unit: str, consumption_unit: str,
) -> tuple[str, bool]:
    """Returns (possibly-corrected final_demand_unit, was_corrected).

    Only acts when ALL of these hold, so it can never silently relabel a
    driver that genuinely needs no conversion:
      1. `consumption_unit` is a rate-shaped "<X> per <Y>" string (an
         intensity factor is actually being applied -- see
         `demand_quantification_node`'s `conversion.apply_conversion`).
      2. The CURRENT `final_demand_unit` still matches the RAW driver's own
         unit (`driver_unit`) -- i.e. it was never updated to reflect the
         conversion that `consumption_unit` describes.
      3. The intensity's own numerator (X) is a DIFFERENT unit than the raw
         driver unit -- otherwise there's nothing to reconcile.

    When all three hold, `final_demand_unit` is corrected to X (the
    intensity's numerator) -- the physically correct label for
    `driver_value * intensity_value`. Never invents a value, never changes
    `driver_unit`/`consumption_unit`/`conversion_formula` themselves."""
    target = intensity_target_unit(consumption_unit)
    if target is None:
        return final_demand_unit, False
    if _normalize_base(final_demand_unit or "") != _normalize_base(driver_unit or ""):
        return final_demand_unit, False  # already diverges from the raw driver unit -- leave alone
    if _normalize_base(target) == _normalize_base(final_demand_unit or ""):
        return final_demand_unit, False  # already correct
    return target, True


# --------------------------------------------------------------------------- #
# Correctness-audit addendum (2026-08-26, `CEMENT_FORENSIC_AUDIT.md` finding
# F): a monetary fact extracted with a requested canonical unit (e.g.
# "USD Mn") sometimes comes back with its `value` ALREADY fully expanded to
# raw dollars (e.g. 425,500,000,000) but its `unit` field still carrying the
# SOURCE's own scale word (e.g. "USD Bn", from "$425.50 Billion") -- an
# internally inconsistent value/unit pair. Deterministic, rule-based
# normalization: no LLM call, no hardcoded market-specific number.
# --------------------------------------------------------------------------- #
_MONETARY_SCALE_TO_MN: dict[str, float] = {
    "tn": 1_000_000.0, "trillion": 1_000_000.0,
    "bn": 1_000.0, "billion": 1_000.0,
    "mn": 1.0, "million": 1.0,
    "k": 0.001, "thousand": 0.001,
}
_MONETARY_SCALE_RE = {
    token: re.compile(rf"\b{re.escape(token)}\b", re.IGNORECASE) for token in _MONETARY_SCALE_TO_MN
}
# A well-formed extraction pairs a COMPACT number with its scale word (e.g.
# value=425.50, unit="USD Bn"). A value already at or above this magnitude
# WHILE still carrying a >1x scale word is the signature of the "value was
# already expanded to raw dollars, unit was not adjusted to match" defect --
# not a hardcoded threshold for any one market's benchmark, just the point
# past which "425.50 in billions" stops being a plausible compact figure.
_ALREADY_EXPANDED_THRESHOLD = 1_000_000.0


def normalize_monetary_value(value: float | int | None, unit: str | None) -> tuple[float | None, str]:
    """Returns (value_in_usd_mn, "USD Mn") -- the canonical unit this
    pipeline reports everything else in (`market_unit` default). Handles
    USD / USD Mn / USD Bn / USD Tn (and bare "million"/"billion"/"trillion"/
    "thousand" scale words) in either direction: a normal compact
    value+scale-word pair, OR a value already expanded to raw dollars but
    mislabeled with a scale word. Returns (None, "USD Mn") for a None value
    -- callers must not treat that as zero."""
    if value is None:
        return None, "USD Mn"
    value = float(value)
    u = (unit or "").lower()

    scale = None
    for token, factor in _MONETARY_SCALE_TO_MN.items():
        if _MONETARY_SCALE_RE[token].search(u):
            scale = factor
            break

    if scale is None:
        # No recognized scale word at all (bare "USD"/"US$"/"$") -- treat the
        # value as already being in raw, absolute dollars.
        return value / 1_000_000.0, "USD Mn"

    if scale > 1.0 and abs(value) >= _ALREADY_EXPANDED_THRESHOLD:
        # The "already expanded to raw dollars, unit not adjusted" defect --
        # the scale word is stale; the value itself is already absolute
        # dollars.
        return value / 1_000_000.0, "USD Mn"

    return value * scale, "USD Mn"


# --------------------------------------------------------------------------- #
# Correctness fix (2026-08-28, live Crude Oil run finding): `pricing_node`'s
# price-basis conversion factor is DESIGNED to be expressed as
# "<price_basis_unit> per <final_demand_unit>" (see `_resolve_price_basis`'s
# own prompt, e.g. "barrels of fuel per vehicle-kilometer") so that
# `calculate_node` can multiply it straight in:
#     final_demand_quantity * factor = price_basis_quantity
# The LLM does not always follow this convention -- verified failure mode:
# for Crude Oil's Transportation Fuels bucket, the LLM returned "liters of
# transportation fuel per barrel" (final_unit per price_basis_unit, the
# OPPOSITE direction) while Lubricants and Greases' factor ("tonnes per
# liter", i.e. price_basis_unit per final_unit) was already correct. Because
# only ONE of the two buckets was actually inverted, a blanket "always
# divide instead of multiply" fix would have broken the correct case while
# fixing the inverted one. This function detects the ACTUAL direction from
# the factor's own unit string and returns a value that is always safe to
# multiply by, regardless of which way the LLM phrased it.
# --------------------------------------------------------------------------- #
def _split_rate_unit(unit: str) -> tuple[str, str] | None:
    """Splits a rate-shaped '<X> per <Y>' string into (X, Y). None if not
    rate-shaped."""
    if not unit or not unit.strip():
        return None
    parts = _PER_SPLIT_RE.split(unit.strip(), maxsplit=1)
    if len(parts) != 2:
        return None
    x, y = parts[0].strip(), parts[1].strip()
    return (x, y) if x and y else None


def _singularize(word: str) -> str:
    """Crude, deterministic de-pluralization (strip a trailing 's') -- just
    enough to make 'tonne' match 'tonnes' and 'barrel' match 'barrels' in a
    word-overlap check; never claims true morphological correctness."""
    return word[:-1] if len(word) > 4 and word.endswith("s") else word


def _words_overlap(a: str, b: str) -> bool:
    wa = {_singularize(w) for w in _significant_words_units(a)}
    wb = {_singularize(w) for w in _significant_words_units(b)}
    return bool(wa & wb)


def _significant_words_units(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", (text or "").lower()) if len(w) > 3}


def reconcile_conversion_factor_direction(
    value: float, factor_unit: str, price_basis_unit: str, final_demand_unit: str,
) -> tuple[float, bool]:
    """Returns (value_safe_to_multiply, was_inverted).

    Only inverts (returns `1/value`) when the factor's own unit string is
    UNAMBIGUOUSLY in the wrong direction -- its numerator overlaps
    `final_demand_unit` (not `price_basis_unit`) AND its denominator
    overlaps `price_basis_unit` (not `final_demand_unit`). Any ambiguous,
    partially-matching, or non-rate-shaped case is left UNCHANGED -- this
    must never guess-invert a factor it isn't sure about, since that would
    turn a correct value into a wrong one exactly as easily as the reverse."""
    if not value:
        return value, False
    split = _split_rate_unit(factor_unit)
    if split is None:
        return value, False
    numerator, denominator = split

    numerator_is_final = _words_overlap(numerator, final_demand_unit)
    numerator_is_basis = _words_overlap(numerator, price_basis_unit)
    denominator_is_basis = _words_overlap(denominator, price_basis_unit)
    denominator_is_final = _words_overlap(denominator, final_demand_unit)

    # Already correct (price_basis per final_demand) -- explicitly checked
    # first so an ambiguous unit that happens to ALSO look invertible is
    # never flipped when it's already fine.
    if numerator_is_basis and denominator_is_final:
        return value, False

    # Unambiguously inverted (final_demand per price_basis).
    if numerator_is_final and denominator_is_basis and not (numerator_is_basis or denominator_is_final):
        return (1.0 / value if value else value), True

    return value, False

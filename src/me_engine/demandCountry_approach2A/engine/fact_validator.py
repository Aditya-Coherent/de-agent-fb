"""Evidence Extraction Quality layer (`DEMAND_SIDE_EVIDENCE_PLAN.md` addendum,
post-Phase-12) -- the deterministic "does this candidate actually answer the
requested metric" gate every acquirer's LLM extraction step must pass
through BEFORE a candidate reaches `evidence.build_fact`/
`build_structured_fact`.

Phase 12's cross-market validation showed the acquisition-layer hardening
(Phases 1/3/4/5) worked -- `no_source_found`/`download_failed` collapsed --
but real, successfully-fetched sources still routinely fail to yield a
*correct* number: a real page/PDF/dataset/API response gets found and
opened, and the LLM (or a deterministic column/path pick) then reports a
number that measures something ADJACENT to what was actually requested --
market value in dollars instead of a physical production/consumption
volume, installed capacity instead of production, a different country, a
different year. `evidence.verify_and_tag`'s text-traceability check already
catches "the claimed number literally isn't in the text" (Case C); this
module catches the harder case: "the number IS in the text, and it's a real
number, but it's not an answer to the question that was asked."

Deliberately generic -- every check here is a market-independent keyword/
string comparison, never a market-name lookup table. Deliberately
conservative -- an AMBIGUOUS or unclassifiable input never produces a
rejection, only a clearly-detected, specific mismatch does (mirrors every
other gate in this package's "absence of a signal is not itself a
conflict" discipline, e.g. `units.periods_conflict`,
`magnitude_validation.check_compatibility`). This module never invents a
value, page, table, column, row, citation, year, unit, or geography --
it only decides whether to accept or reject what a deterministic
extraction step (or an LLM, constrained to point at already-fetched real
content) already produced.
"""
from __future__ import annotations

import re

from . import units

# --------------------------------------------------------------------------- #
# Currency / monetary-value signal -- the single cheapest, most reliable
# check: a driver/intensity request almost never wants a dollar figure, so a
# candidate whose OWN stated unit/text carries a currency signal when the
# request did not ask for one is the classic "market value quoted for a
# physical driver" confusion this plan's Phase 12 report calls out by name.
# --------------------------------------------------------------------------- #
_CURRENCY_RE = re.compile(
    r"\b(usd|currency|dollars?|eur|gbp|inr|revenue|market\s*value|market\s*size|sales\s*value)\b"
    r"|us\$|\$",
    re.IGNORECASE,
)


def has_currency_signal(text: str | None) -> bool:
    """True if `text` carries an explicit monetary/currency signal. Pure
    string match, no market-specific vocabulary."""
    return bool(text) and bool(_CURRENCY_RE.search(text))


# --------------------------------------------------------------------------- #
# Generic metric-family classifier -- NEVER a market-specific lookup table.
# A text can match zero, one, or several families; an empty result means
# "not classifiable from keywords alone," which this module always treats as
# "cannot rule out a match" (never a rejection reason on its own).
# --------------------------------------------------------------------------- #
METRIC_FAMILIES: dict[str, tuple[str, ...]] = {
    "monetary_value": (
        "market value", "market size", "revenue", "sales value", "valued at",
        "worth $", "usd billion", "usd million", "in revenue",
    ),
    "growth_rate": (
        "cagr", "growth rate", "% growth", "yoy growth", "year-over-year growth",
        "y-o-y growth", "compound annual growth",
    ),
    "production": (
        "production", "produced", "manufactured", "output of", "extraction volume",
    ),
    "consumption": (
        "consumption", "demand for", "usage of", "consumed", "utilization of",
    ),
    "capacity": (
        "installed capacity", "production capacity", "nameplate capacity",
        "capacity of", "rated capacity",
    ),
    "reserves": (
        "reserves", "proven reserves", "resource estimate", "resource base",
    ),
    "trade": (
        "exports of", "imports of", "trade volume", "export volume", "import volume",
    ),
}

# Pairs of families that are common, real-world adjacent-metric confusions --
# NOT every pair of distinct families conflicts (e.g. "production" and
# "trade" can legitimately co-occur in one sentence), only the pairs known to
# be genuinely different quantities that get mistaken for one another.
# Symmetric: (a, b) implies (b, a) is also a conflict.
_ADJACENT_CONFLICTS: frozenset[frozenset[str]] = frozenset(
    frozenset(pair)
    for pair in (
        ("monetary_value", "production"), ("monetary_value", "consumption"),
        ("monetary_value", "capacity"), ("monetary_value", "reserves"),
        ("monetary_value", "trade"),
        ("growth_rate", "production"), ("growth_rate", "consumption"),
        ("growth_rate", "capacity"), ("growth_rate", "monetary_value"),
        ("production", "capacity"), ("production", "reserves"),
        ("consumption", "capacity"), ("consumption", "reserves"),
    )
)


def classify_metric_families(text: str | None) -> set[str]:
    """Deterministic, pure keyword scan. Returns the set of metric families
    whose keywords appear in `text` -- may be empty (unclassifiable), never
    raises."""
    low = (text or "").lower()
    return {family for family, keywords in METRIC_FAMILIES.items() if any(k in low for k in keywords)}


def metric_family_conflict(requested_text: str | None, candidate_text: str | None) -> tuple[str, str] | None:
    """Compares the metric family implied by the REQUEST against the metric
    family implied by the CANDIDATE's own label/evidence text. Returns the
    conflicting `(requested_family, candidate_family)` pair the first time a
    known adjacent-metric confusion is detected, else `None`. Conservative:
    if either side classifies to nothing, or the families simply don't
    overlap AND aren't a known conflicting pair, this returns `None` --
    ambiguity is never treated as a rejection."""
    requested = classify_metric_families(requested_text)
    candidate = classify_metric_families(candidate_text)
    if not requested or not candidate:
        return None
    for r in requested:
        for c in candidate:
            if r != c and frozenset((r, c)) in _ADJACENT_CONFLICTS:
                return r, c
    return None


def _normalize_geo(geo: str | None) -> str:
    return re.sub(r"\s+", " ", str(geo or "").strip().lower())

_GLOBAL_GEO_ALIASES = {"", "global", "world", "worldwide", "all countries", "international"}


def geography_conflict(requested_geo: str | None, candidate_geo: str | None) -> bool:
    """True only when BOTH sides name a SPECIFIC, different geography --
    a candidate labelled global/world/unspecified is never treated as a
    conflict against a specific requested geography (that is a legitimate
    derivation-basis case, handled by `is_direct_figure`/`derivation_basis`
    upstream, not a mismatch this function should flag)."""
    r, c = _normalize_geo(requested_geo), _normalize_geo(candidate_geo)
    if not r or not c or c in _GLOBAL_GEO_ALIASES or r in _GLOBAL_GEO_ALIASES:
        return False
    return r != c


_YEAR_TOLERANCE = 1  # a stated reporting-lag year (e.g. "2024 data" for a 2025 request) is not a conflict


def year_conflict(requested_year: int | None, matched_year: int | None, tolerance: int = _YEAR_TOLERANCE) -> bool:
    """True only when BOTH sides state an explicit year AND they differ by
    more than `tolerance` -- absence of a stated year on either side is
    never treated as a conflict (mirrors `units.periods_conflict`'s own
    "absence is not a conflict" discipline)."""
    if requested_year is None or matched_year is None:
        return False
    try:
        return abs(int(requested_year) - int(matched_year)) > tolerance
    except (TypeError, ValueError):
        return False


def physical_unit_conflict(requested_unit: str | None, matched_unit: str | None) -> bool:
    """A light, string-level physical-unit sanity check reusing `units.py`'s
    existing deterministic parser (the same one the unit-contract gate
    uses) -- deliberately conservative: only flags a conflict when BOTH
    sides parse successfully into a DIFFERENT base-unit noun, and neither
    base is a substring of the other (so 'barrels' vs 'barrels per day'
    phrasing differences, or an unparseable/absent `matched_unit`, are
    never treated as a conflict -- only a genuinely different physical
    quantity, e.g. 'tonnes' vs 'vehicles', is)."""
    if not requested_unit or not matched_unit:
        return False
    req, got = units.parse_unit_string(requested_unit), units.parse_unit_string(matched_unit)
    if req is None or got is None:
        return False
    if units.units_share_base(req, got):
        return False
    return req.base_unit not in got.base_unit and got.base_unit not in req.base_unit


def validate_candidate(
    *,
    requested_unit: str = "",
    requested_context: str = "",
    requested_geography: str | None = None,
    requested_year: int | None = None,
    matched_unit: str = "",
    matched_geography: str = "",
    matched_metric_label: str = "",
    matched_year: int | None = None,
    evidence_text: str = "",
    is_direct_figure: bool = True,
) -> str | None:
    """The one entry point every acquirer calls after the LLM (or a
    deterministic column/path pick) proposes a candidate, and BEFORE the
    candidate is handed to `evidence.build_fact`/`build_structured_fact`.
    Returns a specific rejection-reason code the first clearly-detected
    mismatch is found, else `None` (the candidate may proceed to the
    ordinary acceptance gate, which still separately verifies the value is
    text-traceable). Never raises, never mutates its inputs.

    Geography and monetary/adjacent-metric checks only apply when
    `is_direct_figure` is true -- a candidate the LLM has explicitly
    declared to be a REGIONAL/GLOBAL figure it intends to derive from
    (`is_direct_figure=False`, with a `derivation_basis`) is expected to
    carry a different geography/label than the target and must not be
    rejected for that reason; `evidence.verify_and_tag` already caps such a
    candidate at `derived`, never `verified_observed`.
    """
    if is_direct_figure:
        if not has_currency_signal(requested_unit) and not has_currency_signal(requested_context):
            label_blob = f"{matched_metric_label} {matched_unit}".strip()
            if has_currency_signal(label_blob):
                return "wrong_metric_monetary_value_for_physical_driver"
            if not label_blob and has_currency_signal(evidence_text[:200]):
                # Neither the LLM's own label nor its declared unit gave us
                # anything to check -- fall back to scanning the evidence
                # text itself, but only when we have NO more specific signal
                # to trust instead.
                return "wrong_metric_monetary_value_for_physical_driver"

        if requested_geography and matched_geography:
            if geography_conflict(requested_geography, matched_geography):
                return "geography_mismatch"

        if year_conflict(requested_year, matched_year):
            return "year_mismatch"

        if physical_unit_conflict(requested_unit, matched_unit):
            return "unit_mismatch"

    conflict = metric_family_conflict(
        requested_context, f"{matched_metric_label} {evidence_text[:300]}",
    )
    if conflict is not None:
        return f"wrong_metric_family:{conflict[0]}_vs_{conflict[1]}"

    return None

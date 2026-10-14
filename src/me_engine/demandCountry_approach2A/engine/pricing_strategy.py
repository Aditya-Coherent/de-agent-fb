"""Phase 8 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) -- Price Basis + Price Evidence
Strategy.

Pricing evidence has been ~0% observed across cross-market runs, and the
root cause is TWO-fold: (1) usage-level ASP requests are often not real
market/commercial price units in the first place (e.g. "USD per
vehicle-kilometer" is not something any commodity exchange or price index
actually publishes), and (2) pricing has never consumed the same explicit,
auditable per-driver Evidence Strategy framework (`evidence_strategy.py`,
Phase 7) that already grounds driver evidence retrieval.

This module is the pricing-specific extension of that framework, sitting
between `agents._resolve_price_basis` (UNCHANGED -- already correctly
distinguishes a real sellable commodity unit from a usage-level one) and
`evidence_router.propose_pricing_evidence_plan`:

    Demand Quantity Unit
        -> _resolve_price_basis            (agents.py, unchanged)
        -> validate_price_basis            (this module, deterministic flag-only)
        -> build_price_evidence_strategy   (this module, deterministic, no LLM)
        -> evidence_router (strategy-aware, same machinery as driver/Phase 7)
        -> verified_observed / source_found_unverified / derived / assumption
        -> normalize_price_record          (this module, deterministic)
        -> Quantity x Price                (calculate_node, unchanged)

Hard discipline (this module is pure Python, no LLM call anywhere in it):
    - Never invents a price basis or a price value -- it only VALIDATES what
      `_resolve_price_basis` already proposed and SHAPES a search strategy
      around it.
    - Never lets a "derived" price fact through if its own derivation text
      reveals it was actually backed out of a market-size or assumed-
      quantity figure -- that would force the market size into the price,
      which is exactly circular and explicitly forbidden by the plan.
"""
from __future__ import annotations

import re
from typing import Any

from . import units

# --------------------------------------------------------------------------- #
# Price-basis validation -- flag, never silently fix.
# --------------------------------------------------------------------------- #

# A curated, DELIBERATELY NARROW vocabulary of units that are genuinely
# established commercial pricing bases in real markets (what a commodity
# exchange, price index, or industry price report actually quotes prices
# in) -- narrower than evidence_strategy.py's own broader physical-unit-noun
# list, which is driver-facing, not price-facing.
_COMMODITY_PRICE_NOUNS = frozenset({
    "barrel", "barrels", "tonne", "tonnes", "ton", "tons", "metric ton", "metric tonne",
    "short ton", "long ton", "kilogram", "kilograms", "kg", "gram", "grams",
    "liter", "liters", "litre", "litres", "gallon", "gallons", "pound", "pounds", "lb", "lbs",
    "bushel", "bushels", "ounce", "ounces", "troy ounce", "carat", "carats",
    "unit", "units", "piece", "pieces", "dozen", "pallet", "container",
    "kwh", "mwh", "gwh", "cubic meter", "cubic meters", "cubic foot", "cubic feet",
    "square meter", "square meters", "square foot", "square feet",
})

# Activity/usage-composite tokens -- the exact anti-pattern the plan calls
# out by name ("USD per vehicle-km", "USD per lane-km"). A price basis whose
# base unit matches this pattern is a strong signal it is NOT a genuinely
# priced commodity, unless it also happens to already be a known commodity
# noun (deliberately impossible in practice, but the check stays symmetric).
_ACTIVITY_COMPOSITE_RE = re.compile(
    r"\b(vehicle|lane|passenger|seat|person|household|capita|ton)s?[\s-]*"
    r"(km|kilometer|kilometre|mile|hour|trip|day)s?\b",
    re.IGNORECASE,
)


def _looks_like_activity_unit(base_unit: str) -> bool:
    return bool(_ACTIVITY_COMPOSITE_RE.search(base_unit or ""))


def validate_price_basis(price_basis: dict[str, Any], *, final_unit: str) -> list[dict[str, str]]:
    """Deterministic, no LLM. Flags (never silently fixes, never blocks) a
    resolved price basis (`agents._resolve_price_basis`'s output, UNCHANGED
    by this function) that looks like an invented usage-level unit rather
    than a genuinely established commercial pricing unit -- the plan's core
    pricing anti-pattern ('USD per vehicle-km', 'USD per lane-km')."""
    flags: list[dict[str, str]] = []
    price_basis_unit = str(price_basis.get("price_basis_unit") or "").strip()
    parsed = units.parse_unit_string(price_basis_unit)
    if not price_basis_unit or parsed is None:
        flags.append({
            "code": "missing_or_unparseable_price_unit", "field": "price_basis_unit",
            "message": f"price_basis_unit '{price_basis_unit}' is missing or not a parseable physical unit.",
        })
        return flags  # nothing further to check against an unparseable unit

    is_known_commodity_noun = parsed.base_unit in _COMMODITY_PRICE_NOUNS
    is_activity_unit = _looks_like_activity_unit(parsed.base_unit) or _looks_like_activity_unit(final_unit)
    if is_activity_unit and not is_known_commodity_noun:
        flags.append({
            "code": "non_commercial_price_basis", "field": "price_basis_unit",
            "message": (
                f"price_basis_unit '{price_basis_unit}' looks like a usage-level activity unit, not a "
                "genuinely established commercial pricing unit -- confirm this market truly prices its "
                "product this way before trusting evidence retrieved against it."
            ),
        })
        if price_basis.get("same_as_final_unit"):
            flags.append({
                "code": "usage_unit_priced_directly", "field": "same_as_final_unit",
                "message": (
                    "same_as_final_unit=True but the final unit is an activity/usage unit with no known "
                    "commercial pricing basis -- a conversion to a real commodity/product unit is expected "
                    "(the price-basis resolution step should have proposed one)."
                ),
            })
    return flags


def validate_price_period(strategy_period: str, final_unit: str) -> list[dict[str, str]]:
    """Deterministic period-consistency check between the Price Evidence
    Strategy's expected reporting period and the demand quantity's own
    final_demand_unit period (units.py, no LLM) -- absence of a stated
    period on either side is never treated as a conflict (mirrors
    `units.periods_conflict`'s own discipline)."""
    flags: list[dict[str, str]] = []
    parsed_final = units.parse_unit_string(final_unit)
    period = (strategy_period or "").strip().lower()
    if parsed_final and parsed_final.per_period and period and period != parsed_final.per_period:
        flags.append({
            "code": "price_period_mismatch", "field": "period",
            "message": (
                f"Price evidence strategy expects a '{period}' price but the demand quantity's "
                f"final unit states period '{parsed_final.per_period}' -- reconcile before multiplying "
                "quantity x price."
            ),
        })
    return flags


# --------------------------------------------------------------------------- #
# Price Evidence Strategy -- SAME schema as evidence_strategy.py so it flows
# through the identical, already-tested `evidence_router._strategy_guidance`
# / `_merge_strategy_sources` machinery. Deterministic, no LLM call: the
# LLM already spoke (via `_resolve_price_basis`) about WHAT the price basis
# is; this function only shapes HOW to search for it.
# --------------------------------------------------------------------------- #
_DEFAULT_PRICE_SOURCE_TYPES = [
    "commodity_exchange", "price_index_provider", "government_statistics", "industry_association",
]
_DEFAULT_PRICE_FALLBACK_TYPES = ["research_consulting", "trade_publication"]

# The plan's explicit anti-pattern, made structural: a price is never
# legitimately "derived" from a market-size or assumed-quantity figure.
_FORBIDDEN_DERIVATION_NOTE = (
    "NEVER derive this price from total market size or an assumed/estimated quantity -- dividing a "
    "market-size figure by a quantity (observed or assumed) just forces that quantity's own error back "
    "into the price and is circular. A 'derived' price is only legitimate when computed from another "
    "independently verified/derived PRICE fact via an explicit, published conversion ratio (e.g. a "
    "per-kg price derived from a verified per-tonne price)."
)


def build_price_evidence_strategy(
    price_basis: dict[str, Any], *, market: str, bucket_name: str, driver_name: str,
) -> dict[str, Any]:
    """Deterministic -- no LLM call. Builds an explicit Price Evidence
    Strategy from the already-resolved price basis, in the SAME field
    schema `evidence_strategy.py` uses for drivers (`STRATEGY_FIELDS`), so
    pricing now consumes the Phase 7 Evidence Strategy framework instead of
    a bespoke, un-auditable path."""
    priced_unit = str(price_basis.get("price_basis_unit") or "unit").strip() or "unit"
    return {
        "driver_name": f"Average selling price of {driver_name}",
        "metric_required": f"Spot/benchmark market price of {priced_unit}",
        "physical_unit": f"USD per {priced_unit}",
        "period": "year",
        "geography": ["global"],
        "primary_source_types": list(_DEFAULT_PRICE_SOURCE_TYPES),
        "preferred_sources": [],
        "search_query_templates": [f"{{country}} {priced_unit} spot price benchmark"],
        "fallback_source_types": list(_DEFAULT_PRICE_FALLBACK_TYPES),
        "fallback_queries": [f"average price of {priced_unit} {market}"],
        "derivation_path": _FORBIDDEN_DERIVATION_NOTE,
        "expected_evidence": (
            f"A commodity exchange quote, published price index value, or industry price report "
            f"stating US$ per {priced_unit}."
        ),
        "why_this_driver_measures_the_usage": str(price_basis.get("rationale") or ""),
        "strategy_source": "deterministic_price_basis",
    }


# --------------------------------------------------------------------------- #
# Circular-derivation rejection -- deterministic, applies to every pricing
# fact (ASP and bridging conversion factor alike) regardless of the
# verification_status the acceptance gate assigned it.
# --------------------------------------------------------------------------- #
_CIRCULAR_PRICE_PATTERNS = re.compile(
    r"market\s*siz|market\s*value|total\s*market|implied\s*price|back[- ]?calculat|"
    r"backed[\s-]*out|divid(?:e|ing)\s*(?:the\s*)?(?:market|revenue)|assumed\s*quantity|"
    r"estimated\s*quantity|revenue\s*(?:divided|/)\s*(?:by\s*)?volume",
    re.IGNORECASE,
)


def is_circular_price_derivation(fact: dict[str, Any] | None) -> bool:
    """Deterministic guard against the plan's explicitly-forbidden pattern:
    a price 'derived' by dividing a market-size/revenue figure by an
    (observed or assumed) quantity. Never trusts the LLM's own
    verification_status label -- scans the actual text every pricing fact
    carries (derivation_basis/citation/evidence_text/reasoning), regardless
    of which tier the acceptance gate assigned it."""
    if not fact:
        return False
    text = " ".join(
        str(fact.get(k) or "") for k in ("derivation_basis", "citation", "evidence_text", "reasoning")
    )
    return bool(_CIRCULAR_PRICE_PATTERNS.search(text))


# --------------------------------------------------------------------------- #
# Deterministic price normalization -- preserves raw + normalized price/unit
# alongside full provenance, per the plan's explicit "Price normalization"
# requirement. Reuses `units.py`'s existing parser on the physical-unit
# remainder only (never on the whole currency-tainted "USD per X" string,
# which `units.parse_unit_string` correctly rejects outright as currency).
# --------------------------------------------------------------------------- #
_PRICE_UNIT_RE = re.compile(r"^\s*(?:usd|us\$|\$)?\s*per\s+(.+)$", re.IGNORECASE)


def parse_price_unit(raw_unit: str) -> tuple[float, str] | None:
    """'USD per <physical unit>' -> (scale, normalized_base_unit), or None
    if the string isn't a parseable '<currency> per <unit>' price string."""
    if not raw_unit or not raw_unit.strip():
        return None
    m = _PRICE_UNIT_RE.match(raw_unit.strip())
    if not m:
        return None
    parsed = units.parse_unit_string(m.group(1))
    if parsed is None:
        return None
    return parsed.scale, parsed.base_unit


def normalize_price_record(
    *, raw_value: float, raw_unit: str, source: str, source_url: str, year: Any,
    geography: str, verification_status: str, conversion_formula: str = "",
) -> dict[str, Any]:
    """Deterministic price normalization (spec: 'Price normalization') --
    preserves raw price/unit, a normalized per-single-base-unit price/unit,
    and full provenance (source, year, geography, verification status,
    conversion formula if derived). Never invents a value: an unparseable
    raw_unit is reported as such (`normalization_status`), not silently
    defaulted to 'ok'."""
    parsed = parse_price_unit(raw_unit)
    base_record = {
        "raw_price": raw_value, "raw_unit": raw_unit,
        "source": source, "source_url": source_url, "year": year,
        "geography": geography, "verification_status": verification_status,
        "conversion_formula": conversion_formula,
    }
    if parsed is None:
        return {
            **base_record,
            "normalized_price": None, "normalized_unit": None,
            "normalization_status": "unparseable_price_unit",
        }
    scale, base_unit = parsed
    normalized_price = round(raw_value / scale, 10) if scale else raw_value
    return {
        **base_record,
        "normalized_price": normalized_price, "normalized_unit": f"USD per {base_unit}",
        "normalization_status": "ok",
    }

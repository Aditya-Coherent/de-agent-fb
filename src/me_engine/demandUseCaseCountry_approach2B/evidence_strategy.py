"""Phase 7 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) -- explicit, auditable per-driver
Evidence Strategy.

Before evidence retrieval begins, every approved Geography x Usage x Demand
Driver combination gets an explicit, persisted STRATEGY describing exactly
what factual quantity the system is trying to find and how it should search
for it -- not just a flat ranked source list (that's what
`evidence_router.propose_*_evidence_plan` already does, at RETRIEVAL time).
This module is the STRATEGY layer that sits one step upstream of it, visible
to the user at taxonomy-approval time, before any search/download happens.

Hard discipline (this module is pure Python, no LLM call anywhere in it):
    - The LLM (in `agents.demand_driver_node`) proposes the strategy's text
      fields in the SAME call that already proposes the driver itself -- no
      extra round-trip.
    - This module only NORMALIZES what the LLM returned (never invents a
      numeric driver value, never invents a conversion factor, never
      declares an input "observed") and VALIDATES it deterministically,
      flagging problems for human review rather than silently fixing them.
    - A missing/malformed strategy is an enrichment, not a hard requirement
      -- absence must never block taxonomy approval or sizing (see
      `default_strategy_for_reused_driver` / `empty_strategy`).
"""
from __future__ import annotations

import re
from typing import Any

from . import units

try:  # pragma: no cover -- exercised indirectly; embedding is best-effort only
    from me_engine.generation.semantic_search import embed
except Exception:  # noqa: BLE001
    embed = None  # type: ignore[assignment]

# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
STRATEGY_FIELDS = (
    "driver_name",
    "metric_required",
    "physical_unit",
    "period",
    "geography",
    "primary_source_types",
    "preferred_sources",
    "search_query_templates",
    "fallback_source_types",
    "fallback_queries",
    "derivation_path",
    "expected_evidence",
    "why_this_driver_measures_the_usage",
)
# Fields the LLM is allowed to leave empty without triggering a hard
# validation flag (search/fallback breadth is a "the more the better", not a
# "must be present" requirement) -- everything else is checked in
# `validate_evidence_strategy`.
_LIST_FIELDS = (
    "geography", "primary_source_types", "preferred_sources", "search_query_templates",
    "fallback_source_types", "fallback_queries",
)
_TEXT_FIELDS = (
    "driver_name", "metric_required", "physical_unit", "period",
    "derivation_path", "expected_evidence", "why_this_driver_measures_the_usage",
)

_RECOGNIZED_PERIODS = {"day", "daily", "month", "monthly", "quarter", "quarterly", "year", "annual", "annually"}
_SUPPORTED_DERIVATION_MARKERS = (
    "population_share", "driver_value", "identity", "regional_allocation",
    "no conversion", "same as driver", "same unit", "direct observation",
)
_LOW_RELEVANCE_SIMILARITY = 0.15
# A small, curated vocabulary of common physical-quantity nouns -- used ONLY
# to detect a derivation path that explicitly names a DIFFERENT unit family
# than the driver's own physical_unit (e.g. "convert tonnes to barrels").
# Deliberately NOT `units.parse_unit_string` on arbitrary tokens -- that
# parser is intentionally lenient (it accepts almost any noun phrase as a
# "unit" so it never misses a genuine unit string upstream), which would
# make ordinary prose words in a derivation_path (e.g. "observed", "daily")
# false-positive as a "different unit."
_KNOWN_PHYSICAL_UNIT_NOUNS = frozenset({
    "barrels", "barrel", "tonnes", "tons", "ton", "tonne", "kilograms", "kilogram", "kg",
    "liters", "liter", "litres", "litre", "gallons", "gallon", "cubic meters", "cubic feet",
    "kwh", "mwh", "gwh", "watts", "megawatts", "gigawatts", "joules", "pounds", "lbs",
    "ounces", "bushels", "vehicles", "units", "pieces", "containers", "square meters",
    "square feet", "acres", "hectares", "meters", "kilometers", "miles", "gigajoules",
})


def _as_str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    return [s for s in (str(v).strip() for v in value) if s]


def _country_templated(template: str) -> str:
    """Every source_query_template the evidence router dispatches on must
    carry a literal '{country}' placeholder (see `evidence_router`'s own
    acceptance filter) -- a strategy-authored template that just says
    'country + road fuel consumption + year' in prose (the plan's own
    example) is transformed, never silently dropped."""
    if "{country}" in template:
        return template
    if re.search(r"\bcountry\b", template, re.IGNORECASE):
        return re.sub(r"\bcountry\b", "{country}", template, count=1, flags=re.IGNORECASE)
    return f"{{country}} {template}".strip()


def empty_strategy(driver_name: str, unit: str, countries: list[str]) -> dict[str, Any]:
    """Absence of a strategy must never block anything downstream -- this is
    the explicit "(no explicit strategy recorded)" shape, never a crash."""
    return {
        "driver_name": driver_name,
        "metric_required": "",
        "physical_unit": unit,
        "period": "",
        "geography": list(countries),
        "primary_source_types": [],
        "preferred_sources": [],
        "search_query_templates": [],
        "fallback_source_types": [],
        "fallback_queries": [],
        "derivation_path": "",
        "expected_evidence": "",
        "why_this_driver_measures_the_usage": "",
        "strategy_source": "missing",
    }


def default_strategy_for_reused_driver(
    lib_entry: dict[str, Any], *, driver_name: str, unit: str, countries: list[str],
) -> dict[str, Any]:
    """Deterministic strategy for a library-reused driver -- no extra LLM
    call (only the driver METHODOLOGY was reused from `driver_library`, not
    an evidence chain), built entirely from that library entry's own already
    -persisted fields so it is never blank, and clearly marked as reused
    rather than freshly reasoned about."""
    required_evidence = [str(x) for x in (lib_entry.get("required_evidence") or []) if str(x).strip()]
    return {
        "driver_name": driver_name,
        "metric_required": driver_name,
        "physical_unit": unit,
        "period": "",
        "geography": list(countries),
        "primary_source_types": ["government_statistics", "international_organization"],
        "preferred_sources": [],
        "search_query_templates": [_country_templated(f"{driver_name} {unit}")],
        "fallback_source_types": ["industry_association", "research_consulting"],
        "fallback_queries": required_evidence,
        "derivation_path": lib_entry.get("conversion_formula", "") or "driver_value",
        "expected_evidence": "; ".join(required_evidence),
        "why_this_driver_measures_the_usage": (
            "Reused from a previously-derived driver methodology for a semantically similar usage bucket "
            f"(driver_library match similarity {lib_entry.get('match_similarity', 'n/a')})."
        ),
        "strategy_source": "reused_library_default",
    }


def extract_strategy_from_driver_response(
    data: dict[str, Any], *, driver_name: str, unit: str, countries: list[str],
) -> dict[str, Any]:
    """Pull the `evidence_strategy` sub-object out of the SAME LLM JSON
    payload `demand_driver_node` already parses for the driver itself,
    normalizing every field deterministically -- never trusting the LLM's
    field shapes/types blindly."""
    raw = data.get("evidence_strategy") if isinstance(data, dict) else None
    raw = raw if isinstance(raw, dict) else {}

    geography = _as_str_list(raw.get("geography")) or list(countries)
    search_query_templates = [_country_templated(t) for t in _as_str_list(raw.get("search_query_templates"))]
    fallback_queries = _as_str_list(raw.get("fallback_queries"))

    return {
        "driver_name": _as_str(raw.get("driver_name")) or driver_name,
        "metric_required": _as_str(raw.get("metric_required")),
        "physical_unit": _as_str(raw.get("physical_unit")) or unit,
        "period": _as_str(raw.get("period")).lower(),
        "geography": geography,
        "primary_source_types": _as_str_list(raw.get("primary_source_types")),
        "preferred_sources": _as_str_list(raw.get("preferred_sources")),
        "search_query_templates": search_query_templates,
        "fallback_source_types": _as_str_list(raw.get("fallback_source_types")),
        "fallback_queries": fallback_queries,
        "derivation_path": _as_str(raw.get("derivation_path")),
        "expected_evidence": _as_str(raw.get("expected_evidence")),
        "why_this_driver_measures_the_usage": _as_str(raw.get("why_this_driver_measures_the_usage")),
        "strategy_source": "llm_proposed",
    }


# --------------------------------------------------------------------------- #
# Prompt fragments -- appended to `demand_driver_node`'s EXISTING system/user
# prompt so the strategy is proposed in the SAME LLM call as the driver
# itself (plan's own explicit instruction: avoid an extra round-trip).
# --------------------------------------------------------------------------- #
def strategy_system_fragment() -> str:
    return (
        "\n\nYou must ALSO propose an explicit EVIDENCE STRATEGY for this driver -- what exact factual "
        "quantity to look for and how to search for it -- as a nested 'evidence_strategy' object in the "
        "SAME JSON response. This is a SEARCH PLAN, not a market-size estimate: never put a numeric "
        "driver value or a conversion factor inside it. Fields:\n"
        "  - metric_required: the exact physical quantity to find (e.g. 'annual physical fuel consumption')\n"
        "  - physical_unit: its unit (e.g. 'barrels/year') -- must match the driver's own unit family, never currency\n"
        "  - period: the reporting period this metric is normally published for -- exactly one of "
        "'day'|'month'|'quarter'|'year' (never a vague/compound answer)\n"
        "  - primary_source_types: 2-4 source CLASSES most likely to publish this (e.g. "
        "'national_statistics', 'international_organization', 'industry_association', 'commodity_exchange')\n"
        "  - preferred_sources: 1-4 REAL, named institutions within those classes (e.g. 'EIA', 'IEA', 'USGS')\n"
        "  - search_query_templates: 1-3 concrete search queries, each including the literal placeholder "
        "'{country}' (e.g. '{country} road fuel consumption barrels per year')\n"
        "  - fallback_source_types: source classes to try only if every primary source fails\n"
        "  - fallback_queries: broader queries to try in that fallback case (e.g. a regional/global total "
        "query, to be allocated via population share -- never invent a different conversion factor here)\n"
        "  - derivation_path: IN WORDS, how a value could legitimately be derived if no direct figure "
        "exists (e.g. 'observed daily consumption x 365', or 'regional total x population share') -- "
        "must stay within the driver's own physical unit family, never silently switch units\n"
        "  - expected_evidence: what a successful piece of evidence for this metric actually looks like\n"
        "  - why_this_driver_measures_the_usage: 1-2 sentences on why this metric is a faithful measure "
        "of the usage bucket's real demand, not merely a proxy or an adjacent industry's own scale"
    )


def strategy_json_fragment() -> str:
    return (
        ', "evidence_strategy": {"metric_required": "<...>", "physical_unit": "<...>", '
        '"period": "<day|month|quarter|year>", "primary_source_types": ["..."], '
        '"preferred_sources": ["..."], "search_query_templates": ["... {country} ..."], '
        '"fallback_source_types": ["..."], "fallback_queries": ["..."], "derivation_path": "<...>", '
        '"expected_evidence": "<...>", "why_this_driver_measures_the_usage": "<...>"}'
    )


# --------------------------------------------------------------------------- #
# Deterministic validation -- flag, never silently fix.
# --------------------------------------------------------------------------- #
def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _mentions_unsupported_conversion(derivation_path: str, physical_unit: str) -> bool:
    """Heuristic, deterministic scan: a derivation path that explicitly names
    a DIFFERENT known physical-unit family than the driver's own
    physical_unit, without naming one of the codebase's actually-supported
    derivation shapes, is flagged for review -- never silently trusted with
    an invented conversion factor."""
    if not derivation_path:
        return False
    text = derivation_path.lower()
    if any(marker in text for marker in _SUPPORTED_DERIVATION_MARKERS):
        return False
    parsed_physical = units.parse_unit_string(physical_unit)
    if parsed_physical is None:
        return False
    physical_base = parsed_physical.base_unit
    mentioned = {u for u in _KNOWN_PHYSICAL_UNIT_NOUNS if re.search(rf"\b{re.escape(u)}\b", text)}
    conflicting = {u for u in mentioned if u not in physical_base and physical_base not in u}
    return bool(conflicting)


def validate_evidence_strategy(
    strategy: dict[str, Any] | None, *,
    bucket_name: str,
    bucket_description: str,
    approved_countries: list[str] | None = None,
) -> list[dict[str, str]]:
    """Deterministic checks only -- every problem is a FLAG for human review,
    never a silent auto-fix and never a block. Returns an empty list for a
    clean strategy (including an explicitly-empty/missing one, which is its
    own valid, flagged-elsewhere-as-"no strategy" state, not an error)."""
    flags: list[dict[str, str]] = []
    if not isinstance(strategy, dict) or strategy.get("strategy_source") == "missing":
        return flags

    metric_required = _as_str(strategy.get("metric_required"))
    if not metric_required:
        flags.append({
            "code": "no_measurable_metric",
            "field": "metric_required",
            "message": "Strategy names no measurable metric for this driver -- cannot ground a search.",
        })

    physical_unit = _as_str(strategy.get("physical_unit"))
    if not physical_unit or units.parse_unit_string(physical_unit) is None:
        flags.append({
            "code": "missing_or_unparseable_unit",
            "field": "physical_unit",
            "message": f"physical_unit '{physical_unit}' is missing or not a parseable physical unit.",
        })

    period = _as_str(strategy.get("period")).lower()
    if period and period not in _RECOGNIZED_PERIODS:
        flags.append({
            "code": "ambiguous_period",
            "field": "period",
            "message": f"period '{period}' is not one of day/month/quarter/year -- ambiguous, needs review.",
        })

    if embed is not None and metric_required and bucket_description:
        try:
            why = _as_str(strategy.get("why_this_driver_measures_the_usage"))
            driver_vec = embed(f"{metric_required}. {why}".strip())
            usage_vec = embed(f"{bucket_name}: {bucket_description}")
            similarity = _cosine(driver_vec, usage_vec)
        except Exception:  # noqa: BLE001
            similarity = None
        if similarity is not None and similarity < _LOW_RELEVANCE_SIMILARITY:
            flags.append({
                "code": "driver_usage_relationship_unclear",
                "field": "metric_required",
                "message": (
                    f"'{metric_required}' has low semantic relevance ({similarity:.2f}) to usage bucket "
                    f"'{bucket_name}' -- verify this driver genuinely measures this usage, not an adjacent one."
                ),
            })

    derivation_path = _as_str(strategy.get("derivation_path"))
    if _mentions_unsupported_conversion(derivation_path, physical_unit):
        flags.append({
            "code": "derivation_requires_unsupported_conversion",
            "field": "derivation_path",
            "message": (
                f"derivation_path '{derivation_path}' appears to cross into a different unit family than "
                f"'{physical_unit}' without naming a supported derivation (population_share, driver_value, "
                "identity) -- a cross-dimensional conversion factor would need its own evidence, never an "
                "invented constant."
            ),
        })

    if approved_countries:
        approved_lower = {c.strip().lower() for c in approved_countries}
        strategy_geo = _as_str_list(strategy.get("geography"))
        contradicting = [
            g for g in strategy_geo
            if g.strip().lower() not in approved_lower and g.strip().lower() not in ("global", "worldwide")
        ]
        if contradicting:
            flags.append({
                "code": "scope_contradiction",
                "field": "geography",
                "message": (
                    f"strategy geography {contradicting} is not part of the approved market geography "
                    f"{sorted(approved_countries)} -- scope mismatch, needs review."
                ),
            })

    return flags

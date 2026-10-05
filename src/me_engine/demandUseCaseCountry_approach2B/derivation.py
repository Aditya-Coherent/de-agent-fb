"""Phase 10 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) -- Derived-Demand Methodologies.

Formalizes today's ad-hoc regional/global-then-population-share derivation
(already working, embedded in `agents.demand_quantification_node`) into an
explicit, reusable, auditable derivation-chain mechanism -- one that supports
MORE shapes than population-share allocation, and that every derivation
anywhere in this pipeline (driver quantification, pricing conversion
factors, future callers) can share instead of re-deriving its own ad-hoc
formula + provenance logic.

Hard discipline (this module is pure Python, no LLM call anywhere in it):
    - Every derivation names an explicit, supported method (`SUPPORTED_METHODS`)
      -- an unrecognized method is a hard `DerivationError`, never silently
      treated as "close enough."
    - Every derived fact records its exact formula, its named input facts
      (value, unit, geography, source, verification_status of each), and a
      human-readable explanation -- the audit trail this plan requires.
    - A derived fact's `verification_status` is `worse_of(*input_statuses)`,
      capped so it can NEVER read as `verified_observed` -- the output value
      itself was computed, not independently fetched and traced inside a
      real source, even when every input feeding it was. See
      `combined_verification_status`.
    - Deterministic validation flags/rejects invalid inputs (missing values,
      incompatible units/periods, out-of-range shares, negative quantities,
      circular dependencies, an unsupported method, missing provenance, a
      derived value that mathematically exceeds its own parent total) --
      never silently corrects them.
    - Circular derivations (a value derived from the final/published market
      size, from revenue divided by an assumed/estimated quantity, or from
      any other output of the SAME demand calculation feeding back in as one
      of its own inputs) are rejected outright, with an explicit reason.

Mirrors the discipline already established by `pricing_strategy.py`
(`is_circular_price_derivation`, `normalize_price_record`) and
`evidence.py` (`worse_of`, the acceptance-gate philosophy) -- this module
generalizes that same discipline into a standalone, reusable layer instead
of leaving it duplicated ad-hoc per call site.
"""
from __future__ import annotations

import re
from typing import Any

from . import evidence, units

# --------------------------------------------------------------------------- #
# Supported derivation methods -- explicit, closed set. Adding a new shape
# means adding a new named function below AND its name here; nothing in this
# module ever accepts an arbitrary/unlisted method string.
# --------------------------------------------------------------------------- #
SUPPORTED_METHODS: tuple[str, ...] = (
    "regional_share",        # regional_total * country_share
    "global_share",          # global_total * country_share
    "activity_intensity",    # activity * evidence-backed intensity (driver x consumption factor)
    "production_utilization",  # production * utilization_rate
    "rate_conversion",       # daily_value * days_per_year (or any supported period conversion)
    "unit_conversion",       # equivalent unit/period conversion, same physical dimension
    "explicit_formula",      # generic product/quotient of named, evidence-tagged inputs
)


class DerivationError(ValueError):
    """Raised for every deterministic validation failure this module defines
    (see the module docstring's list) -- callers must handle/log it, never
    silently swallow it into a fabricated value. `code` is a stable,
    machine-checkable reason (matches the plan's "Record an explicit
    rejection reason" requirement); `message` is the human-readable detail."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------- #
# Circularity protection
# --------------------------------------------------------------------------- #
# Textual heuristic: an input fact whose own provenance text reveals it was
# actually backed out of a final/published market size, or of revenue
# divided by an assumed/estimated quantity, is rejected regardless of
# whatever verification_status the acceptance gate assigned it -- the exact
# discipline `pricing_strategy.is_circular_price_derivation` already applies
# to price facts specifically, generalized here to any derivation input.
_CIRCULAR_TEXT_RE = re.compile(
    r"final\s+market\s+siz\w*|published\s+market\s+(?:siz\w*|value)|"
    r"total\s+market\s+(?:siz\w*|value)\s+divided|market\s+(?:siz\w*|value)\s*(?:/|divided)|"
    r"divid(?:e|ing)\s+(?:the\s+)?(?:published\s+|final\s+)?market\s+(?:siz\w*|value)|"
    r"revenue\s*(?:/|divided\s+by)\s*(?:an?\s+)?(?:assumed|estimated)\s+(?:volume|quantity)|"
    r"assumed\s+(?:quantity|volume)|estimated\s+(?:quantity|volume)|"
    r"same\s+demand\s+calculation|this\s+demand\s+calculation|self[- ]referential",
    re.IGNORECASE,
)


def detect_circularity(
    inputs: dict[str, dict[str, Any]], *, output_id: str | None = None,
) -> str | None:
    """Deterministic. Returns an explicit rejection reason string if any
    input is circular, else None. Two independent checks, either sufficient
    to reject:

    1. Textual: the input's own derivation_basis/citation/evidence_text/
       reasoning/source names a final/published market size, or a
       revenue-divided-by-an-assumed-quantity backed-out figure.
    2. Lineage: the input IS (or was itself derived from, transitively) the
       very output this derivation is trying to produce -- `output_id`
       identifies the value being derived (e.g. "{bucket}:{geography}:
       {quantity_kind}"), and `derivation_lineage`/`output_id` on each input
       fact (stamped by `_build_derived_fact` below on every prior
       derivation) is checked against it, catching not just direct
       self-reference but any transitive cycle.
    """
    for name, fact in inputs.items():
        if not isinstance(fact, dict):
            continue
        text = " ".join(
            str(fact.get(k) or "") for k in
            ("derivation_basis", "citation", "evidence_text", "reasoning", "source")
        )
        if _CIRCULAR_TEXT_RE.search(text):
            return (
                f"input '{name}' appears circularly derived from a final/published market size, or "
                f"from revenue divided by an assumed/estimated quantity -- rejected outright: "
                f"{text.strip()[:200]!r}"
            )
        if output_id:
            input_output_id = fact.get("output_id")
            if input_output_id and input_output_id == output_id:
                return (
                    f"input '{name}' has the same output_id ('{output_id}') as the value being "
                    "derived -- a demand calculation's own output cannot feed back in as its own input."
                )
            lineage = fact.get("derivation_lineage") or ()
            if output_id in lineage:
                return (
                    f"input '{name}' is itself a downstream derivation of '{output_id}' "
                    f"(lineage: {lineage}) -- using it back as an input would create a circular "
                    "dependency chain."
                )
    return None


def _derived_lineage(inputs: dict[str, dict[str, Any]]) -> tuple[str, ...]:
    """The output's own lineage: every ancestor `output_id` reachable
    through its inputs, plus each direct input's own `output_id` -- so a
    LATER derivation can detect a transitive cycle even when the immediate
    input doesn't itself name the ultimate ancestor."""
    lineage: set[str] = set()
    for fact in inputs.values():
        if not isinstance(fact, dict):
            continue
        lineage.update(fact.get("derivation_lineage") or ())
        if fact.get("output_id"):
            lineage.add(fact["output_id"])
    return tuple(sorted(lineage))


# --------------------------------------------------------------------------- #
# Provenance -- combined verification_status, never upgraded to observed.
# --------------------------------------------------------------------------- #
def _status_of(fact: dict[str, Any]) -> str:
    return fact.get("verification_status") or fact.get("evidence_type") or "assumption"


def combined_verification_status(*statuses: str) -> str:
    """`worse_of(*statuses)`, per the plan's own examples (observed x
    observed -> derived; observed x derived -> derived; observed x
    unverified -> unverified; observed x assumption -> assumption) --
    EXCEPT that the ceiling is always `derived`, never `verified_observed`,
    even if every input independently reached that top tier. The output of
    a derivation is, by definition, a value code computed -- never something
    code itself fetched and traced inside a real source -- so it can never
    honestly claim the top tier reserved for that."""
    worst = evidence.worse_of(*statuses)
    return "derived" if worst == "verified_observed" else worst


def _require_provenance(inputs: dict[str, dict[str, Any]]) -> None:
    for name, fact in inputs.items():
        if not isinstance(fact, dict) or (not fact.get("verification_status") and not fact.get("evidence_type")):
            raise DerivationError(
                "missing_provenance",
                f"input '{name}' carries no verification_status/evidence_type -- every derivation "
                "input must have explicit provenance, never an untagged bare number.",
            )


# --------------------------------------------------------------------------- #
# The one shared constructor every derive_* function below funnels through --
# this is the single place all the deterministic validation/provenance/audit
# rules in the module docstring are actually enforced.
# --------------------------------------------------------------------------- #
def build_derived_fact(
    *, method: str, formula: str, value: float, unit: str, inputs: dict[str, dict[str, Any]],
    geography: str | None = None, period: str | None = None, year: int | None = None,
    output_id: str | None = None, explanation: str = "", parent_total: float | None = None,
    emitter: Any | None = None, stage: str = "derivation", bucket: str | None = None,
) -> dict[str, Any]:
    """Deterministic. Builds and validates a derived fact from named input
    facts -- never invents a value, never silently corrects an invalid
    input. Raises `DerivationError` (with a stable `.code`) for every
    validation failure the plan requires to be caught, and stamps full
    provenance (method, formula, input facts + their own provenance, output
    value/unit/period/geography, verification status, human-readable
    explanation) on success."""
    if method not in SUPPORTED_METHODS:
        raise DerivationError(
            "unsupported_method",
            f"'{method}' is not a supported derivation method (supported: {SUPPORTED_METHODS}).",
        )
    if not inputs:
        raise DerivationError("missing_input", "a derivation requires at least one named input fact.")
    _require_provenance(inputs)

    circular_reason = detect_circularity(inputs, output_id=output_id)
    if circular_reason:
        raise DerivationError("circular_derivation", circular_reason)

    if value < 0:
        raise DerivationError("negative_quantity", f"derived value {value} is negative -- rejected, not clamped.")
    if parent_total is not None and value > parent_total * (1 + 1e-9):
        raise DerivationError(
            "exceeds_parent_total",
            f"derived value {value:,.6f} exceeds its own parent total {parent_total:,.6f} -- "
            f"mathematically invalid for method '{method}'.",
        )

    statuses = [_status_of(f) for f in inputs.values()]
    verification_status = combined_verification_status(*statuses)
    confidences = [f.get("confidence") for f in inputs.values() if isinstance(f.get("confidence"), (int, float))]
    confidence = min(confidences) if confidences else 0.5

    input_facts = {
        name: {
            "value": f.get("value"), "unit": f.get("unit"), "year": f.get("year"),
            "geography": f.get("geography"), "source": f.get("source") or f.get("source_name"),
            "source_url": f.get("source_url"), "verification_status": _status_of(f),
        }
        for name, f in inputs.items()
    }

    result = {
        "value": round(value, 6), "unit": unit, "year": year, "geography": geography, "period": period,
        "evidence_type": verification_status, "verification_status": verification_status,
        "confidence": confidence,
        "derivation_method": method, "formula": formula, "input_facts": input_facts,
        "output_id": output_id, "derivation_lineage": _derived_lineage(inputs),
        "derivation_basis": explanation or f"{method}: {formula}",
        "explanation": explanation or f"Method: {method}. Formula: {formula}.",
        "source": f"derived ({method})", "citation": formula, "is_direct_figure": False,
    }
    if emitter is not None:
        emitter.emit(
            stage, "derivation", bucket=bucket, country=geography,
            derivation={
                "derivation_method": method, "formula": formula, "input_facts": input_facts,
                "output_id": output_id,
            },
            output_data={"value": result["value"], "unit": unit, "verification_status": verification_status},
            status="success",
        )
    return result


# --------------------------------------------------------------------------- #
# regional_share / global_share -- total x allocation-share.
# --------------------------------------------------------------------------- #
def _validate_ratio(fact: dict[str, Any], *, name: str) -> float:
    if fact is None or fact.get("value") is None:
        raise DerivationError("missing_input", f"'{name}' fact is missing or has no value.")
    value = float(fact["value"])
    if value < 0 or value > 1:
        raise DerivationError("invalid_share", f"'{name}' value {value} is out of the valid [0, 1] range.")
    return value


def _derive_by_ratio(
    total_fact: dict[str, Any] | None, ratio_fact: dict[str, Any] | None, *,
    method: str, ratio_label: str = "country_share",
    geography: str | None, output_id: str | None, year: int | None,
    emitter: Any | None = None, bucket: str | None = None,
) -> dict[str, Any]:
    if total_fact is None or ratio_fact is None:
        raise DerivationError("missing_input", f"'{method}' requires both a total fact and a {ratio_label} fact.")
    if total_fact.get("value") is None:
        raise DerivationError("missing_input", "total fact has no value.")
    total_value = float(total_fact["value"])
    if total_value < 0:
        raise DerivationError("negative_quantity", f"total value {total_value} is negative.")
    ratio = _validate_ratio(ratio_fact, name=ratio_label)

    unit = total_fact.get("unit", "")
    value = total_value * ratio
    formula = (
        f"{method}: value = total_value({total_value:,.4f} {unit}) * {ratio_label}({ratio:.6f})"
    )
    explanation = (
        f"Method: {method}. Total ({total_fact.get('geography') or 'aggregate'}): {total_value:,.4f} {unit} "
        f"[{_status_of(total_fact)}]. {ratio_label.replace('_', ' ').title()} for {geography or 'target'}: "
        f"{ratio:.2%} [{_status_of(ratio_fact)}]. Formula: {total_value:,.4f} x {ratio:.6f}. "
        f"Derived value: {value:,.4f} {unit}."
    )
    return build_derived_fact(
        method=method, formula=formula, value=value, unit=unit,
        inputs={"total": total_fact, ratio_label: ratio_fact},
        geography=geography, year=total_fact.get("year") or year,
        output_id=output_id, explanation=explanation, parent_total=total_value,
        emitter=emitter, bucket=bucket,
    )


def derive_regional_share(
    regional_fact: dict[str, Any] | None, share_fact: dict[str, Any] | None, *,
    geography: str, ratio_label: str = "country_share",
    output_id: str | None = None, year: int | None = None,
    emitter: Any | None = None, bucket: str | None = None,
) -> dict[str, Any]:
    """`regional_total x country_share` -- e.g. a regional demand figure
    allocated to one country via a defensible, evidence-tagged allocation
    driver (never an arbitrary/uniform split -- `share_fact` must itself
    carry real provenance, verified/derived/assumption alike). `ratio_label`
    names WHICH allocation driver was actually used (e.g. 'population_share',
    'gdp_share', 'refinery_throughput_share') so the formula/audit trail says
    exactly what basis was applied, not just "some share.\""""
    return _derive_by_ratio(
        regional_fact, share_fact, method="regional_share", ratio_label=ratio_label,
        geography=geography, output_id=output_id, year=year, emitter=emitter, bucket=bucket,
    )


def derive_global_share(
    global_fact: dict[str, Any] | None, share_fact: dict[str, Any] | None, *,
    geography: str, ratio_label: str = "country_share",
    output_id: str | None = None, year: int | None = None,
    emitter: Any | None = None, bucket: str | None = None,
) -> dict[str, Any]:
    """`global_total x country_share` -- same mechanics as `derive_regional_share`,
    kept as a distinct named method because the plan requires the audit
    trail to say which broader anchor (regional vs. global) was actually
    used, not just that "some total x share" happened."""
    return _derive_by_ratio(
        global_fact, share_fact, method="global_share", ratio_label=ratio_label,
        geography=geography, output_id=output_id, year=year, emitter=emitter, bucket=bucket,
    )


def derive_production_utilization(
    production_fact: dict[str, Any] | None, utilization_fact: dict[str, Any] | None, *,
    geography: str | None = None, output_id: str | None = None, year: int | None = None,
    emitter: Any | None = None, bucket: str | None = None,
) -> dict[str, Any]:
    """`production x utilization_rate` -- e.g. installed/nameplate production
    capacity times an observed/derived utilization rate (a fraction, [0,1])
    yields real usable output."""
    return _derive_by_ratio(
        production_fact, utilization_fact, method="production_utilization", ratio_label="utilization_rate",
        geography=geography or (production_fact or {}).get("geography"), output_id=output_id, year=year,
        emitter=emitter, bucket=bucket,
    )


# --------------------------------------------------------------------------- #
# activity_intensity -- activity x evidence-backed intensity.
# --------------------------------------------------------------------------- #
def derive_activity_intensity(
    activity_fact: dict[str, Any] | None, intensity_fact: dict[str, Any] | None, *,
    output_unit: str | None = None, geography: str | None = None,
    output_id: str | None = None, year: int | None = None,
) -> dict[str, Any]:
    """`activity x evidence-backed intensity` -- the generalized shape of the
    driver x consumption-intensity multiplication `conversion.apply_conversion`
    already performs arithmetically; this wrapper additionally validates
    period compatibility and produces full derivation provenance around it."""
    if activity_fact is None or intensity_fact is None:
        raise DerivationError("missing_input", "'activity_intensity' requires both an activity fact and an intensity fact.")
    if activity_fact.get("value") is None or intensity_fact.get("value") is None:
        raise DerivationError("missing_input", "activity or intensity fact has no value.")
    activity_value = float(activity_fact["value"])
    intensity_value = float(intensity_fact["value"])
    if activity_value < 0 or intensity_value < 0:
        raise DerivationError("negative_quantity", "activity/intensity values must be non-negative.")

    activity_unit = units.parse_unit_string(activity_fact.get("unit", ""))
    intensity_unit = units.parse_unit_string(intensity_fact.get("unit", ""))
    if activity_unit is not None and intensity_unit is not None and units.periods_conflict(activity_unit, intensity_unit):
        raise DerivationError(
            "period_mismatch",
            f"activity unit '{activity_fact.get('unit')}' states period '{activity_unit.per_period}' but "
            f"intensity unit '{intensity_fact.get('unit')}' states '{intensity_unit.per_period}' -- "
            "explicit, conflicting time bases.",
        )

    unit = output_unit or activity_fact.get("final_unit") or intensity_fact.get("final_unit") or activity_fact.get("unit", "")
    value = activity_value * intensity_value
    formula = (
        f"activity_intensity: value = activity({activity_value:,.4f} {activity_fact.get('unit', '')}) * "
        f"intensity({intensity_value:,.6f} {intensity_fact.get('unit', '')})"
    )
    explanation = (
        f"Method: activity_intensity. Activity: {activity_value:,.4f} {activity_fact.get('unit', '')} "
        f"[{_status_of(activity_fact)}]. Intensity: {intensity_value:,.6f} {intensity_fact.get('unit', '')} "
        f"[{_status_of(intensity_fact)}]. Formula: {activity_value:,.4f} x {intensity_value:,.6f}. "
        f"Derived value: {value:,.4f} {unit}."
    )
    return build_derived_fact(
        method="activity_intensity", formula=formula, value=value, unit=unit,
        inputs={"activity": activity_fact, "intensity": intensity_fact},
        geography=geography or activity_fact.get("geography"), year=activity_fact.get("year") or year,
        output_id=output_id, explanation=explanation,
    )


# --------------------------------------------------------------------------- #
# rate_conversion -- daily_value x days_per_year (or any supported period pair).
# --------------------------------------------------------------------------- #
_PERIOD_ALIASES: dict[str, str] = {
    "day": "day", "days": "day", "daily": "day",
    "month": "month", "months": "month", "monthly": "month",
    "quarter": "quarter", "quarters": "quarter", "quarterly": "quarter",
    "year": "year", "years": "year", "yearly": "year", "annum": "year",
    "annual": "year", "annually": "year",
}
# Deterministic calendar constants ONLY -- these are mathematical identities
# (days/months/quarters per year), never empirical evidence, so applying one
# never downgrades/upgrades the combined verification_status beyond what the
# input value's own status already was (see `build_derived_fact` / the
# "derived requires a logged formula" rule -- the formula here is fixed math,
# not a claim about the world).
_PERIOD_FACTORS: dict[tuple[str, str], float] = {
    ("day", "month"): 30.0, ("day", "quarter"): 91.25, ("day", "year"): 365.0,
    ("month", "quarter"): 3.0, ("month", "year"): 12.0, ("quarter", "year"): 4.0,
}


def _normalize_period(period: str) -> str:
    key = (period or "").strip().lower()
    if key not in _PERIOD_ALIASES:
        raise DerivationError("period_mismatch", f"'{period}' is not a recognized period (day/month/quarter/year).")
    return _PERIOD_ALIASES[key]


def derive_rate_conversion(
    fact: dict[str, Any] | None, *, to_period: str, from_period: str | None = None,
    factor: float | None = None, geography: str | None = None,
    output_id: str | None = None, year: int | None = None,
) -> dict[str, Any]:
    """`daily_value x days_per_year` (or any other supported period-pair
    conversion, e.g. month->year, quarter->year) -- a deterministic
    equivalent-period conversion, not an empirical claim. `factor` may be
    supplied explicitly (e.g. a market whose fiscal year genuinely uses 360
    days); otherwise a fixed calendar constant from `_PERIOD_FACTORS` is
    used, and the conversion is refused (not guessed) if neither is
    available for the requested period pair."""
    if fact is None or fact.get("value") is None:
        raise DerivationError("missing_input", "'rate_conversion' requires an input fact with a value.")
    value = float(fact["value"])
    if value < 0:
        raise DerivationError("negative_quantity", f"value {value} is negative.")

    parsed = units.parse_unit_string(fact.get("unit", ""))
    resolved_from = from_period or (parsed.per_period if parsed else None) or fact.get("period")
    if not resolved_from:
        raise DerivationError(
            "period_mismatch",
            "input fact states no source period (unit carries none, and no `from_period` was supplied).",
        )
    from_norm = _normalize_period(resolved_from)
    to_norm = _normalize_period(to_period)
    if from_norm == to_norm:
        raise DerivationError("period_mismatch", f"source and target period are both '{to_norm}' -- nothing to convert.")

    if factor is None:
        factor = _PERIOD_FACTORS.get((from_norm, to_norm))
        if factor is None:
            raise DerivationError(
                "unsupported_method",
                f"no deterministic conversion factor is defined from '{from_norm}' to '{to_norm}' -- "
                "supply `factor` explicitly.",
            )
    if factor <= 0:
        raise DerivationError("invalid_share", f"period conversion factor {factor} must be positive.")

    unit = fact.get("unit", "")
    result_value = value * factor
    formula = f"rate_conversion: value = {from_norm}_value({value:,.6f} {unit}) * {from_norm}s_per_{to_norm}({factor})"
    explanation = (
        f"Method: rate_conversion. {from_norm.title()} value: {value:,.6f} {unit} [{_status_of(fact)}]. "
        f"Conversion factor: {factor} {from_norm}s per {to_norm} (calendar constant, not empirical evidence). "
        f"Formula: {value:,.6f} x {factor}. Derived {to_norm}ly value: {result_value:,.6f} {unit}."
    )
    return build_derived_fact(
        method="rate_conversion", formula=formula, value=result_value, unit=unit,
        inputs={"value": fact}, geography=geography or fact.get("geography"),
        period=to_norm, year=fact.get("year") or year, output_id=output_id, explanation=explanation,
    )


# --------------------------------------------------------------------------- #
# unit_conversion -- equivalent unit conversion, same physical dimension.
# --------------------------------------------------------------------------- #
def derive_unit_conversion(
    fact: dict[str, Any] | None, *, to_unit: str, geography: str | None = None,
    output_id: str | None = None, year: int | None = None,
) -> dict[str, Any]:
    """Equivalent unit/period conversion SUPPORTED BY THE EXISTING UNIT
    CONTRACT (`units.py`) -- same physical-dimension base unit, different
    scale word and/or period (e.g. "million tonnes per year" -> "tonnes per
    year"). Deliberately does NOT perform cross-dimensional conversion
    (barrels <-> tonnes via density) -- that requires an evidence-backed
    conversion factor and belongs in `derive_activity_intensity`, not here;
    see `units.py`'s own documented scope."""
    if fact is None or fact.get("value") is None:
        raise DerivationError("missing_input", "'unit_conversion' requires an input fact with a value.")
    value = float(fact["value"])
    if value < 0:
        raise DerivationError("negative_quantity", f"value {value} is negative.")

    from_parsed = units.parse_unit_string(fact.get("unit", ""))
    to_parsed = units.parse_unit_string(to_unit)
    if from_parsed is None or to_parsed is None:
        raise DerivationError(
            "unit_mismatch",
            f"cannot parse '{fact.get('unit')}' and/or '{to_unit}' as physical units.",
        )
    if not units.units_share_base(from_parsed, to_parsed):
        raise DerivationError(
            "unit_mismatch",
            f"'{from_parsed.base_unit}' and '{to_parsed.base_unit}' are not the same physical-unit "
            "family -- cross-dimensional conversion (e.g. barrels<->tonnes) is out of scope for "
            "'unit_conversion'; use 'activity_intensity' with an evidence-backed conversion factor instead.",
        )
    if units.periods_conflict(from_parsed, to_parsed):
        raise DerivationError(
            "period_mismatch",
            f"'{fact.get('unit')}' states period '{from_parsed.per_period}' but target unit "
            f"'{to_unit}' states '{to_parsed.per_period}'.",
        )

    result_value = value * from_parsed.scale / to_parsed.scale
    formula = (
        f"unit_conversion: value = source_value({value:,.6f} {fact.get('unit')}) * "
        f"scale_ratio({from_parsed.scale}/{to_parsed.scale})"
    )
    explanation = (
        f"Method: unit_conversion. Source: {value:,.6f} {fact.get('unit')} [{_status_of(fact)}]. "
        f"Equivalent-unit conversion to '{to_unit}' (same physical dimension, scale/period "
        f"normalization only). Formula: {value:,.6f} x ({from_parsed.scale}/{to_parsed.scale}). "
        f"Derived value: {result_value:,.6f} {to_unit}."
    )
    return build_derived_fact(
        method="unit_conversion", formula=formula, value=result_value, unit=to_unit,
        inputs={"value": fact}, geography=geography or fact.get("geography"),
        year=fact.get("year") or year, output_id=output_id, explanation=explanation,
    )


# --------------------------------------------------------------------------- #
# explicit_formula -- generic product/quotient of named, evidence-tagged inputs.
# --------------------------------------------------------------------------- #
def derive_explicit_formula(
    inputs: dict[str, dict[str, Any]], *, numerator: list[str], denominator: list[str] | None = None,
    output_unit: str, geography: str | None = None, period: str | None = None,
    output_id: str | None = None, year: int | None = None,
) -> dict[str, Any]:
    """The generic fallback shape: `value = product(numerator facts) /
    product(denominator facts)`, over any set of named, evidence-tagged
    input facts -- for a derivation whose formula doesn't match one of the
    other named methods above. Deliberately restricted to a product/quotient
    of named inputs (never an arbitrary expression string / `eval`) so the
    formula stays both auditable and safe to execute."""
    denominator = list(denominator or [])
    referenced = [*numerator, *denominator]
    missing = [name for name in referenced if inputs.get(name) is None]
    if missing:
        raise DerivationError("missing_input", f"missing required input(s): {missing}.")
    used = {name: inputs[name] for name in referenced}

    numerator_value = 1.0
    for name in numerator:
        raw = used[name].get("value")
        if raw is None:
            raise DerivationError("missing_input", f"input '{name}' has no value.")
        raw = float(raw)
        if raw < 0:
            raise DerivationError("negative_quantity", f"input '{name}' value {raw} is negative.")
        numerator_value *= raw

    denominator_value = 1.0
    for name in denominator:
        raw = used[name].get("value")
        if raw is None:
            raise DerivationError("missing_input", f"input '{name}' has no value.")
        raw = float(raw)
        if raw <= 0:
            raise DerivationError("invalid_share", f"denominator input '{name}' value {raw} must be positive.")
        denominator_value *= raw

    value = numerator_value / denominator_value
    numerator_txt = " * ".join(f"{n}({used[n].get('value')})" for n in numerator)
    denominator_txt = " * ".join(f"{n}({used[n].get('value')})" for n in denominator)
    formula = f"explicit_formula: value = ({numerator_txt})" + (f" / ({denominator_txt})" if denominator else "")
    explanation = f"Method: explicit_formula. Formula: {formula}. Derived value: {value:,.6f} {output_unit}."
    return build_derived_fact(
        method="explicit_formula", formula=formula, value=value, unit=output_unit, inputs=used,
        geography=geography, period=period, year=year, output_id=output_id, explanation=explanation,
    )


# --------------------------------------------------------------------------- #
# Phase 7 (Evidence Strategy) integration.
# --------------------------------------------------------------------------- #
def annotate_strategy_derivation(
    strategy: dict[str, Any] | None, derived_fact: dict[str, Any],
) -> dict[str, Any]:
    """Phase 7's `evidence_strategy.derivation_path` is a human-authored,
    pre-search SEARCH PLAN ("regional total x population share", in words).
    This records, alongside it -- WITHOUT overwriting the original text --
    the actual deterministic method/formula Phase 10 executed, so the audit
    trail shows both what was planned and what actually ran. Returns a new
    dict; never mutates the caller's `strategy` in place."""
    base = dict(strategy) if isinstance(strategy, dict) else {}
    base.setdefault("derivation_path", "")
    base["derivation_path_executed"] = {
        "method": derived_fact.get("derivation_method"),
        "formula": derived_fact.get("formula"),
        "verification_status": derived_fact.get("verification_status"),
    }
    return base

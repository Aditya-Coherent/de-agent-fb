"""Phase 11 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) -- Magnitude & Cross-Source
Validation.

A generic, market-independent evidence-based validation layer that decides
whether a driver, intensity, derived quantity, or price is plausible in
MAGNITUDE relative to independent evidence. Extends (does not replace) the
existing single-reference, flag-only checks in `magnitude.py`
(`driver_magnitude_validation_node`) and `triangulation.py`
(`triangulation_node`) -- both keep working exactly as before; this module
is a separate, additive layer wired in as its own graph node.

Hard discipline, identical to every other acceptance/derivation gate in this
package (`evidence.py`, `derivation.py`):
    - NEVER silently corrects a value. The original value, unit, source, and
      provenance are always preserved unchanged in the returned record.
    - A discrepancy is CLASSIFIED and FLAGGED, never auto-fixed.
    - Independent references are gathered where possible (up to two,
      reusing the existing Evidence Router / evidence-extraction machinery),
      each carrying its own full provenance (source, URL, value, unit,
      geography, year, verification_status, evidence text/location).
    - Two pieces of evidence are never compared unless first checked for
      unit, geography, time-period, and scope compatibility -- an
      incompatible reference is reported as such, never silently used.
    - No market-specific thresholds anywhere in this module -- every
      constant here (`_MATCH_RATIO`, `_DISCREPANCY_RATIO`, ...) is a single,
      market-independent number applied identically to a driver in barrels,
      tonnes, kWh, vehicle-km, or a price in USD/unit.

Generic checks this module supports (`check_type`, see `evaluate_magnitude`):
    - "driver_magnitude"              -- a usage bucket's driver total
    - "intensity_magnitude"           -- a consumption-intensity/conversion factor
    - "derived_quantity_magnitude"    -- a Phase 10 `derivation.py` output
    - "price_magnitude"               -- an ASP / commodity price
    - "country_vs_regional_or_global" -- a country allocation vs. its own parent total
    - "country_vs_independent_reference" -- a country figure vs. a same-geography reference
    - "usage_vs_parent_category"      -- a usage bucket's $ value vs. a broader parent total
    - "year_period_consistency"       -- driver/intensity/price years & periods, within one bucket
"""
from __future__ import annotations

import re
from typing import Any

from me_engine.generation.llm import GenLLM

from . import evidence, evidence_router, units
from .web_search import gather

# --------------------------------------------------------------------------- #
# Generic, market-independent thresholds -- the SAME numbers apply to every
# market/unit/quantity kind. Deliberately wide (mirrors `magnitude.py`'s own
# `_MAGNITUDE_RATIO_THRESHOLD` philosophy): this catches genuine scale errors
# (unit/period mixups, order-of-magnitude blowups), not routine source-to-
# source variance.
# --------------------------------------------------------------------------- #
_MATCH_RATIO = 1.5
_DISCREPANCY_RATIO = 3.0
_ORDER_OF_MAGNITUDE_RATIO = 10.0
_PARENT_TOTAL_TOLERANCE = 1e-6
_YEAR_TOLERANCE_YEARS = 2
_REFERENCES_AGREE_RATIO = 1.5
_MAX_REFERENCES = 2

_CLASSIFICATION_SEVERITY: dict[str, int] = {
    "exceeds_parent_total": 5,
    "order_of_magnitude_mismatch": 4,
    "large_discrepancy": 3,
    "minor_discrepancy": 1,
    "match": 0,
    "not_comparable": -1,
    "incompatible": -1,
    "no_reference": -2,
}

# --------------------------------------------------------------------------- #
# Evidence hierarchy -- generic keyword classification, NEVER a specific
# institution/market lookup table. Used only to LABEL a reference's own
# authority tier for the audit trail (e.g. "this reference is a government/
# statistical source, treat as more authoritative than an unlabelled
# secondary source") -- never to discard/replace a value.
# --------------------------------------------------------------------------- #
_GOVERNMENT_KEYWORDS = (
    "ministry", "department of", "bureau of", "national statistic", "census",
    "government", "federal", ".gov", "central bank", "regulatory authority",
    "national agency", "statistics office", "statistical office",
)
_INTERNATIONAL_ORG_KEYWORDS = (
    "world bank", "imf", "united nations", "oecd", "iea", "who", "fao",
    "unctad", "wto", "international energy agency", "international monetary fund",
    "world health organization", "world trade organization",
)
_INDUSTRY_ASSOCIATION_KEYWORDS = (
    "association", "institute", "federation", "council", "chamber of commerce",
    "industry group", "trade body", "consortium",
)


def source_authority_rank(source_name: str) -> tuple[int, str]:
    """Generic evidence-hierarchy classifier -- rank 4 (most authoritative)
    down to 1 (unclassified/secondary). Pure keyword match, no market/
    institution-specific lookup table -- adding a new market never requires
    touching this function."""
    name = (source_name or "").lower()
    if any(k in name for k in _GOVERNMENT_KEYWORDS):
        return 4, "government_or_statistical"
    if any(k in name for k in _INTERNATIONAL_ORG_KEYWORDS):
        return 3, "international_organization"
    if any(k in name for k in _INDUSTRY_ASSOCIATION_KEYWORDS):
        return 2, "industry_association"
    return 1, "secondary_or_unclassified"


def classify_discrepancy(ratio: float | None) -> str:
    """Pure, market-independent ratio classification."""
    if ratio is None:
        return "not_comparable"
    if ratio < _MATCH_RATIO:
        return "match"
    if ratio < _DISCREPANCY_RATIO:
        return "minor_discrepancy"
    if ratio < _ORDER_OF_MAGNITUDE_RATIO:
        return "large_discrepancy"
    return "order_of_magnitude_mismatch"


def _ratio(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return None
    if a <= 0 or b <= 0:
        return None
    return max(a, b) / min(a, b)


def _normalize_geo(geo: Any) -> str:
    return re.sub(r"\s+", " ", str(geo or "").strip().lower())


def check_compatibility(
    subject_unit: str | None, reference_unit: str | None, *,
    subject_geo: str | None = None, reference_geo: str | None = None,
    subject_year: int | None = None, reference_year: int | None = None,
    require_same_geography: bool = True, year_tolerance: int = _YEAR_TOLERANCE_YEARS,
) -> dict[str, Any]:
    """Deterministic, never raises. Two pieces of evidence are only ever
    compared after passing this check -- absence of information (an unknown
    geography/year on one side) is never treated as a conflict, only an
    EXPLICIT, stated mismatch is (mirrors `units.periods_conflict`'s own
    "absence is not a conflict" discipline)."""
    reasons: list[str] = []
    su = units.parse_unit_string(subject_unit) if subject_unit else None
    ru = units.parse_unit_string(reference_unit) if reference_unit else None
    if su is not None and ru is not None:
        if not units.units_share_base(su, ru):
            reasons.append("unit_mismatch")
        elif units.periods_conflict(su, ru):
            reasons.append("period_mismatch")
    # Unparseable unit on either side: can't affirmatively confirm
    # compatibility, but also can't invent a mismatch -- left uncompared only
    # if the caller supplies no ratio-worthy values; otherwise treated as
    # "unknown, proceed with caution" (still compared, per the plan's
    # tolerance for prose-derived units elsewhere in this package).

    if require_same_geography and subject_geo and reference_geo:
        if _normalize_geo(subject_geo) != _normalize_geo(reference_geo):
            reasons.append("geography_mismatch")

    if subject_year is not None and reference_year is not None:
        try:
            if abs(int(subject_year) - int(reference_year)) > year_tolerance:
                reasons.append("period_mismatch")
        except (TypeError, ValueError):
            pass

    return {"compatible": not reasons, "reasons": reasons}


# --------------------------------------------------------------------------- #
# The core generic comparator -- every check_type funnels through this.
# --------------------------------------------------------------------------- #
def evaluate_magnitude(
    *, check_type: str, subject_label: str, value: float | None, unit: str,
    source_name: str = "", source_url: str = "", verification_status: str = "assumption",
    geography: str | None = None, year: int | None = None,
    references: list[dict[str, Any]] | None = None,
    parent_total: float | None = None, parent_label: str = "",
    ratio_threshold: float = _DISCREPANCY_RATIO,
    require_same_geography: bool | None = None,
    emitter: Any | None = None, stage: str = "magnitude_check", bucket: str | None = None,
) -> dict[str, Any]:
    """Deterministic, code-only. Compares `value` against up to
    `_MAX_REFERENCES` independent `references` and/or a `parent_total`
    ceiling. NEVER mutates or replaces `value` -- the returned record always
    echoes the original value/unit/source/provenance unchanged, alongside the
    comparison outcome. Safe with zero references (returns `status="no_reference"`,
    never a failure)."""
    references = list(references or [])[:_MAX_REFERENCES]
    if require_same_geography is None:
        require_same_geography = check_type not in (
            "country_vs_regional_or_global", "usage_vs_parent_category",
        )

    result: dict[str, Any] = {
        "check_type": check_type,
        "subject_label": subject_label,
        "original_value": value,
        "original_unit": unit,
        "original_source": source_name,
        "original_source_url": source_url,
        "original_verification_status": verification_status,
        "geography": geography,
        "year": year,
        "references": [],
        "references_agree": None,
        "ratio": None,
        "classification": "no_reference",
        "status": "no_reference",
        "reason": "",
        "flagged": False,
        "notes": [],
    }

    best_classification = "no_reference"

    # --- parent-total ceiling (country > parent, usage > parent category, a
    # derived country allocation exceeding its own regional/global source) ---
    if parent_total is not None and value is not None:
        try:
            parent_total = float(parent_total)
        except (TypeError, ValueError):
            parent_total = None
    if parent_total is not None and parent_total > 0 and value is not None:
        result["parent_total"] = parent_total
        result["parent_label"] = parent_label
        if value > parent_total * (1 + _PARENT_TOTAL_TOLERANCE):
            parent_ratio = value / parent_total
            result["parent_ratio"] = round(parent_ratio, 6)
            best_classification = "exceeds_parent_total"
            result["notes"].append(
                f"{subject_label} ({value:,.4f} {unit}) EXCEEDS its own parent total "
                f"({parent_label or 'parent'}: {parent_total:,.4f} {unit}, ratio {parent_ratio:.2f}x) -- "
                "mathematically implausible, flagged for review. Value NOT changed."
            )

    # --- independent-reference comparison ---
    compared: list[dict[str, Any]] = []
    for ref in references:
        ref_value = ref.get("value")
        ref_unit = ref.get("unit", unit)
        ref_source = ref.get("source") or ref.get("source_name") or ""
        compat = check_compatibility(
            unit, ref_unit, subject_geo=geography, reference_geo=ref.get("geography"),
            subject_year=year, reference_year=ref.get("year"),
            require_same_geography=require_same_geography,
        )
        ratio = _ratio(value, ref_value) if compat["compatible"] else None
        entry: dict[str, Any] = {
            "source": ref_source,
            "source_url": ref.get("source_url", ""),
            "value": ref_value,
            "unit": ref_unit,
            "geography": ref.get("geography"),
            "year": ref.get("year"),
            "verification_status": ref.get("verification_status") or ref.get("evidence_type") or "assumption",
            "evidence_text": ref.get("evidence_text", ""),
            "location_ref": ref.get("location_ref", ""),
            "citation": ref.get("citation", ""),
            "source_authority": source_authority_rank(ref_source)[1],
            "compatible": compat["compatible"],
            "incompatibility_reasons": compat["reasons"],
            "ratio": round(ratio, 4) if ratio is not None else None,
            "classification": classify_discrepancy(ratio) if compat["compatible"] else "incompatible",
        }
        result["references"].append(entry)
        if compat["compatible"] and ratio is not None:
            compared.append(entry)

    if compared:
        if len(compared) >= 2:
            pair_ratio = _ratio(compared[0]["value"], compared[1]["value"])
            result["references_agree"] = pair_ratio is not None and pair_ratio < _REFERENCES_AGREE_RATIO

        worst = max(compared, key=lambda r: r["ratio"] or 0.0)
        result["ratio"] = worst["ratio"]
        if _CLASSIFICATION_SEVERITY[worst["classification"]] > _CLASSIFICATION_SEVERITY[best_classification]:
            best_classification = worst["classification"]

        n_verified = sum(1 for r in compared if r["verification_status"] == "verified_observed")
        if worst["ratio"] is not None and worst["ratio"] >= ratio_threshold:
            agreement_note = ""
            if result["references_agree"] is True:
                agreement_note = f" {len(compared)}/{len(compared)} independent reference(s) agree the pipeline value is off."
            elif result["references_agree"] is False:
                agreement_note = " independent references do NOT agree with each other -- treat with more caution."
            confidence_note = f" Reference confidence: {n_verified}/{len(compared)} verified_observed."
            if n_verified == 0 and compared:
                confidence_note += " No reference here was independently verified -- treat this flag with caution."
            result["notes"].append(
                f"{subject_label} ({value:,.4f} {unit}) differs from an independent reference "
                f"({worst['value']:,.4f} {worst['unit']}, source: {worst['source'] or 'n/a'}, "
                f"verification_status: {worst['verification_status']}) by {worst['ratio']:.2f}x "
                f"({worst['classification']}) -- flagged for review, value NOT changed."
                + agreement_note + confidence_note
            )
    elif references:
        # Every supplied reference existed but was incompatible (unit/geo/period).
        reasons = sorted({r for e in result["references"] for r in e["incompatibility_reasons"]})
        if reasons:
            if _CLASSIFICATION_SEVERITY["incompatible"] > _CLASSIFICATION_SEVERITY[best_classification]:
                pass  # incompatible never outranks an already-detected parent-total breach
            result["notes"].append(
                f"{len(references)} reference(s) found for {subject_label} but none were comparable "
                f"({', '.join(reasons)}) -- not compared, original value unchanged."
            )
            if best_classification == "no_reference":
                best_classification = "incompatible"

    result["classification"] = best_classification
    result["flagged"] = _CLASSIFICATION_SEVERITY[best_classification] >= _CLASSIFICATION_SEVERITY["large_discrepancy"]
    if result["flagged"]:
        result["status"] = "flagged"
        result["reason"] = (
            "exceeds_parent_total" if best_classification == "exceeds_parent_total" else "magnitude_discrepancy"
        )
    elif best_classification == "incompatible":
        result["status"] = "incompatible_reference"
        result["reason"] = "incompatible_evidence"
    elif best_classification == "no_reference":
        result["status"] = "no_reference"
    else:
        result["status"] = "reviewed"

    if emitter is not None:
        emitter.emit(
            stage, "magnitude_check", bucket=bucket, country=geography,
            data={
                "check_type": check_type, "ratio": result.get("ratio"),
                "classification": result.get("classification"), "references": result.get("references"),
            },
            status=result.get("status"),
        )

    return result


# --------------------------------------------------------------------------- #
# Year / period consistency -- a distinct check across MULTIPLE facts within
# one bucket (driver, intensity, price), not a magnitude/ratio comparison.
# --------------------------------------------------------------------------- #
def check_year_period_consistency(
    facts: dict[str, dict[str, Any]], *, year_tolerance: int = _YEAR_TOLERANCE_YEARS,
) -> dict[str, Any]:
    """`facts` maps a label (e.g. "driver", "intensity", "price") to a fact
    dict carrying `year`/`unit`. Flags pairwise year gaps beyond
    `year_tolerance`, and explicit stated-period conflicts (via
    `units.periods_conflict`) -- never invents a conflict from an absent
    year/period on either side."""
    labelled = {k: f for k, f in facts.items() if isinstance(f, dict)}
    mismatches: list[dict[str, Any]] = []
    labels = list(labelled.keys())
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            a_label, b_label = labels[i], labels[j]
            a, b = labelled[a_label], labelled[b_label]
            a_year, b_year = a.get("year"), b.get("year")
            if a_year is not None and b_year is not None:
                try:
                    gap = abs(int(a_year) - int(b_year))
                except (TypeError, ValueError):
                    gap = None
                if gap is not None and gap > year_tolerance:
                    mismatches.append({
                        "kind": "year_gap", "a": a_label, "b": b_label,
                        "a_year": a_year, "b_year": b_year, "gap_years": gap,
                        "note": f"'{a_label}' ({a_year}) and '{b_label}' ({b_year}) are {gap} year(s) apart "
                                f"-- beyond the {year_tolerance}-year tolerance, flagged for review.",
                    })
            au = units.parse_unit_string(a.get("unit")) if a.get("unit") else None
            bu = units.parse_unit_string(b.get("unit")) if b.get("unit") else None
            if au is not None and bu is not None and units.periods_conflict(au, bu):
                mismatches.append({
                    "kind": "period_conflict", "a": a_label, "b": b_label,
                    "a_period": au.per_period, "b_period": bu.per_period,
                    "note": f"'{a_label}' states period '{au.per_period}' but '{b_label}' states "
                            f"'{bu.per_period}' -- explicit, conflicting time bases.",
                })
    return {"flagged": bool(mismatches), "mismatches": mismatches}


# --------------------------------------------------------------------------- #
# Derived-quantity origin diagnosis (Phase 10 integration).
# --------------------------------------------------------------------------- #
def diagnose_derivation_origin(derived_fact: dict[str, Any]) -> dict[str, Any]:
    """Uses Phase 10's derivation metadata (`derivation_method`, `input_facts`,
    `formula`, `derivation_lineage`) -- when present -- to identify WHICH part
    of a derivation chain a magnitude blowup most likely originated from: an
    input's own magnitude, an allocation share, an intensity, a period
    conversion, or another (upstream) derivation step. Never guesses when the
    metadata is absent -- returns `origin="unknown"` rather than inventing a
    diagnosis from an ordinary (non-derived) fact."""
    method = derived_fact.get("derivation_method")
    input_facts: dict[str, Any] = derived_fact.get("input_facts") or {}
    if not method:
        return {"origin": "unknown", "detail": "fact carries no derivation_method -- not a Phase 10 derivation output."}

    # Which named input is itself the largest-magnitude number, and whether
    # any input is itself a further derivation (an upstream derivation step).
    upstream_derivations = [
        name for name, f in input_facts.items()
        if isinstance(f, dict) and f.get("verification_status") == "derived" and method not in ("rate_conversion", "unit_conversion")
    ]

    if method in ("regional_share", "global_share"):
        origin = "allocation_share" if "country_share" in input_facts or any(
            "share" in n for n in input_facts
        ) else "input_magnitude"
        detail = (
            f"Derived via '{method}': value = total x share. A discrepancy here most likely traces to "
            "either the total input's own magnitude, or the allocation share used -- inspect "
            f"input_facts {list(input_facts.keys())} individually."
        )
    elif method == "activity_intensity":
        origin = "intensity"
        detail = (
            "Derived via 'activity_intensity': value = activity x intensity. A magnitude blowup here "
            "most commonly traces to the intensity (consumption-rate) input, since activity/driver "
            "figures are usually independently sized in this pipeline -- inspect the 'intensity' input."
        )
    elif method in ("rate_conversion", "unit_conversion"):
        origin = "period_conversion" if method == "rate_conversion" else "unit_conversion"
        detail = f"Derived via '{method}' -- a deterministic period/unit conversion, not an empirical claim; a discrepancy here indicates the SOURCE value or the conversion basis, not new evidence."
    elif method == "production_utilization":
        origin = "input_magnitude"
        detail = "Derived via 'production_utilization': value = production x utilization_rate -- inspect the production input's own magnitude first."
    elif upstream_derivations:
        origin = "another_derivation_step"
        detail = f"One or more inputs ({upstream_derivations}) are themselves derived -- the discrepancy may originate further upstream in the derivation chain."
    else:
        origin = "input_magnitude"
        detail = f"Derived via '{method}' -- inspect the named input facts {list(input_facts.keys())}."

    return {
        "origin": origin, "detail": detail, "derivation_method": method,
        "formula": derived_fact.get("formula"), "input_facts": input_facts,
        "derivation_lineage": derived_fact.get("derivation_lineage"),
    }


# --------------------------------------------------------------------------- #
# Independent-reference acquisition -- reuses the existing Evidence Router
# (`evidence_router.propose_evidence_plan` + `execute_evidence_plan`) for one
# reference, and the plain web-search + `evidence.extract_structured_fact`
# path (the same mechanism `magnitude.py`/`triangulation.py` already use) for
# a second, independently-sourced one -- so a magnitude check is never
# resting on a single acquisition mechanism's own blind spot.
# --------------------------------------------------------------------------- #
def gather_independent_references(
    llm: GenLLM, cfg: Any, *, market: str, bucket_name: str, bucket_description: str,
    quantity_name: str, unit: str, geography: str = "global", year: int | None = None,
    max_references: int = _MAX_REFERENCES,
) -> list[dict[str, Any]]:
    """Best-effort. Never raises -- a failed/empty search just yields fewer
    (possibly zero) references, which `evaluate_magnitude` handles as
    `status="no_reference"`, never a failure. Each reference already carries
    its own `verification_status` (stamped by `evidence.py`, the same
    acceptance-gate every other acquirer in this package funnels through)."""
    refs: list[dict[str, Any]] = []

    # Reference 1 -- Evidence Router: typed, ranked, real-institution source
    # chain, same mechanism the driver/intensity/price call sites use.
    try:
        plan = evidence_router.propose_evidence_plan(
            llm, market, bucket_name, bucket_description, quantity_name, unit,
            "independent magnitude-validation reference",
            "This is used ONLY as an independent sanity-check reference for an already-built value -- "
            "never as a replacement figure. Propose a genuinely real, checkable institutional source.",
        )
        fact, _attempts = evidence_router.execute_evidence_plan(
            llm, plan, geography, context=f"Independent reference figure for '{quantity_name}' ({unit}) in {market}",
            year=year,
        )
        if fact and fact.get("value"):
            refs.append({**fact, "geography": fact.get("geography") or geography, "acquisition_route": "evidence_router"})
    except Exception:  # noqa: BLE001
        pass

    # Reference 2 -- plain web-search + extract_structured_fact (a distinct
    # acquisition path from reference 1, mirroring `magnitude.py`'s existing
    # single-reference mechanism), only attempted if more references wanted.
    if len(refs) < max_references and getattr(cfg, "enable_web", False):
        try:
            snippets = gather(
                [f"global {market} {quantity_name} total {unit}", f"worldwide {quantity_name} {unit} statistics"],
                per_query=4, cap=8,
            )
            fact = evidence.extract_structured_fact(
                llm, snippets, unit=unit,
                context=(
                    f"Independent reference figure for '{quantity_name}' ({unit}) in {market} -- used ONLY "
                    "to sanity-check magnitude, never as a replacement value"
                ),
            )
            if fact and fact.get("value"):
                refs.append({**fact, "geography": fact.get("geography") or geography, "acquisition_route": "web_search"})
        except Exception:  # noqa: BLE001
            pass

    return refs[:max_references]


# --------------------------------------------------------------------------- #
# Graph node -- additive. Produces `magnitude_validation_flags` /
# `magnitude_validation_records`, distinct from `magnitude.py`'s own
# `driver_magnitude_flags` (unchanged, still wired, still passes its own
# tests unmodified).
# --------------------------------------------------------------------------- #
def magnitude_cross_validation_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg = state["config"]
    llm: GenLLM = state["llm"]
    market = state["market_name"]
    log = state.setdefault("log", [])
    emitter = state.get("emitter")
    buckets = state.get("usage_buckets", [])
    drivers = state.get("drivers", {})
    quantities = state.get("quantities", {})
    driver_facts = state.get("driver_facts", {})
    prices = state.get("prices", {})
    price_facts = state.get("price_facts", {})
    usage_values = state.get("usage_values", {})
    total_market_value = state.get("total_market_value")

    records: list[dict[str, Any]] = []

    for b in buckets:
        name = b["name"]
        driver = drivers.get(name, {})
        driver_name = driver.get("driver_name", name)
        unit = driver.get("unit", "units")
        per_country = quantities.get(name, {})
        per_country_facts = driver_facts.get(name, {})
        if not per_country:
            continue

        used_total = sum(v.get("value", 0.0) for v in per_country.values())

        # --- driver_magnitude ---
        if used_total > 0:
            refs = gather_independent_references(
                llm, cfg, market=market, bucket_name=name, bucket_description=b.get("description", ""),
                quantity_name=driver_name, unit=unit,
            )
            rec = evaluate_magnitude(
                check_type="driver_magnitude", subject_label=f"'{name}' driver total",
                value=round(used_total, 6), unit=unit, source_name="pipeline (summed per-country)",
                verification_status=driver.get("evidence_type", "assumption"), references=refs,
                emitter=emitter, bucket=name,
            )
            rec["bucket"] = name
            records.append(rec)

        # --- intensity_magnitude (global constant, one representative fact) ---
        intensity_fact = next(
            (f.get("intensity") for f in per_country_facts.values() if isinstance(f.get("intensity"), dict)),
            None,
        )
        if intensity_fact and intensity_fact.get("value") not in (None, 0):
            refs = gather_independent_references(
                llm, cfg, market=market, bucket_name=name, bucket_description=b.get("description", ""),
                quantity_name=f"consumption intensity for {driver_name}", unit=intensity_fact.get("unit", "units"),
            )
            rec = evaluate_magnitude(
                check_type="intensity_magnitude", subject_label=f"'{name}' intensity",
                value=intensity_fact.get("value"), unit=intensity_fact.get("unit", "units"),
                source_name=intensity_fact.get("source", ""),
                source_url=intensity_fact.get("source_url", ""),
                verification_status=intensity_fact.get("evidence_type", "assumption"),
                year=intensity_fact.get("year"), references=refs,
                emitter=emitter, bucket=name,
            )
            rec["bucket"] = name
            records.append(rec)

        # --- derived_quantity_magnitude + country_vs_regional_or_global ---
        for country, fact_bundle in per_country_facts.items():
            driver_raw = fact_bundle.get("driver_raw") or {}
            if not driver_raw.get("derivation_method"):
                continue  # only Phase 10 derivation outputs get this check
            input_facts = driver_raw.get("input_facts") or {}
            total_input = input_facts.get("total")
            parent_total = total_input.get("value") if isinstance(total_input, dict) else None
            country_value = per_country.get(country, {}).get("value")
            diagnosis = diagnose_derivation_origin(driver_raw)
            rec = evaluate_magnitude(
                check_type="derived_quantity_magnitude",
                subject_label=f"'{name}' derived driver value for {country}",
                value=country_value, unit=unit, source_name=driver_raw.get("source", ""),
                verification_status=driver_raw.get("evidence_type", "derived"),
                geography=country, year=driver_raw.get("year"),
                parent_total=parent_total,
                parent_label=f"regional/global total feeding '{name}'/{country}",
                emitter=emitter, bucket=name,
            )
            rec["bucket"] = name
            rec["country"] = country
            rec["derivation_diagnosis"] = diagnosis
            if rec["flagged"] or parent_total is not None:
                records.append(rec)

        # --- price_magnitude ---
        price = prices.get(name)
        pfact = price_facts.get(name) or {}
        if price and price.get("asp_usd_per_unit"):
            priced_unit = price.get("priced_unit", unit)
            refs = gather_independent_references(
                llm, cfg, market=market, bucket_name=name, bucket_description=b.get("description", ""),
                quantity_name=f"average selling price for {driver_name}", unit=f"USD per {priced_unit}",
            )
            rec = evaluate_magnitude(
                check_type="price_magnitude", subject_label=f"'{name}' price",
                value=price.get("asp_usd_per_unit"), unit=f"USD per {priced_unit}",
                source_name=pfact.get("source", ""), source_url=pfact.get("source_url", ""),
                verification_status=price.get("evidence_type", "assumption"),
                year=pfact.get("year"), references=refs,
                emitter=emitter, bucket=name,
            )
            rec["bucket"] = name
            records.append(rec)

        # --- usage_vs_parent_category (bucket $ value vs. the total it rolls into) ---
        bucket_total_usd_mn = sum((usage_values.get(name) or {}).values())
        if bucket_total_usd_mn and total_market_value:
            rec = evaluate_magnitude(
                check_type="usage_vs_parent_category", subject_label=f"'{name}' usage value",
                value=round(bucket_total_usd_mn, 4), unit="USD Mn",
                verification_status="derived", parent_total=total_market_value,
                parent_label="Included Demand (total market value)",
                emitter=emitter, bucket=name,
            )
            rec["bucket"] = name
            if rec["flagged"]:
                records.append(rec)

        # --- year_period_consistency across driver/intensity/price for this bucket ---
        rep_driver = next((f.get("driver_raw") for f in per_country_facts.values() if isinstance(f.get("driver_raw"), dict)), None)
        facts_for_period_check = {
            k: v for k, v in {"driver": rep_driver, "intensity": intensity_fact, "price": pfact}.items()
            if isinstance(v, dict)
        }
        if len(facts_for_period_check) >= 2:
            period_check = check_year_period_consistency(facts_for_period_check)
            if period_check["flagged"]:
                records.append({
                    "check_type": "year_period_consistency", "bucket": name,
                    "flagged": True, "status": "flagged", "reason": "period_mismatch",
                    "classification": "period_mismatch", "mismatches": period_check["mismatches"],
                    "notes": [m["note"] for m in period_check["mismatches"]],
                })

    flagged = [r for r in records if r.get("flagged")]
    log.append(
        f"Magnitude & cross-source validation: {len(flagged)} discrepancy flag(s) out of "
        f"{len(records)} check(s) run across {len(buckets)} usage bucket(s)."
    )
    return {"magnitude_validation_records": records, "magnitude_validation_flags": flagged}

"""Evidence extraction + deterministic verification -- the choke point that
stops "the LLM says this is observed" from ever being trusted at face value.

The LLM's job is only to point at a specific web-search snippet and read a
number out of it (or a regional/global figure plus a stated derivation
basis). Whether that number actually counts as `observed`/`derived`/
`assumption` is then decided here, in code, by checking whether the claimed
value is actually traceable inside the snippet text it says it came from.
Mirrors the discipline already used in `supply/estimator.py`
(`_ground_company`: real data fetched first, LLM only derives from it, never
trusted to self-report groundedness) and `supply/evidence_estimator.py`
(deterministic regex extraction, no LLM in that path at all -- here an LLM
is needed because driver units vary market-to-market, so extraction is
LLM-assisted, but acceptance is not).
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from me_engine.generation.llm import GenLLM

from . import fact_validator

# --- Phase 0 (DEMAND_SIDE_EVIDENCE_PLAN.md) -- additive schema only ---------
#
# `VerificationStatus` is the target 4-value replacement for today's 3-value
# `evidence_type` (`observed`/`derived`/`assumption`). It is defined here, now,
# so every later phase has a stable name to migrate toward, but Phase 0 itself
# changes NO node's behavior -- nothing in this module yet produces
# `verified_observed` or `source_found_unverified`; `verify_and_tag`/
# `build_fact`/`build_structured_fact`/`worse_of` below are unchanged.
#
# The 4 states (see the plan's "Critical evidence contract" section):
#   verified_observed       -- LLM found a source+number AND code independently
#                              verified the source is accessible and the
#                              number is traceable in the retrieved content.
#   source_found_unverified -- LLM found a source+number but the source could
#                              not be verified as accessible; not corroborated.
#   derived                 -- deterministically computed from verified/derived
#                              inputs, with a logged formula.
#   assumption               -- no evidence and no derivation path exists.
VerificationStatus = Literal[
    "verified_observed", "source_found_unverified", "derived", "assumption",
]

# Conservative compatibility mapping for code that has not yet been migrated
# (that migration is Phase 6's job) -- an unverified claim must never be
# allowed to look stronger than a bare assumption to a 3-value consumer, so
# `source_found_unverified` maps down to `assumption`, never up to `observed`.
_LEGACY_MAP: dict[str, str] = {
    "verified_observed": "observed",
    "source_found_unverified": "assumption",
    "derived": "derived",
    "assumption": "assumption",
}


def legacy_evidence_type(verification_status: str) -> str:
    """Map a (future) 4-value `VerificationStatus` down to today's 3-value
    `evidence_type` (`observed`/`derived`/`assumption`), for any code not yet
    migrated to the 4-value enum. Unrecognized input defaults to the safest
    (weakest) interpretation, `assumption`, never silently promoted."""
    return _LEGACY_MAP.get(verification_status, "assumption")


# --- Phase 6 (DEMAND_SIDE_EVIDENCE_PLAN.md) -- the field-name decision -----
#
# Decision (documented per that phase's instructions): the field is kept
# named `evidence_type` everywhere (it is already the entrenched name across
# every acquirer/consumer/test written in Phases 0-5, including the
# `source_found_unverified` value Phase 2 already produces under that same
# key) -- renaming the KEY would touch far more call sites than renaming the
# VALUE vocabulary underneath it, for zero behavioral gain. What Phase 6
# actually changes is the value vocabulary: `evidence_type` now carries the
# full, authoritative 4-value `VerificationStatus` (with `"observed"` renamed
# to `"verified_observed"` at every producer), not a 3-value stand-in. Every
# gate function below additionally stamps a `verification_status` key with
# the IDENTICAL value, so any caller written against either name (this
# module's own docstrings/the plan's contract example use
# `verification_status`; every pre-existing call site uses `evidence_type`)
# reads the same, single, authoritative classification -- there is exactly
# one decision made per fact, exposed under two names, never two competing
# judgments that could drift apart.
PROVENANCE_STATES: tuple[str, ...] = (
    "verified_observed", "derived", "source_found_unverified", "assumption",
)


def _stamp_verification_status(fact: dict[str, Any]) -> dict[str, Any]:
    """Mirror `evidence_type` onto `verification_status` in place. The ONE
    place this mirroring happens, so the two names can never independently
    drift -- every gate function below routes its return value through this
    before handing it back to a caller."""
    fact["verification_status"] = fact["evidence_type"]
    return fact

_SCALE_WORDS = {
    "trillion": 1e12, "billion": 1e9, "bn": 1e9,
    "million": 1e6, "mn": 1e6, "thousand": 1e3, "k": 1e3,
}
_NUM_RE = re.compile(
    r"([-+]?\d[\d,]*\.?\d*)\s*(trillion|billion|bn|million|mn|thousand|k)?\b",
    re.IGNORECASE,
)
_REL_TOL = 0.03  # 3% -- snippet text is prose, not a structured field


def _numbers_in_text(text: str) -> list[float]:
    out: list[float] = []
    for m in _NUM_RE.finditer(text or ""):
        raw, scale = m.group(1), m.group(2)
        try:
            val = float(raw.replace(",", ""))
        except ValueError:
            continue
        if val == 0 and not scale:
            continue  # bare "0" matches are noise, not real figures
        if scale:
            val *= _SCALE_WORDS[scale.lower()]
        out.append(val)
    return out


def _is_close(a: float, b: float, rel_tol: float = _REL_TOL) -> bool:
    if a == 0 and b == 0:
        return True
    return abs(a - b) / max(abs(a), abs(b), 1e-9) <= rel_tol


def verify_and_tag(fact: dict[str, Any]) -> tuple[str, float]:
    """Deterministic, code-only. Returns (evidence_type, confidence), where
    `evidence_type` is one of the `VerificationStatus` values this function
    can actually produce (`verified_observed`/`derived`/`assumption` --
    `source_found_unverified` is never returned here; that tag is only ever
    stamped by `build_unverified_claim_fact`, the LLM-claim last-resort
    rung's own gate, never by this text-traceability check)."""
    evidence_text = str(fact.get("evidence_text") or "")
    value = fact.get("value")
    if not evidence_text or value is None:
        return "assumption", 0.2

    numbers = _numbers_in_text(evidence_text)
    value_traceable = any(_is_close(float(value), n) for n in numbers)
    is_direct = bool(fact.get("is_direct_figure", True))

    if value_traceable and is_direct:
        return "verified_observed", 0.9
    if fact.get("derivation_basis") or value_traceable:
        # either explicitly named as a derivation from a bigger figure, or the
        # figure IS present in the snippet but was declared non-direct (e.g. a
        # regional total the LLM says it then allocated) -- either way it is
        # traceable to real text, just not a direct per-target figure.
        return "derived", 0.6
    return "assumption", 0.3


def extract_structured_fact(
    llm: GenLLM, snippets: list[str], *, unit: str, context: str, geography: str = "",
    emitter: Any | None = None, stage: str = "fact_extraction", bucket: str | None = None,
) -> dict[str, Any] | None:
    """Ask the LLM to point at ONE snippet and read a figure out of it.
    Returns None if there was no evidence to search at all, or the LLM
    honestly found nothing -- callers must treat that as "no evidence",
    never fabricate a number to fill the gap themselves.

    Extraction-quality gate (`fact_validator.py`, post-Phase-12 addendum):
    the LLM must ALSO declare what geography/unit/metric it believes the
    snippet's figure actually measures, and a clearly-detected mismatch
    (e.g. a dollar-value figure returned for a physical-quantity request, or
    a different named geography for a claimed DIRECT figure) is rejected
    outright here -- the candidate never reaches the traceability check
    below at all. Ambiguous/unclassifiable input is never rejected -- only a
    specific, clearly-detected mismatch is."""
    if not snippets:
        return None

    system = (
        "You are an evidence-extraction analyst. You will be given real web-search "
        f"snippets. Your ONLY job is to find, inside those snippets, an explicit "
        f"numeric figure for: {context} (target unit: {unit}). Do NOT estimate or "
        "invent a number -- if no snippet states one, say so honestly by returning "
        "found=false. If a snippet only gives a regional/global figure instead of "
        "the exact one needed, you may propose deriving from it, but set "
        "is_direct_figure=false and name the derivation basis explicitly. "
        "CRITICAL: 'value' must be the FULLY EXPANDED absolute number, with any scale "
        "word applied -- if the snippet says '8.9 million barrels per day', value must "
        "be 8900000, NOT 8.9; if it says '$2.1 billion', value must be 2100000000. "
        "Never return the bare digits in front of a scale word as the value. "
        "If the number you found actually measures something DIFFERENT from what was "
        "requested -- e.g. a market value in dollars instead of a physical production/"
        "consumption volume, installed capacity instead of production, a different "
        "country/region, or a different year -- you must return found=false rather "
        "than reporting it anyway. Never invent a citation, year, unit, or geography "
        "that isn't actually stated in the snippet."
    )
    user = (
        f"{context}\nTarget unit: {unit}\n\nSNIPPETS:\n"
        + "\n".join(f"[{i}] {s}" for i, s in enumerate(snippets))
        + "\n\n"
        'Return JSON: {"found": <true|false>, "value": <number or null>, '
        '"snippet_index": <int index of the snippet this came from, or null>, '
        '"year": <int or null>, "source": "<short source label from the snippet>", '
        '"citation": "<the exact phrase in the snippet stating the figure>", '
        '"is_direct_figure": <true if the snippet states exactly this figure; false '
        'if derived from a bigger regional/global number>, "derivation_basis": '
        '"<if is_direct_figure is false, the formula/basis used, e.g. \'population '
        'share of region\'; else empty>, "matched_metric_label": "<in your own words, '
        'what this number actually measures, per the snippet>", "matched_geography": '
        '"<the geography the snippet actually states this figure for, or empty if '
        'unstated>", "matched_unit": "<the unit the snippet actually states, or empty>"}'
    )
    llm_error: str | None = None
    try:
        data = llm.complete_json(system, user)
    except Exception as exc:  # noqa: BLE001
        data = None
        llm_error = str(exc)
    if emitter is not None:
        emitter.emit(
            stage, "llm_call", bucket=bucket, country=geography or None,
            llm={"prompt": f"{system}\n\n{user}", "response": json.dumps(data, ensure_ascii=False) if data else "", "error": llm_error},
            status="failed" if llm_error else "success",
        )
    if not data or not data.get("found"):
        return None
    try:
        value = float(data.get("value"))
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None

    idx = data.get("snippet_index")
    snippet_text = snippets[idx] if isinstance(idx, int) and 0 <= idx < len(snippets) else ""
    is_direct_figure = bool(data.get("is_direct_figure", True))

    rejection_reason = fact_validator.validate_candidate(
        requested_unit=unit, requested_context=context, requested_geography=geography,
        matched_unit=str(data.get("matched_unit") or ""),
        matched_geography=str(data.get("matched_geography") or ""),
        matched_metric_label=str(data.get("matched_metric_label") or ""),
        evidence_text=snippet_text, is_direct_figure=is_direct_figure,
    )
    if emitter is not None:
        emitter.emit(
            stage, "fact_validation", bucket=bucket, country=geography or None,
            validation={
                "requested_unit": unit, "requested_context": context,
                "matched_unit": data.get("matched_unit"), "matched_geography": data.get("matched_geography"),
                "matched_metric_label": data.get("matched_metric_label"),
                "rejection_reason": rejection_reason,
            },
            status="rejected" if rejection_reason else "pass",
        )
    if rejection_reason:
        # NOTE: the rejection reason is captured in the audit event above --
        # the function's own return type/behavior for existing callers is
        # intentionally unchanged (still bare `None`).
        return None

    fact: dict[str, Any] = {
        "value": value,
        "unit": unit,
        "year": data.get("year"),
        "source": str(data.get("source") or "").strip(),
        "citation": str(data.get("citation") or "").strip(),
        "evidence_text": snippet_text,
        "is_direct_figure": is_direct_figure,
        "derivation_basis": str(data.get("derivation_basis") or "").strip(),
    }
    fact["evidence_type"], fact["confidence"] = verify_and_tag(fact)
    return _stamp_verification_status(fact)


def extract_fact_from_source(
    llm: GenLLM, results: list, *, unit: str, context: str, source_name: str, geography: str = "",
    emitter: Any | None = None, stage: str = "fact_extraction", bucket: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Same acceptance discipline as `extract_structured_fact`, but scoped to
    ONE named source's own search results (`web_search.SearchResult` list --
    real results, each with a real URL). Used by `web_acquirer.py`, one of
    the acquirers dispatched by `evidence_router.py`'s typed source chain. Unlike `extract_structured_fact`, the returned
    fact's `source`/`source_url` are NEVER the LLM's own guess -- `source`
    is the caller-supplied, human-checkable institution name for this rung
    of the chain, and `source_url` is the actual matched result's URL. A
    fact is only tagged `observed` if BOTH the value is text-traceable AND
    a real URL backs it -- a traceable value with no URL is downgraded to
    `derived`, never presented as directly-observed.

    Returns `(fact_or_None, rejection_reason_or_None)` -- the rejection
    reason is only populated when a candidate WAS found but rejected by the
    extraction-quality gate below (`fact_validator.py`, post-Phase-12
    addendum: metric/geography mismatch), so the caller's attempt log can
    distinguish "genuinely nothing found" (`extraction_failed`) from "found
    something, but it answered a different question" (a specific
    `wrong_metric_*`/`geography_mismatch` code)."""
    if not results:
        return None, None

    system = (
        "You are an evidence-extraction analyst. You will be given real web-search results, "
        f"ALL from the source '{source_name}'. Your ONLY job is to find, inside these results, "
        f"an explicit numeric figure for: {context} (target unit: {unit}). Do NOT estimate or "
        "invent a number -- if none of these results states one, say so honestly by returning "
        "found=false. If a result only gives a regional/global figure instead of the exact one "
        "needed, you may propose deriving from it, but set is_direct_figure=false and name the "
        "derivation basis explicitly. "
        "CRITICAL: 'value' must be the FULLY EXPANDED absolute number, with any scale word "
        "applied -- if the result says '8.9 million barrels per day', value must be 8900000, "
        "NOT 8.9; if it says '$2.1 billion', value must be 2100000000. Never return the bare "
        "digits in front of a scale word as the value. "
        "If the number you found actually measures something DIFFERENT from what was requested "
        "-- e.g. a market value in dollars instead of a physical production/consumption volume, "
        "installed capacity instead of production, a different country/region, or a different "
        "year -- you must return found=false rather than reporting it anyway. Never invent a "
        "citation, year, unit, or geography that isn't actually stated in the result."
    )
    user = (
        f"{context}\nTarget unit: {unit}\nSource: {source_name}\n\nRESULTS:\n"
        + "\n".join(f"[{i}] {r.snippet}" for i, r in enumerate(results))
        + "\n\n"
        'Return JSON: {"found": <true|false>, "value": <number or null>, '
        '"result_index": <int index of the result this came from, or null>, '
        '"year": <int or null>, "citation": "<the exact phrase in the result stating the figure>", '
        '"is_direct_figure": <true if the result states exactly this figure; false if derived '
        'from a bigger regional/global number>, "derivation_basis": "<if is_direct_figure is '
        "false, the formula/basis used, e.g. 'population share of region'; else empty>\", "
        '"matched_metric_label": "<in your own words, what this number actually measures, per '
        'the result>", "matched_geography": "<the geography the result actually states this '
        'figure for, or empty if unstated>", "matched_unit": "<the unit the result actually '
        'states, or empty>"}'
    )
    llm_error: str | None = None
    try:
        data = llm.complete_json(system, user)
    except Exception as exc:  # noqa: BLE001
        data = None
        llm_error = str(exc)
    if emitter is not None:
        emitter.emit(
            stage, "llm_call", bucket=bucket, country=geography or None,
            llm={"prompt": f"{system}\n\n{user}", "response": json.dumps(data, ensure_ascii=False) if data else "", "error": llm_error},
            status="failed" if llm_error else "success",
        )
    if not data or not data.get("found"):
        return None, None
    try:
        value = float(data.get("value"))
    except (TypeError, ValueError):
        return None, None
    if value < 0:
        return None, None

    idx = data.get("result_index")
    if not isinstance(idx, int) or not (0 <= idx < len(results)):
        # Never accept a figure that isn't anchored to one of the ACTUAL
        # retrieved results -- an out-of-range/missing index means the LLM
        # didn't really point at real evidence, whatever it claims found=true.
        return None, None
    result = results[idx]
    is_direct_figure = bool(data.get("is_direct_figure", True))

    rejection = fact_validator.validate_candidate(
        requested_unit=unit, requested_context=context, requested_geography=geography,
        matched_unit=str(data.get("matched_unit") or ""),
        matched_geography=str(data.get("matched_geography") or ""),
        matched_metric_label=str(data.get("matched_metric_label") or ""),
        evidence_text=result.snippet, is_direct_figure=is_direct_figure,
    )
    if emitter is not None:
        emitter.emit(
            stage, "fact_validation", bucket=bucket, country=geography or None,
            validation={
                "requested_unit": unit, "matched_unit": data.get("matched_unit"),
                "matched_geography": data.get("matched_geography"),
                "matched_metric_label": data.get("matched_metric_label"),
                "rejection_reason": rejection,
            },
            status="rejected" if rejection else "pass",
        )
    if rejection:
        return None, rejection

    fact = build_fact(
        value=value, unit=unit, year=data.get("year"), source_name=source_name,
        source_url=result.url, citation=str(data.get("citation") or "").strip(),
        evidence_text=result.snippet, is_direct_figure=is_direct_figure,
        derivation_basis=str(data.get("derivation_basis") or "").strip(),
    )
    return fact, None


def build_fact(
    *, value: float, unit: str, year: int | None, source_name: str, source_url: str,
    citation: str, evidence_text: str, is_direct_figure: bool = True, derivation_basis: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The ONE shared acceptance gate every TEXT-based acquirer (web page,
    PDF) calls to turn a candidate (value, evidence_text, source_url) into a
    tagged fact -- centralizes `verify_and_tag` + the "no URL -> never
    observed" rule so there is exactly one place deciding observed/derived/
    assumption for prose-text evidence, not a slightly-different copy per
    acquirer."""
    fact: dict[str, Any] = {
        "value": value, "unit": unit, "year": year, "source": source_name,
        "source_url": source_url, "citation": citation, "evidence_text": evidence_text,
        "is_direct_figure": is_direct_figure, "derivation_basis": derivation_basis,
    }
    if extra:
        fact.update(extra)
    fact["evidence_type"], fact["confidence"] = verify_and_tag(fact)
    if fact["evidence_type"] == "verified_observed" and not fact["source_url"]:
        # Text-traceable but no verifiable URL behind it (e.g. a malformed
        # search result) -- never let that pass as directly observed.
        fact["evidence_type"] = "derived"
        fact["confidence"] = min(fact["confidence"], 0.5)
    return _stamp_verification_status(fact)


def build_structured_fact(
    *, value: float | None, unit: str, year: int | None, source_name: str, source_url: str,
    location_ref: str, geography_matched: bool, ambiguous: bool = False,
) -> dict[str, Any]:
    """Acceptance gate for STRUCTURED sources (CSV/XLSX/API) -- here
    'observed' means a deterministic CODE-LEVEL lookup resolved to exactly
    one cell/field, not a text-regex match (there's no prose to regex
    against). `ambiguous` (the LLM's column/row/path mapping required a
    fuzzy step, e.g. summing several matching rows) downgrades to
    `derived` -- never silently promoted to `observed`."""
    if value is None:
        evidence_type, confidence = "assumption", 0.2
    elif ambiguous:
        evidence_type, confidence = "derived", 0.6
    else:
        evidence_type, confidence = "verified_observed", 0.9
    if evidence_type == "verified_observed" and not source_url:
        evidence_type, confidence = "derived", min(confidence, 0.5)
    fact = {
        "value": value, "unit": unit, "year": year, "source": source_name, "source_url": source_url,
        "citation": location_ref, "evidence_text": location_ref, "location_ref": location_ref,
        "is_direct_figure": not ambiguous,
        "derivation_basis": "" if not ambiguous else "aggregated/matched multiple rows or fields",
        "evidence_type": evidence_type, "confidence": confidence,
    }
    return _stamp_verification_status(fact)


def build_unverified_claim_fact(
    *, value: float, unit: str, year: int | None, source_name: str, source_url: str = "",
    document_title: str = "", geography: str = "", citation: str = "", evidence_text: str = "",
    reasoning: str = "",
) -> dict[str, Any]:
    """Phase 2 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) acceptance gate -- the ONLY
    way `evidence_router`'s last-resort LLM-claim rung is allowed to turn a
    candidate (value, source) into a fact. ALWAYS returns
    `evidence_type="source_found_unverified"`, hard-coded here and never
    overridable by anything the LLM says -- no confidence wording, no
    plausible-sounding institution name, no repeated claim can promote this
    to `observed`/`derived`. That promotion can only happen through the
    ordinary `build_fact`/`build_structured_fact` gates, when code itself
    independently verified the source (Phase 1's `asset_verifier`) and
    traced the value inside the actually-fetched content.

    Requires a non-empty `source_name`: an unnamed claim carries no more
    information than a bare assumption and must not be given this stronger,
    more-trusted-looking label -- callers must check for that themselves
    before calling this (the whole point of the check living at the call
    site too is that this function has no way to tell "empty because the
    LLM had nothing" apart from "empty because of a caller bug", so it
    refuses either way)."""
    if not source_name:
        raise ValueError("build_unverified_claim_fact requires a non-empty source_name")
    fact = {
        "value": value, "unit": unit, "year": year, "source": source_name,
        "source_url": source_url, "document_title": document_title, "geography": geography,
        "citation": citation, "evidence_text": evidence_text, "is_direct_figure": False,
        "derivation_basis": reasoning,
        "evidence_type": "source_found_unverified", "confidence": 0.3,
    }
    return _stamp_verification_status(fact)


# Phase 6 -- the 4-value ranking (`DEMAND_SIDE_EVIDENCE_PLAN.md`'s "worse_of /
# combined provenance" requirement): a source that was FOUND by the LLM but
# never independently verified is weaker than anything code actually
# confirmed or deterministically derived from confirmed inputs, but it is
# still a specific, attributable, real claim -- strictly more informative
# than a bare, unattributed guess. Hence:
#   verified_observed (3) > derived (2) > source_found_unverified (1) > assumption (0)
# An unrecognized/missing value defaults to rank 0 (`assumption`) -- the
# safest, weakest interpretation, never silently promoted (mirrors
# `legacy_evidence_type`'s own conservative-default philosophy above).
_PROVENANCE_RANK = {
    "verified_observed": 3, "derived": 2, "source_found_unverified": 1, "assumption": 0,
}
_RANK_TO_PROVENANCE = {v: k for k, v in _PROVENANCE_RANK.items()}


def worse_of(*evidence_types: str) -> str:
    """Overall provenance for a value built from multiple facts (e.g. driver x
    intensity, or a price bridged by a conversion factor) is never stronger
    than its weakest input, ranked per the 4-value order above. A combined
    driver x intensity (or driver x price) result can therefore never read as
    `verified_observed` unless EVERY required input independently was --
    one `source_found_unverified` or `assumption` input among otherwise-
    verified ones caps the whole combination at that weaker tier."""
    ranks = [_PROVENANCE_RANK.get(t, 0) for t in evidence_types if t]
    if not ranks:
        return "assumption"
    return _RANK_TO_PROVENANCE[min(ranks)]

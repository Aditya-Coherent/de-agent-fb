"""Agent node functions for the usage/application-based demand-side pipeline.

V2 discipline: the LLM is an intelligence/research layer -- it defines the
market, discovers usage buckets, proposes candidate demand drivers, reads
numbers out of real web-search snippets, and resolves ambiguity. It never
gets to unilaterally declare a number "observed" -- `evidence.verify_and_tag`
decides that deterministically by checking the claimed value is actually
traceable inside the snippet text it says it came from. All arithmetic
(conversion, aggregation) happens in code (`conversion.py`, `calculate_node`
below), never inside an LLM call.
"""
from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from me_engine.generation.llm import GenLLM
from me_engine.generation.semantic_search import embed

from . import anchor, conversion, derivation, driver_library, evidence, evidence_router, evidence_strategy, period_guard, pricing_strategy, reference_data, units
from .web_search import gather

_OVERLAP_SIMILARITY_THRESHOLD = 0.83  # cosine sim above this = likely double-counting

# Generic (not tied to any one market) vocabulary, split into two SHAPES of
# non-consumption concept -- deliberately not one bucket, because they get
# routed differently:
#   FINANCIAL_ACTIVITY -- trading/investing/speculating on the product as a
#     paper asset. Not demand in any sense; fully excluded, never quantified.
#   INVENTORY_RESERVE -- holding real physical stock (a real quantity that
#     genuinely exists) for future use/security. Not CONSUMPTION demand, but
#     a real, separately-reportable number -- quantified same as any other
#     bucket, just kept out of the "Included Demand" total.
# Neither regex names a specific product, so both apply equally to crude oil,
# gold, wheat, currencies, or fine wine. This is the deterministic half of
# the physical-end-use check: it can force routing even if the LLM classifier
# is fooled, so the LLM is never the only protection (verified failure mode:
# crude oil's usage_decomposition_node produced "Financial Trading and
# Investment" and "Strategic Reserves and Government Stockpiling" as ordinary
# usage buckets, and "paper barrel" trading volume swelled to 73% of total).
_FINANCIAL_ACTIVITY_TERMS = re.compile(
    r"\b(trading|invest(?:ment|ing|or)?|speculat\w*|hedg\w*|futures\s+market|"
    r"financial\s+market|portfolio)\b",
    re.IGNORECASE,
)
_INVENTORY_RESERVE_TERMS = re.compile(
    r"\b(stockpil\w*|strategic\s+reserves?|government\s+reserves?|"
    r"inventory\s+(?:build-?up|stock)|warehousing\s+stock)\b",
    re.IGNORECASE,
)

_DEFAULT_COVERAGE_THRESHOLD_PCT = 85.0


def _coverage_threshold_pct() -> float:
    """Configurable, not hardcoded as universally acceptable for every market --
    read from the environment (own knob, deliberately NOT added to the shared
    `GenConfig` used by Desk Research, to keep this pipeline's config changes
    fully isolated). Defaults to 85%, per the initial guidance."""
    try:
        return float(os.environ.get("DEMAND_USAGE_COVERAGE_THRESHOLD_PCT", _DEFAULT_COVERAGE_THRESHOLD_PCT))
    except (TypeError, ValueError):
        return _DEFAULT_COVERAGE_THRESHOLD_PCT


_IDENTITY_INTENSITY: dict[str, Any] = {
    "value": 1.0, "unit": "identity", "evidence_type": "verified_observed",
    "verification_status": "verified_observed", "confidence": 1.0,
    "source": "", "citation": "identity conversion -- driver is already denominated in the final demand unit",
    "evidence_text": "", "year": None, "derivation_basis": "",
}


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))  # embed() already L2-normalises


def _evidence_brief(snippets: list[str], limit: int = 10) -> str:
    if not snippets:
        return "(no external evidence available)"
    return "WEB EVIDENCE:\n" + "\n".join(f"- {s}" for s in snippets[:limit])


def _fact_for_report(fact: dict[str, Any], snippet_cap: int = 300) -> dict[str, Any]:
    """Trim a structured fact to a report-friendly shape (short snippet, no
    giant text blobs duplicated everywhere)."""
    return {k: v for k, v in fact.items() if k != "evidence_text"} | {
        "evidence_snippet": (fact.get("evidence_text") or "")[:snippet_cap]
    }


def _fallback_assumption(llm: GenLLM, system: str, user: str, unit: str) -> dict[str, Any]:
    """Last-resort LLM estimate when no evidence exists at any level. Always
    tagged `assumption` in code -- the LLM is told to label it that way, but
    the tag is set here regardless of what the LLM says, so it can never be
    silently upgraded.

    `call_failed` distinguishes two very different situations that both used
    to collapse into the same "assumption, value=0" shape: the LLM genuinely
    judging 0 as its best estimate, vs. the call itself throwing (network,
    auth, rate-limit, TLS) and silently defaulting to 0/$1 downstream. The
    former is a real (if weak) data point; the latter is an infrastructure
    failure masquerading as one and must be visibly flagged, never silent."""
    try:
        data = llm.complete_json(system, user)
        value = max(0.0, float(data.get("value", 0.0) or 0.0))
        rationale = str(data.get("rationale") or "").strip()
        call_failed = False
    except Exception as exc:  # noqa: BLE001
        value, rationale, call_failed = 0.0, f"LLM call failed: {exc}", True
    return {
        "value": value, "unit": unit, "evidence_type": "assumption",
        "verification_status": "assumption", "confidence": 0.2,
        "source": "", "citation": "", "evidence_text": "", "year": None,
        "derivation_basis": rationale, "call_failed": call_failed,
    }


def _fallback_assumption_batch(
    llm: GenLLM, *, system: str, user: str, countries: list[str], unit: str
) -> dict[str, dict[str, Any]]:
    """Batched last-resort estimate across every country that still lacks ANY
    evidence (country, regional, or global) for this bucket -- ONE LLM call,
    never N independent single-country calls. A single-country call has no
    way to anchor its guess against its neighbours' plausible relative scale
    (verified failure mode: independent per-country calls produced the US at
    100x the UK's value for the same consumer-driven bucket, with nothing
    resembling a population/market-size relationship behind it). Always
    tagged `assumption` in code regardless of what the LLM claims.

    `call_failed` (see `_fallback_assumption`'s docstring) is set for EVERY
    country in this batch when the single underlying LLM call throws -- a
    connection/auth/rate-limit failure here silently produced value=0 for an
    entire bucket's worth of countries in one shot before this was tracked."""
    call_failed = False
    try:
        data = llm.complete_json(system, user)
    except Exception as exc:  # noqa: BLE001
        data, call_failed = {}, True
        call_error = str(exc)
    out: dict[str, dict[str, Any]] = {}
    for c in countries:
        raw = data.get(c, {}) or {}
        try:
            value = max(0.0, float(raw.get("value", 0.0) or 0.0))
        except (TypeError, ValueError):
            value = 0.0
        rationale = f"LLM call failed: {call_error}" if call_failed else str(raw.get("rationale") or "").strip()
        out[c] = {
            "value": value, "unit": unit, "evidence_type": "assumption",
            "verification_status": "assumption", "confidence": 0.2,
            "source": "", "citation": "", "evidence_text": "", "year": None,
            "derivation_basis": rationale, "call_failed": call_failed,
        }
    return out


# --------------------------------------------------------------------------- #
# NOTE: market understanding + geography scope now live in `setup.py`
# (`market_setup_draft_node`), as ONE user-editable/approvable Market Setup
# Draft (Scope, Geography, Base Year, Market Unit, Physical Unit) -- the
# former `market_understanding_node`/`geography_node` here were redundant
# with that and have been removed. `usage_decomposition_node` below now
# reads the approved setup's `market_definition`/`countries` (see
# `taxonomy.run_taxonomy_phase`), never re-deriving either.
# --------------------------------------------------------------------------- #
# 3. usage/application decomposition
# --------------------------------------------------------------------------- #
def usage_decomposition_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg = state["config"]
    llm: GenLLM = state["llm"]
    market = state["market_name"]
    log = state.setdefault("log", [])
    emitter = state.get("emitter")
    if emitter is not None:
        emitter.emit("usage_decomposition", "stage_start", data={"node": "usage_decomposition_node"})

    snippets: list[str] = []
    if cfg.enable_web:
        try:
            snippets = gather([
                f"{market} applications",
                f"{market} end-use industries",
                f"{market} usage segments by application",
            ], per_query=5, cap=15)
        except Exception as exc:  # noqa: BLE001
            log.append(f"[usage_decomposition] could not fetch web evidence: {exc}")

    system = (
        "You are a demand-decomposition analyst. Discover THIS market's own real "
        "usage/application buckets -- the distinct ways the underlying product/service is "
        "actually consumed or applied. Do NOT invent a generic textbook list (e.g. never "
        "default to 'Industrial, Commercial, Residential' unless the evidence specifically "
        "supports that for THIS market). Buckets MUST be mutually exclusive -- no bucket "
        "should be a subset or rewording of another. Prefer 3-7 buckets that together cover "
        "the great majority of real demand.\n\n"
        "Each unit of the product sold in a year must fall in EXACTLY ONE bucket, so the buckets "
        "add up to total annual demand. Split by WHO buys/consumes it or WHAT it is used in "
        "(e.g. households, restaurants & catering, food industry, non-food industry; or consumer, "
        "enterprise, government) -- NOT by activities one item is used for at the same time "
        "(e.g. never split a phone into 'messaging', 'navigation' and 'payments': the same phone "
        "would be counted three times)."
    )
    user = (
        f"Market: {market}\nDefinition: {state.get('market_definition', {}).get('text', '')}\n\n"
        f"{_evidence_brief(snippets, limit=15)}\n\n"
        'Return JSON: {"buckets": [{"name": "<short label>", "description": "<1-2 sentences, '
        'specific enough to distinguish it from every other bucket>"}]}'
    )
    data = llm.complete_json(system, user)
    if emitter is not None:
        emitter.emit(
            "usage_decomposition", "llm_call",
            llm={"prompt": f"{system}\n\n{user}", "response": json.dumps(data, ensure_ascii=False)},
            status="success",
        )
    buckets = []
    for b in data.get("buckets", []) or []:
        name = str(b.get("name") or "").strip()
        desc = str(b.get("description") or "").strip()
        if name:
            buckets.append({"name": name, "description": desc or name})
    if not buckets:
        buckets = [{"name": market, "description": market}]
        log.append("[usage_decomposition] LLM returned no buckets -- falling back to one whole-market bucket")

    # Deterministic overlap check (embeddings), never silently merged -- just flagged.
    overlap_flags: list[str] = []
    try:
        vecs = [embed(b["description"]) for b in buckets]
        for i in range(len(buckets)):
            for j in range(i + 1, len(buckets)):
                sim = _cosine(vecs[i], vecs[j])
                if sim >= _OVERLAP_SIMILARITY_THRESHOLD:
                    overlap_flags.append(
                        f"{buckets[i]['name']} ~ {buckets[j]['name']} (similarity {sim:.2f})"
                    )
    except Exception as exc:  # noqa: BLE001
        log.append(f"[usage_decomposition] overlap embedding check failed: {exc}")

    log.append(f"Discovered {len(buckets)} usage/application buckets"
               + (f"; {len(overlap_flags)} possible overlap(s) flagged" if overlap_flags else ""))
    if emitter is not None:
        emitter.emit("usage_decomposition", "stage_end", data={"node": "usage_decomposition_node", "num_buckets": len(buckets)})
    return {"usage_buckets": buckets, "overlap_flags": overlap_flags, "usage_snippets": snippets}


# --------------------------------------------------------------------------- #
# 3a2. scope -> taxonomy validation -- generic (no market-specific rules).
#    The APPROVED Market Setup's scope statement must control the taxonomy:
#    a proposed usage bucket can be in_scope, ambiguous, or out_of_scope
#    relative to that scope text. This is a FLAG, never an auto-removal --
#    same "flag before approval, let the human decide" pattern already used
#    by usage_coverage_node's missing-category check -- so the approval gate
#    is where the actual decision belongs, not this node.
#
#    Verified failure mode this catches (without ever naming it here): a
#    market named just "Cement" produced a "Medical and dental applications"
#    usage bucket (conflating construction cement with orthopedic/dental
#    "bone cement") -- purely from the scope text, with zero hardcoded
#    knowledge of cement, bone cement, or any other market's typical
#    exclusions, this check would flag that bucket ambiguous/out_of_scope
#    for a human to see before approving.
# --------------------------------------------------------------------------- #
#    Phase 9 (DEMAND_SIDE_EVIDENCE_PLAN.md) -- aggregate interpretation layer.
#    A high FLAGGED RATIO across buckets usually means the SCOPE STATEMENT
#    itself is too narrow for a usage-based decomposition of this market
#    (e.g. all 6 Crude Oil buckets flagging at once), not that every
#    individual bucket happens to be wrong. Pure deterministic arithmetic on
#    top of the per-bucket verdicts above -- no LLM call, no market-specific
#    threshold.
_SCOPE_TOO_NARROW_RATIO_THRESHOLD = 0.5

# Generic, market-agnostic parse of an explicit exclusion clause in a scope
# statement (e.g. "crude oil; refined products excluded" or "excluding
# downstream refined products") into a flat list of excluded phrases. Purely
# syntactic -- no vocabulary tied to any one market -- so it only catches the
# "obvious structural contradiction" case where an excluded phrase's own
# words literally reappear in a proposed bucket's name/description. The
# harder semantic cases (a downstream/refined product that doesn't share a
# word with the raw-material scope, an unrelated industry that merely shares
# a word with the market name) are left to the LLM classification below,
# which is explicitly prompted to reason about exactly those two shapes.
# "excluding X" / "excludes X" / "not including X" -- exclusion verb BEFORE
# the excluded phrase.
_EXCLUSION_PATTERN_PRE = re.compile(
    r"\b(?:excluding|excludes|does\s+not\s+include|not\s+including|excl\.)\b[:,]?\s*"
    r"([^.;\n]+)",
    re.IGNORECASE,
)
# "X excluded" / "X is excluded" -- exclusion verb AFTER the excluded phrase
# (e.g. "crude oil; refined products excluded"), delimited by the previous
# clause boundary (start of text, `;`, `,`, or `.`).
_EXCLUSION_PATTERN_POST = re.compile(
    r"(?:^|[;,.]\s*)([^.;,\n]+?)\s+(?:is\s+|are\s+)?excluded\b",
    re.IGNORECASE,
)
_SCOPE_STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "for", "in", "on", "to", "any", "all", "other",
    "related", "products", "product", "with", "without", "is", "are", "this", "that",
}


def _extract_excluded_terms(scope_text: str) -> list[str]:
    scope_text = scope_text or ""
    terms: list[str] = []
    for pattern in (_EXCLUSION_PATTERN_PRE, _EXCLUSION_PATTERN_POST):
        for m in pattern.finditer(scope_text):
            phrase = m.group(1).strip(" .;:")
            for part in re.split(r"\s*(?:,|;|\band\b|\bor\b)\s*", phrase, flags=re.IGNORECASE):
                part = part.strip()
                if part:
                    terms.append(part)
    return terms


def _significant_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zA-Z]+", (text or "").lower())
            if len(w) > 3 and w not in _SCOPE_STOPWORDS}


def _deterministic_exclusion_conflict(scope_text: str, bucket_name: str, bucket_description: str) -> str | None:
    """Returns a reason string if a scope-excluded phrase's significant
    words are a subset of this bucket's own words (a literal, structural
    contradiction), else None.

    Methodology fix (2026-08-26, CRUDE_OIL_METHODOLOGY_FIX): this is a
    purely SYNTACTIC word-overlap signal -- it cannot distinguish "this
    bucket describes a downstream/refined product used as a legitimate
    demand-derivation pathway for the target commodity" (never disqualifying
    on its own -- see `scope_taxonomy_validation_node`'s Target-Commodity
    Demand Relevance prompt) from "this bucket is a pure trade/distribution/
    resale activity of an already-excluded product" (genuinely out of
    scope). Because it cannot tell those apart, the caller now applies it
    only as a floor of 'ambiguous' (surfacing the tension for optional human
    review, never blocking approval on its own -- see `taxonomy.approve_taxonomy`'s
    out_of_scope-only gate), never as a hard, one-way escalation to
    'out_of_scope'. An LLM verdict that independently reached 'out_of_scope'
    on its own reasoning is left exactly as the LLM said."""
    bucket_words = _significant_words(f"{bucket_name} {bucket_description}")
    if not bucket_words:
        return None
    for term in _extract_excluded_terms(scope_text):
        term_words = _significant_words(term)
        if term_words and term_words <= bucket_words:
            return f"scope explicitly excludes '{term}', whose terms appear directly in this bucket"
    return None


def scope_taxonomy_validation_node(state: dict[str, Any]) -> dict[str, Any]:
    llm: GenLLM = state["llm"]
    market = state["market_name"]
    log = state.setdefault("log", [])
    scope_text = state.get("market_definition", {}).get("text", "")
    buckets = state.get("usage_buckets", [])
    emitter = state.get("emitter")
    if emitter is not None:
        emitter.emit("scope_taxonomy_validation", "stage_start", data={"node": "scope_taxonomy_validation_node"})

    physical_unit = state.get("physical_unit", "") or "its own physical unit"
    system = (
        "You are a Target-Commodity Demand Relevance auditor for a bottom-up, USAGE-BASED "
        f"demand-side market-sizing methodology. The market being sized is '{market}', measured in "
        f"'{physical_unit}'. This is the KEY distinction the audit must apply: a usage bucket does "
        "NOT need to itself BE the target commodity, and does NOT need to be priced/valued as the "
        "target commodity directly -- a bucket may legitimately describe DOWNSTREAM, refined, or "
        "adjacent-product demand/activity, used ONLY as an intermediate signal to derive how much "
        "of the TARGET commodity that demand requires (e.g. for a Crude Oil market, 'asphalt "
        "production volume' or 'transportation fuel consumption' are legitimate usage buckets, "
        "because that downstream demand implies a crude-oil/feedstock requirement -- the bucket is "
        "never itself valued at the downstream product's own price; only the derived crude-oil "
        "quantity is ever priced).\n\n"
        "Classify EACH usage bucket as in_scope, ambiguous, or out_of_scope using ONLY this "
        "'Target-Commodity Demand Relevance Check':\n"
        "  1. Does this usage genuinely create demand for the target commodity -- directly, or via "
        "a downstream/refined/adjacent product that consumes the target commodity as an input?\n"
        "  2. Is there a defensible, explainable causal relationship between the usage and the "
        "target commodity's own consumption?\n"
        "  3. Could that relationship plausibly be quantified with evidence, a documented "
        "conversion factor/ratio, or a transparent derivation -- even if no such evidence has been "
        "found yet at this stage?\n"
        "  4. Could the resulting quantity ultimately be expressed in the target commodity's own "
        f"unit ('{physical_unit}')?\n\n"
        "Mark 'out_of_scope' ONLY when the usage genuinely cannot be connected to the target "
        "commodity's own demand at all -- e.g. a wholly unrelated industry/product that merely "
        "shares a word or a similar-sounding name with the market (a construction material vs. an "
        "unrelated medical product with a similar name), or a pure trade/distribution/resale "
        "activity of an ALREADY-refined product that creates no NEW demand for the target commodity "
        "itself. NEVER mark a bucket out_of_scope merely because it describes a downstream, refined, "
        "or adjacent product -- that is not disqualifying on its own. Mark 'ambiguous' when a real, "
        "explainable link plausibly exists but the derivation path or evidence availability is "
        "unclear; a human should decide. Judge ONLY from the scope statement and this relevance "
        "test, NEVER from hardcoded knowledge of what a specific market typically excludes -- this "
        "must work for ANY market. For every bucket, also return a short 'evidence' quote/paraphrase "
        "of the specific scope language (or its absence) that supports your verdict, and a "
        "'confidence' from 0.0 to 1.0."
    )
    user = (
        f"Market: {market}\nApproved scope: {scope_text}\n\n"
        + json.dumps([{"name": b["name"], "description": b["description"]} for b in buckets], ensure_ascii=False)
        + '\n\nReturn JSON: {"buckets": [{"name": "<same name>", "classification": '
        '"<in_scope|ambiguous|out_of_scope>", "reason": "<1 sentence>", "evidence": '
        '"<short quote/paraphrase from the scope text, or empty if none>", "confidence": <0.0-1.0>}]}'
    )
    llm_error: str | None = None
    try:
        data = llm.complete_json(system, user)
        by_name = {str(e.get("name", "")).strip(): e for e in (data.get("buckets") or [])}
    except Exception as exc:  # noqa: BLE001
        log.append(f"[scope_taxonomy_validation] LLM call failed: {exc}")
        by_name = {}
        llm_error = str(exc)
    if emitter is not None:
        emitter.emit(
            "scope_taxonomy_validation", "llm_call",
            llm={"prompt": f"{system}\n\n{user}", "response": json.dumps(by_name, ensure_ascii=False) if by_name else "", "error": llm_error},
            status="failed" if llm_error else "success",
        )

    flags: dict[str, dict[str, Any]] = {}
    for b in buckets:
        entry = by_name.get(b["name"], {})
        classification = str(entry.get("classification") or "in_scope").strip()
        if classification not in ("in_scope", "ambiguous", "out_of_scope"):
            classification = "in_scope"  # absence of a verdict must not silently flag a legitimate bucket
        reason = str(entry.get("reason") or "").strip()
        llm_evidence = str(entry.get("evidence") or "").strip()
        try:
            confidence = float(entry.get("confidence", 0.6))
        except (TypeError, ValueError):
            confidence = 0.6
        confidence = max(0.0, min(1.0, confidence))
        source = "llm"

        det_reason = _deterministic_exclusion_conflict(scope_text, b["name"], b.get("description", ""))
        if det_reason and classification == "in_scope":
            # Methodology fix (2026-08-26, CRUDE_OIL_METHODOLOGY_FIX): a
            # literal exclusion-phrase word-overlap is only a FLOOR of
            # 'ambiguous' now, never a forced 'out_of_scope' -- it cannot,
            # on its own, distinguish a legitimate downstream-demand
            # derivation pathway from a genuinely excluded activity (see
            # `_deterministic_exclusion_conflict`'s docstring). Surfaces the
            # tension for optional review without requiring an override to
            # approve (`taxonomy.approve_taxonomy`'s gate only blocks
            # `out_of_scope`, not `ambiguous`). An LLM verdict that already
            # said `out_of_scope`/`ambiguous` on its own reasoning is left as-is.
            classification = "ambiguous"
            reason = det_reason if not reason else f"{det_reason}; {reason}"
            llm_evidence = llm_evidence or det_reason
            confidence = max(confidence, 0.6)
            source = "deterministic"

        flags[b["name"]] = {
            "classification": classification,
            "reason": reason,
            "evidence": llm_evidence,
            "confidence": confidence,
            "source": source,
        }
        if classification != "in_scope":
            log.append(
                f"[scope_taxonomy_validation] '{b['name']}' flagged {classification} vs. approved "
                f"scope -- {reason} (flag only -- not auto-removed; review before approving)"
            )

    n_flagged = sum(1 for f in flags.values() if f["classification"] != "in_scope")
    flagged_ratio = (n_flagged / len(buckets)) if buckets else 0.0
    likely_scope_too_narrow = flagged_ratio > _SCOPE_TOO_NARROW_RATIO_THRESHOLD
    aggregate = {
        "flagged_count": n_flagged,
        "total_buckets": len(buckets),
        "flagged_ratio": flagged_ratio,
        "likely_scope_too_narrow": likely_scope_too_narrow,
    }
    log.append(
        f"Scope/taxonomy validation: {n_flagged}/{len(buckets)} bucket(s) flagged ambiguous/"
        "out-of-scope vs. the approved Market Setup scope"
    )
    if likely_scope_too_narrow:
        log.append(
            f"[scope_taxonomy_validation] {flagged_ratio:.0%} of buckets were flagged -- this "
            "usually means the approved Market Setup's SCOPE STATEMENT is too narrow for this "
            "usage-based taxonomy; consider revising the scope rather than the taxonomy."
        )
    if emitter is not None:
        emitter.emit("scope_taxonomy_validation", "stage_end", data={"node": "scope_taxonomy_validation_node", "n_flagged": n_flagged})
    return {"scope_validation_flags": flags, "scope_taxonomy_aggregate": aggregate}


# --------------------------------------------------------------------------- #
# 3b. physical end-use validation -- generic (no market-specific rules).
#    A usage bucket only earns a place in the demand->value calculation if it
#    represents genuine physical consumption/application of the market's own
#    product. Financial/inventory concepts (trading, investment, stockpiling,
#    reserves) are NOT demand and must not be priced as if they were -- but
#    they are kept in the audit trail, never silently deleted.
# --------------------------------------------------------------------------- #
def usage_validation_node(state: dict[str, Any]) -> dict[str, Any]:
    llm: GenLLM = state["llm"]
    market = state["market_name"]
    log = state.setdefault("log", [])
    buckets = state.get("usage_buckets", [])
    emitter = state.get("emitter")
    if emitter is not None:
        emitter.emit("usage_validation", "stage_start", data={"node": "usage_validation_node"})

    # Deterministic pass first, independent of the LLM -- a generic financial or
    # inventory/reserve vocabulary match can force routing on its own, so the
    # LLM is never the sole line of defense. Financial-activity match always
    # wins if both would somehow match the same text.
    deterministic_financial: dict[str, str] = {}
    deterministic_inventory: dict[str, str] = {}
    for b in buckets:
        text = f"{b['name']} {b['description']}"
        m_fin = _FINANCIAL_ACTIVITY_TERMS.search(text)
        if m_fin:
            deterministic_financial[b["name"]] = m_fin.group(0)
            continue
        m_inv = _INVENTORY_RESERVE_TERMS.search(text)
        if m_inv:
            deterministic_inventory[b["name"]] = m_inv.group(0)

    system = (
        "You are a demand-methodology auditor. For EACH usage/application bucket below, "
        "decide whether it represents GENUINE PHYSICAL CONSUMPTION/APPLICATION of the "
        "market's own product -- i.e. the product is actually used up, installed, applied, "
        "or consumed for some real-world purpose. It is NOT a physical end-use if it instead "
        "describes a financial/economic activity (trading, investing, speculation, hedging, "
        "futures/derivatives) or a storage/inventory concept (stockpiling, strategic reserves, "
        "warehousing) where the product itself is not being consumed or applied for anything -- "
        "these measure market ACTIVITY or HOLDINGS, not demand, and must not be priced as if "
        "they were. This check must work for ANY market, not just the one below -- judge each "
        "bucket on this general principle, not on market-specific knowledge."
    )
    user = (
        f"Market: {market}\n\n"
        + json.dumps([{"name": b["name"], "description": b["description"]} for b in buckets], ensure_ascii=False)
        + '\n\nReturn JSON: {"buckets": [{"name": "<same name>", "is_physical_end_use": <true|false>, '
        '"classification": "<physical_consumption|financial_activity|inventory_reserve|other_irrelevant>", '
        '"reason": "<1 sentence>", "confidence": <0-1>}]}'
    )
    llm_error: str | None = None
    try:
        data = llm.complete_json(system, user)
        llm_by_name = {str(e.get("name", "")).strip(): e for e in (data.get("buckets") or [])}
    except Exception as exc:  # noqa: BLE001
        log.append(f"[usage_validation] classifier LLM call failed: {exc}")
        llm_by_name = {}
        llm_error = str(exc)
    if emitter is not None:
        emitter.emit(
            "usage_validation", "llm_call",
            llm={"prompt": f"{system}\n\n{user}", "response": json.dumps(llm_by_name, ensure_ascii=False) if llm_by_name else "", "error": llm_error},
            status="failed" if llm_error else "success",
        )

    # Three-way routing (NOT a binary keep/exclude anymore):
    #   physical_consumption -> "usage_buckets", flows through demand_driver/
    #     quantification/pricing exactly as before -> counted as Included Demand.
    #   inventory_reserve     -> ALSO flows through the same downstream nodes
    #     (quantified/priced identically -- a reserve level is a real number),
    #     but tagged so calculate_node keeps its value OUT of Included Demand
    #     and reports it separately ("Separately Reported Related Demand").
    #   financial_activity / other_irrelevant -> fully excluded, never
    #     quantified at all (unchanged from the prior binary behavior).
    demand_buckets: list[dict[str, Any]] = []  # physical_consumption + inventory_reserve, tagged
    excluded_buckets: list[dict[str, Any]] = []
    classifications: dict[str, dict[str, Any]] = {}

    for b in buckets:
        name = b["name"]
        entry = llm_by_name.get(name, {})
        llm_physical = bool(entry.get("is_physical_end_use", True))  # default true: absence of a
        # verdict must not silently exclude a legitimate bucket -- only an
        # explicit false, or a deterministic hit below, routes it away.
        llm_classification = str(entry.get("classification") or "").strip() or "physical_consumption"
        reason = str(entry.get("reason") or "").strip()
        confidence = entry.get("confidence")
        confidence = float(confidence) if isinstance(confidence, (int, float)) else 0.5

        fin_hit = deterministic_financial.get(name)
        inv_hit = deterministic_inventory.get(name)

        if fin_hit or (not llm_physical and llm_classification in ("financial_activity", "other_irrelevant")):
            category = "excluded_financial_or_other"
        elif inv_hit or llm_classification == "inventory_reserve":
            category = "inventory_reserve"
        else:
            category = "physical_consumption"

        det_hit = fin_hit or inv_hit
        record = {
            "usage_name": name,
            "is_physical_end_use": category == "physical_consumption",
            "bucket_category": category,
            "classification": llm_classification if not det_hit else category,
            "reason": reason or (f"Matched generic term: '{det_hit}'" if det_hit else ""),
            "confidence": confidence,
            "deterministic_flag": det_hit,
            "llm_verdict": llm_physical,
        }
        classifications[name] = record

        if category == "excluded_financial_or_other":
            excluded_buckets.append({**b, **record, "status": "excluded_non_physical_usage"})
            log.append(
                f"[usage_validation] '{name}' excluded as non-physical "
                f"({record['classification']}"
                + (f", deterministic match: '{det_hit}'" if det_hit else "")
                + f") -- {record['reason']}"
            )
        else:
            demand_buckets.append({**b, "bucket_category": category})
            if category == "inventory_reserve":
                log.append(
                    f"[usage_validation] '{name}' routed to inventory/reserve -- will be quantified "
                    f"but reported separately from Included (consumption) Demand -- {record['reason']}"
                )

    if not demand_buckets:
        # Never leave the pipeline with zero buckets to calculate -- if the
        # classifier (wrongly) rejected everything, fall back to the original
        # set (as physical_consumption) rather than silently producing a $0 market.
        log.append("[usage_validation] every bucket was classified non-physical -- "
                   "keeping the original set rather than producing an empty demand base")
        demand_buckets = [{**b, "bucket_category": "physical_consumption"} for b in buckets]
        excluded_buckets = []

    n_inventory = sum(1 for b in demand_buckets if b["bucket_category"] == "inventory_reserve")
    n_physical = len(demand_buckets) - n_inventory
    log.append(
        f"Physical end-use validation: {n_physical}/{len(buckets)} bucket(s) kept as Included "
        f"(consumption) demand, {n_inventory} routed to inventory/reserve (reported separately)"
        + (f", {len(excluded_buckets)} excluded: {[e['usage_name'] for e in excluded_buckets]}"
           if excluded_buckets else "")
    )
    if emitter is not None:
        emitter.emit("usage_validation", "stage_end", data={"node": "usage_validation_node", "n_excluded": len(excluded_buckets)})
    return {
        "usage_buckets": demand_buckets,
        "excluded_usage_buckets": excluded_buckets,
        "usage_classifications": classifications,
    }


# --------------------------------------------------------------------------- #
# 3c. usage coverage review -- generic (no market-specific rules), evidence-first.
#    Checks whether the FINAL physical bucket set plausibly covers the market's
#    major real-world applications. A gap found here is a FLAG ONLY -- it never
#    becomes a priced bucket in this run, and it is never accepted from the LLM
#    without either a cited evidence snippet or an explicit "no evidence, this
#    is reasoning only" admission. A deterministic embedding-similarity filter
#    (same technique as the overlap check in usage_decomposition_node) discards
#    any "missing" candidate that's actually just a reworded duplicate of a
#    bucket that already exists, so the LLM is not the only protection here
#    either. Runs BEFORE demand_driver/calculate -- coverage must be assessed
#    before any number is built, not diagnosed after the fact.
# --------------------------------------------------------------------------- #
def usage_coverage_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg = state["config"]
    llm: GenLLM = state["llm"]
    market = state["market_name"]
    log = state.setdefault("log", [])
    buckets = state.get("usage_buckets", [])
    emitter = state.get("emitter")
    if emitter is not None:
        emitter.emit("usage_coverage", "stage_start", data={"node": "usage_coverage_node"})

    snippets: list[str] = []
    if cfg.enable_web:
        try:
            snippets = gather([
                f"{market} major applications",
                f"{market} key end-use industries",
            ], per_query=5, cap=15)
        except Exception as exc:  # noqa: BLE001
            log.append(f"[usage_coverage] could not fetch web evidence: {exc}")

    system = (
        "You are a coverage auditor for a bottom-up demand model. You will be given the "
        "FINAL list of physical usage/application buckets already discovered for this market, "
        "and some web evidence. Your ONLY job is to identify any MAJOR real-world physical "
        "application of this market's product that is NOT represented by the given buckets -- "
        "genuinely missing coverage, not a rewording of an existing bucket. This must work for "
        "ANY market -- judge on general principle, not specific knowledge of one industry.\n\n"
        "For EVERY candidate you propose: if the evidence below actually mentions it, set "
        "evidence_type='evidence_backed' and quote the supporting phrase in evidence_snippet. "
        "If you believe it's missing but the evidence doesn't state it, set "
        "evidence_type='reasoning_only' and leave evidence_snippet empty -- never blend the two, "
        "and never invent a citation. If the existing buckets already look complete, return an "
        "empty list -- do not manufacture a gap to fill a quota.\n\n"
        "Also give your own best estimate of what PERCENTAGE of this market's real physical "
        "consumption demand is represented by the given buckets (estimated_coverage_pct, 0-100). "
        "Be honest and critical -- absence of an obvious gap is not proof of 100% coverage; most "
        "real markets have some genuinely hard-to-categorize long tail."
    )
    user = (
        f"Market: {market}\n"
        f"Existing physical usage buckets: "
        + json.dumps([{"name": b["name"], "description": b["description"]} for b in buckets], ensure_ascii=False)
        + f"\n\n{_evidence_brief(snippets, limit=15)}\n\n"
        'Return JSON: {"missing": [{"name": "<application name>", "reason": "<1-2 sentences>", '
        '"evidence_type": "<evidence_backed|reasoning_only>", "evidence_snippet": "<quoted phrase '
        'or empty string>", "confidence": <0-1>}], "estimated_coverage_pct": <0-100>}'
    )
    llm_error: str | None = None
    try:
        data = llm.complete_json(system, user)
        candidates = data.get("missing") or []
        llm_coverage_pct = data.get("estimated_coverage_pct")
        llm_coverage_pct = float(llm_coverage_pct) if isinstance(llm_coverage_pct, (int, float)) else 70.0
    except Exception as exc:  # noqa: BLE001
        log.append(f"[usage_coverage] gap-check LLM call failed: {exc}")
        candidates = []
        llm_coverage_pct = 70.0  # conservative default when the call itself fails -- never assume complete
        llm_error = str(exc)
    if emitter is not None:
        emitter.emit(
            "usage_coverage", "llm_call",
            llm={"prompt": f"{system}\n\n{user}", "response": json.dumps(candidates, ensure_ascii=False) if candidates else "", "error": llm_error},
            status="failed" if llm_error else "success",
        )

    # Deterministic dedup filter: a "missing" candidate that's actually just a
    # reworded duplicate of an existing bucket is a false positive, not a gap.
    missing_usage_flags: list[dict[str, Any]] = []
    try:
        existing_vecs = [embed(b["description"]) for b in buckets] if buckets else []
    except Exception as exc:  # noqa: BLE001
        log.append(f"[usage_coverage] embedding existing buckets failed: {exc}")
        existing_vecs = []

    for c in candidates:
        name = str(c.get("name") or "").strip()
        reason = str(c.get("reason") or "").strip()
        if not name:
            continue
        evidence_type = str(c.get("evidence_type") or "").strip()
        if evidence_type not in ("evidence_backed", "reasoning_only"):
            evidence_type = "reasoning_only"  # never let an unlabeled claim pass as evidence-backed
        evidence_snippet = str(c.get("evidence_snippet") or "").strip() if evidence_type == "evidence_backed" else ""
        confidence = c.get("confidence")
        confidence = float(confidence) if isinstance(confidence, (int, float)) else 0.5

        try:
            cand_vec = embed(f"{name}: {reason}")
            max_sim = max((_cosine(cand_vec, v) for v in existing_vecs), default=0.0)
        except Exception:  # noqa: BLE001
            max_sim = 0.0
        if max_sim >= _OVERLAP_SIMILARITY_THRESHOLD:
            log.append(
                f"[usage_coverage] discarded candidate gap '{name}' -- {max_sim:.2f} similarity to "
                "an existing bucket suggests it's already covered, just reworded."
            )
            continue

        missing_usage_flags.append({
            "name": name,
            "reason": reason,
            "evidence_type": evidence_type,
            "evidence_snippet": evidence_snippet,
            "confidence": confidence,
            "max_similarity_to_existing": round(max_sim, 4),
        })

    # Deterministic ceiling on the LLM's own coverage estimate -- not just a
    # suggestion, a hard bound. Never let the LLM claim near-total coverage
    # (absence of a found gap isn't proof of completeness), and reduce the
    # ceiling further for every evidence-backed / reasoning-only gap that
    # actually survived the dedup filter above.
    n_evidence_backed = sum(1 for m in missing_usage_flags if m["evidence_type"] == "evidence_backed")
    n_reasoning_only = len(missing_usage_flags) - n_evidence_backed
    ceiling = min(95.0, 100.0 - 8.0 * n_evidence_backed - 3.0 * n_reasoning_only)
    ceiling = max(ceiling, 30.0)  # a handful of flags should never collapse this to near-zero
    llm_coverage_pct = min(100.0, max(0.0, llm_coverage_pct))
    coverage_pct = round(min(llm_coverage_pct, ceiling), 1)

    threshold = _coverage_threshold_pct()
    log.append(
        f"Usage coverage review: {len(missing_usage_flags)} potentially missing major physical "
        f"application(s) flagged" + (f": {[m['name'] for m in missing_usage_flags]}" if missing_usage_flags else "")
        + f" -- flag only, not added to this run's calculation. Estimated coverage: {coverage_pct:.1f}% "
        f"(configurable threshold: {threshold:.0f}%)"
    )
    if emitter is not None:
        emitter.emit("usage_coverage", "stage_end", data={"node": "usage_coverage_node", "coverage_pct": coverage_pct})
    return {"missing_usage_flags": missing_usage_flags, "coverage_pct": coverage_pct}


# --------------------------------------------------------------------------- #
# 4. demand driver identification -- library lookup first, LLM proposal + evidence-need
#    declaration second. The library stores REUSABLE driver/conversion methodologies,
#    never a fixed usage taxonomy (usage buckets above stay market-specific).
# --------------------------------------------------------------------------- #
# A driver that counts a STOCK (people, users, installed base, fleet) is not
# demand by itself -- demand is what is bought/consumed in one year. Verified
# failure (Smartphones 2B): "1.8 billion smartphone users" x $420 was taken
# as one year's purchases, $756B for a single cell.
_STOCK_DRIVER_RE = re.compile(
    r"\b(installed base|in (active )?use|in service|in operation|users?|subscribers?|owners?|"
    r"population|people|persons|households|fleet|stock of)\b",
    re.IGNORECASE,
)


def _singular(word: str) -> str:
    word = word.strip()
    return word[:-1] if word.endswith("s") and not word.endswith("ss") else word


def enforce_annual_flow(driver: dict[str, Any], physical_unit: str, log: list[str], bucket_name: str) -> dict[str, Any]:
    """If the driver is a stock and has no per-year conversion, add one:
    demand = stock x <product units bought per stock unit per year>. The
    rate is then searched for / estimated like any other intensity."""
    if driver.get("consumption_unit"):
        return driver
    text = f"{driver.get('driver_name', '')} {driver.get('unit', '')}"
    if not _STOCK_DRIVER_RE.search(text):
        return driver
    product_unit = physical_unit or driver.get("final_demand_unit") or "units"
    stock_unit = _singular(str(driver.get("unit") or "unit").split("(")[0]) or "unit"
    fixed = {
        **driver,
        "final_demand_unit": product_unit,
        "consumption_unit": f"{product_unit} per {stock_unit} per year",
        "intensity_kind": "rate_per_driver_unit",
        "conversion_formula": "driver_value * intensity_value",
        "annual_flow_fix": (
            f"driver '{driver.get('driver_name')}' counts a stock ({driver.get('unit')}); demand is the "
            f"yearly purchase, so it is multiplied by {product_unit} bought per {stock_unit} per year"
        ),
    }
    log.append(f"[demand_driver] '{bucket_name}': {fixed['annual_flow_fix']}.")
    return fixed


def demand_driver_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg = state["config"]
    llm: GenLLM = state["llm"]
    market = state["market_name"]
    physical_unit = state.get("physical_unit", "") or ""
    log = state.setdefault("log", [])
    buckets = state.get("usage_buckets", [])
    emitter = state.get("emitter")
    if emitter is not None:
        emitter.emit("demand_driver", "stage_start", data={"node": "demand_driver_node"})

    drivers: dict[str, dict[str, Any]] = {}
    for b in buckets:
        name, desc = b["name"], b["description"]

        lib_entry = driver_library.lookup(desc, log=log, market_name=market)
        # Two use cases of the same market must never share one driver (seen:
        # Foodservice reusing Home Culinary's "household consumption" driver
        # just saved moments earlier) -- that sizes the same demand twice.
        if lib_entry is not None:
            taken_by = next((n for n, d in drivers.items() if d.get("driver_name") == lib_entry.get("driver_name")), None)
            if taken_by is not None:
                log.append(
                    f"[demand_driver] '{name}': NOT reusing library driver '{lib_entry.get('driver_name')}' -- "
                    f"already used by sibling use case '{taken_by}'; deriving a use-case-specific driver."
                )
                lib_entry = None
        if lib_entry is not None:
            reused_final_unit = lib_entry.get("final_demand_unit") or lib_entry["driver_unit"]
            reused_final_unit, reused_unit_was_corrected = units.reconcile_final_demand_unit(
                reused_final_unit, lib_entry["driver_unit"], lib_entry.get("consumption_unit", ""),
            )
            if reused_unit_was_corrected:
                log.append(
                    f"[demand_driver] '{name}': reused-library final_demand_unit corrected from "
                    f"'{lib_entry['driver_unit']}' to '{reused_final_unit}' (same reconciliation as a "
                    "freshly-derived driver -- see units.reconcile_final_demand_unit)."
                )
            drivers[name] = {
                "driver_name": lib_entry["driver_name"],
                "unit": lib_entry["driver_unit"],
                "final_demand_unit": reused_final_unit,
                "consumption_unit": lib_entry.get("consumption_unit", ""),
                "conversion_formula": lib_entry.get("conversion_formula", ""),
                "required_evidence": lib_entry.get("required_evidence", []),
                # Informational only -- computed once when this entry was saved. The
                # authoritative check is always calculate_node's own fresh re-derivation.
                "unit_contract_status": lib_entry.get("unit_contract_status", "unknown"),
                # Safe-fail default for entries predating intensity_kind: 'fraction_of_driver'
                # (bounded [0,1]) rather than the unbounded 'rate_per_driver_unit' -- an
                # incorrectly-bounded legitimate rate under-estimates value; an incorrectly
                # unbounded adjacent-industry share compounds into a blowup (the actual
                # verified failure: a stale pre-fix library entry bypassed the new clamp
                # entirely by defaulting to the unbounded kind). When in doubt, fail small.
                "intensity_kind": lib_entry.get("intensity_kind", "fraction_of_driver"),
                "rationale": f"Reused driver-library methodology (similarity {lib_entry.get('match_similarity')}), "
                             f"previously derived for: {', '.join(lib_entry.get('markets_seen', []) or [])}.",
                "library_status": "reused",
            }
            # Phase 7 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) -- every approved driver
            # gets an explicit, auditable Evidence Strategy, even a reused one.
            # No extra LLM call here: built deterministically from the library
            # entry's own already-persisted fields.
            strategy = evidence_strategy.default_strategy_for_reused_driver(
                lib_entry, driver_name=lib_entry["driver_name"], unit=lib_entry["driver_unit"],
                countries=state.get("countries", []),
            )
            strategy_flags = evidence_strategy.validate_evidence_strategy(
                strategy, bucket_name=name, bucket_description=desc,
                approved_countries=state.get("countries", []),
            )
            drivers[name]["evidence_strategy"] = strategy
            drivers[name]["evidence_strategy_flags"] = strategy_flags
            if strategy_flags:
                log.append(
                    f"[demand_driver] '{name}': reused-library evidence strategy flagged for review -- "
                    f"{[f['code'] for f in strategy_flags]}"
                )
            drivers[name] = enforce_annual_flow(drivers[name], physical_unit, log, name)
            log.append(f"[demand_driver] '{name}': reused library methodology "
                       f"(similarity {lib_entry.get('match_similarity')})")
            continue

        snippets: list[str] = []
        if cfg.enable_web:
            try:
                snippets = gather([f"{market} {name} driver statistics", f"{name} {market} volume units"], per_query=4, cap=8)
            except Exception as exc:  # noqa: BLE001
                log.append(f"[demand_driver] could not fetch evidence for '{name}': {exc}")

        system = (
            "You are a demand-driver analyst. Identify the SINGLE most measurable real-world "
            "driver whose volume determines demand for this specific usage bucket (e.g. units "
            "produced, installed base, population needing the service, fleet size, floor area, "
            "procedure count -- pick what's actually right for THIS bucket, not a generic "
            "default). Give the driver a clear name and a unit.\n\n"
            f"CRITICAL: `final_demand_unit` MUST end in the TARGET commodity's own unit "
            f"('{physical_unit or market}') -- this is the unit the market itself is measured and "
            "priced in, NEVER a downstream/refined/adjacent product's own unit. It is completely "
            "normal, and often necessary, for the DRIVER itself to be denominated in a downstream "
            "or adjacent product's unit when that's what real-world evidence actually reports (e.g. "
            "'asphalt production volume in metric tons' as the driver for a Crude Oil market's "
            "'Asphalt & Road Construction' bucket) -- but `final_demand_unit` must always be the "
            f"TARGET commodity's own unit ('{physical_unit or market}'), reached via the intensity/"
            "conversion factor below, never left as the driver's own downstream unit.\n\n"
            "If this driver ALREADY directly measures the target commodity's own volume as "
            "consumed in this bucket, set requires_conversion=false and final_demand_unit = "
            "the same unit as the driver.\n\n"
            "If the driver instead measures a downstream, refined, or adjacent quantity that must "
            f"be converted to reach the target commodity's own quantity ('{physical_unit or market}'), "
            "set requires_conversion=true and classify the SHAPE of that conversion via "
            "intensity_kind -- this distinction is critical, code will enforce it as a hard bound, "
            "not just a suggestion:\n"
            "  - 'rate_per_driver_unit': the intensity factor is a per-unit RATE that can "
            "legitimately exceed 1 (e.g. 'vehicle fleet' x 'liters consumed PER VEHICLE per "
            "year' -> lubricant demand; a vehicle can plausibly use many liters/year).\n"
            "  - 'fraction_of_driver': the driver is a BROADER/ADJACENT aggregate (e.g. total "
            "personal-care industry volume, total supplement industry volume) and the intensity "
            "factor is the SHARE of that aggregate attributable to this market's own product -- "
            "this MUST be a fraction between 0 and 1 (e.g. 0.002 = 0.2% of personal-care volume "
            "contains this ingredient). If you cannot express the intensity as a bounded "
            "fraction here, the driver is wrong -- go back and find a driver specific to this "
            "market's own product instead (e.g. 'this market's own production/consumption "
            "volume' rather than a whole adjacent industry's volume).\n"
            "Name the intensity factor's own unit as consumption_unit, and name final_demand_unit "
            "as the unit the resulting finished-product demand quantity will be in.\n\n"
            "CRITICAL constraints:\n"
            "(1) NEVER give the driver OR final_demand_unit a currency-denominated unit (e.g. "
            "'USD billion of industry output') -- it will be multiplied by a price next, so a "
            "dollar-valued driver double-counts value. Units must be physical counts/volumes/mass.\n"
            "(2) STRONGLY PREFER a driver specific to this market's own product over an "
            "adjacent-industry aggregate. Only use 'fraction_of_driver' as a last resort when no "
            "market-specific driver exists.\n"
            "(3) List, in required_evidence, what real-world data (country statistics, industry "
            "reports, trade data, etc.) would be needed to ground this driver and its conversion "
            "factor -- this guides the evidence search that follows.\n"
            "(4) Demand is what is BOUGHT or CONSUMED IN ONE YEAR. For a durable product (phones, "
            "watches, vehicles, machines) use annual unit sales/shipments, or the installed base x "
            "the share replaced/bought per year -- NEVER the number of users, owners or the "
            "installed base by itself. For a consumable, use people/outlets/plants x the amount "
            "each consumes per year."
            + evidence_strategy.strategy_system_fragment()
        )
        user = (
            f"Market: {market}\nUsage bucket: {name} -- {desc}\n\n{_evidence_brief(snippets, limit=8)}\n\n"
            'Return JSON: {"driver_name": "<name>", "unit": "<physical unit -- NEVER currency>", '
            '"requires_conversion": <true|false>, "intensity_kind": "<\'rate_per_driver_unit\' or '
            '\'fraction_of_driver\'; empty string if requires_conversion is false>", '
            '"consumption_unit": "<intensity factor unit, e.g. \'liters per vehicle per year\' or '
            '\'fraction (0-1)\'; empty string if requires_conversion is false>", '
            '"final_demand_unit": "<the TARGET commodity\'s own unit -- never a downstream product\'s unit>", '
            '"conversion_formula": "<driver_value * intensity_value, or driver_value if no '
            'conversion needed>", "required_evidence": ["<evidence needed>", ...], '
            '"rationale": "<1-2 sentences>"'
            + evidence_strategy.strategy_json_fragment() + "}"
        )
        driver_llm_error: str | None = None
        try:
            data = llm.complete_json(system, user)
        except Exception as exc:  # noqa: BLE001
            data = {}
            log.append(f"[demand_driver] LLM call failed for '{name}': {exc}")
            driver_llm_error = str(exc)
        if emitter is not None:
            emitter.emit(
                "demand_driver", "llm_call", bucket=name,
                llm={"prompt": f"{system}\n\n{user}", "response": json.dumps(data, ensure_ascii=False) if data else "", "error": driver_llm_error},
                status="failed" if driver_llm_error else "success",
            )

        unit = str(data.get("unit") or "").strip() or "units"
        final_unit = str(data.get("final_demand_unit") or "").strip() or unit
        for label, u in (("unit", unit), ("final_demand_unit", final_unit)):
            if re.search(r"\b(usd|us\$|\$|currency|dollars?)\b", u, re.IGNORECASE):
                log.append(
                    f"[demand_driver] '{name}' returned a currency-denominated {label} ('{u}') -- "
                    "rejecting it and falling back to a generic physical-unit driver to avoid "
                    "double-counting value."
                )
                unit = final_unit = "units"

        requires_conversion = bool(data.get("requires_conversion"))
        consumption_unit = str(data.get("consumption_unit") or "").strip() if requires_conversion else ""
        intensity_kind = str(data.get("intensity_kind") or "").strip() if requires_conversion else ""
        if intensity_kind not in ("rate_per_driver_unit", "fraction_of_driver"):
            intensity_kind = "rate_per_driver_unit"  # safe default -- unbounded, no false clamping
        conversion_formula = str(data.get("conversion_formula") or "").strip() or (
            "driver_value * intensity_value" if consumption_unit else "driver_value"
        )
        required_evidence = [str(x).strip() for x in (data.get("required_evidence") or []) if str(x).strip()]
        driver_name = str(data.get("driver_name") or "").strip() or f"{name} demand units"

        # Correctness-audit fix (2026-08-26, CEMENT_FORENSIC_AUDIT.md finding
        # A-3/C) -- if `consumption_unit` describes an intensity conversion
        # (driver_value * intensity_value) but `final_demand_unit` was never
        # updated off the raw driver's own unit, relabel it to the
        # intensity's own target unit -- deterministic, label-only, no value
        # touched. Prevents `pricing_node` from later inventing a second,
        # redundant conversion factor to bridge back to the unit the first
        # conversion already reached.
        final_unit, unit_was_corrected = units.reconcile_final_demand_unit(final_unit, unit, consumption_unit)
        if unit_was_corrected:
            log.append(
                f"[demand_driver] '{name}': final_demand_unit corrected from the raw driver's own "
                f"unit ('{unit}') to the intensity's target unit ('{final_unit}') -- consumption_unit "
                f"('{consumption_unit}') already describes this conversion; leaving the label as the "
                "raw driver unit would cause a downstream pricing step to double-convert."
            )

        # Informational-only pre-check (units.py, deterministic) so a newly-saved
        # library entry's own unit string is visible at a glance -- the
        # AUTHORITATIVE gate is always calculate_node's fresh re-derivation.
        unit_contract_status = "ok" if units.parse_unit_string(final_unit) is not None else "rejected_compound_or_unparseable_unit"

        drivers[name] = {
            "driver_name": driver_name,
            "unit": unit,
            "final_demand_unit": final_unit,
            "consumption_unit": consumption_unit,
            "intensity_kind": intensity_kind,
            "conversion_formula": conversion_formula,
            "required_evidence": required_evidence,
            "rationale": str(data.get("rationale") or "").strip(),
            "library_status": "newly_derived",
            "unit_contract_status": unit_contract_status,
        }
        drivers[name] = enforce_annual_flow(drivers[name], physical_unit, log, name)
        if drivers[name].get("annual_flow_fix"):
            consumption_unit = drivers[name]["consumption_unit"]
            conversion_formula = drivers[name]["conversion_formula"]
            final_unit = drivers[name]["final_demand_unit"]
            intensity_kind = drivers[name]["intensity_kind"]
        driver_library.append(
            usage_description=desc, driver_name=driver_name, driver_unit=unit,
            consumption_unit=consumption_unit, conversion_formula=conversion_formula,
            required_evidence=required_evidence, market_name=market, intensity_kind=intensity_kind,
            unit_contract_status=unit_contract_status, final_demand_unit=final_unit,
        )

        # Phase 7 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) -- pull the evidence_strategy
        # sub-object out of the SAME LLM response (no extra round-trip),
        # normalize it deterministically, and validate it. A missing/malformed
        # strategy never blocks anything -- it just carries no flags and reads
        # as "(no explicit strategy recorded)" downstream.
        strategy = evidence_strategy.extract_strategy_from_driver_response(
            data, driver_name=driver_name, unit=final_unit, countries=state.get("countries", []),
        )
        strategy_flags = evidence_strategy.validate_evidence_strategy(
            strategy, bucket_name=name, bucket_description=desc,
            approved_countries=state.get("countries", []),
        )
        drivers[name]["evidence_strategy"] = strategy
        drivers[name]["evidence_strategy_flags"] = strategy_flags
        if strategy_flags:
            log.append(
                f"[demand_driver] '{name}': evidence strategy flagged for review -- "
                f"{[f['code'] for f in strategy_flags]}"
            )
    log.append(f"Identified demand drivers for {len(drivers)} usage bucket(s) "
               f"({sum(1 for d in drivers.values() if d['library_status'] == 'reused')} reused from library)")
    if emitter is not None:
        emitter.emit("demand_driver", "stage_end", data={"node": "demand_driver_node", "n_drivers": len(drivers)})
    return {"drivers": drivers}


# --------------------------------------------------------------------------- #
# 5. demand quantification -- evidence-first per bucket x country:
#    search -> extract -> deterministically verify -> (fallback: regional/global
#    evidence + population-share allocation, always tagged `derived`) ->
#    deterministic conversion (conversion.py) -> demand quantity.
# --------------------------------------------------------------------------- #
# Every use case is priced at the SAME level, the one published market sizes
# use. Seen (Olive Oil 2B): household at retail $8,500/t next to bulk
# $4,000-4,500/t for the other use cases, putting 2B 51% over the market.
PRICE_LEVEL_RULE = (
    "Price level: the MANUFACTURER's selling price (ex-factory / wholesale, what producers and brands "
    "receive), the same level for every use case -- NOT the retail shelf price paid by consumers, and "
    "excluding retail margin and sales tax."
)

_REST_OF_WORLD_RE = re.compile(r"^rest of (the )?world$", re.IGNORECASE)


def rest_of_world_fact(global_fact: dict[str, Any], others: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Worldwide total minus the listed countries, or None when the global
    figure can't be the same quantity (not clearly larger than the listed
    countries, or more than 10x them)."""
    listed = sum(float(f.get("value") or 0.0) for f in others.values())
    try:
        world = float(global_fact.get("value") or 0.0)
    except (TypeError, ValueError):
        return None
    if listed <= 0 or not (1.05 * listed <= world <= 10 * listed):
        return None
    status = evidence.worse_of(global_fact.get("evidence_type") or "assumption", "derived")
    basis = (f"Rest of World = worldwide total {world:,.0f} ({global_fact.get('source') or 'source'}: "
             f"'{str(global_fact.get('citation') or '')[:100]}') minus the listed countries {listed:,.0f}")
    return {**global_fact, "value": world - listed, "evidence_type": status, "verification_status": status,
            "confidence": min(float(global_fact.get("confidence") or 0.5), 0.6), "derivation_basis": basis}


def _rest_of_world_from_global(
    cfg: Any, llm: GenLLM, market: str, bucket: str, driver_name: str, unit: str, base_year: Any,
    others: dict[str, dict[str, Any]], log: list[str], emitter: Any,
) -> dict[str, Any] | None:
    if not cfg.enable_web:
        return None
    year = base_year or 2025
    try:
        snippets = gather([f"global {driver_name} {year} worldwide total",
                           f"worldwide {market} {unit} {year}"], per_query=4, cap=8)
    except Exception as exc:  # noqa: BLE001
        log.append(f"[demand_quantification] '{bucket}': worldwide total search failed: {exc}")
        return None
    global_fact = evidence.extract_structured_fact(
        llm, snippets, unit=unit,
        context=f"WORLDWIDE (all countries) annual total of '{driver_name}' ({unit}) in {year} for {market}",
        emitter=emitter, bucket=bucket,
    )
    global_fact, note = period_guard.check_driver_fact(global_fact, base_year, unit)
    if note:
        log.append(f"[demand_quantification] '{bucket}' worldwide total: {note}")
    if global_fact is None:
        return None
    fact = rest_of_world_fact(global_fact, others)
    log.append(
        f"[demand_quantification] '{bucket}': Rest of World "
        + (f"= {fact['derivation_basis']}" if fact else
           f"kept as assumption -- worldwide figure {global_fact.get('value')} doesn't fit the listed countries")
    )
    return fact


def demand_quantification_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg = state["config"]
    llm: GenLLM = state["llm"]
    market = state["market_name"]
    log = state.setdefault("log", [])
    buckets = state.get("usage_buckets", [])
    drivers = state.get("drivers", {})
    countries = state.get("countries", [])
    emitter = state.get("emitter")

    quantities: dict[str, dict[str, dict[str, Any]]] = {}
    driver_facts: dict[str, dict[str, dict[str, Any]]] = {}
    intensity_clamped_buckets: list[str] = []
    # Correctness-audit fix (2026-08-26, CEMENT_FORENSIC_AUDIT.md finding
    # A-1/C-3) -- buckets where an intensity candidate claiming EXACTLY 0.0
    # was rejected (no strong citation to justify a genuine zero) and an
    # explicit assumption fallback was used instead. See the guard below.
    zero_intensity_rejected_buckets: list[str] = []
    llm_call_failures: list[str] = []
    # Evidence Retrieval Plan (spec: "For every approved Geography x Usage x
    # Demand Driver, create an explicit Evidence Retrieval Plan") -- ONE plan
    # per bucket (source_priority + search_query templates are a property of
    # the driver, not of any one country), EXECUTED once per country below.
    # `retrieval_attempts` records EVERY source tried per bucket x country,
    # not just the winning one -- persisted into the final audit output.
    retrieval_plans: dict[str, dict[str, Any]] = {}
    retrieval_attempts: dict[str, dict[str, list[dict[str, Any]]]] = {}
    # Same Evidence Retrieval Plan discipline, extended to the consumption-
    # intensity/conversion-factor quantity -- one plan + one execution per
    # bucket (geography "global": this pipeline treats intensity as a
    # market-wide constant, not country-varying -- see driver's per-country
    # plan above for the quantity that DOES vary by geography).
    intensity_retrieval_plans: dict[str, dict[str, Any]] = {}
    intensity_retrieval_attempts: dict[str, dict[str, list[dict[str, Any]]]] = {}

    progress = state.get("progress")  # runtime-hardening (2026-08-24): optional
    # incremental progress recorder -- see `progress.py`; a no-op when absent
    # (e.g. every existing test that builds a bare state dict), never affects
    # any value computed below.

    for b in buckets:
        name, desc = b["name"], b["description"]
        driver = drivers.get(name, {})
        driver_name = driver.get("driver_name", "")
        unit = driver.get("unit", "units")
        final_unit = driver.get("final_demand_unit", unit)
        consumption_unit = driver.get("consumption_unit", "")

        if progress is not None:
            progress.bucket_started(name)

        plan = evidence_router.propose_driver_evidence_plan(llm, market, name, desc, driver, emitter=emitter)
        retrieval_plans[name] = plan
        retrieval_attempts[name] = {}
        log.append(
            f"[demand_quantification] '{name}': evidence retrieval plan -- "
            f"{len(plan['sources'])} source(s) in priority order: "
            f"{[s['source_name'] for s in plan['sources']]}"
        )

        # Intensity factor is a market-level constant for this bucket (not
        # country-varying) -- one Evidence Retrieval Plan (source_priority ->
        # search_query -> source_URL -> extracted_fact), same discipline as
        # the driver's above, executed once with geography "global".
        if consumption_unit:
            intensity_plan = evidence_router.propose_intensity_evidence_plan(llm, market, name, desc, driver, emitter=emitter)
            intensity_retrieval_plans[name] = intensity_plan
            log.append(
                f"[demand_quantification] '{name}': intensity retrieval plan -- "
                f"{len(intensity_plan['sources'])} source(s) in priority order: "
                f"{[s['source_name'] for s in intensity_plan['sources']]}"
            )
            intensity_fact = None
            intensity_attempts: list[dict[str, Any]] = []
            if cfg.enable_web:
                intensity_fact, intensity_attempts = evidence_router.execute_evidence_plan(
                    llm, intensity_plan, "global",
                    context=f"Consumption intensity of '{driver_name}' for usage bucket '{name}' in {market}",
                    emitter=emitter, bucket=name,
                )
            intensity_retrieval_attempts[name] = {"global": intensity_attempts}
            if progress is not None:
                progress.intensity_done(name, len(intensity_attempts))

            # Correctness-audit fix (2026-08-26, CEMENT_FORENSIC_AUDIT.md
            # finding A-1/C-3): a candidate claiming the intensity is
            # EXACTLY 0.0 must never be silently accepted -- a
            # rate_per_driver_unit intensity of a real physical quantity
            # (e.g. "tons of cement per housing unit") cannot genuinely be
            # zero, and the verified failure mode is exactly this: a
            # last-resort llm_claim candidate with an EMPTY citation,
            # tagged only `source_found_unverified`, silently zeroing out
            # every country's demand for the whole bucket -- including
            # countries with real, independently `verified_observed` driver
            # evidence. A zero is only trusted through when it is BOTH
            # `verified_observed` or `derived` AND carries a real citation
            # (i.e. the evidence genuinely, explicitly supports zero) --
            # never invents a replacement value, only rejects the
            # unsupported candidate and falls through to the SAME
            # `_fallback_assumption` path every other missing-evidence case
            # already uses, so the reason is fully visible downstream.
            if intensity_fact is not None and float(intensity_fact.get("value", 0.0) or 0.0) == 0.0:
                citation = str(intensity_fact.get("citation") or intensity_fact.get("evidence_text") or "").strip()
                genuinely_evidenced_zero = (
                    intensity_fact.get("verification_status") in ("verified_observed", "derived") and citation
                )
                if not genuinely_evidenced_zero:
                    log.append(
                        f"[demand_quantification] '{name}': REJECTED an intensity candidate claiming "
                        f"EXACTLY 0.0 {consumption_unit} (tier: {intensity_fact.get('verification_status')}, "
                        f"citation: {'none' if not citation else 'present but not strong enough'}) -- a "
                        "physical rate intensity cannot genuinely be zero without real evidence saying so. "
                        "Falling back to an explicit assumption instead of silently zeroing this bucket's "
                        "demand."
                    )
                    zero_intensity_rejected_buckets.append(name)
                    intensity_fact = None

            if intensity_fact is None:
                intensity_fact = _fallback_assumption(
                    llm,
                    system=(
                        "No usable evidence was found for this consumption-intensity factor. Give "
                        "your own best-informed estimate anyway -- you MUST label it plainly as an "
                        "assumption; do not claim it is observed or derived from a cited source."
                    ),
                    user=(
                        f"Market: {market}\nUsage bucket: {name} -- {desc}\nDriver: {driver_name}\n"
                        f"Consumption-intensity unit: {consumption_unit}\n\n"
                        'Return JSON: {"value": number, "rationale": "<1-2 sentences>"}'
                    ),
                    unit=consumption_unit,
                )
                if intensity_fact.get("call_failed"):
                    log.append(
                        f"[demand_quantification] '{name}': the intensity-fallback LLM call FAILED "
                        f"(not just 'no evidence found') -- {intensity_fact.get('derivation_basis', '')}"
                    )
        else:
            intensity_fact = dict(_IDENTITY_INTENSITY)

        # Hard, code-level bound -- not just a prompt instruction. When the driver
        # is an adjacent/broader-industry aggregate, demand_driver_node was told to
        # classify the intensity as 'fraction_of_driver', meaning it MUST be a share
        # in [0,1]. Verified failure mode: an unbounded intensity value multiplying
        # an adjacent-industry driver compounded into a $900B+ blowup for one bucket.
        # Clamping (and flagging when clamping actually changes the value) prevents
        # that compounding regardless of how confidently the LLM reports the number.
        intensity_kind = driver.get("intensity_kind", "rate_per_driver_unit")
        intensity_clamped = False
        if intensity_kind == "fraction_of_driver":
            raw_val = float(intensity_fact.get("value", 0.0) or 0.0)
            clamped_val = min(1.0, max(0.0, raw_val))
            if clamped_val != raw_val:
                intensity_clamped = True
                log.append(
                    f"[demand_quantification] '{name}': intensity value {raw_val} is out of bounds "
                    f"for a fraction_of_driver factor -- clamped to {clamped_val} (driver is an "
                    "adjacent-industry aggregate; this factor must be the market's own share of it)."
                )
            intensity_fact = {**intensity_fact, "value": clamped_val}
            if intensity_clamped:
                intensity_fact["evidence_type"] = "assumption"
                intensity_fact["verification_status"] = "assumption"
                intensity_fact["confidence"] = min(intensity_fact.get("confidence", 0.2), 0.2)
                intensity_clamped_buckets.append(name)

        per_country: dict[str, dict[str, Any]] = {}
        per_country_facts: dict[str, dict[str, Any]] = {}
        country_driver_facts: dict[str, dict[str, Any]] = {}
        allocation_notes: dict[str, str] = {}

        # regional/global fallback is a property of the BUCKET, not of any one
        # country -- search/extract it at most once, not once per country that
        # lacks a country-level figure (was: repeated identical search+LLM
        # extraction up to len(countries) times for the same evidence).
        regional_fact: dict[str, Any] | None = None
        regional_checked = False
        needs_assumption: list[str] = []

        # Runtime optimization (2026-08-26): each country's driver-evidence
        # fetch (`execute_evidence_plan`) is fully independent of every other
        # country's, and this is the dominant cost of the whole sizing run
        # (~85% of total evidence attempts across a market). Bounded-
        # parallel-fetch this phase first, THEN run the existing sequential
        # fallback/regional/assumption logic below completely UNCHANGED,
        # reading from the pre-fetched results instead of calling
        # `execute_evidence_plan` inline. This keeps "regional fact checked
        # at most once", fallback ordering, and final per-country insertion
        # order IDENTICAL to the sequential baseline -- only the network/LLM
        # WAIT time for the independent fetches themselves overlaps; no
        # evidence-selection, retry, or acceptance-gate logic changed.
        country_evidence: dict[str, tuple[dict[str, Any] | None, list[dict[str, Any]]]] = {}
        # Official statistics (e.g. FAOSTAT, attached by 2A's driver phase)
        # replace the web search for the countries they cover.
        official_for_bucket = (state.get("official_driver_facts") or {}).get(name) or {}
        fetch_countries = [c for c in countries if c not in official_for_bucket]
        if official_for_bucket:
            log.append(
                f"[demand_quantification] '{name}': official statistics used for "
                f"{len([c for c in countries if c in official_for_bucket])} of {len(countries)} countries "
                f"({next(iter(official_for_bucket.values())).get('source', 'official data')})"
            )
        for c in countries:
            if c in official_for_bucket:
                country_evidence[c] = (official_for_bucket[c], [])
        if cfg.enable_web and fetch_countries:
            max_workers = max(1, min(getattr(cfg, "evidence_workers", 8), len(fetch_countries)))
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {
                    pool.submit(
                        evidence_router.execute_evidence_plan, llm, plan, c,
                        context=f"'{driver_name}' ({unit}) for usage bucket '{name}' in {c} ({market})",
                        keywords=[driver_name, unit, c], emitter=emitter, bucket=name,
                    ): c
                    for c in fetch_countries
                }
                for fut in as_completed(futures):
                    c = futures[fut]
                    try:
                        fact, attempts = fut.result()
                    except Exception as exc:  # noqa: BLE001 -- one country's failure must
                        # never crash the others (same "never raise" discipline every
                        # acquirer already follows for a single source failure).
                        fact, attempts = None, [{
                            "source_type": "unknown", "source_name": "unknown", "found": False,
                            "rejection_reason": "extraction_failed", "error": str(exc),
                        }]
                    country_evidence[c] = (fact, attempts)
                    if progress is not None:
                        progress.driver_attempts_live(len(attempts))
        else:
            for c in fetch_countries:
                country_evidence[c] = (None, [])

        for c in countries:
            driver_fact, attempts = country_evidence[c]
            retrieval_attempts[name][c] = attempts
            if c in official_for_bucket:
                driver_fact = official_for_bucket[c]
            else:
                driver_fact, period_note = period_guard.check_driver_fact(driver_fact, state.get("base_year"), unit)
                if period_note:
                    log.append(f"[demand_quantification] '{name}' @ {c}: {period_note}")
            if driver_fact is not None:
                country_driver_facts[c] = driver_fact
                continue

            if not regional_checked:
                regional_checked = True
                regional_snippets: list[str] = []
                if cfg.enable_web:
                    try:
                        regional_snippets = gather(
                            [f"{market} production volume {unit}", f"{market} volume forecast {unit}"],
                            per_query=3, cap=6,
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.append(f"[demand_quantification] could not fetch regional evidence for '{name}': {exc}")
                regional_fact = evidence.extract_structured_fact(
                    llm, regional_snippets, unit=unit,
                    context=f"Global/regional total '{driver_name}' ({unit}) for usage bucket '{name}' in {market}",
                    emitter=emitter, bucket=name,
                )
                regional_fact, period_note = period_guard.check_driver_fact(regional_fact, state.get("base_year"), unit)
                if period_note:
                    log.append(f"[demand_quantification] '{name}' global/regional figure: {period_note}")

            if regional_fact is not None:
                share = reference_data.population_share(c, countries)
                # Phase 10 (`DEMAND_SIDE_EVIDENCE_PLAN.md`): the population-
                # share country allocation is now the named, reusable,
                # auditable `derivation.derive_regional_share` strategy,
                # instead of ad-hoc inline arithmetic + provenance logic
                # duplicated here. Numerically IDENTICAL to the pre-refactor
                # inline version (see `tests/test_demand_usage_derivation.py`
                # regression test): `derivation.combined_verification_status`
                # caps at 'derived' the same way `evidence.worse_of(regional_
                # status, "derived")` did, and confidence is likewise
                # `min(regional_confidence, 0.6)` via the share fact's own
                # confidence=0.6.
                share_fact = {
                    "value": share, "unit": "share", "evidence_type": "derived",
                    "verification_status": "derived", "confidence": 0.6,
                    "source": "reference_data.population_share",
                    "derivation_basis": (
                        "deterministic population-share allocation basis "
                        f"({len(countries)}-country reference table, not a live-fetched figure)"
                    ),
                }
                derived = derivation.derive_regional_share(
                    regional_fact, share_fact, geography=c, ratio_label="population_share",
                    output_id=f"{name}:{c}:driver", year=regional_fact.get("year"),
                    emitter=emitter, bucket=name,
                )
                strategy_prefix = (
                    # Phase 7 (`DEMAND_SIDE_EVIDENCE_PLAN.md`): if this bucket's
                    # approved evidence strategy pre-declared a derivation_path,
                    # show it alongside the actual formula Phase 10 executed --
                    # never silently substituted for it -- so the audit trail can
                    # show WHICH named path was actually exercised, not just that
                    # a derivation happened.
                    f"[strategy derivation_path: {driver.get('evidence_strategy', {}).get('derivation_path')}] "
                    if driver.get("evidence_strategy", {}).get("derivation_path") else ""
                )
                country_driver_facts[c] = {
                    **regional_fact,
                    "value": derived["value"],
                    "evidence_type": derived["verification_status"],
                    "verification_status": derived["verification_status"],
                    "confidence": derived["confidence"],
                    "derivation_basis": strategy_prefix + derived["formula"],
                    "derivation_method": derived["derivation_method"],
                    # Phase 11 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) -- additive
                    # validation metadata only, no behavior change: carries
                    # Phase 10's own input-fact/lineage provenance through so
                    # `magnitude_validation.py` can (a) enforce the derived
                    # country value never exceeds its own regional/global
                    # parent total, and (b) trace a flagged discrepancy back
                    # to the specific input (total magnitude vs. allocation
                    # share) it most likely originated from.
                    "input_facts": derived["input_facts"],
                    "derivation_lineage": derived["derivation_lineage"],
                }
                allocation_notes[c] = (
                    f"Allocated from a global/regional total ({regional_fact['value']:,.2f} {unit}, "
                    f"{regional_fact.get('evidence_type', 'assumption')}) via population-share "
                    f"({share:.1%} of {len(countries)} countries) -- explicit, formula-based, "
                    f"tagged '{derived['verification_status']}' (never stronger than the regional fact "
                    "it was derived from). Retains provenance to that regional/global fact's own "
                    "source/evidence_text."
                )
            else:
                needs_assumption.append(c)

        # Batched, not per-country: one call asking for every assumption-needed
        # country at once, explicitly told to keep relative scale realistic
        # across them (verified failure mode when this was N independent
        # single-country calls: wildly inconsistent scale between neighbours
        # with no population/market anchoring).
        if needs_assumption:
            batch_facts = _fallback_assumption_batch(
                llm,
                system=(
                    "No usable evidence exists for this driver at country, regional, or global level "
                    f"for the {len(needs_assumption)} countries below. Give your own best-informed "
                    "estimate for EACH one -- keep the relative scale between countries realistic "
                    "(driven by relative population/market size/maturity); do not give unrelated "
                    "countries near-identical values, and do not let one country be wildly "
                    "disproportionate to the others without a stated reason. Label all of these "
                    "plainly as assumptions."
                ),
                user=(
                    f"Market: {market}\nUsage bucket: {name} -- {desc}\nDriver: {driver_name} ({unit})\n"
                    f"Countries needing an estimate: {json.dumps(needs_assumption, ensure_ascii=False)}\n\n"
                    'Return JSON: {"<country>": {"value": number, "rationale": "<1-2 sentences>"}, ...} '
                    "for EVERY country listed."
                ),
                countries=needs_assumption,
                unit=unit,
            )
            for c in needs_assumption:
                country_driver_facts[c] = batch_facts[c]
                allocation_notes[c] = "No country, regional, or global evidence found -- best-informed assumption."
            if any(f.get("call_failed") for f in batch_facts.values()):
                log.append(
                    f"[demand_quantification] '{name}': the batched assumption-fallback LLM call "
                    f"FAILED for {len(needs_assumption)} countries (not just 'no evidence found') -- "
                    f"{next(iter(batch_facts.values())).get('derivation_basis', '')}"
                )

        # "Rest of World" with no evidence of its own = the worldwide total
        # minus the listed countries, not a guess (seen: Smartphones 2A put
        # 0 phones in Rest of World, ~500M units missing).
        row_country = next((c for c in countries if _REST_OF_WORLD_RE.match(c.strip())), None)
        if (row_country and row_country not in official_for_bucket
                and (country_driver_facts.get(row_country) or {}).get("evidence_type") == "assumption"):
            row_fact = _rest_of_world_from_global(
                cfg, llm, market, name, driver_name, unit, state.get("base_year"),
                {c: f for c, f in country_driver_facts.items() if c != row_country}, log, emitter,
            )
            if row_fact is not None:
                country_driver_facts[row_country] = row_fact
                allocation_notes[row_country] = row_fact["derivation_basis"]

        # driver x intensity lands in the intensity's own unit (e.g. meals x
        # litres/meal = litres); if that isn't final_unit (tonnes), convert it
        # here -- the LLM's formula text ("* 0.91") is never executed itself.
        scale = 1.0
        consumption_unit = driver.get("consumption_unit") or ""
        split_unit = units._split_rate_unit(consumption_unit) if consumption_unit else None
        if split_unit and driver.get("intensity_kind") != "fraction_of_driver":
            s = units.unit_scale(split_unit[0], final_unit, units.formula_density(driver.get("conversion_formula", "")))
            if s is not None and abs(s - 1.0) > 1e-9:
                scale = s
                log.append(
                    f"[demand_quantification] '{name}': quantity comes out in '{split_unit[0]}' but the "
                    f"demand unit is '{final_unit}' -- converted with factor {scale:g}."
                )

        for c in countries:
            driver_fact = country_driver_facts[c]
            conv = conversion.apply_conversion(driver_fact["value"], intensity_fact["value"] * scale)
            overall_prov = evidence.worse_of(driver_fact["evidence_type"], intensity_fact["evidence_type"])
            call_failed = bool(driver_fact.get("call_failed")) or bool(intensity_fact.get("call_failed"))
            if call_failed:
                llm_call_failures.append(f"{name}@{c}")

            note = allocation_notes.get(c) or (
                f"driver={driver_fact['evidence_type']}, intensity={intensity_fact['evidence_type']}"
            )
            per_country[c] = {
                "value": round(conv.quantity, 6),
                "unit": final_unit,
                "provenance": overall_prov,
                "note": (note + " [LLM CALL FAILED -- not a genuine estimate]") if call_failed else note,
                "call_failed": call_failed,
            }
            per_country_facts[c] = {
                "driver_raw": _fact_for_report(driver_fact),
                "intensity": _fact_for_report(intensity_fact),
                "formula": conv.formula,
                "library_status": driver.get("library_status"),
            }
            if progress is not None:
                # attempts_count=0 -- these driver-evidence attempts were
                # already recorded in real time via `driver_attempts_live`
                # during the (now bounded-parallel) fetch phase above; this
                # call only needs to record the cell/provenance, not attempts
                # a second time.
                progress.country_cell_done(name, c, overall_prov, 0)

        quantities[name] = per_country
        driver_facts[name] = per_country_facts
        if progress is not None:
            progress.bucket_done(name)

    # 2B only: use cases must fit inside each country's 2A total (anchor.py).
    country_anchor_flags = anchor.apply_country_anchor(quantities, buckets, state.get("country_anchor"), log)

    counts = dict.fromkeys(evidence.PROVENANCE_STATES, 0)
    for pc in quantities.values():
        for entry in pc.values():
            # Safe default (never KeyError, never silently promoted) if an
            # entry ever carries an unrecognized provenance value.
            counts[entry.get("provenance") if entry.get("provenance") in counts else "assumption"] += 1
    log.append(
        f"Quantified demand across {len(quantities)} bucket(s) x {len(countries)} countries "
        f"(verified_observed: {counts['verified_observed']}, derived: {counts['derived']}, "
        f"source_found_unverified: {counts['source_found_unverified']}, assumption: {counts['assumption']})"
        + (f" -- {len(intensity_clamped_buckets)} bucket(s) had an out-of-bounds fraction_of_driver "
           f"intensity clamped: {intensity_clamped_buckets}" if intensity_clamped_buckets else "")
        + (f" -- {len(llm_call_failures)} cell(s) hit an LLM CALL FAILURE (infrastructure, not a "
           f"genuine estimate): {llm_call_failures}" if llm_call_failures else "")
    )
    def _hit_and_total_counts(attempts_by_bucket: dict[str, dict[str, list[dict[str, Any]]]]) -> tuple[int, int]:
        atts = [a for per_geo in attempts_by_bucket.values() for lst in per_geo.values() for a in lst]
        return sum(1 for a in atts if a.get("found")), len(atts)

    n_driver_hits, n_driver_attempts = _hit_and_total_counts(retrieval_attempts)
    n_intensity_hits, n_intensity_attempts = _hit_and_total_counts(intensity_retrieval_attempts)
    log.append(
        f"Evidence retrieval plans: driver {n_driver_hits}/{n_driver_attempts} source attempt(s) across "
        f"{len(retrieval_plans)} bucket(s), intensity {n_intensity_hits}/{n_intensity_attempts} source "
        f"attempt(s) across {len(intensity_retrieval_plans)} bucket(s) yielded a directly-cited figure "
        "(with a real source URL)"
    )
    return {
        "quantities": quantities, "driver_facts": driver_facts,
        "intensity_clamped_buckets": intensity_clamped_buckets,
        "zero_intensity_rejected_buckets": zero_intensity_rejected_buckets,
        "llm_call_failures": llm_call_failures,
        "retrieval_plans": retrieval_plans, "retrieval_attempts": retrieval_attempts,
        "intensity_retrieval_plans": intensity_retrieval_plans,
        "intensity_retrieval_attempts": intensity_retrieval_attempts,
        "country_anchor_flags": country_anchor_flags,
    }


# --------------------------------------------------------------------------- #
# 6. pricing -- evidence-first ASP per bucket, same extract/verify discipline.
# --------------------------------------------------------------------------- #
def _resolve_price_basis(
    llm: GenLLM, market: str, bucket_name: str, bucket_description: str, final_unit: str,
    emitter: Any | None = None,
) -> dict[str, Any]:
    """ONE LLM call: determine the REAL sellable commodity/product unit and
    price basis for this bucket -- which may differ from `final_demand_unit`
    (e.g. a 'vehicle-kilometers' demand quantity is not itself priced; the
    FUEL consumed to produce those vehicle-kilometers is what has a real
    published price). Never let a nonsensical basis like 'USD per
    vehicle-kilometer' reach the pricing search -- that was the root cause
    of 0% observed pricing evidence across every market in the cross-market
    validation."""
    system = (
        "You are a commodity-pricing analyst. Given a usage bucket's finished-demand unit, "
        "determine the REAL, sellable commodity/product unit that actually has a published "
        "market price -- this is sometimes the SAME as the finished-demand unit, but sometimes "
        "one step removed (e.g. 'vehicle-kilometers' is not itself priced -- the FUEL consumed to "
        "produce those vehicle-kilometers is, in USD per barrel or USD per gallon). NEVER propose "
        "a nonsensical basis like 'USD per vehicle-kilometer' or 'USD per lane-kilometer' unless "
        "that is genuinely how this specific market prices its product. If a conversion is "
        "needed, name the conversion factor explicitly (e.g. 'barrels of fuel per vehicle-"
        "kilometer') so it can be separately, transparently evidenced -- never silently assumed "
        "equal to 1."
    )
    user = (
        f"Market: {market}\nUsage bucket: {bucket_name} -- {bucket_description}\n"
        f"Finished-demand unit: {final_unit}\n\n"
        'Return JSON: {"price_basis_unit": "<the real sellable unit, e.g. \'barrel\' or \'tonne\'>", '
        '"same_as_final_unit": <true|false>, "conversion_factor_name": "<name if a conversion is '
        'needed, else empty>", "conversion_factor_unit": "<e.g. \'barrels of fuel per vehicle-km\', '
        'else empty>", "rationale": "<1-2 sentences>"}'
    )
    price_basis_error: str | None = None
    try:
        data = llm.complete_json(system, user)
    except Exception as exc:  # noqa: BLE001
        data = {}
        price_basis_error = str(exc)
    if emitter is not None:
        emitter.emit(
            "pricing", "llm_call", bucket=bucket_name,
            llm={"prompt": f"{system}\n\n{user}", "response": json.dumps(data, ensure_ascii=False) if data else "", "error": price_basis_error},
            status="failed" if price_basis_error else "success",
        )
    price_basis_unit = str(data.get("price_basis_unit") or "").strip() or final_unit
    conversion_factor_unit = str(data.get("conversion_factor_unit") or "").strip()
    same_as_final = bool(data.get("same_as_final_unit", True)) or not conversion_factor_unit
    return {
        "price_basis_unit": price_basis_unit,
        "same_as_final_unit": same_as_final,
        "conversion_factor_name": str(data.get("conversion_factor_name") or "").strip(),
        "conversion_factor_unit": conversion_factor_unit,
        "rationale": str(data.get("rationale") or "").strip(),
    }


def pricing_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg = state["config"]
    llm: GenLLM = state["llm"]
    market = state["market_name"]
    log = state.setdefault("log", [])
    buckets = state.get("usage_buckets", [])
    drivers = state.get("drivers", {})
    progress = state.get("progress")  # runtime-hardening (2026-08-24); see demand_quantification_node
    emitter = state.get("emitter")

    prices: dict[str, dict[str, Any]] = {}
    price_facts: dict[str, dict[str, Any]] = {}
    price_basis_records: dict[str, dict[str, Any]] = {}
    price_evidence_strategies: dict[str, dict[str, Any]] = {}
    pricing_conversion_factors: dict[str, dict[str, Any]] = {}
    # Same Evidence Router discipline as the driver/intensity paths in
    # demand_quantification_node -- one plan + one execution per bucket
    # (geography "global": ASP is a market-wide constant in this pipeline's
    # design, not sub-divided by country).
    price_retrieval_plans: dict[str, dict[str, Any]] = {}
    price_retrieval_attempts: dict[str, dict[str, list[dict[str, Any]]]] = {}
    price_conversion_retrieval_attempts: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for b in buckets:
        name, desc = b["name"], b["description"]
        driver = drivers.get(name, {})
        final_unit = driver.get("final_demand_unit") or driver.get("unit", "unit")
        driver_name = driver.get("driver_name", "the driver")

        # A. What is the correct price basis? (spec: never ask for "USD per
        # vehicle-km" -- resolve the real sellable unit first.)
        # `_resolve_price_basis` itself is UNCHANGED (Phase 8 must not touch
        # its core logic) -- `validate_price_basis` only FLAGS a basis that
        # still looks like an invented usage-level unit, never silently
        # fixes/blocks it.
        price_basis = _resolve_price_basis(llm, market, name, desc, final_unit, emitter=emitter)
        price_basis_flags = pricing_strategy.validate_price_basis(price_basis, final_unit=final_unit)
        price_basis_records[name] = {**price_basis, "flags": price_basis_flags}
        priced_unit = final_unit

        # A2. Phase 8 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) -- pricing now
        # consumes the same Evidence Strategy framework Phase 7 gave
        # drivers, instead of a bespoke, un-auditable path. Deterministic,
        # no extra LLM call: shaped entirely from the price basis above.
        price_strategy = pricing_strategy.build_price_evidence_strategy(
            price_basis, market=market, bucket_name=name, driver_name=driver_name,
        )
        price_strategy_flags = price_basis_flags + pricing_strategy.validate_price_period(
            price_strategy.get("period", ""), final_unit,
        )
        price_evidence_strategies[name] = {"strategy": price_strategy, "flags": price_strategy_flags}

        conversion_fact = None
        if not price_basis["same_as_final_unit"] and price_basis["conversion_factor_unit"]:
            conv_quantity_name = price_basis["conversion_factor_name"] or f"Conversion factor for {driver_name}"
            conv_plan = evidence_router.propose_evidence_plan(
                llm, market, name, desc, conv_quantity_name, price_basis["conversion_factor_unit"],
                "price-basis conversion factor",
                "This bridges the finished-demand unit to the priced commodity unit -- typically a "
                "physical engineering/technical ratio (e.g. liters of fuel per vehicle-kilometer), "
                "NOT a market-value figure.",
                emitter=emitter,
            )
            conv_context = f"{conv_quantity_name} for usage bucket '{name}' in {market}"
            conv_attempts: list[dict[str, Any]] = []
            if cfg.enable_web:
                conversion_fact, conv_attempts = evidence_router.execute_evidence_plan(
                    llm, conv_plan, "global", context=conv_context, emitter=emitter, bucket=name,
                )
            price_conversion_retrieval_attempts[name] = {"global": conv_attempts}
            if progress is not None:
                progress.price_conversion_attempts(len(conv_attempts))
            # Circular-derivation rejection (spec: "Do not derive price
            # circularly from market size / assumed quantity") -- applies
            # even to the bridging conversion factor, never just the ASP
            # itself. A conversion "fact" whose own text reveals it was
            # backed out of a market-size/revenue figure is rejected
            # outright, not silently trusted because it passed acceptance.
            if conversion_fact is not None and pricing_strategy.is_circular_price_derivation(conversion_fact):
                log.append(
                    f"[pricing] '{name}': REJECTED a conversion-factor fact that appears circularly "
                    f"derived from market size/assumed quantity -- {conversion_fact.get('derivation_basis', '')}"
                )
                conversion_fact = None
            if conversion_fact is None:
                conversion_fact = _fallback_assumption(
                    llm,
                    system=(
                        "No usable evidence was found for this price-basis conversion factor. Give "
                        "your own best-informed estimate anyway -- you MUST label it plainly as an "
                        "assumption; do not claim it is observed or derived from a cited source."
                    ),
                    user=(
                        f"Market: {market}\nUsage bucket: {name} -- {desc}\n{conv_quantity_name}\n"
                        f"Unit: {price_basis['conversion_factor_unit']}\n\n"
                        'Return JSON: {"value": number, "rationale": "<1-2 sentences>"}'
                    ),
                    unit=price_basis["conversion_factor_unit"],
                )
            # Correctness fix (2026-08-28, live Crude Oil run finding): the
            # conversion factor is DESIGNED to be "<price_basis_unit> per
            # <final_demand_unit>" so calculate_node can multiply it
            # straight in -- but the LLM does not always phrase it that way
            # (verified: Transportation Fuels returned it inverted while
            # Lubricants and Greases did not). Deterministically detect and
            # correct the DIRECTION only -- never invents a new value, never
            # touches a factor that's already correct or ambiguous.
            corrected_value, was_inverted = units.reconcile_conversion_factor_direction(
                conversion_fact.get("value"), price_basis["conversion_factor_unit"],
                price_basis["price_basis_unit"], final_unit,
            )
            if was_inverted:
                log.append(
                    f"[pricing] '{name}': conversion factor '{conv_quantity_name}' was expressed "
                    f"backwards ('{price_basis['conversion_factor_unit']}' is final-unit-per-price-basis, "
                    f"not price-basis-per-final-unit) -- inverted {conversion_fact.get('value')} to "
                    f"{corrected_value} before use so the multiplication in calculate_node stays correct."
                )
                conversion_fact = {**conversion_fact, "value": corrected_value, "direction_corrected": True}
            pricing_conversion_factors[name] = conversion_fact
            priced_unit = price_basis["price_basis_unit"]
            log.append(
                f"[pricing] '{name}': price basis is '{priced_unit}' (not '{final_unit}') -- bridged "
                f"via conversion factor '{conv_quantity_name}' = {conversion_fact.get('value')} "
                f"{price_basis['conversion_factor_unit']} [{conversion_fact.get('evidence_type')}]"
            )

        # B. Find the actual price, via the SAME Evidence Router discipline,
        # now grounded in the Price Evidence Strategy built above.
        plan = evidence_router.propose_pricing_evidence_plan(
            llm, market, name, desc, priced_unit,
            f"Average manufacturer selling price (ex-factory/wholesale, not retail shelf price) of {driver_name}",
            strategy=price_strategy, emitter=emitter,
        )
        price_retrieval_plans[name] = plan
        log.append(
            f"[pricing] '{name}': pricing retrieval plan -- {len(plan['sources'])} source(s) in "
            f"priority order: {[s['source_name'] for s in plan['sources']]}"
        )
        context = (
            f"Average selling price in US$ per ONE {priced_unit} of '{driver_name}' for usage bucket "
            f"'{name}' in {market} (if a source quotes a box/pack/case price, divide by the pack size). "
            f"{PRICE_LEVEL_RULE}"
        )
        fact = None
        attempts: list[dict[str, Any]] = []
        if cfg.enable_web:
            fact, attempts = evidence_router.execute_evidence_plan(
                llm, plan, "global", context=context, emitter=emitter, bucket=name,
            )
        price_retrieval_attempts[name] = {"global": attempts}
        # Circular-derivation rejection on the ASP itself -- same discipline
        # as the conversion factor above (spec: never let market size /
        # assumed quantity get forced back into the price).
        if fact is not None and pricing_strategy.is_circular_price_derivation(fact):
            log.append(
                f"[pricing] '{name}': REJECTED a price fact that appears circularly derived from "
                f"market size/assumed quantity -- {fact.get('derivation_basis', '')}"
            )
            fact = None
        # Same year rule as quantities: a price for a year after the base year
        # (seen: IDC's 2026 forecast ASP of $581 for Smartphones) is never used.
        price_year = period_guard.fact_year(fact) if fact is not None else None
        base_year = state.get("base_year")
        if fact is not None and base_year and price_year and price_year > int(base_year):
            log.append(f"[pricing] '{name}': REJECTED a {price_year} price ({fact.get('value')}) -- only prices "
                       f"up to the base year {base_year} are used")
            fact = None
        if fact is None:
            fact = _fallback_assumption(
                llm,
                system=(
                    "You are a pricing analyst. No usable price evidence was found. Give your own "
                    "best-informed average-selling-price estimate anyway -- label it plainly as an "
                    "assumption, do not claim it is observed. Give the price for the year "
                    f"{state.get('base_year') or 2025}, not a later forecast."
                ),
                user=(
                    f"Market: {market}\nUsage bucket: {name} -- {desc}\n"
                    f"{PRICE_LEVEL_RULE}\n"
                    f"Price basis: US$ per ONE {priced_unit} of '{driver_name}'. If you know a "
                    "box/pack/case price, divide it by the pack size -- never return a pack price.\n\n"
                    'Return JSON: {"value": number, "rationale": "<1-2 sentences>"}'
                ),
                unit=f"USD per {priced_unit}",
            )

        call_failed = bool(fact.get("call_failed"))
        if call_failed:
            log.append(
                f"[pricing] '{name}': the pricing LLM call FAILED (not just 'no evidence found') -- "
                f"ASP floored to $1/unit as an arithmetic placeholder only. {fact.get('derivation_basis', '')}"
            )
        asp = fact["value"] if fact["value"] > 0 else 1.0
        # Overall price provenance is never stronger than its weakest input --
        # if a conversion factor bridges the units, its provenance counts too.
        overall_price_evidence_type = fact["evidence_type"]
        conversion_formula = ""
        if conversion_fact is not None:
            overall_price_evidence_type = evidence.worse_of(fact["evidence_type"], conversion_fact["evidence_type"])
            conversion_formula = (
                f"{price_basis.get('conversion_factor_name', 'conversion factor')} = "
                f"{conversion_fact.get('value')} {price_basis.get('conversion_factor_unit', '')} "
                f"[{conversion_fact.get('evidence_type')}]"
            )
        # Deterministic price normalization (spec: "Price normalization") --
        # preserves raw price/unit, normalized per-single-base-unit price/
        # unit, and full provenance, independent of calculate_node's own
        # (unchanged) final_demand_unit-scale arithmetic.
        price_normalization = pricing_strategy.normalize_price_record(
            raw_value=asp, raw_unit=f"USD per {priced_unit}",
            source=fact.get("source", ""), source_url=fact.get("source_url", ""),
            year=fact.get("year"), geography="global",
            verification_status=overall_price_evidence_type,
            conversion_formula=conversion_formula,
        )
        prices[name] = {
            "asp_usd_per_unit": asp,
            "priced_unit": priced_unit,
            "grounded": overall_price_evidence_type == "verified_observed",
            "evidence_type": overall_price_evidence_type,
            "verification_status": overall_price_evidence_type,
            "rationale": fact.get("citation") or fact.get("derivation_basis") or "",
            "call_failed": call_failed,
            "price_normalization": price_normalization,
        }
        price_facts[name] = _fact_for_report(fact)
        if progress is not None:
            progress.price_done(name, len(attempts), call_failed)
    failed_count = sum(1 for p in prices.values() if p.get("call_failed"))
    log.append(f"Priced {len(prices)} usage bucket(s) "
               f"(verified_observed: {sum(1 for p in prices.values() if p['evidence_type'] == 'verified_observed')}"
               + (f", {failed_count} LLM CALL FAILURE(S) -- floored to $1/unit, not a real estimate" if failed_count else "")
               + ")")
    return {
        "prices": prices, "price_facts": price_facts,
        "price_retrieval_plans": price_retrieval_plans, "price_retrieval_attempts": price_retrieval_attempts,
        "price_basis_records": price_basis_records,
        "price_evidence_strategies": price_evidence_strategies,
        "pricing_conversion_factors": pricing_conversion_factors,
        "price_conversion_retrieval_attempts": price_conversion_retrieval_attempts,
    }


# --------------------------------------------------------------------------- #
# 7. deterministic calculation -- NO LLM call
#
#    This is also the unit-contract GATE: before any multiplication, each
#    bucket's final_demand_unit (and its driver/consumption-unit siblings) are
#    parsed by `units.py` -- deterministic, no LLM. A bucket whose unit string
#    is compound/unparseable, or whose driver/intensity/final-unit periods
#    explicitly conflict, is EXCLUDED from calculation entirely (contributes
#    $0, never an invented price/quantity) and reported as a violation --
#    never silently priced with a fallback interpretation.
# --------------------------------------------------------------------------- #
def _check_target_commodity_unit(final_unit_raw: str, physical_unit: str) -> str | None:
    """Deterministic, no LLM (methodology fix, 2026-08-26,
    CRUDE_OIL_METHODOLOGY_FIX -- 'Downstream Leakage' / 'Target Commodity
    Integrity' check). Returns a reason string if `final_unit_raw`'s
    significant words share NO overlap with the market's own
    `physical_unit` -- the signature of a bucket whose final demand
    quantity is still denominated in a downstream/adjacent product's own
    unit (e.g. 'metric tons of asphalt') rather than the TARGET commodity's
    own unit (e.g. 'barrels') -- exactly the failure mode that let a
    downstream product's price get multiplied into the target commodity's
    market value. Never flags when `physical_unit` is empty (nothing to
    compare against, e.g. pre-existing checkpoints from before this field
    was required) or when the words genuinely overlap."""
    if not physical_unit or not physical_unit.strip():
        return None
    target_words = _significant_words(physical_unit)
    if not target_words:
        return None
    final_words = _significant_words(final_unit_raw)
    if not final_words:
        return None  # already caught by the parse/empty check the caller runs first
    if target_words.isdisjoint(final_words):
        return (
            f"final_demand_unit '{final_unit_raw}' shares no words with the market's own target-"
            f"commodity unit ('{physical_unit}') -- this usually means the bucket's final quantity "
            "is still denominated in a downstream/adjacent product's own unit, not the target "
            "commodity's own unit, and would be priced with the WRONG commodity's price."
        )
    return None


def _check_unit_contract(driver: dict[str, Any]) -> dict[str, Any]:
    """Deterministic, no LLM. Returns {"status", "reason", "parsed_final_unit",
    "normalization_scale"}. status == "ok" means safe to calculate; any other
    status means the caller must exclude the bucket, not invent a fallback.

    Note: `_check_target_commodity_unit` (methodology fix, 2026-08-26) is
    DELIBERATELY NOT wired in here as a hard exclusion -- its word-overlap
    heuristic can false-positive on legitimate synonym/plural variation
    (e.g. 'tonnes' vs 'tons'), and silently zeroing a bucket's contribution
    on a false positive is worse than the alternative. `calculate_node`
    calls it separately to populate `target_commodity_unit_mismatch_flags`
    (informational/critical -- flagged loudly, value NOT excluded) instead."""
    final_unit_raw = driver.get("final_demand_unit", "") or driver.get("unit", "")
    driver_unit_raw = driver.get("unit", "")
    consumption_unit_raw = driver.get("consumption_unit", "")

    parsed_final = units.parse_unit_string(final_unit_raw)
    if parsed_final is None:
        return {
            "status": "rejected_compound_or_unparseable_unit",
            "reason": f"final_demand_unit '{final_unit_raw}' could not be unambiguously parsed "
                      "(compound, currency-tainted, or empty).",
            "parsed_final_unit": None,
            "normalization_scale": None,
        }

    parsed_driver = units.parse_unit_string(driver_unit_raw) if driver_unit_raw else None
    parsed_intensity = units.parse_unit_string(consumption_unit_raw) if consumption_unit_raw else None

    if parsed_driver is not None and units.periods_conflict(parsed_driver, parsed_final):
        return {
            "status": "rejected_period_mismatch",
            "reason": f"driver unit '{driver_unit_raw}' states period '{parsed_driver.per_period}' but "
                      f"final_demand_unit '{final_unit_raw}' states '{parsed_final.per_period}' -- "
                      "explicit, conflicting time bases in the same bucket's chain.",
            "parsed_final_unit": parsed_final,
            "normalization_scale": None,
        }
    if parsed_intensity is not None and parsed_driver is not None and units.periods_conflict(parsed_intensity, parsed_driver):
        return {
            "status": "rejected_period_mismatch",
            "reason": f"consumption_unit '{consumption_unit_raw}' states period "
                      f"'{parsed_intensity.per_period}' but driver unit '{driver_unit_raw}' states "
                      f"'{parsed_driver.per_period}' -- explicit, conflicting time bases.",
            "parsed_final_unit": parsed_final,
            "normalization_scale": None,
        }

    return {
        "status": "ok",
        "reason": "",
        "parsed_final_unit": parsed_final,
        "normalization_scale": parsed_final.scale,
    }


def calculate_node(state: dict[str, Any]) -> dict[str, Any]:
    log = state.setdefault("log", [])
    buckets = state.get("usage_buckets", [])
    quantities = state.get("quantities", {})
    prices = state.get("prices", {})
    drivers = state.get("drivers", {})
    countries = state.get("countries", [])
    physical_unit = state.get("physical_unit", "")

    unit_contract: dict[str, dict[str, Any]] = {}
    unit_rejected_buckets: list[str] = []
    # Methodology fix (2026-08-26, CRUDE_OIL_METHODOLOGY_FIX) -- "Target
    # Commodity Integrity" / "Downstream Leakage" check: flags (never
    # excludes -- see `_check_unit_contract`'s docstring) a bucket whose
    # final_demand_unit shares no words with the market's own physical_unit,
    # the signature of a downstream product's own unit/price silently
    # leaking into the target commodity's final value.
    target_commodity_unit_mismatch_flags: list[str] = []
    usage_values: dict[str, dict[str, float]] = {}

    for b in buckets:
        name = b["name"]
        driver = drivers.get(name, {})
        contract = _check_unit_contract(driver)

        target_conflict = _check_target_commodity_unit(driver.get("final_demand_unit", ""), physical_unit)
        if target_conflict:
            target_commodity_unit_mismatch_flags.append(f"'{name}': {target_conflict}")
            log.append(f"[calculate] '{name}': FLAGGED (not excluded) -- {target_conflict}")

        if contract["status"] != "ok":
            unit_rejected_buckets.append(name)
            log.append(
                f"[calculate] '{name}' EXCLUDED from calculation -- unit contract violation "
                f"({contract['status']}): {contract['reason']}"
            )
            usage_values[name] = {c: 0.0 for c in countries}
            normalized_asp = None
        else:
            asp = prices.get(name, {}).get("asp_usd_per_unit", 0.0)
            per_country = quantities.get(name, {})
            # Price-basis bridge (spec section 8/12): when the priced unit is
            # NOT the same as final_demand_unit (e.g. pricing the fuel behind
            # a vehicle-kilometers demand quantity), an evidence-backed
            # conversion factor multiplies in here -- 1.0 (no-op) for the
            # ordinary case where price basis == final_demand_unit, so this
            # never changes existing behavior for buckets without one.
            conv = state.get("pricing_conversion_factors", {}).get(name)
            conv_multiplier = conv["value"] if conv and conv.get("value") is not None else 1.0
            usage_values[name] = {
                c: round(per_country.get(c, {}).get("value", 0.0) * conv_multiplier * asp / 1_000_000.0, 4)
                for c in countries
            }
            # Normalized per-base-unit price, for the plausibility check ONLY --
            # the actual $ value above is unaffected (the scale cancels: quantity
            # and price are both quoted against the SAME final_demand_unit
            # string, so their raw product is already correct regardless of
            # what that string's scale factor numerically is).
            normalized_asp = round(asp / contract["normalization_scale"], 8)

        parsed = contract["parsed_final_unit"]
        unit_contract[name] = {
            "status": contract["status"],
            "reason": contract["reason"],
            "final_unit_raw": driver.get("final_demand_unit", ""),
            "parsed_scale": parsed.scale if parsed else None,
            "parsed_base_unit": parsed.base_unit if parsed else None,
            "parsed_period": parsed.per_period if parsed else None,
            "normalized_asp_per_base_unit": normalized_asp,
        }

    # Split by bucket_category -- Included (physical consumption) demand vs.
    # Related demand (inventory/reserve) are NEVER summed together. A reserve
    # level is a real, separately-reportable number, but it is not consumption
    # demand and must not inflate "the market size" (total_market_value keeps
    # exactly the meaning it always had: physical consumption only). A
    # unit-contract-rejected bucket contributes $0 either way (usage_values
    # was already zeroed above), regardless of which category it belongs to.
    physical_buckets = [b for b in buckets if b.get("bucket_category", "physical_consumption") == "physical_consumption"]
    inventory_buckets = [b for b in buckets if b.get("bucket_category") == "inventory_reserve"]

    country_totals = {
        c: round(sum(usage_values[b["name"]].get(c, 0.0) for b in physical_buckets), 4)
        for c in countries
    }
    total_market_value = round(sum(country_totals.values()), 4)

    related_demand_country_totals = {
        c: round(sum(usage_values[b["name"]].get(c, 0.0) for b in inventory_buckets), 4)
        for c in countries
    }
    related_demand_value = round(sum(related_demand_country_totals.values()), 4)

    log.append(
        f"Deterministic calculation: {len(physical_buckets)} Included-demand bucket(s) x "
        f"{len(countries)} countries -> Included Demand = ${total_market_value:,.1f}M"
        + (f"; {len(inventory_buckets)} inventory/reserve bucket(s) -> Related Demand = "
           f"${related_demand_value:,.1f}M (kept separate, not counted in Included Demand)"
           if inventory_buckets else "")
        + (f" -- {len(unit_rejected_buckets)} bucket(s) EXCLUDED for unit contract violations: "
           f"{unit_rejected_buckets}" if unit_rejected_buckets else "")
    )
    return {
        "usage_values": usage_values,
        "unit_contract": unit_contract,
        "unit_rejected_buckets": unit_rejected_buckets,
        "target_commodity_unit_mismatch_flags": target_commodity_unit_mismatch_flags,
        "country_totals": country_totals,
        "total_market_value": total_market_value,
        "related_demand_country_totals": related_demand_country_totals,
        "related_demand_value": related_demand_value,
    }


# --------------------------------------------------------------------------- #
# 8. validation -- deterministic checks (evidence-oriented + arithmetic) +
#    LLM judgement, plus the evidence-quality headline metric.
# --------------------------------------------------------------------------- #
def _desk_research_reference(market_name: str) -> float | None:
    """Read the most recent Desk Research history entry for this market, as an
    informational-only benchmark. Never raises; returns None if unavailable."""
    try:
        from me_engine.generation.history import read_history
        for row in read_history(limit=50):
            if str(row.get("market_name", "")).strip().lower() == market_name.strip().lower():
                # Rows written before 2026-10-05 carry a 2026 anchor under the old key.
                val = row.get("global_anchor_2025", row.get("global_anchor_2026"))
                if isinstance(val, (int, float)):
                    return float(val)
    except Exception:  # noqa: BLE001
        pass
    return None


_PRICE_MIN_USD = 0.0001
_PRICE_MAX_USD = 10_000_000.0  # generous upper band -- catches unit-mismatch blowups, not tight pricing review


def validation_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg = state["config"]
    llm: GenLLM = state["llm"]
    log = state.setdefault("log", [])
    market = state["market_name"]
    buckets = state.get("usage_buckets", [])
    countries = state.get("countries", [])
    quantities = state.get("quantities", {})
    prices = state.get("prices", {})
    country_totals = state.get("country_totals", {})
    overlap_flags = state.get("overlap_flags", [])

    # --- deterministic checks (existing) ---
    problems: list[str] = []

    missing_coverage = [
        f"{b['name']}@{c}" for b in buckets for c in countries
        if c not in quantities.get(b["name"], {})
    ]
    if missing_coverage:
        problems.append(f"{len(missing_coverage)} bucket/country combinations missing quantities")

    # country_totals is Included Demand only (physical_consumption buckets) --
    # the re-check must sum over the SAME subset, or a correctly-computed
    # inventory/reserve exclusion looks like an arithmetic bug.
    physical_only = [b for b in buckets if b.get("bucket_category", "physical_consumption") == "physical_consumption"]
    recomputed = {
        c: round(sum(state.get("usage_values", {}).get(b["name"], {}).get(c, 0.0) for b in physical_only), 4)
        for c in countries
    }
    arithmetic_ok = all(abs(recomputed.get(c, 0.0) - country_totals.get(c, 0.0)) < 0.01 for c in countries)
    if not arithmetic_ok:
        problems.append("country totals do not match the re-summed usage values")

    if overlap_flags:
        problems.append(f"{len(overlap_flags)} usage-bucket overlap(s) flagged")

    usage_values = state.get("usage_values", {})
    total = state.get("total_market_value", 0.0) or 0.0
    # Scoped to physical_only (Included Demand) -- comparing an inventory/reserve
    # bucket's value against the Included-only total would be an apples-to-oranges
    # comparison (the reserve bucket is deliberately excluded from `total`).
    bucket_totals = {b["name"]: sum(usage_values.get(b["name"], {}).values()) for b in physical_only}
    dominant_bucket_flags: list[str] = []
    if len(physical_only) >= 3 and total > 0:
        for bname, bval in bucket_totals.items():
            share = bval / total
            if share >= 0.6:
                dominant_bucket_flags.append(
                    f"'{bname}' alone is {share:.0%} of the total across {len(physical_only)} bucket(s) -- "
                    "check its driver/price for a unit mismatch before trusting this run."
                )
    if dominant_bucket_flags:
        problems.extend(dominant_bucket_flags)

    # cell-level outlier check: catches a driver x intensity assumption blowup
    # that's confined to ONE country within a bucket -- doesn't necessarily
    # make that bucket dominate the GLOBAL total (so dominant_bucket_flags can
    # miss it), but is still not defensible (verified failure mode: one
    # country landed at 100x every other country's value for the same
    # consumer-driven bucket, with no population/market-size relationship
    # behind it -- two independent, uncorrelated LLM guesses compounding).
    outlier_flags: list[str] = []
    for b in buckets:
        bname = b["name"]
        vals = {c: v for c, v in usage_values.get(bname, {}).items() if v > 0}
        nums = sorted(vals.values())
        if len(nums) >= 4:
            median = nums[len(nums) // 2]
            if median > 0:
                for c, v in vals.items():
                    if v / median >= 15.0:
                        outlier_flags.append(
                            f"'{bname}'@{c} (${v:,.1f}M) is {v / median:.0f}x this bucket's median "
                            "country value -- check for a driver/intensity assumption blowup."
                        )
    if outlier_flags:
        problems.extend(outlier_flags)

    # --- new, evidence-oriented deterministic checks (V2) ---

    # 1. Evidence-quality percentages -- the headline metric (spec section 11).
    # Phase 6: threaded through the full 4-value `VerificationStatus` model
    # (`verified_observed`/`derived`/`source_found_unverified`/`assumption`)
    # -- `source_found_unverified` is reported as its OWN percentage, never
    # merged into `observed_pct` (which would inflate evidence quality) nor
    # silently collapsed into `assumption_pct` (which would discard the real
    # signal Phase 2's LLM-claim rung captured).
    def _safe_prov(value: Any) -> str:
        return value if value in evidence.PROVENANCE_STATES else "assumption"

    prov_counts = dict.fromkeys(evidence.PROVENANCE_STATES, 0)
    for pc in quantities.values():
        for entry in pc.values():
            prov_counts[_safe_prov(entry.get("provenance"))] += 1
    price_prov_counts = dict.fromkeys(evidence.PROVENANCE_STATES, 0)
    for p in prices.values():
        price_prov_counts[_safe_prov(p.get("evidence_type"))] += 1
    total_inputs = sum(prov_counts.values()) + sum(price_prov_counts.values())
    combined = {k: prov_counts.get(k, 0) + price_prov_counts.get(k, 0) for k in evidence.PROVENANCE_STATES}
    evidence_quality = {
        "observed_pct": round(100.0 * combined["verified_observed"] / total_inputs, 1) if total_inputs else 0.0,
        "derived_pct": round(100.0 * combined["derived"] / total_inputs, 1) if total_inputs else 0.0,
        "source_found_unverified_pct": round(100.0 * combined["source_found_unverified"] / total_inputs, 1) if total_inputs else 0.0,
        "assumption_pct": round(100.0 * combined["assumption"] / total_inputs, 1) if total_inputs else 0.0,
        "total_inputs": total_inputs,
    }

    # 2. Price plausibility band -- checked on the NORMALIZED (per-base-unit)
    #    price from unit_contract, not the raw ASP. A raw ASP quoted against a
    #    scale-prefixed unit (e.g. "$450M per million tonnes") is NOT
    #    implausible -- it normalizes to a sane $450/tonne. Checking the raw
    #    number against a fixed band was a false-alarm generator; checking the
    #    normalized number catches a genuine scale error on a comparable
    #    footing instead. Falls back to the raw ASP only if a bucket's
    #    contract info is missing (e.g. computed before this field existed).
    unit_contract = state.get("unit_contract", {})
    price_plausibility_flags = []
    for name, p in prices.items():
        contract = unit_contract.get(name, {})
        normalized = contract.get("normalized_asp_per_base_unit")
        check_value = normalized if normalized is not None else p.get("asp_usd_per_unit", 0.0)
        if not (_PRICE_MIN_USD <= check_value <= _PRICE_MAX_USD):
            price_plausibility_flags.append(
                f"'{name}' normalized ASP (${check_value:,.4g} per {contract.get('parsed_base_unit') or 'unit'}) "
                "is outside a plausible band -- check for a genuine unit mismatch (not just a scale prefix)."
            )
    if price_plausibility_flags:
        problems.extend(price_plausibility_flags)

    # 3. Unit consistency -- every bucket's demand-quantity unit must match its
    #    priced unit (final_demand_unit), else the multiply in calculate_node
    #    is silently multiplying mismatched units.
    drivers = state.get("drivers", {})
    unit_mismatch_flags: list[str] = []
    for b in buckets:
        name = b["name"]
        expected_unit = drivers.get(name, {}).get("final_demand_unit", "")
        seen_units = {e.get("unit") for e in quantities.get(name, {}).values()}
        if expected_unit and seen_units and seen_units != {expected_unit}:
            unit_mismatch_flags.append(f"'{name}': quantity unit(s) {seen_units} do not match final_demand_unit '{expected_unit}'")
    if unit_mismatch_flags:
        problems.extend(unit_mismatch_flags)

    # 3b. Unit-contract violations (units.py, deterministic, no LLM) -- buckets
    #     whose unit string was compound/unparseable, or whose driver/
    #     intensity/final-unit periods explicitly conflicted, were EXCLUDED
    #     from calculation entirely by calculate_node (never priced with an
    #     invented fallback). Surfacing them here makes that exclusion visible
    #     rather than looking like an ordinary $0 bucket.
    unit_rejected_buckets = state.get("unit_rejected_buckets", [])
    unit_contract_violations = [
        f"'{name}': {unit_contract.get(name, {}).get('status', 'unknown')} -- "
        f"{unit_contract.get(name, {}).get('reason', '')}"
        for name in unit_rejected_buckets
    ]
    if unit_contract_violations:
        problems.append(
            f"{len(unit_contract_violations)} bucket(s) EXCLUDED from calculation for unit contract "
            f"violations: {unit_contract_violations}"
        )

    # 4. Source-year staleness -- informational only, not a hard failure.
    stale_source_flags: list[str] = []
    driver_facts = state.get("driver_facts", {})
    for bname, per_country in driver_facts.items():
        for c, facts in per_country.items():
            year = facts.get("driver_raw", {}).get("year")
            if isinstance(year, (int, float)) and year < 2022:
                stale_source_flags.append(f"'{bname}'@{c}: driver evidence dated {int(year)}")
    if stale_source_flags:
        problems.append(f"{len(stale_source_flags)} driver evidence source(s) predate 2022")

    # 5. Out-of-bounds fraction_of_driver intensities that had to be clamped --
    #    the LLM proposed an adjacent-industry driver + a share factor outside
    #    [0,1], which the code-level bound in demand_quantification_node caught
    #    and corrected. Surfacing it here means a human can see exactly which
    #    bucket's methodology needs a better (market-specific) driver.
    clamped_buckets = state.get("intensity_clamped_buckets", [])
    if clamped_buckets:
        problems.append(
            f"{len(clamped_buckets)} bucket(s) had an out-of-bounds fraction_of_driver intensity "
            f"clamped to [0,1]: {clamped_buckets} -- their driver methodology likely needs a "
            "market-specific driver instead of an adjacent-industry aggregate."
        )

    # 6. LLM CALL FAILURES -- distinct from, and far worse than, an ordinary
    #    "assumption" (the LLM genuinely judging a best-guess value). This is
    #    the underlying LLM call itself throwing (network/auth/rate-limit/TLS)
    #    and silently defaulting to 0/$1 downstream. Verified failure mode:
    #    this alone produced a $1.4M "total" for a market that should be in
    #    the hundreds of millions -- every price floored to exactly $1/unit
    #    because every pricing call failed, with nothing distinguishing it
    #    from a normal low-confidence run until this check existed.
    quantity_call_failures = state.get("llm_call_failures", [])
    price_call_failures = [name for name, p in prices.items() if p.get("call_failed")]
    llm_call_failures = quantity_call_failures + [f"{n} (pricing)" for n in price_call_failures]
    if llm_call_failures:
        problems.append(
            f"{len(llm_call_failures)} LLM CALL FAILURE(S) (infrastructure -- network/auth/rate-limit/"
            f"TLS -- not genuine 'no evidence found' estimates): {llm_call_failures[:6]}"
            + (" ..." if len(llm_call_failures) > 6 else "")
        )

    checks = [
        not missing_coverage, arithmetic_ok, not overlap_flags, not dominant_bucket_flags,
        not outlier_flags, not price_plausibility_flags, not unit_mismatch_flags,
        not clamped_buckets, not llm_call_failures, not unit_contract_violations,
    ]
    deterministic_score = sum(1.0 for c in checks if c) / len(checks)

    # Missing-category signal now comes from usage_coverage_node -- evidence-first,
    # and evaluated BEFORE calculation, not this post-hoc call's bare opinion.
    # Formatted here into the same readable-string shape the rest of the report
    # expects, with the evidence type visible so a reviewer can tell a cited gap
    # from a speculative one at a glance.
    missing_usage_flags = state.get("missing_usage_flags", [])
    missing_category_flags = [
        f"{m['name']} ({m['evidence_type']}, confidence {m.get('confidence', 0):.0%})"
        + (f" -- \"{m['evidence_snippet']}\"" if m.get("evidence_snippet") else f" -- {m.get('reason', '')}")
        for m in missing_usage_flags
    ]

    # --- LLM judgement pass (double-counting + overall plausibility only --
    #     missing-category coverage is usage_coverage_node's job, not this call's) ---
    plaus = 0.6
    try:
        system = (
            "You are a skeptical market-research reviewer auditing a bottom-up, usage-based "
            "demand construction. Judge: (1) do any of the usage buckets double-count the same "
            "underlying demand; (2) overall plausibility of the resulting country totals and "
            "global total. Be critical, not agreeable."
        )
        user = (
            f"Market: {market}\nUsage buckets: {json.dumps([b['name'] for b in buckets], ensure_ascii=False)}\n"
            f"Country totals (US$ Mn): {json.dumps(country_totals, ensure_ascii=False)}\n"
            f"Global total (US$ Mn): {state.get('total_market_value')}\n\n"
            'Return JSON: {"overall_plausibility": number (0-1), "double_counting_concerns": [string]}'
        )
        data = llm.complete_json(system, user)
        sc = data.get("overall_plausibility")
        if isinstance(sc, (int, float)):
            plaus = min(1.0, max(0.0, float(sc)))
        dc = [str(x) for x in (data.get("double_counting_concerns") or []) if str(x).strip()]
        problems.extend(dc)
    except Exception as exc:  # noqa: BLE001
        log.append(f"[validation] reviewer could not evaluate this run: {exc}")

    plausibility_score = round(0.4 * deterministic_score + 0.6 * plaus, 4)
    if llm_call_failures:
        # Hard cap, not just a weighted-average nudge -- a run tainted by a genuine
        # infrastructure failure (network/auth/rate-limit/TLS) must never score as
        # trustworthy just because the OTHER checks happened to pass. Verified
        # failure mode: every price floored to $1/unit (LLM calls failing, not
        # 'no evidence') still produced a deterministic_score of 75% before this cap.
        plausibility_score = round(min(plausibility_score, 0.25), 4)

    # --- informational-only reference against Desk Research (never overrides) ---
    ref_value = _desk_research_reference(market)
    ref_delta_pct = None
    total = state.get("total_market_value", 0.0)
    if ref_value and ref_value > 0:
        ref_delta_pct = round((total - ref_value) / ref_value * 100.0, 2)
        log.append(
            f"Reference only: Desk Research total for this market is ${ref_value:,.1f}M vs. "
            f"this bottom-up total ${total:,.1f}M ({ref_delta_pct:+.1f}%) -- not used to adjust "
            "the bottom-up number."
        )
    else:
        log.append("No Desk Research history entry found for this market -- no reference comparison available")

    # --- Coverage %, Residual/Uncovered, Related Demand, Reported Market View ---
    # `coverage_pct` (from usage_coverage_node) is the pipeline's own bounded
    # ESTIMATE of how much of the market's real usage landscape the Included
    # buckets represent -- it is NEVER evidence that we KNOW that percentage
    # of the real market; it's an upper-bounded guess used only to size a
    # transparent, clearly-labeled Residual line, never to certify coverage.
    # Included Demand (physical consumption) + Residual/Uncovered (algebraic
    # implication of the coverage gap -- NEVER built from evidence, NEVER
    # distributed into any usage bucket) + Related Demand (inventory/reserve,
    # quantified but kept separate) = Reported Market View. The residual is
    # reported, not manufactured: no new bucket, no invented driver/quantity.
    coverage_pct = state.get("coverage_pct")
    coverage_threshold = _coverage_threshold_pct()
    related_demand_value = state.get("related_demand_value", 0.0) or 0.0
    residual_implied_usd_mn = None
    if isinstance(coverage_pct, (int, float)) and 0 < coverage_pct < 100:
        residual_implied_usd_mn = round(total * (100.0 / coverage_pct - 1.0), 4)
    reported_market_view_usd_mn = round(total + (residual_implied_usd_mn or 0.0) + related_demand_value, 4)

    if isinstance(coverage_pct, (int, float)) and coverage_pct < coverage_threshold:
        problems.append(
            f"Estimated usage coverage ({coverage_pct:.1f}%, the pipeline's own bounded estimate -- "
            f"NOT a verified measurement of the real market) is below the configured threshold "
            f"({coverage_threshold:.0f}%) -- Included Demand likely understates the real market; "
            f"see Residual/Uncovered (${residual_implied_usd_mn:,.1f}M, implied, not evidence-built)."
        )

    # --- V3: driver-magnitude cross-check + expanded external triangulation
    #     (magnitude.py / triangulation.py run BEFORE this node in the Phase 3
    #     graph -- see graph.py -- so their output is already in `state`;
    #     folded in here only so the report/CLI/API surface it in one place). ---
    driver_magnitude_flags = state.get("driver_magnitude_flags", [])
    if driver_magnitude_flags:
        problems.append(
            f"{len(driver_magnitude_flags)} bucket(s) had a driver total diverge from an independent "
            f"reference figure by 3x+: {[f['bucket'] for f in driver_magnitude_flags]}"
        )
    triangulation = state.get("triangulation", [])
    triangulation_large_gap_flags = state.get("triangulation_large_gap_flags", [])

    # --- Phase 11 (DEMAND_SIDE_EVIDENCE_PLAN.md): generic magnitude/cross-
    #     source validation (magnitude_validation.py runs BEFORE this node --
    #     see graph.py -- so its output is already in `state`; folded in here
    #     only so the report/CLI/API surface it in one place, same pattern
    #     as the V3 fold-in above). ---
    magnitude_validation_flags = state.get("magnitude_validation_flags", [])
    magnitude_validation_records = state.get("magnitude_validation_records", [])
    if magnitude_validation_flags:
        problems.append(
            f"{len(magnitude_validation_flags)} magnitude/cross-source discrepancy flag(s) across "
            f"{sorted({f.get('check_type', '') for f in magnitude_validation_flags})}: "
            f"{[f.get('subject_label', f.get('bucket', '')) for f in magnitude_validation_flags[:4]]}"
        )

    validation = {
        "coverage_pct": coverage_pct,
        "coverage_threshold_pct": coverage_threshold,
        "included_demand_usd_mn": total,
        "related_demand_value_usd_mn": related_demand_value,
        "residual_implied_usd_mn": residual_implied_usd_mn,
        "reported_market_view_usd_mn": reported_market_view_usd_mn,
        "deterministic_score": round(deterministic_score, 4),
        "plausibility_score": plausibility_score,
        "evidence_quality": evidence_quality,
        "overlap_flags": overlap_flags,
        "missing_category_flags": missing_category_flags,
        "missing_coverage": missing_coverage,
        "dominant_bucket_flags": dominant_bucket_flags,
        "outlier_flags": outlier_flags,
        "price_plausibility_flags": price_plausibility_flags,
        "unit_mismatch_flags": unit_mismatch_flags,
        "driver_magnitude_flags": driver_magnitude_flags,
        "triangulation": triangulation,
        "triangulation_large_gap_flags": triangulation_large_gap_flags,
        "magnitude_validation_flags": magnitude_validation_flags,
        "magnitude_validation_records": magnitude_validation_records,
        "unit_contract_violations": unit_contract_violations,
        "unit_rejected_buckets": unit_rejected_buckets,
        "stale_source_flags": stale_source_flags,
        "intensity_clamped_buckets": clamped_buckets,
        "llm_call_failures": llm_call_failures,
        "bucket_totals_usd_mn": bucket_totals,
        "arithmetic_ok": arithmetic_ok,
        "problems": problems,
        "desk_research_reference_value": ref_value,
        "reference_delta_pct": ref_delta_pct,
    }
    log.append(
        f"Validation: plausibility {plausibility_score:.0%} (deterministic {deterministic_score:.0%}) | "
        f"evidence quality: observed {evidence_quality['observed_pct']:.0f}%, "
        f"derived {evidence_quality['derived_pct']:.0f}%, assumption {evidence_quality['assumption_pct']:.0f}%"
    )
    return {"validation": validation}

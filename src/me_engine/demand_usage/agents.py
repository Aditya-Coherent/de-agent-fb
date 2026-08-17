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
from typing import Any

from me_engine.generation.llm import GenLLM
from me_engine.generation.semantic_search import embed
from me_engine.generation.web_evidence import gather

from . import conversion, driver_library, evidence, reference_data, units

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
    "value": 1.0, "unit": "identity", "evidence_type": "observed", "confidence": 1.0,
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
        "value": value, "unit": unit, "evidence_type": "assumption", "confidence": 0.2,
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
            "value": value, "unit": unit, "evidence_type": "assumption", "confidence": 0.2,
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
        "the great majority of real demand."
    )
    user = (
        f"Market: {market}\nDefinition: {state.get('market_definition', {}).get('text', '')}\n\n"
        f"{_evidence_brief(snippets, limit=15)}\n\n"
        'Return JSON: {"buckets": [{"name": "<short label>", "description": "<1-2 sentences, '
        'specific enough to distinguish it from every other bucket>"}]}'
    )
    data = llm.complete_json(system, user)
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
    return {"usage_buckets": buckets, "overlap_flags": overlap_flags, "usage_snippets": snippets}


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
    try:
        data = llm.complete_json(system, user)
        llm_by_name = {str(e.get("name", "")).strip(): e for e in (data.get("buckets") or [])}
    except Exception as exc:  # noqa: BLE001
        log.append(f"[usage_validation] classifier LLM call failed: {exc}")
        llm_by_name = {}

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
    try:
        data = llm.complete_json(system, user)
        candidates = data.get("missing") or []
        llm_coverage_pct = data.get("estimated_coverage_pct")
        llm_coverage_pct = float(llm_coverage_pct) if isinstance(llm_coverage_pct, (int, float)) else 70.0
    except Exception as exc:  # noqa: BLE001
        log.append(f"[usage_coverage] gap-check LLM call failed: {exc}")
        candidates = []
        llm_coverage_pct = 70.0  # conservative default when the call itself fails -- never assume complete

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
    return {"missing_usage_flags": missing_usage_flags, "coverage_pct": coverage_pct}


# --------------------------------------------------------------------------- #
# 4. demand driver identification -- library lookup first, LLM proposal + evidence-need
#    declaration second. The library stores REUSABLE driver/conversion methodologies,
#    never a fixed usage taxonomy (usage buckets above stay market-specific).
# --------------------------------------------------------------------------- #
def demand_driver_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg = state["config"]
    llm: GenLLM = state["llm"]
    market = state["market_name"]
    log = state.setdefault("log", [])
    buckets = state.get("usage_buckets", [])

    drivers: dict[str, dict[str, Any]] = {}
    for b in buckets:
        name, desc = b["name"], b["description"]

        lib_entry = driver_library.lookup(desc)
        if lib_entry is not None:
            drivers[name] = {
                "driver_name": lib_entry["driver_name"],
                "unit": lib_entry["driver_unit"],
                "final_demand_unit": lib_entry.get("final_demand_unit") or lib_entry["driver_unit"],
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
            "If this driver ALREADY directly measures the market's own finished product/service "
            "as consumed in this bucket, set requires_conversion=false and final_demand_unit = "
            "the same unit as the driver.\n\n"
            "If the driver instead measures an upstream or adjacent quantity that must be "
            "converted to reach the finished-product quantity, set requires_conversion=true and "
            "classify the SHAPE of that conversion via intensity_kind -- this distinction is "
            "critical, code will enforce it as a hard bound, not just a suggestion:\n"
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
            "factor -- this guides the evidence search that follows."
        )
        user = (
            f"Market: {market}\nUsage bucket: {name} -- {desc}\n\n{_evidence_brief(snippets, limit=8)}\n\n"
            'Return JSON: {"driver_name": "<name>", "unit": "<physical unit -- NEVER currency>", '
            '"requires_conversion": <true|false>, "intensity_kind": "<\'rate_per_driver_unit\' or '
            '\'fraction_of_driver\'; empty string if requires_conversion is false>", '
            '"consumption_unit": "<intensity factor unit, e.g. \'liters per vehicle per year\' or '
            '\'fraction (0-1)\'; empty string if requires_conversion is false>", '
            '"final_demand_unit": "<unit the finished-product demand quantity will be in>", '
            '"conversion_formula": "<driver_value * intensity_value, or driver_value if no '
            'conversion needed>", "required_evidence": ["<evidence needed>", ...], '
            '"rationale": "<1-2 sentences>"}'
        )
        try:
            data = llm.complete_json(system, user)
        except Exception as exc:  # noqa: BLE001
            data = {}
            log.append(f"[demand_driver] LLM call failed for '{name}': {exc}")

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
        driver_library.append(
            usage_description=desc, driver_name=driver_name, driver_unit=unit,
            consumption_unit=consumption_unit, conversion_formula=conversion_formula,
            required_evidence=required_evidence, market_name=market, intensity_kind=intensity_kind,
            unit_contract_status=unit_contract_status,
        )
    log.append(f"Identified demand drivers for {len(drivers)} usage bucket(s) "
               f"({sum(1 for d in drivers.values() if d['library_status'] == 'reused')} reused from library)")
    return {"drivers": drivers}


# --------------------------------------------------------------------------- #
# 5. demand quantification -- evidence-first per bucket x country:
#    search -> extract -> deterministically verify -> (fallback: regional/global
#    evidence + population-share allocation, always tagged `derived`) ->
#    deterministic conversion (conversion.py) -> demand quantity.
# --------------------------------------------------------------------------- #
def demand_quantification_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg = state["config"]
    llm: GenLLM = state["llm"]
    market = state["market_name"]
    log = state.setdefault("log", [])
    buckets = state.get("usage_buckets", [])
    drivers = state.get("drivers", {})
    countries = state.get("countries", [])

    quantities: dict[str, dict[str, dict[str, Any]]] = {}
    driver_facts: dict[str, dict[str, dict[str, Any]]] = {}
    intensity_clamped_buckets: list[str] = []
    llm_call_failures: list[str] = []

    for b in buckets:
        name, desc = b["name"], b["description"]
        driver = drivers.get(name, {})
        driver_name = driver.get("driver_name", "")
        unit = driver.get("unit", "units")
        final_unit = driver.get("final_demand_unit", unit)
        consumption_unit = driver.get("consumption_unit", "")

        # Intensity factor is a market-level constant for this bucket (not
        # country-varying) -- one evidence search + extraction per bucket.
        if consumption_unit:
            intensity_snippets: list[str] = []
            if cfg.enable_web:
                try:
                    intensity_snippets = gather(
                        [f"{driver_name} {consumption_unit} average", f"{market} {name} {consumption_unit}"],
                        per_query=4, cap=8,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.append(f"[demand_quantification] could not fetch intensity evidence for '{name}': {exc}")
            intensity_fact = evidence.extract_structured_fact(
                llm, intensity_snippets, unit=consumption_unit,
                context=f"Consumption intensity of '{driver_name}' for usage bucket '{name}' in {market}",
            )
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

        for c in countries:
            snippets: list[str] = []
            if cfg.enable_web:
                try:
                    # Natural, keyword-search-friendly phrasing -- NOT a raw concatenation
                    # of driver_name + country + market. Verified failure mode: a long
                    # concatenated string diluted the match against a keyword backend, and
                    # generic filler words (e.g. "global") drifted the match onto unrelated
                    # results (a "global ... tonnes" query once matched "Global Industrial
                    # Company" / "Global Credit Union"). Lead with the physical unit being
                    # hunted for (volume/tonnes/units) since that's what's actually missing.
                    snippets = gather(
                        [f"{market} {c} volume {unit}", f"{c} {market} production statistics"],
                        per_query=3, cap=6,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.append(f"[demand_quantification] could not fetch evidence for '{name}'/{c}: {exc}")

            driver_fact = evidence.extract_structured_fact(
                llm, snippets, unit=unit,
                context=f"'{driver_name}' ({unit}) for usage bucket '{name}' in {c} ({market})",
            )
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
                )

            if regional_fact is not None:
                share = reference_data.population_share(c, countries)
                country_driver_facts[c] = {
                    **regional_fact,
                    "value": round(regional_fact["value"] * share, 6),
                    "evidence_type": "derived",
                    "confidence": min(regional_fact["confidence"], 0.6),
                }
                allocation_notes[c] = (
                    f"Allocated from a global/regional total ({regional_fact['value']:,.2f} {unit}) via "
                    f"population-share ({share:.1%} of {len(countries)} countries) -- explicit, formula-based, "
                    "never an arbitrary split."
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

        for c in countries:
            driver_fact = country_driver_facts[c]
            conv = conversion.apply_conversion(driver_fact["value"], intensity_fact["value"])
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

        quantities[name] = per_country
        driver_facts[name] = per_country_facts

    counts = {"observed": 0, "derived": 0, "assumption": 0}
    for pc in quantities.values():
        for entry in pc.values():
            counts[entry["provenance"]] += 1
    log.append(
        f"Quantified demand across {len(quantities)} bucket(s) x {len(countries)} countries "
        f"(observed: {counts['observed']}, derived: {counts['derived']}, assumption: {counts['assumption']})"
        + (f" -- {len(intensity_clamped_buckets)} bucket(s) had an out-of-bounds fraction_of_driver "
           f"intensity clamped: {intensity_clamped_buckets}" if intensity_clamped_buckets else "")
        + (f" -- {len(llm_call_failures)} cell(s) hit an LLM CALL FAILURE (infrastructure, not a "
           f"genuine estimate): {llm_call_failures}" if llm_call_failures else "")
    )
    return {
        "quantities": quantities, "driver_facts": driver_facts,
        "intensity_clamped_buckets": intensity_clamped_buckets,
        "llm_call_failures": llm_call_failures,
    }


# --------------------------------------------------------------------------- #
# 6. pricing -- evidence-first ASP per bucket, same extract/verify discipline.
# --------------------------------------------------------------------------- #
def pricing_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg = state["config"]
    llm: GenLLM = state["llm"]
    market = state["market_name"]
    log = state.setdefault("log", [])
    buckets = state.get("usage_buckets", [])
    drivers = state.get("drivers", {})

    prices: dict[str, dict[str, Any]] = {}
    price_facts: dict[str, dict[str, Any]] = {}
    for b in buckets:
        name, desc = b["name"], b["description"]
        driver = drivers.get(name, {})
        final_unit = driver.get("final_demand_unit") or driver.get("unit", "unit")
        driver_name = driver.get("driver_name", "the driver")

        snippets: list[str] = []
        if cfg.enable_web:
            try:
                snippets = gather(
                    [f"{market} {name} average selling price {final_unit}", f"{driver_name} price per {final_unit}"],
                    per_query=4, cap=8,
                )
            except Exception as exc:  # noqa: BLE001
                log.append(f"[pricing] could not fetch evidence for '{name}': {exc}")

        context = f"Average selling price in US$ per {final_unit} of '{driver_name}' for usage bucket '{name}' in {market}"
        fact = evidence.extract_structured_fact(llm, snippets, unit=f"USD per {final_unit}", context=context)
        if fact is None:
            fact = _fallback_assumption(
                llm,
                system=(
                    "You are a pricing analyst. No usable price evidence was found. Give your own "
                    "best-informed average-selling-price estimate anyway -- label it plainly as an "
                    "assumption, do not claim it is observed."
                ),
                user=(
                    f"Market: {market}\nUsage bucket: {name} -- {desc}\n"
                    f"Price basis: US$ per {final_unit} of '{driver_name}'\n\n"
                    'Return JSON: {"value": number, "rationale": "<1-2 sentences>"}'
                ),
                unit=f"USD per {final_unit}",
            )

        call_failed = bool(fact.get("call_failed"))
        if call_failed:
            log.append(
                f"[pricing] '{name}': the pricing LLM call FAILED (not just 'no evidence found') -- "
                f"ASP floored to $1/unit as an arithmetic placeholder only. {fact.get('derivation_basis', '')}"
            )
        asp = fact["value"] if fact["value"] > 0 else 1.0
        prices[name] = {
            "asp_usd_per_unit": asp,
            "grounded": fact["evidence_type"] == "observed",
            "evidence_type": fact["evidence_type"],
            "rationale": fact.get("citation") or fact.get("derivation_basis") or "",
            "call_failed": call_failed,
        }
        price_facts[name] = _fact_for_report(fact)
    failed_count = sum(1 for p in prices.values() if p.get("call_failed"))
    log.append(f"Priced {len(prices)} usage bucket(s) "
               f"(observed: {sum(1 for p in prices.values() if p['evidence_type'] == 'observed')}"
               + (f", {failed_count} LLM CALL FAILURE(S) -- floored to $1/unit, not a real estimate" if failed_count else "")
               + ")")
    return {"prices": prices, "price_facts": price_facts}


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
def _check_unit_contract(driver: dict[str, Any]) -> dict[str, Any]:
    """Deterministic, no LLM. Returns {"status", "reason", "parsed_final_unit",
    "normalization_scale"}. status == "ok" means safe to calculate; any other
    status means the caller must exclude the bucket, not invent a fallback."""
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

    unit_contract: dict[str, dict[str, Any]] = {}
    unit_rejected_buckets: list[str] = []
    usage_values: dict[str, dict[str, float]] = {}

    for b in buckets:
        name = b["name"]
        driver = drivers.get(name, {})
        contract = _check_unit_contract(driver)

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
            usage_values[name] = {
                c: round(per_country.get(c, {}).get("value", 0.0) * asp / 1_000_000.0, 4)
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
                val = row.get("global_anchor_2026")
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
    prov_counts = {"observed": 0, "derived": 0, "assumption": 0}
    for pc in quantities.values():
        for entry in pc.values():
            prov_counts[entry.get("provenance", "assumption")] = prov_counts.get(entry.get("provenance", "assumption"), 0) + 1
    price_prov_counts = {"observed": 0, "derived": 0, "assumption": 0}
    for p in prices.values():
        et = p.get("evidence_type", "assumption")
        price_prov_counts[et] = price_prov_counts.get(et, 0) + 1
    total_inputs = sum(prov_counts.values()) + sum(price_prov_counts.values())
    combined = {k: prov_counts.get(k, 0) + price_prov_counts.get(k, 0) for k in ("observed", "derived", "assumption")}
    evidence_quality = {
        "observed_pct": round(100.0 * combined["observed"] / total_inputs, 1) if total_inputs else 0.0,
        "derived_pct": round(100.0 * combined["derived"] / total_inputs, 1) if total_inputs else 0.0,
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

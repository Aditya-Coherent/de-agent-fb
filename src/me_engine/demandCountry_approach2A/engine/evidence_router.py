"""Evidence Router -- the general-purpose entry point for acquiring a
required fact via whichever mechanism (web page, PDF, CSV/Excel, API/
structured data) is actually most likely to have it, instead of always
falling back to a web-search snippet (the root cause the cross-market
validation exposed: 0% observed for Cement/Avocado Oil driver evidence).

    propose_*_evidence_plan  (ONE LLM call -- ranked, TYPED source chain)
        -> execute_evidence_plan (dispatches each source to its acquirer,
           in priority order, stops at the first verified fact)

'EIA' / 'IEA' / 'USGS' etc. are never hardcoded here -- the LLM proposes
both the source NAME and its most likely ACQUISITION TYPE for THIS
specific fact; the router just picks the matching acquirer module. Every
attempt (any type, successful or not) is recorded, including WHY a source
failed (no_source_found / download_failed / not_relevant_page /
extraction_failed / geography_mismatch / ...) -- see each acquirer module
-- so an assumption's root cause is always visible in the audit trail.

Supersedes the earlier web-only retrieval-plan module for the driver/
intensity/price call sites in `agents.py` (now retired).
"""
from __future__ import annotations

import json
from typing import Any

from me_engine.generation.llm import GenLLM

from . import api_acquirer, asset_verifier, evidence, fact_validator, pdf_acquirer, tabular_acquirer, web_acquirer

_MAX_SOURCES = 4
_MAX_CLAIM_CANDIDATES = 3
_SOURCE_TYPES = ("web_page", "pdf", "csv", "excel", "api", "dataset")
_ACQUIRER_KIND = {
    "web_page": "web", "pdf": "pdf", "csv": "tabular", "excel": "tabular",
    "dataset": "tabular", "api": "api",
}


def propose_evidence_plan(
    llm: GenLLM, market: str, bucket_name: str, bucket_description: str,
    quantity_name: str, unit: str, kind_label: str, extra_guidance: str,
    *, emitter: Any | None = None, stage: str = "evidence_search",
) -> dict[str, Any]:
    """ONE LLM call -- proposes a ranked, TYPED source chain for one specific
    quantity. Each source gets a `source_type` (web_page/pdf/csv/excel/api/
    dataset) the LLM judges most likely to actually contain the fact, plus a
    `locator` (site: domain / direct URL pattern / dataset name / API base
    URL) and a `search_query_template` with a literal '{country}'
    placeholder ('global' is a legitimate substitution for a market-wide
    quantity, same convention as the driver/intensity/price call sites)."""
    system = (
        f"You are a research-planning analyst building an EVIDENCE ACQUISITION PLAN for one "
        f"specific {kind_label}. Propose 2-4 REAL, named, checkable institutional sources "
        "(national/international statistics agencies, industry regulators, trade associations, "
        "commodity exchanges, equipment manufacturers, or government bodies actually known to "
        "publish this kind of figure), ranked by priority -- the most authoritative/likely-to-"
        "have-it source first.\n\n"
        "For EACH source, judge which ACQUISITION TYPE is most likely to actually contain the "
        "fact -- this is critical, not cosmetic:\n"
        "  - 'pdf': the source typically publishes this as an annual report / statistical bulletin PDF\n"
        "  - 'csv' or 'excel': the source publishes an open, downloadable dataset file\n"
        "  - 'api': the source exposes a public, machine-readable API/bulk-data endpoint -- prefer "
        "a GENUINELY real, well-known public API when one plausibly applies to THIS specific fact "
        "(e.g. World Bank Open Data API, FRED, UN Comtrade, FAOSTAT bulk-data API, EIA Open Data), "
        "never a plausible-sounding but unverified endpoint guess; if no real public API is known "
        "for this fact, propose a different source_type instead\n"
        "  - 'web_page': the source's own website states this kind of figure directly in a page\n"
        "  - 'dataset': a structured dataset not neatly CSV/Excel/API (treat like csv/excel)\n\n"
        "For EACH source also write ONE search_query_template with a literal '{country}' "
        "placeholder (or 'global' if this figure is market-wide, not per-country), and a "
        "'locator' -- a 'site:<domain>' filter, a direct file/API URL pattern, or a dataset name, "
        "whichever fits the source_type. NEVER a vague, generic market-size-style query.\n\n"
        f"{extra_guidance}\n\n"
        "BAD example (rejected): 'global {country} market size'\n"
        "GOOD example: source_type='pdf', search_query_template='site:eia.gov {country} road fuel "
        "consumption 2025 barrels per day filetype:pdf'"
    )
    user = (
        f"Market: {market}\nUsage bucket: {bucket_name} -- {bucket_description}\n"
        f"{kind_label.capitalize()}: {quantity_name} ({unit})\n\n"
        'Return JSON: {"sources": [{"source_name": "<real institution name>", '
        '"source_type": "<web_page|pdf|csv|excel|api|dataset>", '
        '"locator": "<site:domain, direct URL, dataset name, or API base URL>", '
        '"search_query_template": "<query with a literal {country} placeholder>", '
        '"rationale": "<why this source AND this acquisition type, 1 sentence>"}]}'
    )
    llm_error: str | None = None
    try:
        data = llm.complete_json(system, user)
    except Exception as exc:  # noqa: BLE001
        data = {}
        llm_error = str(exc)
    if emitter is not None:
        emitter.emit(
            stage, "llm_call", bucket=bucket_name,
            llm={"prompt": f"{system}\n\n{user}", "response": json.dumps(data, ensure_ascii=False) if data else "", "error": llm_error},
            status="failed" if llm_error else "success",
        )

    sources: list[dict[str, str]] = []
    for s in (data.get("sources") or [])[:_MAX_SOURCES]:
        source_name = str(s.get("source_name") or "").strip()
        source_type = str(s.get("source_type") or "").strip().lower()
        template = str(s.get("search_query_template") or "").strip()
        if not source_name or source_type not in _SOURCE_TYPES or not template or "{country}" not in template:
            continue  # never accept an unparseable/untyped/unsubstitutable source
        sources.append({
            "source_name": source_name, "source_type": source_type,
            "locator": str(s.get("locator") or "").strip(),
            "search_query_template": template,
            "rationale": str(s.get("rationale") or "").strip(),
        })
    if not sources:
        # Never leave a bucket with an empty plan -- generic fallback, web_page
        # type since that's the only acquirer that never needs a real locator.
        sources = [{
            "source_name": "General web search", "source_type": "web_page", "locator": "",
            "search_query_template": f"{{country}} {quantity_name} {unit}",
            "rationale": f"No specific institutional source proposed -- generic fallback for this {kind_label}.",
        }]
    return {"bucket": bucket_name, "quantity_name": quantity_name, "unit": unit, "kind": kind_label, "sources": sources}


def _strategy_guidance(strategy: dict[str, Any] | None) -> str:
    """Phase 7 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) -- if this driver carries a
    user-approved Evidence Strategy, ground the router's own source-proposal
    LLM call in it (named preferred sources, the exact metric/period to look
    for, the approved fallback) instead of letting it re-derive a research
    plan from scratch. This is guidance text only -- the router still makes
    its own typed-source proposal call; see `_strategy_sources` below for the
    deterministic half (the approved sources/queries actually get tried,
    not just described to the LLM)."""
    if not isinstance(strategy, dict) or strategy.get("strategy_source") == "missing":
        return ""
    parts = []
    if strategy.get("metric_required"):
        parts.append(f"Approved metric to find: {strategy['metric_required']} ({strategy.get('physical_unit', '')}).")
    if strategy.get("period"):
        parts.append(f"Expected reporting period: {strategy['period']}.")
    if strategy.get("preferred_sources"):
        parts.append(f"User-approved preferred sources (prioritize these if genuinely applicable): {strategy['preferred_sources']}.")
    if strategy.get("fallback_source_types") or strategy.get("fallback_queries"):
        parts.append(
            f"Approved fallback, ONLY if no primary source works: source types "
            f"{strategy.get('fallback_source_types')}, queries {strategy.get('fallback_queries')}."
        )
    if strategy.get("derivation_path"):
        parts.append(f"Approved derivation path if no direct figure exists: {strategy['derivation_path']}.")
    return " ".join(parts)


def _strategy_sources(strategy: dict[str, Any] | None, unit: str) -> list[dict[str, str]]:
    """Deterministic half of Phase 7's 'execute approved queries through the
    existing evidence router' requirement -- turns the strategy's own
    preferred_sources/primary_source_types/search_query_templates into REAL
    source entries the router will actually dispatch, not just prose fed to
    an LLM that might ignore it. Never invents a value here -- only shapes a
    source-chain entry, exactly like every LLM-proposed source does."""
    if not isinstance(strategy, dict) or strategy.get("strategy_source") == "missing":
        return []
    names = strategy.get("preferred_sources") or []
    types = strategy.get("primary_source_types") or []
    templates = strategy.get("search_query_templates") or []
    if not names and not templates:
        return []
    sources: list[dict[str, str]] = []
    n = max(len(names), 1)
    for i in range(n):
        template = templates[i] if i < len(templates) else (templates[-1] if templates else f"{{country}} {unit}")
        if "{country}" not in template:
            continue  # never dispatch an unsubstitutable template
        source_type = types[i] if i < len(types) else (types[-1] if types else "web_page")
        sources.append({
            "source_name": names[i] if i < len(names) else (names[-1] if names else "Evidence Strategy source"),
            "source_type": source_type if source_type in _SOURCE_TYPES else "web_page",
            "locator": "",
            "search_query_template": template,
            "rationale": "From the approved per-driver Evidence Strategy (Phase 7).",
            "origin": "evidence_strategy",
        })
    return sources


def _merge_strategy_sources(plan: dict[str, Any], strategy: dict[str, Any] | None) -> dict[str, Any]:
    """Prepend the strategy's own primary sources (tried FIRST, since they're
    user-approved) ahead of the LLM-proposed ones, deduped by source_name,
    capped to keep retrieval volume bounded (same discipline as every other
    acquirer in this module)."""
    strategy_sources = _strategy_sources(strategy, plan.get("unit", "units"))
    if not strategy_sources:
        return plan
    seen = {s["source_name"] for s in strategy_sources}
    merged = strategy_sources + [s for s in plan.get("sources", []) if s["source_name"] not in seen]
    plan = {**plan, "sources": merged[: max(_MAX_SOURCES, len(strategy_sources))]}
    return plan


def propose_driver_evidence_plan(
    llm: GenLLM, market: str, bucket_name: str, bucket_description: str, driver: dict[str, Any],
    *, emitter: Any | None = None,
) -> dict[str, Any]:
    driver_name = driver.get("driver_name") or bucket_name
    unit = driver.get("unit") or "units"
    strategy = driver.get("evidence_strategy")
    extra_guidance = (
        "The query must ask for the exact driver figure, in its own physical unit -- never a "
        "market-value ($) estimate, and never a derived/converted quantity."
    )
    strategy_note = _strategy_guidance(strategy)
    if strategy_note:
        extra_guidance = f"{extra_guidance} An approved Evidence Strategy already exists for this driver: {strategy_note}"
    plan = propose_evidence_plan(
        llm, market, bucket_name, bucket_description, driver_name, unit, "demand driver", extra_guidance,
        emitter=emitter,
    )
    return _merge_strategy_sources(plan, strategy)


def propose_intensity_evidence_plan(
    llm: GenLLM, market: str, bucket_name: str, bucket_description: str, driver: dict[str, Any],
    *, emitter: Any | None = None,
) -> dict[str, Any]:
    driver_name = driver.get("driver_name") or bucket_name
    consumption_unit = driver.get("consumption_unit") or "units"
    quantity_name = f"Consumption intensity / conversion factor for {driver_name}"
    return propose_evidence_plan(
        llm, market, bucket_name, bucket_description, quantity_name, consumption_unit,
        "consumption intensity / conversion factor",
        "This is typically a per-unit RATE (e.g. liters consumed per vehicle per year) published "
        "by an industry/trade association, equipment manufacturer's technical spec sheet, "
        "engineering standard, or a government efficiency/consumption survey -- NOT a market-size "
        "or market-value figure.",
        emitter=emitter,
    )


def propose_pricing_evidence_plan(
    llm: GenLLM, market: str, bucket_name: str, bucket_description: str,
    price_basis_unit: str, quantity_name: str, strategy: dict[str, Any] | None = None,
    *, emitter: Any | None = None,
) -> dict[str, Any]:
    """Phase 8 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) -- pricing now consumes the
    same Evidence Strategy machinery driver evidence already does (Phase 7):
    `strategy` (built by `pricing_strategy.build_price_evidence_strategy`,
    deterministic, no LLM) grounds this call's guidance text AND injects its
    own preferred sources/queries ahead of the LLM's own proposal, via the
    SAME `_strategy_guidance`/`_merge_strategy_sources` functions the driver
    path uses. Pricing sources (commodity exchanges, price indices, industry
    price reports) skew heavily toward PDF/API over a plain web page --
    guidance below nudges the LLM toward those source TYPES explicitly,
    which today's generic `propose_evidence_plan` guidance does not."""
    extra_guidance = (
        "This should be a spot/benchmark price from a commodity exchange, published price index, "
        "or industry price report -- in US dollars per the stated physical unit, NEVER a total "
        "market value, and NEVER computed by dividing a market-size figure by a quantity. Pricing "
        "sources of this kind are commonly published as a PDF (industry price report / statistical "
        "bulletin) or exposed via an API (commodity exchange data feed, price index API) far more "
        "often than as a plain web page -- prefer 'pdf' or 'api' as the source_type whenever a real, "
        "named source of that type plausibly applies to THIS specific commodity/product; only fall "
        "back to 'web_page' when no such PDF/API source is genuinely known."
    )
    strategy_note = _strategy_guidance(strategy)
    if strategy_note:
        extra_guidance = f"{extra_guidance} An approved Price Evidence Strategy already exists: {strategy_note}"
    plan = propose_evidence_plan(
        llm, market, bucket_name, bucket_description, quantity_name, f"USD per {price_basis_unit}",
        "average selling price (ASP)", extra_guidance,
        emitter=emitter,
    )
    return _merge_strategy_sources(plan, strategy)


def execute_evidence_plan(
    llm: GenLLM, plan: dict[str, Any], geography: str, *, context: str,
    keywords: list[str] | None = None, year: int | None = None,
    emitter: Any | None = None, stage: str = "evidence_search", bucket: str | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Runs the plan's TYPED source chain, in priority order -- each source
    dispatched to its matching acquirer (web/pdf/tabular/api). Stops at the
    first source that yields a genuinely verified fact. Returns
    (winning_fact_or_None, full_attempt_log) -- every attempt recorded,
    including a `rejection_reason` on every miss, regardless of type."""
    unit = plan.get("unit", "units")
    keywords = keywords or [plan.get("quantity_name", ""), unit, geography]
    attempts: list[dict[str, Any]] = []
    for rank, source in enumerate(plan.get("sources", []), start=1):
        acquirer_kind = _ACQUIRER_KIND.get(source.get("source_type", "web_page"), "web")
        try:
            if acquirer_kind == "web":
                fact, attempt = web_acquirer.acquire(llm, source, geography, unit=unit, context=context, emitter=emitter, stage=stage, bucket=bucket)
            elif acquirer_kind == "pdf":
                fact, attempt = pdf_acquirer.acquire(llm, source, geography, unit=unit, context=context, keywords=keywords, emitter=emitter, stage=stage, bucket=bucket)
            elif acquirer_kind == "tabular":
                fact, attempt = tabular_acquirer.acquire(llm, source, geography, unit=unit, context=context, year=year, emitter=emitter, stage=stage, bucket=bucket)
            else:
                fact, attempt = api_acquirer.acquire(llm, source, geography, unit=unit, context=context, year=year, emitter=emitter, stage=stage, bucket=bucket)
        except Exception as exc:  # noqa: BLE001
            fact, attempt = None, {
                "source_type": source.get("source_type", "web_page"), "source_name": source["source_name"],
                "found": False, "rejection_reason": "extraction_failed", "error": str(exc),
            }
        attempt["rank"] = rank
        attempts.append(attempt)
        if emitter is not None:
            emitter.emit(
                stage, "source_attempt", bucket=bucket, country=geography if geography != "global" else None,
                query={"search_query": source.get("search_query_template")},
                source={
                    "name": attempt.get("source_name"), "url": attempt.get("source_url") or attempt.get("resolved_url"),
                    "type": attempt.get("source_type"),
                },
                status="success" if fact is not None else ("failed" if attempt.get("error") else "rejected"),
                error=attempt.get("rejection_reason"),
                data=attempt,
            )
        if fact is not None:
            return fact, attempts  # stop at the first source that actually has it

    # Phase 2 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) -- every typed source in the
    # chain above is now exhausted with no verified fact. Last-resort rung:
    # ask the LLM one final, explicit question for a specific, named-source
    # claim (optionally verifying any candidate URL it offers via Phase 1's
    # `asset_verifier`) instead of falling straight through to a bare,
    # unattributed assumption. Never returns anything stronger than
    # `source_found_unverified` unless a candidate URL was independently
    # verified AND its fetched content actually states the value.
    claim_fact, claim_attempt = _llm_claim_fallback(llm, plan, geography, context=context)
    claim_attempt["rank"] = len(attempts) + 1
    attempts.append(claim_attempt)
    return claim_fact, attempts


def _llm_claim_fallback(
    llm: GenLLM, plan: dict[str, Any], geography: str, *, context: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Last-resort rung, run ONLY after every typed acquirer in the chain has
    exhausted its sources with no verified fact. Asks the LLM for up to
    `_MAX_CLAIM_CANDIDATES` specific, named-source claims for this fact --
    including a candidate URL and numeric value -- even when the underlying
    asset is hard for code to independently fetch. Any candidate whose URL
    Phase 1's shared verifier confirms accessible, and whose fetched body
    actually states the value, is accepted through the SAME acceptance gate
    (`evidence.build_fact`) every other web-sourced fact goes through --
    never a bespoke "trust the LLM's URL" shortcut. Otherwise, the top-
    ranked candidate's claim is preserved as `source_found_unverified`
    (never silently promoted) rather than discarded outright -- an unnamed/
    vague claim with no specific source in mind is explicitly rejected here
    and falls through to the ordinary `assumption` fallback, unchanged."""
    unit = plan.get("unit", "units")
    attempt: dict[str, Any] = {
        "source_type": "llm_claim", "source_name": "LLM best-effort claim(s)", "found": False,
    }
    system = (
        "Every typed evidence source (web page, PDF, dataset, API) has already been tried for this "
        "specific fact and none could be independently verified. This is a LAST-RESORT question: "
        "based on everything you actually know (not a guess), name up to 3 SPECIFIC, attributable "
        "candidate sources you believe state this figure -- even ones you cannot currently access or "
        "re-fetch -- ranked by how authoritative/likely-correct each is. For each candidate, give the "
        "real institution/report name, a candidate URL if you know one, the specific numeric value you "
        "believe it states, and why it's relevant. If you have nothing more specific than a vague, "
        "unattributed estimate, say so honestly by returning candidates=[] -- that is NOT what this "
        "question is asking for."
    )
    user = (
        f"{context}\nTarget unit: {unit}\nGeography: {geography}\n\n"
        'Return JSON: {"candidates": [{"source_name": "<real, specific institution/publication name>", '
        '"document_title": "<report/document title if known, else empty>", '
        '"source_url": "<URL if known, else empty>", "value": <number>, "year": <int or null>, '
        '"evidence_text": "<the specific fact/snippet-like text you recall stating this, else empty>", '
        '"citation": "<short citation phrase, else empty>", '
        '"reasoning": "<why this source is relevant, 1 sentence>"}]}'
    )
    try:
        data = llm.complete_json(system, user)
    except Exception as exc:  # noqa: BLE001
        attempt["rejection_reason"] = "call_failed"
        attempt["error"] = str(exc)
        return None, attempt

    raw_candidates = (data or {}).get("candidates") or []
    candidates: list[dict[str, Any]] = []
    for c in raw_candidates[:_MAX_CLAIM_CANDIDATES]:
        if not isinstance(c, dict):
            continue
        source_name = str(c.get("source_name") or "").strip()
        if not source_name:
            continue  # an unnamed claim is just an assumption, never this tier
        try:
            value = float(c.get("value"))
        except (TypeError, ValueError):
            continue
        if value < 0:
            continue
        candidates.append({
            "source_name": source_name,
            "document_title": str(c.get("document_title") or "").strip(),
            "source_url": str(c.get("source_url") or "").strip(),
            "value": value, "year": c.get("year"),
            "evidence_text": str(c.get("evidence_text") or "").strip(),
            "citation": str(c.get("citation") or "").strip(),
            "reasoning": str(c.get("reasoning") or "").strip(),
        })
    attempt["num_candidates"] = len(candidates)
    if not candidates:
        attempt["rejection_reason"] = "no_specific_claim"
        return None, attempt

    # Extraction-quality gate (`fact_validator.py`, post-Phase-12 addendum):
    # a claim that clearly measures a different metric than requested (e.g.
    # a dollar market-value claim for a physical driver) is dropped from
    # consideration entirely -- it must not reach EITHER the verified-URL
    # path below or the unverified-claim fallback, even though this rung has
    # no separate matched_geography/matched_unit field to check (only the
    # candidate's own reasoning/citation/evidence text).
    def _claim_metric_mismatch(cand: dict[str, Any]) -> bool:
        return fact_validator.validate_candidate(
            requested_unit=unit, requested_context=context,
            matched_metric_label=cand["reasoning"],
            evidence_text=f"{cand['evidence_text']} {cand['citation']}",
            is_direct_figure=True,
        ) is not None

    plausible_candidates = [c for c in candidates if not _claim_metric_mismatch(c)]
    if not plausible_candidates:
        attempt["rejection_reason"] = "wrong_metric_family"
        return None, attempt

    for cand in plausible_candidates:
        url = cand["source_url"]
        if not url:
            continue
        verification = asset_verifier.verify_asset(url, "html")
        if not (verification.is_accessible and verification.matches_expected_type and verification.content):
            continue
        page_text = web_acquirer.extract_page_text(verification.content)
        fact = evidence.build_fact(
            value=cand["value"], unit=unit, year=cand["year"], source_name=cand["source_name"],
            source_url=verification.resolved_url or url, citation=cand["citation"],
            evidence_text=page_text or cand["evidence_text"], is_direct_figure=True,
            extra={"document_title": cand["document_title"], "geography": geography},
        )
        attempt.update({
            "found": True, "value": fact["value"], "unit": fact["unit"], "year": fact.get("year"),
            "source_name": cand["source_name"], "source_url": fact.get("source_url", ""),
            "resolved_url": verification.resolved_url, "document_title": cand["document_title"],
            "citation": fact.get("citation", ""), "evidence_type": fact.get("evidence_type"),
        })
        return fact, attempt

    # No candidate URL was independently verifiable (or none had a URL at
    # all) -- preserve the top-ranked STILL-PLAUSIBLE candidate's claim as
    # `source_found_unverified`, never silently discarded just because code
    # couldn't confirm it, and never silently promoted just because the LLM
    # sounded confident.
    top = plausible_candidates[0]
    fact = evidence.build_unverified_claim_fact(
        value=top["value"], unit=unit, year=top["year"], source_name=top["source_name"],
        source_url=top["source_url"], document_title=top["document_title"], geography=geography,
        citation=top["citation"], evidence_text=top["evidence_text"], reasoning=top["reasoning"],
    )
    attempt.update({
        "found": True, "value": fact["value"], "unit": fact["unit"], "year": fact.get("year"),
        "source_name": top["source_name"], "source_url": fact.get("source_url", ""),
        "document_title": top["document_title"], "citation": fact.get("citation", ""),
        "evidence_type": fact.get("evidence_type"),
    })
    return fact, attempt

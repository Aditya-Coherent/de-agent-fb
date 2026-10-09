"""Hybrid validation: deterministic scrape signals + batched LLM for borderline cases."""
from __future__ import annotations

import json
from typing import Any

from vendor_intel.discovery.company_registry import (
    get_registry_scope,
    is_blocklisted_domain,
    is_registry_company,
    registry_domain_for_name,
)
from vendor_intel.discovery.entity_extract import is_validation_ready_name
from vendor_intel.models import Entity
from vendor_intel.scraping.website import scrape_company_website
from vendor_intel.validation.scrape_signals import analyze_site_text


def _site_text(entity: Entity) -> str:
    return (entity.scraped_text or entity.company_description or "").strip()


def apply_scrape_gate_hints(entity: Entity, *, market: str, geo: str) -> None:
    """Boost gate_pass from website text before tier assignment."""
    sig = analyze_site_text(_site_text(entity), market=market, scope=get_registry_scope())
    if not sig["has_substance"] or sig["junk_site"]:
        return
    if sig.get("market_relevant") or sig.get("pharma_relevant"):
        entity.gate_pass["product"] = True
    if sig["looks_like_company"]:
        entity.gate_pass["operational"] = True
    if sig["geo_india"] or (geo and geo.lower() != "global" and "india" in geo.lower()):
        if sig["geo_india"] or is_registry_company(entity.canonical_name):
            entity.gate_pass["geography"] = True


def try_deterministic_promotion(
    entity: Entity,
    *,
    market: str,
    geo: str,
) -> bool:
    """
    Promote tier C → B without LLM when scrape + registry/domain strongly indicate pharma.
    Returns True if tier was changed.
    """
    if entity.tier not in ("C",):
        return False
    if not is_validation_ready_name(entity.canonical_name, entity.primary_domain):
        return False
    if is_blocklisted_domain(entity.primary_domain):
        return False

    sig = analyze_site_text(
        _site_text(entity), market=market, scope=get_registry_scope()
    )
    if not sig["has_substance"] or sig["junk_site"] or not (
        sig.get("market_relevant") or sig.get("pharma_relevant")
    ):
        return False

    reg_dom = registry_domain_for_name(entity.canonical_name, get_registry_scope())
    domain_ok = bool(reg_dom) and entity.primary_domain == reg_dom
    strong = sig["confidence"] >= 0.75 and sig["looks_like_company"]
    registry = is_registry_company(entity.canonical_name)

    if strong and (domain_ok or registry):
        entity.tier = "B"
        entity.composite_score = 0.72 + min(float(sig["confidence"]) * 0.1, 0.12)
        entity.suppression_reason = None
        entity.inclusion_reason = "hybrid_deterministic_scrape"
        apply_scrape_gate_hints(entity, market=market, geo=geo)
        return True
    return False


def borderline_for_agent_review(entity: Entity, *, geo: str, market: str) -> bool:
    """Cases where rules/scrape are ambiguous — send to LLM."""
    if not is_validation_ready_name(entity.canonical_name, entity.primary_domain):
        if not is_registry_company(entity.canonical_name):
            return False

    gates = entity.gate_pass or {}
    op = bool(gates.get("operational"))
    prod = bool(gates.get("product"))
    geo_ok = bool(gates.get("geography"))
    text = _site_text(entity)
    sig = analyze_site_text(text, market=market, scope=get_registry_scope())

    # Tier B but site looks like junk — double-check
    if entity.tier in ("A", "B") and sig["junk_site"]:
        return True

    # Tier B without enough site text
    if entity.tier in ("A", "B") and len(text) < 100 and not is_registry_company(
        entity.canonical_name
    ):
        return True

    # Tier C but credible site
    if entity.tier == "C" and sig["has_substance"] and op and (prod or geo_ok):
        if entity.suppression_reason in (
            "gates_insufficient",
            "weak_evidence",
            None,
        ):
            return True

    if is_registry_company(entity.canonical_name) and entity.tier == "C" and op:
        return True

    if entity.discovery_count >= 2 and entity.tier == "C" and (
        sig.get("market_relevant") or sig.get("pharma_relevant")
    ) and op:
        return True

    return False


async def ensure_scrape_for_review(
    entity: Entity,
    settings,
    *,
    market: str,
) -> None:
    """Fetch website text before agent review if missing."""
    if len(_site_text(entity)) >= 150:
        return
    if not settings.web_fetch_enabled or not entity.primary_domain:
        return
    if is_blocklisted_domain(entity.primary_domain):
        return
    try:
        profile = await scrape_company_website(entity.primary_domain, mode="profile")
        if profile.alive and profile.text:
            entity.scraped_text = profile.text
            entity.scraped_urls = list({*entity.scraped_urls, profile.final_url})
            entity.company_description = profile.text[:2000]
    except Exception:
        pass


async def run_deterministic_pass(
    entities: list[Entity],
    *,
    market: str,
    geo: str,
) -> dict[str, int]:
    """Auto-promote obvious pharma companies from scrape signals (no LLM)."""
    promoted = 0
    for ent in entities:
        if try_deterministic_promotion(ent, market=market, geo=geo):
            promoted += 1
    return {"promoted": promoted}


def _build_batch_prompt(
    entities: list[Entity],
    *,
    query: str,
    market: str,
    geo: str,
) -> str:
    rows: list[dict[str, Any]] = []
    for e in entities:
        text = _site_text(e)[:1500]
        sig = analyze_site_text(text, market=market, scope=get_registry_scope())
        sc = get_registry_scope() or {}
        rows.append(
            {
                "canonical_name": e.canonical_name,
                "primary_domain": e.primary_domain,
                "discovery_count": e.discovery_count,
                "rule_tier": e.tier,
                "rule_suppression": e.suppression_reason,
                "gate_pass": e.gate_pass,
                "scrape_signals": sig,
                "website_excerpt": text,
                "registry_known": is_registry_company(e.canonical_name),
                "scope_market": sc.get("market"),
                "scope_geography": (sc.get("geographies") or [""])[0],
                "scope_company_type": sc.get("company_type"),
                "relevance_keywords": (sc.get("relevance_keywords") or [])[:12],
                "negative_keywords": (sc.get("negative_keywords") or [])[:10],
            }
        )
    return (
        f"Query: {query}\n"
        f"Market: {market}\n"
        f"Geography: {geo}\n\n"
        f"Return JSON: {{\"results\": [ ... ]}}\n\n"
        f"Candidates:\n{json.dumps(rows, indent=2)}"
    )


def _normalize_verdicts(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        if isinstance(raw.get("results"), list):
            return raw["results"]
        if isinstance(raw.get("candidates"), list):
            return raw["candidates"]
    if isinstance(raw, list):
        return raw
    return []


def apply_agent_verdicts(entities: list[Entity], verdicts: list[dict[str, Any]]) -> int:
    by_name = {str(v.get("canonical_name") or "").strip().lower(): v for v in verdicts}
    changed = 0
    for ent in entities:
        key = ent.canonical_name.strip().lower()
        v = by_name.get(key)
        if not v:
            continue

        is_real = bool(v.get("is_real_company"))
        is_pharma = bool(v.get("is_pharma_relevant"))
        if not is_real or not is_pharma:
            if ent.tier != "C" or ent.suppression_reason != "agent_rejected":
                changed += 1
            ent.tier = "C"
            ent.composite_score = 0.18
            ent.suppression_reason = "agent_rejected"
            ent.inclusion_reason = str(v.get("reason") or "agent_rejected")[:200]
            continue

        suggested = str(v.get("suggested_tier") or "B").upper()
        if suggested not in ("A", "B", "C"):
            suggested = "B"
        conf = float(v.get("confidence") or 0.75)

        from vendor_intel.scoring.metrics import compute_score_breakdown

        sc = get_registry_scope() or {}
        bd = compute_score_breakdown(
            ent,
            market=str(sc.get("market") or ""),
            scope=sc,
        )
        if suggested in ("A", "B") and ent.tier == "C":
            if bd.composite < 0.38 and not is_registry_company(ent.canonical_name):
                continue
            ent.tier = suggested
            ent.composite_score = max(bd.composite, 0.52 + min(conf * 0.15, 0.15))
            ent.suppression_reason = None
            ent.inclusion_reason = f"agent_promoted: {v.get('reason', '')}"[:200]
            changed += 1
        elif suggested == "C" and ent.tier in ("A", "B"):
            ent.tier = "C"
            ent.composite_score = min(bd.composite, 0.28)
            ent.suppression_reason = "agent_rejected"
            ent.inclusion_reason = str(v.get("reason") or "")[:200]
            changed += 1
    return changed


async def run_agentic_adjudication(
    entities: list[Entity],
    *,
    query: str,
    market: str,
    geo: str,
    claude,
    settings,
    max_entities: int = 25,
    batch_size: int = 8,
) -> dict[str, Any]:
    """Batched LLM review for borderline entities."""
    from pathlib import Path

    from vendor_intel.config import _project_root

    out: dict[str, Any] = {
        "enabled": False,
        "reviewed": 0,
        "changed": 0,
        "llm_calls": 0,
        "skipped_reason": "",
        "deterministic_promoted": 0,
    }
    if not getattr(settings, "phase3_agentic_validation", True):
        out["skipped_reason"] = "disabled"
        return out
    if not claude or not getattr(claude, "available", False):
        out["skipped_reason"] = "llm_not_configured"
        return out

    det = await run_deterministic_pass(entities, market=market, geo=geo)
    out["deterministic_promoted"] = det.get("promoted", 0)
    if det.get("promoted", 0):
        print(
            f"  [phase3] Hybrid deterministic: {det['promoted']} promoted from scrape signals",
            flush=True,
        )

    borderline: list[Entity] = []
    for e in entities:
        if borderline_for_agent_review(e, geo=geo, market=market):
            borderline.append(e)
    borderline = borderline[:max_entities]

    if not borderline:
        out["skipped_reason"] = "no_borderline_entities"
        out["enabled"] = True
        return out

    for ent in borderline:
        await ensure_scrape_for_review(ent, settings, market=market)

    prompt_path = _project_root() / "config" / "prompts" / "validation_adjudicator_system.txt"
    system = prompt_path.read_text(encoding="utf-8") if prompt_path.is_file() else (
        'Return JSON {"results":[]} only.'
    )

    out["enabled"] = True
    out["reviewed"] = len(borderline)
    total_changed = 0
    calls = 0
    max_calls = max(1, int(getattr(settings, "phase3_agentic_max_llm_calls", 3)))

    for i in range(0, len(borderline), batch_size):
        if calls >= max_calls:
            print(
                f"  [phase3] Agentic cap: max {max_calls} LLM batch(es) reached",
                flush=True,
            )
            break
        batch = borderline[i : i + batch_size]
        user = _build_batch_prompt(batch, query=query, market=market, geo=geo)
        try:
            raw = claude.complete_json(system, user)
            calls += 1
            verdicts = _normalize_verdicts(raw)
            n = apply_agent_verdicts(batch, verdicts)
            total_changed += n
            print(
                f"  [phase3] Agentic batch {calls}: {len(batch)} reviewed, {n} tier updates",
                flush=True,
            )
        except Exception as exc:
            print(f"  [phase3] Agentic batch failed: {exc}", flush=True)
            break

    out["llm_calls"] = calls
    out["changed"] = total_changed
    return out


async def run_hybrid_post_validation(
    entities: list[Entity],
    *,
    query: str,
    market: str,
    geo: str,
    claude,
    settings,
) -> dict[str, Any]:
    """Full hybrid pass after rule-based validation."""
    return await run_agentic_adjudication(
        entities,
        query=query,
        market=market,
        geo=geo,
        claude=claude,
        settings=settings,
        max_entities=getattr(settings, "phase3_agentic_max_entities", 25),
        batch_size=getattr(settings, "phase3_agentic_batch_size", 8),
    )


def final_quality_sweep(entities: list[Entity]) -> dict[str, int]:
    """Last deterministic filter on A/B rows."""
    from vendor_intel.validation.site_kind import is_non_product_site

    demoted = 0
    for ent in entities:
        if ent.tier not in ("A", "B"):
            continue
        if not is_validation_ready_name(ent.canonical_name, ent.primary_domain):
            ent.tier = "C"
            ent.suppression_reason = "final_quality_name"
            demoted += 1
            continue
        if is_blocklisted_domain(ent.primary_domain):
            ent.tier = "C"
            ent.suppression_reason = "final_quality_domain"
            demoted += 1
            continue
        from vendor_intel.discovery.entity_extract import (
            is_generic_category_name,
            is_generic_phrase_name,
        )

        if is_generic_phrase_name(ent.canonical_name) or is_generic_category_name(
            ent.canonical_name
        ):
            ent.tier = "C"
            ent.suppression_reason = "generic_phrase_not_company"
            demoted += 1
            continue
        # Seed/registry companies are trusted — skip content-based gates for them
        is_reg = is_registry_company(ent.canonical_name)

        # CHANGED: phase3 quality fix — media/blog/directory not product companies
        # Guard: never demote seed/registry companies via site_kind heuristics
        if not is_reg and is_non_product_site(
            ent.primary_domain or "",
            text=_site_text(ent),
            name=ent.canonical_name,
        ):
            ent.tier = "C"
            ent.suppression_reason = "non_product_site"
            demoted += 1
            continue
        sig = analyze_site_text(_site_text(ent), scope=get_registry_scope())
        if not is_reg and sig["junk_site"]:
            ent.tier = "C"
            ent.suppression_reason = "final_quality_junk_site"
            demoted += 1
            continue
        # Require market relevance for A/B — not just scrape length
        if not (sig.get("market_relevant") or sig.get("looks_like_company")):
            if not is_reg:
                ent.tier = "C"
                ent.suppression_reason = "not_market_relevant_site"
                demoted += 1
    return {"demoted": demoted}

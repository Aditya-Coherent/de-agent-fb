"""Phase 2 — Discovery & backend website scraping."""
from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vendor_intel.clients.claude import ClaudeClient
from vendor_intel.clients.search_router import FreeSearchRouter
from vendor_intel.config import Settings, _project_root
from vendor_intel.funnel.levels import merge_funnel_into_config
from vendor_intel.live_checks import validate_live_settings
from vendor_intel.mock.fixtures import is_mock_run
from vendor_intel.models import DiscoveryHit, Entity, RunConfig, RunState
from vendor_intel.clients.api_health import check_all_apis
from vendor_intel.placeholders.wikidata import lookup_parent_org
from vendor_intel.discovery.candidate_quality import (
    build_prompt_function_map,
    filter_junk_entities,
    functions_from_hits,
    infer_function_from_content,
    merge_scope_with_global_junk,
    scrape_priority_key,
)
from vendor_intel.discovery.company_registry import (
    enrich_entity_domain,
    is_registry_company,
    set_registry_scope,
)
from vendor_intel.funnel.scope_schema import scope_summary
from vendor_intel.discovery.entity_extract import pick_primary_domain
from vendor_intel.scraping.corporate_intel import gather_corporate_intel, parse_corporate_signals
from vendor_intel.scraping.website import scrape_company_website
from vendor_intel.stages.a_compiler import compile_query
from vendor_intel.stages.b_discovery import run_discovery
from vendor_intel.utils.dedupe import build_entities_from_hits
from vendor_intel.utils.description import build_company_description


def load_phase1_plan(path: str | Path) -> tuple[str, RunConfig, dict[str, Any]]:
    """Load query + RunConfig from a Phase 1 JSON manifest."""
    p = Path(path)
    if not p.is_file():
        if not p.is_absolute():
            alt = _project_root() / p
            if alt.is_file():
                p = alt
        if not p.is_file():
            plans_dir = _project_root() / "output" / "phase1"
            available = sorted(plans_dir.glob("phase1_plan_*.json")) if plans_dir.is_dir() else []
            hint = ""
            if available:
                hint = "\n  Available plans:\n" + "\n".join(
                    f"    {f.relative_to(_project_root())}" for f in available
                )
            raise FileNotFoundError(f"Phase 1 plan not found: {path}{hint}")
    plan = json.loads(p.read_text(encoding="utf-8"))
    if plan.get("phase") != 1:
        raise ValueError(f"Not a Phase 1 plan (phase={plan.get('phase')}): {p}")
    query = str(plan.get("query") or "").strip()
    if not query:
        raise ValueError(f"Phase 1 plan missing query: {p}")
    from vendor_intel.funnel.scope_schema import normalize_run_scope

    scope = normalize_run_scope(dict(plan.get("scope") or {}), query)
    if not scope.get("relevance_keywords"):
        print(
            "  [phase2] Warning: Phase 1 plan lacks LLM relevance_keywords — "
            "re-run Phase 1 with updated compiler for best results.",
            flush=True,
        )
    config = RunConfig(
        scope=scope,
        funnel_prompts=list(plan.get("funnel_prompts") or []),
        prompts=list(plan.get("discovery_prompts") or []),
    )
    return query, config, plan


def _slug_from_scope(scope: dict[str, Any], query: str) -> str:
    slug_base = f"{scope.get('market', query)}_{(scope.get('geographies') or [''])[0]}"
    return re.sub(r"[^a-z0-9]+", "_", slug_base.lower())[:56].strip("_") or "run"


def _discovery_stats(hits: list[DiscoveryHit], widen_loops: int) -> dict[str, Any]:
    by_prompt: dict[str, int] = {}
    by_level: dict[str, int] = {}
    names: set[str] = set()
    for h in hits:
        by_prompt[h.prompt_id] = by_prompt.get(h.prompt_id, 0) + 1
        lv = (h.funnel_level or "discovery").strip() or "discovery"
        by_level[lv] = by_level.get(lv, 0) + 1
        names.add(h.name_raw.lower())
    return {
        "raw_hits": len(hits),
        "unique_names": len(names),
        "by_prompt_id": dict(sorted(by_prompt.items())),
        "by_funnel_level": dict(sorted(by_level.items())),
        "widen_loops": widen_loops,
    }


def _entity_to_candidate(
    entity: Entity,
    hits: list[DiscoveryHit],
    scrape_info: dict[str, Any] | None,
    *,
    company_function: str = "unknown",
    discovered_functions: list[str] | None = None,
) -> dict[str, Any]:
    from vendor_intel.utils.domains import normalize_name

    norm = normalize_name(entity.canonical_name).lower()
    urls: list[str] = []
    prompt_ids: list[str] = []
    for h in hits:
        if normalize_name(h.name_raw).lower() != norm:
            continue
        if h.source_url and h.source_url not in urls:
            urls.append(h.source_url)
        pid = (h.prompt_id or "").strip()
        if pid and pid not in prompt_ids:
            prompt_ids.append(pid)

    # Layer 3: content-based inference — most accurate signal, overrides name/prompt tags
    scraped_text = (
        (scrape_info or {}).get("company_description", "")
        or (scrape_info or {}).get("scraped_text", "")
        or entity.scraped_text
        or ""
    )
    content_fn, content_all_fns, content_conf = infer_function_from_content(
        scraped_text,
        company_function,
        company_name=entity.canonical_name,
        domain=entity.primary_domain or "",
    )
    # Use content result when it has a strong signal
    final_function = content_fn
    final_all_fns = content_all_fns if content_all_fns else (discovered_functions or [company_function])

    return {
        "canonical_name": entity.canonical_name,
        "primary_domain": entity.primary_domain,
        "discovery_count": entity.discovery_count,
        "hits": entity.discovery_count,
        "distinct_domains_discovery": entity.distinct_domains_discovery,
        "funnel_levels_seen": entity.funnel_levels_seen,
        "aliases": entity.aliases,
        "sample_urls": urls[:15],
        "company_function": final_function,
        "discovered_functions": final_all_fns,
        "discovered_via_function": company_function,  # original prompt-based tag preserved
        "content_function_confidence": round(content_conf, 3),
        "source_prompt_ids": prompt_ids,
        "company_description": scrape_info.get("company_description", "") if scrape_info else "",
        "parent_group_hint": scrape_info.get("parent_group_hint", "") if scrape_info else "",
        # Carry verification confidence so Phase 3 doesn't start from 0
        "verification_confidence": round(float(entity.verification_confidence or 0.0), 4),
        "scrape": scrape_info,
    }


async def _parent_from_search(
    router: FreeSearchRouter,
    name: str,
    geo: str,
) -> str:
    q = f"{name} parent company subsidiary {geo}"
    try:
        rows = await router.search(q, geo=geo, search_topic=name)
    except Exception:
        return ""
    for r in rows[:5]:
        parent, _ = parse_corporate_signals(f"{r.title} {r.snippet}")
        if parent and parent.lower() != name.lower():
            return parent
    return ""


async def _scrape_entities(
    entities: list[Entity],
    hits: list[DiscoveryHit],
    *,
    max_scrape: int,
    router: FreeSearchRouter,
    geo: str,
    entity_functions: dict[str, str] | None = None,
    concurrency: int = 4,
) -> dict[str, dict[str, Any]]:
    """
    Per candidate: multi-page corporate intel + profile text + Wikidata + search parent hint.
    """
    fn_map = entity_functions or {}

    # CHANGED: phase2 quality fix — scrape high-hit / known-function candidates first
    registry_first = sorted(
        entities,
        key=lambda e: (
            0 if is_registry_company(e.canonical_name) else 1,
            scrape_priority_key(e, company_function=fn_map.get(e.canonical_name, "unknown")),
        ),
    )
    targets: list[Entity] = []
    for e in registry_first:
        if len(targets) >= max_scrape:
            break
        dom = e.primary_domain or pick_primary_domain(
            [h for h in hits if h.name_raw == e.canonical_name],
            e.canonical_name,
        )
        if dom and "wikipedia.org" not in dom and "linkedin.com" not in dom:
            e.primary_domain = dom
            targets.append(e)

    sem = asyncio.Semaphore(concurrency)
    results: dict[str, dict[str, Any]] = {}

    async def one(entity: Entity) -> None:
        async with sem:
            info: dict[str, Any] = {
                "alive": False,
                "url": entity.primary_domain,
                "profile_chars": 0,
                "corporate_chars": 0,
                "pages_fetched": 0,
            }
            try:
                _geo_hint = str(geo).strip().lower() if geo else ""
                conf = float(getattr(entity, "verification_confidence", 0) or 0)
                hits_n = int(entity.discovery_count or 0)
                if conf >= 0.85 or hits_n >= 8 or is_registry_company(entity.canonical_name):
                    max_pages, max_chars, max_lines = 6, 2800, 25
                elif conf >= 0.6 or hits_n >= 4:
                    max_pages, max_chars, max_lines = 2, 1200, 12
                else:
                    max_pages, max_chars, max_lines = 1, 500, 8
                profile = await scrape_company_website(
                    entity.primary_domain, mode="profile", geo_hint=_geo_hint
                )
                corp_intel = await gather_corporate_intel(
                    entity.primary_domain,
                    max_lines=max_lines,
                    max_chars=max_chars,
                    max_pages=max_pages,
                    geo_hint=_geo_hint,
                )
            except Exception as exc:
                info["error"] = str(exc)[:200]
                results[entity.canonical_name] = info
                return

            entity.scraped_urls = []
            if profile.final_url:
                entity.scraped_urls.append(profile.final_url)
            entity.scraped_text = profile.text or corp_intel.combined_text

            parent_hint = corp_intel.parent_hint
            if corp_intel.subsidiary_hints:
                info["subsidiary_hints"] = corp_intel.subsidiary_hints

            try:
                parent, _ = await lookup_parent_org(entity.canonical_name)
                if parent:
                    parent_hint = parent_hint or parent
                    entity.parent_group = parent
                    entity.parent_company = parent
            except Exception:
                pass

            if not parent_hint:
                parent_hint = await _parent_from_search(
                    router, entity.canonical_name, geo
                )
                if parent_hint:
                    entity.parent_group = parent_hint
                    entity.parent_company = parent_hint

            combined_lead = "\n".join(
                p for p in (profile.text, corp_intel.combined_text) if p
            )
            desc = build_company_description(
                entity, hits, lead_text=combined_lead
            )
            entity.company_description = desc

            info.update(
                {
                    "alive": profile.alive or corp_intel.alive,
                    "url": profile.final_url or entity.primary_domain,
                    "profile_chars": len(profile.text),
                    "corporate_chars": len(corp_intel.combined_text),
                    "pages_fetched": corp_intel.pages_fetched,
                    "corporate_preview": (corp_intel.combined_text or "")[:500],
                    "company_description": desc,
                    "parent_group_hint": parent_hint,
                    "is_subsidiary": bool(corp_intel.subsidiary_hints),
                }
            )
            results[entity.canonical_name] = info

    await asyncio.gather(*[one(e) for e in targets])
    return results


def _hits_to_json(hits: list[DiscoveryHit], limit: int = 250) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for h in hits[:limit]:
        out.append(
            {
                "name_raw": h.name_raw,
                "source_url": h.source_url,
                "source_domain": h.source_domain,
                "prompt_id": h.prompt_id,
                "funnel_level": h.funnel_level,
                "backend": h.backend,
                "snippet": (h.snippet or "")[:200],
            }
        )
    return out


async def run_phase2(
    query: str | None = None,
    settings: Settings | None = None,
    *,
    phase1_plan_path: str | Path | None = None,
    scrape_websites: bool = True,
    max_scrape: int = 35,
    fast_discovery: bool = False,
    max_companies: int = 150,
) -> dict[str, Any]:
    """
    Phase 2 exit criteria:
    - fast_discovery=True: search only → [{name, domain}] (no scrape, no smart_crawl)
    - fast_discovery=False: legacy discovery + optional ddgs scrape + full manifest
    """
    if fast_discovery:
        from vendor_intel.phase2.discovery_fast import run_phase2_fast

        return await run_phase2_fast(
            query,
            settings,
            phase1_plan_path=phase1_plan_path,
            max_companies=max_companies,
        )

    from vendor_intel.placeholders.load_keys import apply_env_overrides

    apply_env_overrides()
    settings = settings or Settings.load()
    if (settings.scrape_backend or "ddgs").strip().lower() in (
        "selenium",
        "ddgs_then_selenium",
    ):
        from vendor_intel.scraping.selenium_browser import apply_selenium_env

        apply_selenium_env()
    warnings = validate_live_settings(settings)

    phase1_plan: dict[str, Any] | None = None
    config: RunConfig

    llm_ok: bool | None = None
    if phase1_plan_path:
        q, config, phase1_plan = load_phase1_plan(phase1_plan_path)
        query = query or q
        llm_ok = (config.scope or {}).get("scope_source") in ("llm", "llm_partial")
    else:
        if not (query or "").strip():
            raise ValueError("Provide a query or --from-plan with a Phase 1 JSON file.")
        claude = ClaudeClient(settings)
        config = compile_query(query.strip(), claude, settings)
        config = merge_funnel_into_config(config, query.strip())
        llm_ok = (config.scope or {}).get("scope_source") in ("llm", "llm_partial")

    assert query
    scope = merge_scope_with_global_junk(config.scope or {})
    config = config.model_copy(update={"scope": scope})
    set_registry_scope(scope)
    print(f"  [phase2] LLM scope: {scope_summary(scope)}", flush=True)
    prompt_function_map = build_prompt_function_map(
        list(config.funnel_prompts or []) + list(config.prompts or [])
    )

    claude = ClaudeClient(settings)
    router = FreeSearchRouter(settings)
    state = RunState(query=query, config=config)

    if not is_mock_run(settings):
        from vendor_intel.clients.duckduckgo import reset_network_search_state

        reset_network_search_state()

    await run_discovery(state, config, claude, settings, search_router=router)

    entities = build_entities_from_hits(state.discovery_hits)
    # CHANGED: phase2 quality fix — junk + scope filter before scrape
    entities = filter_junk_entities(entities, state.discovery_hits, scope)
    entity_functions: dict[str, str] = {}
    entity_functions_all: dict[str, list[str]] = {}
    for ent in entities:
        hits_for = [
            h
            for h in state.discovery_hits
            if h.name_raw == ent.canonical_name
            or ent.canonical_name in (h.name_raw or "")
        ]
        hit_doms = [h.source_domain for h in hits_for if h.source_domain]
        ent.primary_domain = enrich_entity_domain(
            ent.canonical_name,
            ent.primary_domain or pick_primary_domain(hits_for, ent.canonical_name),
            hit_doms,
        )
        fn, fns = functions_from_hits(hits_for, prompt_function_map, entity_name=ent.canonical_name)
        entity_functions[ent.canonical_name] = fn
        entity_functions_all[ent.canonical_name] = fns
    entities.sort(key=lambda e: e.discovery_count, reverse=True)
    state.entities = entities

    verification_report: list[dict[str, Any]] = []
    if not is_mock_run(settings) and entities:
        from vendor_intel.discovery.company_verify import verify_company_name
        from vendor_intel.discovery.candidate_pool import ensure_minimum_candidates
        from vendor_intel.discovery.entity_scoring import rank_entity_for_verification

        geo_v = (scope.get("geographies") or ["global"])[0]
        market_v = str(scope.get("market") or query)
        ranked = sorted(
            entities,
            key=lambda e: rank_entity_for_verification(e, scope=scope),
            reverse=True,
        )
        verify_cap = min(len(ranked), settings.verify_top_candidates)
        print(
            f"  [phase2] Verifying top {verify_cap} of {len(ranked)} candidates "
            f"(ranked by discovery strength)",
            flush=True,
        )
        verified_names: set[str] = set()
        for ent in ranked[:verify_cap]:
            vr = await verify_company_name(
                ent.canonical_name,
                geo=geo_v,
                market=market_v,
                router=router,
                discovery_count=ent.discovery_count,
            )
            verification_report.append(
                {
                    "name": vr.name,
                    "verdict": vr.verdict,
                    "confidence": vr.confidence,
                    "reason": vr.reason,
                    "sample_urls": vr.sample_urls,
                }
            )
            ent.verification_confidence = float(vr.confidence)
            verified_names.add(ent.canonical_name.lower())

        for ent in ranked[verify_cap:]:
            if ent.canonical_name.lower() in verified_names:
                continue
            base_conf = min(0.55, 0.2 + ent.discovery_count * 0.06)
            verification_report.append(
                {
                    "name": ent.canonical_name,
                    "verdict": "likely_real" if ent.discovery_count >= 3 else "unclear",
                    "confidence": round(base_conf, 2),
                    "reason": "unverified_ranked_lower",
                    "sample_urls": [],
                }
            )
            ent.verification_confidence = base_conf

        target_solid = getattr(settings, "target_solid_companies", 50)
        entities, pool_warnings = ensure_minimum_candidates(
            ranked,
            verification_report,
            target_solid=target_solid,
            max_pool=max(target_solid + 30, 100),
            scope=scope,
        )
        warnings.extend(pool_warnings)
        state.entities = entities
        if len(entities) < 15:
            warnings.append(
                "Very few candidates after pooling — check search connectivity or re-run Phase 2."
            )

    api_status = await check_all_apis(settings, llm_responded_ok=llm_ok)

    scrape_map: dict[str, dict[str, Any]] = {}
    if scrape_websites and not is_mock_run(settings) and settings.web_fetch_enabled:
        if entities:
            scrape_map = await _scrape_entities(
                entities,
                state.discovery_hits,
                max_scrape=max_scrape,
                router=router,
                geo=(scope.get("geographies") or ["global"])[0],
                entity_functions=entity_functions,
            )
    elif scrape_websites and is_mock_run(settings):
        warnings.append("Mock mode — website scrape skipped.")
    elif scrape_websites and not settings.web_fetch_enabled:
        warnings.append("WEB_FETCH_ENABLED=false — website scrape skipped.")

    candidates = [
        _entity_to_candidate(
            e,
            state.discovery_hits,
            scrape_map.get(e.canonical_name),
            company_function=entity_functions.get(e.canonical_name, "unknown"),
            discovered_functions=entity_functions_all.get(e.canonical_name, []),
        )
        for e in entities
    ]

    stats = _discovery_stats(state.discovery_hits, state.widen_loops)
    if state.query_yield_metrics:
        stats["query_yield"] = state.query_yield_metrics
    if state.query_sector_validated:
        stats["sector_validated"] = state.query_sector_validated
    manifest: dict[str, Any] = {
        "phase": 2,
        "query": query,
        "mock_mode": is_mock_run(settings),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "llm_provider": settings.llm_provider,
        "search_primary": settings.search_primary,
        "search_backup": settings.search_backup,
        "phase1_plan_path": str(Path(phase1_plan_path).resolve()) if phase1_plan_path else None,
        "scope": scope,
        "scope_source": scope.get("scope_source", "phase1_plan" if phase1_plan else "unknown"),
        "funnel_prompts": config.funnel_prompts,
        "discovery_prompts": config.prompts,
        "discovery_stats": stats,
        "results_per_query": settings.results_per_query,
        "api_status": api_status,
        "industry_vertical": scope.get("industry_vertical", ""),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "discovery_hits_truncated": len(state.discovery_hits) > 250,
        "discovery_hits": _hits_to_json(state.discovery_hits),
        "scrape_enabled": scrape_websites and settings.web_fetch_enabled,
        "scrape_max": max_scrape,
        "scrape_count": len(scrape_map),
        "verification_report": verification_report,
        "verified_candidate_count": len(entities),
        "target_solid_companies": getattr(settings, "target_solid_companies", 30),
        "warnings": warnings,
    }

    out_dir = _project_root() / "output" / "phase2"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = _slug_from_scope(scope, query)
    out_path = out_dir / f"phase2_discovery_{slug}.json"
    out_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest["output_path"] = str(out_path.resolve())

    if os.getenv("SAVE_PHASE2_HITS", "").strip().lower() in ("1", "true", "yes"):
        hits_path = out_dir / f"phase2_hits_{slug}.json"
        hits_path.write_text(
            json.dumps([h.model_dump() for h in state.discovery_hits], indent=2),
            encoding="utf-8",
        )
        manifest["full_hits_path"] = str(hits_path.resolve())

    return manifest


def run_phase2_sync(
    query: str | None = None,
    settings: Settings | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return asyncio.run(run_phase2(query, settings, **kwargs))


def print_phase2_summary(manifest: dict[str, Any]) -> None:
    mode = "MOCK" if manifest.get("mock_mode") else "LIVE"
    print(f"\n=== Phase 2 complete ({mode}) ===")
    print(f"  Output: {manifest.get('output_path')}")
    if manifest.get("phase1_plan_path"):
        print(f"  From Phase 1: {manifest.get('phase1_plan_path')}")
    scope = manifest.get("scope") or {}
    print(
        f"  Market: {scope.get('market', '?')} "
        f"| Industry: {scope.get('industry_vertical') or manifest.get('industry_vertical') or '—'} "
        f"| Search topic: {scope.get('search_topic', '?')} "
        f"| Geo: {(scope.get('geographies') or ['?'])[0]}"
    )
    print(f"  Results per prompt: {manifest.get('results_per_query', 15)}")
    print("  API status:")
    for name, info in (manifest.get("api_status") or {}).items():
        print(f"    {name}: {info.get('status', '?')} — {str(info.get('detail', ''))[:60]}")
    stats = manifest.get("discovery_stats") or {}
    print(
        f"  Hits: {stats.get('raw_hits', 0)} raw | "
        f"{stats.get('unique_names', 0)} unique names | "
        f"widen loops: {stats.get('widen_loops', 0)}"
    )
    print(f"  Candidates (deduped): {manifest.get('candidate_count', 0)}")
    if manifest.get("scrape_enabled"):
        print(f"  Websites scraped: {manifest.get('scrape_count', 0)}")
    print("  Top candidates:")
    for c in (manifest.get("candidates") or [])[:12]:
        levels = ",".join(c.get("funnel_levels_seen") or []) or "—"
        scrape = c.get("scrape") or {}
        alive = scrape.get("alive", False) if scrape else None
        scrape_note = ""
        if alive is True:
            scrape_note = (
                f" | scrape OK (profile={scrape.get('profile_chars', 0)}, "
                f"corp={scrape.get('corporate_chars', 0)} chars)"
            )
        desc = (c.get("company_description") or "")[:40]
        if desc:
            scrape_note += f" | {desc}..."
        elif alive is False and scrape:
            scrape_note = " | scrape failed"
        fn = c.get("company_function") or "unknown"
        print(
            f"    - {c.get('canonical_name', '?')[:48]} "
            f"(hits={c.get('discovery_count', 0)}, fn={fn}, levels={levels}){scrape_note}"
        )
    if manifest.get("discovery_hits_truncated"):
        print(f"  Full hits file: {manifest.get('full_hits_path')}")
    for w in manifest.get("warnings") or []:
        print(f"  Warning: {w}")
    print()

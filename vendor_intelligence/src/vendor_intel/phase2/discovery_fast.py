"""Phase 2 — fast discovery: search + name/domain only (no scrape, no smart_crawl)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vendor_intel.clients.claude import ClaudeClient
from vendor_intel.clients.search_router import FreeSearchRouter
from vendor_intel.config import Settings, _project_root
from vendor_intel.discovery.candidate_quality import filter_junk_entities, merge_scope_with_global_junk
from vendor_intel.discovery.company_registry import enrich_entity_domain, is_blocklisted_domain, set_registry_scope
from vendor_intel.discovery.entity_extract import (
    is_blocked_domain,
    is_listicle_domain,
    is_valid_company,
    pick_primary_domain,
)

# RETIRED (precision mode): extended blocklist — use pipeline.entity_gate instead.
# Kept for reference; balanced profile uses entity_gate.filter_companies().
# _PIPELINE_JUNK_DOMAIN_PARTS_LEGACY: tuple[str, ...] = (
_PIPELINE_JUNK_DOMAIN_PARTS: tuple[str, ...] = (
    "researchgate",
    "marketresearch",
    "sphericalinsights",
    "intentmarket",
    "datainsights",
    "expertmarket",
    "theinsightpartners",
    "towardschem",
    "vyansaintelligence",
    "worldstopexports",
    "growyourbusiness",
    "nasdaq.com",
    "chemdive",
    "chemengonline",
    "packaginginsights",
    "process-worldwide",
    "ethanolproducer",
    "sugarindustry",
    "climatepolicyinitiative",
    "renewable-carbon.eu",
    "polyestertime",
    "mltanalytics",
    "brazilianplastics",
    "worldatlas",
    "bakingbusiness",
    "bloomberglinea",
    "bloomberg.com",
    "handwiki",
    "geocountries",
    "marketizer",
    "industrysourcing",
    "indexbox.io",
    "indexbox",
    "emergenresearch",
    "coherentmarketinsights",
    "freyrsolutions",
    "travelbrazil",
    "nationsonline",
    "nationonline",
    "materialpalette",
    "theclimatedrive",
    "masuuglobal",
    "pdfs.",
    "tenenge",
    "valiabrazil",
    "wikipedia.org",
    "wiki.",
    "atlas.",
    "countries.com",
)


_HARD_BLOCK_DOMAIN_RECALL = re.compile(
    r"(wikipedia\.org|facebook\.com|twitter\.com|x\.com|instagram\.com|"
    r"youtube\.com|tiktok\.com|pinterest\.com)",
    re.I,
)

_BLOCKED_DOMAIN_RE = re.compile(
    r"(?:^|\.)("
    r"worldatlas|handwiki|geocountries|marketizer|industrysourcing|indexbox|"
    r"emergenresearch|coherentmarketinsights|freyrsolutions|travelbrazil|"
    r"nationsonline|bloomberglinea|bakingbusiness|materialpalette|theclimatedrive"
    r")(?:\.|$)",
    re.I,
)


def _is_pipeline_junk_domain(
    domain: str,
    *,
    strict: bool = True,
    name: str = "",
) -> bool:
    """strict=False in recall mode only."""
    if not strict:
        return bool(_HARD_BLOCK_DOMAIN_RECALL.search(domain or ""))
    from vendor_intel.pipeline.entity_gate import reject_domain_only

    return reject_domain_only(domain, name=name) is not None


def _discovery_source_for_hits(hits: list) -> str:
    backends = sorted({str(getattr(h, "backend", "") or "") for h in hits if getattr(h, "backend", None)})
    return "+".join(b for b in backends if b) or "search"
from vendor_intel.funnel.levels import merge_funnel_into_config
from vendor_intel.funnel.scope_schema import scope_summary
from vendor_intel.live_checks import validate_live_settings
from vendor_intel.mock.fixtures import is_mock_run
from vendor_intel.models import RunConfig, RunState
from vendor_intel.phase2.runner import _discovery_stats, _slug_from_scope, load_phase1_plan
from vendor_intel.stages.a_compiler import compile_query
from vendor_intel.stages.b_discovery import run_discovery
from vendor_intel.utils.dedupe import build_entities_from_hits
from vendor_intel.utils.domains import company_dedupe_key, domain_from_url

MAX_FAST_COMPANIES = 280
MAX_FAST_COMPANIES_RECALL = 320


def normalize_domain(raw: str) -> str:
    d = (raw or "").strip().lower()
    for prefix in ("https://", "http://"):
        if d.startswith(prefix):
            d = d[len(prefix) :]
    if d.startswith("www."):
        d = d[4:]
    d = d.split("/")[0].split("?")[0].strip(".")
    return d


def hits_to_company_list(
    state: RunState,
    scope: dict[str, Any],
    *,
    max_companies: int = MAX_FAST_COMPANIES,
    recall_mode: bool = False,
    strict_junk: bool = True,
) -> list[dict[str, str]]:
    """Build deduplicated {name, domain} list from discovery hits."""
    raw_hit_count = len(state.discovery_hits)
    entities = build_entities_from_hits(state.discovery_hits)
    n_built = len(entities)
    if not recall_mode:
        entities = filter_junk_entities(entities, state.discovery_hits, scope)
        if n_built and not entities:
            print(
                f"  [phase2-fast] WARNING: built {n_built} entities from {raw_hit_count} hits "
                f"but filter_junk_entities removed all — check domain/name matching",
                flush=True,
            )

    from vendor_intel.discovery.candidate_quality import (
        build_prompt_function_map,
        functions_from_hits,
    )
    from vendor_intel.discovery.company_registry import seed_function_for_name

    prompts = list(getattr(state.config, "prompts", None) or []) + list(
        getattr(state.config, "funnel_prompts", None) or []
    )
    prompt_map = build_prompt_function_map(prompts)

    by_domain: dict[str, dict[str, Any]] = {}
    for ent in entities:
        ent_key = company_dedupe_key(ent.canonical_name)
        hits_for = [
            h
            for h in state.discovery_hits
            if company_dedupe_key(h.name_raw) == ent_key
            or (h.name_raw or "").strip() == (ent.canonical_name or "").strip()
        ]
        hit_doms = [h.source_domain for h in hits_for if h.source_domain]
        dom = normalize_domain(
            enrich_entity_domain(
                ent.canonical_name,
                ent.primary_domain or pick_primary_domain(hits_for, ent.canonical_name),
                hit_doms,
            )
        )
        if not dom:
            for h in hits_for:
                dom = normalize_domain(h.source_domain or domain_from_url(h.source_url or ""))
                if dom:
                    break
        if not dom:
            continue
        name = (ent.canonical_name or "").strip()
        if not name:
            continue
        if _is_pipeline_junk_domain(
            dom, strict=strict_junk and not recall_mode, name=name
        ):
            continue
        if strict_junk and not recall_mode and not is_valid_company(name, dom, scope=scope):
            continue

        primary_fn, _all_fns = functions_from_hits(
            hits_for, prompt_map, entity_name=name
        )
        if not primary_fn or primary_fn == "unknown":
            primary_fn = seed_function_for_name(name, scope) or primary_fn
        prev = by_domain.get(dom)
        score = ent.discovery_count
        src = _discovery_source_for_hits(hits_for)
        if not prev or score > prev.get("_score", 0):
            by_domain[dom] = {
                "name": name,
                "domain": dom,
                "_score": score,
                "discovery_source": src,
                "company_function": primary_fn or "unknown",
            }

    rows = [
        {
            "name": v["name"],
            "domain": v["domain"],
            "discovery_source": v.get("discovery_source", "search"),
            "company_function": v.get("company_function", "unknown"),
        }
        for v in by_domain.values()
    ]
    rows.sort(key=lambda r: by_domain[r["domain"]].get("_score", 0), reverse=True)
    if n_built and not rows and not recall_mode:
        print(
            f"  [phase2-fast] WARNING: {n_built} entities after filter but 0 domains exported "
            f"(junk domain / is_valid_company) — check entity_gate",
            flush=True,
        )
    return rows[:max_companies]


async def run_phase2_fast(
    query: str | None = None,
    settings: Settings | None = None,
    *,
    phase1_plan_path: str | Path | None = None,
    max_companies: int = MAX_FAST_COMPANIES,
) -> dict[str, Any]:
    """Fast Phase 2: discovery search only → list of {name, domain}."""
    from vendor_intel.placeholders.load_keys import apply_env_overrides

    apply_env_overrides()
    settings = settings or Settings.load()
    recall_mode = bool(getattr(settings, "pipeline_recall_mode", False))
    profile = str(getattr(settings, "pipeline_profile", "quality") or "quality")
    strict_junk = profile in ("quality", "balanced") and not recall_mode
    warnings = validate_live_settings(settings)
    if recall_mode:
        print(
            "  [phase2-fast] RECALL MODE — minimal junk filter, up to "
            f"{MAX_FAST_COMPANIES_RECALL} companies",
            flush=True,
        )

    phase1_plan: dict[str, Any] | None = None
    if phase1_plan_path:
        q, config, phase1_plan = load_phase1_plan(phase1_plan_path)
        query = query or q
    else:
        if not (query or "").strip():
            raise ValueError("Provide a query or phase1_plan_path")
        claude = ClaudeClient(settings)
        config = compile_query(query.strip(), claude, settings)
        config = merge_funnel_into_config(config, query.strip())

    assert query
    scope = merge_scope_with_global_junk(config.scope or {})
    config = config.model_copy(update={"scope": scope})
    set_registry_scope(scope)
    print(f"  [phase2-fast] scope: {scope_summary(scope)}", flush=True)

    companies: list[dict[str, str]] = []
    stats: dict[str, Any] = {}

    if is_mock_run(settings):
        warnings.append("Mock mode — fast discovery returns empty company list.")
    else:
        claude = ClaudeClient(settings)
        router = FreeSearchRouter(settings)
        state = RunState(query=query, config=config)

        from vendor_intel.clients.duckduckgo import reset_network_search_state

        reset_network_search_state()
        await run_discovery(state, config, claude, settings, search_router=router)
        geo = (scope.get("geographies") or ["global"])[0]
        from vendor_intel.pipeline.geo_limits import pipeline_limits

        lim = pipeline_limits(settings, recall=recall_mode, country=str(geo))
        cap = (
            MAX_FAST_COMPANIES_RECALL
            if recall_mode
            else int(lim["discover"])
        )
        companies = hits_to_company_list(
            state,
            scope,
            max_companies=cap,
            recall_mode=recall_mode,
            strict_junk=strict_junk,
        )
        if strict_junk:
            from vendor_intel.pipeline.entity_gate import filter_companies

            companies, rejected = filter_companies(companies)
            if rejected:
                print(
                    f"  [phase2-fast] entity_gate removed {len(rejected)} non-participants",
                    flush=True,
                )
        from vendor_intel.utils.domain_corrections import fix_company_list

        geo = str((scope.get("geographies") or scope.get("geography") or ["global"])[0])
        companies = fix_company_list(companies, country=geo, recall_mode=recall_mode)
        stats = _discovery_stats(state.discovery_hits, state.widen_loops)
        print(f"  [phase2-fast] {len(companies)} companies (deduped by domain)", flush=True)

    manifest: dict[str, Any] = {
        "phase": 2,
        "phase2_mode": "fast",
        "query": query,
        "mock_mode": is_mock_run(settings),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "scope": scope,
        "phase1_plan_path": str(Path(phase1_plan_path).resolve()) if phase1_plan_path else None,
        "companies": companies,
        "company_count": len(companies),
        "discovery_stats": stats,
        "warnings": warnings,
    }

    out_dir = _project_root() / "output" / "phase2"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = _slug_from_scope(scope, query)
    out_path = out_dir / f"phase2_companies_{slug}.json"
    out_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest["output_path"] = str(out_path.resolve())
    return manifest

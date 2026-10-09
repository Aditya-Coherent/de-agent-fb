"""Phase 3 — load Phase 2 candidates and run validation gates (no re-discovery)."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vendor_intel.clients.api_health import check_all_apis
from vendor_intel.clients.claude import ClaudeClient
from vendor_intel.clients.search_router import FreeSearchRouter
from vendor_intel.config import Settings, _project_root
from vendor_intel.live_checks import validate_live_settings
from vendor_intel.mock.fixtures import is_mock_run
from vendor_intel.models import DiscoveryHit, Entity, EvidenceItem, RunConfig, RunState
from vendor_intel.placeholders.wikidata import lookup_parent_org
from vendor_intel.stages.a_compiler import MOCK_CONFIG_DEFAULTS
from vendor_intel.stages.d_validation import run_validation
from vendor_intel.utils.domains import domain_from_url

_DEFAULT_GATES = {
    "operational": {"min_domains": 2},
    "geography": {"min_domains": 2},
    "product": {"min_domains": 2},
    "activity": {"min_articles": 1},
    "ma": {"min_domains": 1},
}


def load_phase2_discovery(path: str | Path) -> tuple[str, RunConfig, dict[str, Any], list[Entity]]:
    """Load query, config, manifest, and Entity list from phase2_discovery_*.json."""
    p = Path(path)
    if not p.is_file():
        if not p.is_absolute():
            alt = _project_root() / p
            if alt.is_file():
                p = alt
        if not p.is_file():
            plans_dir = _project_root() / "output" / "phase2"
            available = sorted(plans_dir.glob("phase2_discovery_*.json")) if plans_dir.is_dir() else []
            hint = ""
            if available:
                hint = "\n  Available discovery files:\n" + "\n".join(
                    f"    {f.relative_to(_project_root())}" for f in available
                )
            raise FileNotFoundError(f"Phase 2 discovery file not found: {path}{hint}")

    plan = json.loads(p.read_text(encoding="utf-8"))
    if plan.get("phase") != 2:
        raise ValueError(f"Not a Phase 2 discovery file (phase={plan.get('phase')}): {p}")

    query = str(plan.get("query") or "").strip()
    if not query:
        raise ValueError(f"Phase 2 discovery missing query: {p}")

    from vendor_intel.funnel.scope_schema import normalize_run_scope

    scope = normalize_run_scope(dict(plan.get("scope") or {}), query)
    config = RunConfig(
        scope=scope,
        funnel_prompts=list(plan.get("funnel_prompts") or []),
        prompts=list(plan.get("discovery_prompts") or []),
        gates=_DEFAULT_GATES,
        evidence_policy=MOCK_CONFIG_DEFAULTS["evidence_policy"],
        freshness_policy=MOCK_CONFIG_DEFAULTS["freshness_policy"],
    )
    # CHANGED: dynamic geo gate — pass scope for geography signals
    entities = _entities_from_candidates(
        plan.get("candidates") or [],
        scope=scope,
    )
    return query, config, plan, entities


def _entities_from_candidates(
    candidates: list[dict[str, Any]],
    scope: dict[str, Any] | None = None,
) -> list[Entity]:
    from vendor_intel.discovery.candidate_quality import is_junk_candidate_name
    from vendor_intel.discovery.entity_extract import is_generic_phrase_name
    from vendor_intel.validation.geo_signals import (
        check_geo_match,
        geo_evidence_snippet,
        get_geo_signals,
    )

    # CHANGED: dynamic geo gate — build signals from scope once
    _target_geos = list((scope or {}).get("geographies") or [])
    _geo_signals = get_geo_signals(_target_geos)
    _geo_gate_enabled = bool(_geo_signals)
    _geo_label = ", ".join(_target_geos) if _target_geos else "target geography"

    entities: list[Entity] = []
    for c in candidates:
        if not isinstance(c, dict):
            continue
        name = str(c.get("canonical_name") or "").strip()
        if not name:
            continue

        domain = str(c.get("primary_domain") or "").strip()
        # CHANGED: phase3 quality fix — final junk filter before building entities
        if is_junk_candidate_name(name, domain):
            continue
        if is_generic_phrase_name(name):
            continue
        scrape = c.get("scrape") if isinstance(c.get("scrape"), dict) else {}
        url = str(scrape.get("url") or "").strip()
        if not domain and url:
            domain = domain_from_url(url) or ""
        if domain and not domain.startswith("http"):
            site_url = f"https://{domain.lstrip('/')}"
        else:
            site_url = url

        desc = str(c.get("company_description") or scrape.get("company_description") or "").strip()
        corp_preview = str(scrape.get("corporate_preview") or "")
        scraped_text = desc or corp_preview

        parent_hint = str(c.get("parent_group_hint") or scrape.get("parent_group_hint") or "").strip()

        # CHANGED: phase3 quality fix — carry company_function from Phase 2
        company_fn = str(
            c.get("company_function") or c.get("discovered_via_function") or ""
        ).strip()
        disc_fns = [
            str(x).strip()
            for x in (c.get("discovered_functions") or [])
            if str(x).strip()
        ]
        ent = Entity(
            canonical_name=name,
            primary_domain=domain,
            company_function=company_fn if company_fn and company_fn != "unknown" else "",
            discovered_functions=disc_fns or ([company_fn] if company_fn else []),
            company_type=company_fn.replace("_", " ").title()
            if company_fn and company_fn != "unknown"
            else "Unknown",
            discovery_count=int(c.get("discovery_count") or c.get("hits") or 0),
            distinct_domains_discovery=int(c.get("distinct_domains_discovery") or 0),
            funnel_levels_seen=list(c.get("funnel_levels_seen") or []),
            aliases=list(c.get("aliases") or []),
            parent_group=parent_hint or name,
            company_description=desc[:2000],
            scraped_text=scraped_text[:8000],
            scraped_urls=[site_url] if site_url else [],
        )
        # Carry Phase 2 verification confidence forward — prevents scores from starting at 0
        p2_verif = float(c.get("verification_confidence") or 0.0)
        if p2_verif > 0:
            ent.verification_confidence = p2_verif
        if c.get("content_function_confidence") is not None:
            ent.score_breakdown = {
                "content_function_confidence": float(c.get("content_function_confidence") or 0),
                "discovered_functions": disc_fns,
            }
        if scrape.get("alive") and site_url:
            ent.gates.setdefault("operational", []).append(
                EvidenceItem(
                    url=site_url,
                    domain=domain_from_url(site_url) or domain,
                    type="scrape",
                    snippet=scraped_text[:500],
                    gate="operational",
                )
            )
            if scraped_text:
                ent.gates.setdefault("product", []).append(
                    EvidenceItem(
                        url=site_url,
                        domain=domain_from_url(site_url) or domain,
                        type="scrape",
                        snippet=scraped_text[:400],
                        gate="product",
                    )
                )
                # CHANGED: dynamic geo gate — scope-driven, not hardcoded India cities
                if _geo_gate_enabled:
                    _geo_matched, _geo_signal = check_geo_match(
                        scraped_text, domain, _geo_signals, url=site_url
                    )
                    if _geo_matched:
                        ent.gates.setdefault("geography", []).append(
                            EvidenceItem(
                                url=site_url,
                                domain=domain_from_url(site_url) or domain,
                                type="scrape",
                                snippet=geo_evidence_snippet(_geo_signal, _geo_label),
                                gate="geography",
                            )
                        )
        entities.append(ent)

    entities.sort(key=lambda e: (-e.discovery_count, e.canonical_name))
    return entities


def _load_discovery_hits(phase2_manifest: dict[str, Any]) -> list[DiscoveryHit]:
    hits_raw = phase2_manifest.get("discovery_hits")
    if isinstance(hits_raw, list) and hits_raw:
        return [DiscoveryHit.model_validate(h) for h in hits_raw if isinstance(h, dict)]

    hits_path = phase2_manifest.get("full_hits_path")
    if hits_path:
        p = Path(hits_path)
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [DiscoveryHit.model_validate(h) for h in data if isinstance(h, dict)]
    return []


async def _enrich_parents(entities: list[Entity], settings: Settings) -> None:
    if is_mock_run(settings):
        return
    for ent in entities[: settings.max_validation_entities]:
        if ent.parent_group and ent.parent_group != ent.canonical_name:
            continue
        try:
            parent, _ = await lookup_parent_org(ent.canonical_name)
            if parent:
                ent.parent_group = parent
                ent.parent_company = parent
        except Exception:
            pass


def _entity_validation_row(ent: Entity) -> dict[str, Any]:
    evidence_urls: list[str] = []
    for items in ent.gates.values():
        for it in items:
            if it.url and it.url not in evidence_urls:
                evidence_urls.append(it.url)
    company_fn = (ent.company_function or ent.company_type or "").strip()
    if company_fn.lower() in ("unknown", ""):
        company_fn = ""
    # Normalize Title Case back to snake_case for export labels
    if company_fn and "_" not in company_fn and " " in company_fn:
        company_fn = company_fn.lower().replace(" ", "_")
    return {
        "canonical_name": ent.canonical_name,
        "primary_domain": ent.primary_domain,
        "parent_group": ent.parent_group,
        "company_function": company_fn,
        "discovered_functions": list(ent.discovered_functions or []),
        "tier": ent.tier,
        "composite_score": round(ent.composite_score, 3),
        "score_breakdown": dict(ent.score_breakdown or {}),
        "verification_confidence": round(float(ent.verification_confidence or 0), 3),
        "discovery_count": ent.discovery_count,
        "distinct_domains": ent.distinct_domains,
        "gate_pass": dict(ent.gate_pass),
        "suppression_reason": ent.suppression_reason,
        "company_description": (ent.company_description or ent.scraped_text or "")[:500],
        "evidence_url_count": len(evidence_urls),
        "sample_evidence_urls": evidence_urls[:8],
    }


def _slug_from_scope(scope: dict[str, Any], query: str) -> str:
    slug_base = f"{scope.get('market', query)}_{(scope.get('geographies') or [''])[0]}"
    return re.sub(r"[^a-z0-9]+", "_", slug_base.lower())[:56].strip("_") or "run"


async def run_phase3(
    query: str | None = None,
    settings: Settings | None = None,
    *,
    phase2_discovery_path: str | Path | None = None,
    skip_search_validation: bool = False,
    fast_validation: bool | None = None,
) -> dict[str, Any]:
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

    phase2_plan: dict[str, Any] = {}
    if phase2_discovery_path:
        query, config, phase2_plan, entities = load_phase2_discovery(phase2_discovery_path)
    elif query:
        raise ValueError("Provide --from-discovery PATH to Phase 2 JSON (required).")
    else:
        raise ValueError("Provide --from-discovery PATH or a query with discovery file.")

    scope = config.scope or {}
    from vendor_intel.discovery.company_registry import set_registry_scope
    from vendor_intel.funnel.scope_schema import scope_summary

    set_registry_scope(scope)
    print(f"  [phase3] LLM scope: {scope_summary(scope)}", flush=True)

    claude = ClaudeClient(settings)
    router = FreeSearchRouter(settings)

    state = RunState(query=query, config=config, entities=entities)
    hits = _load_discovery_hits(phase2_plan)
    if hits:
        state.discovery_hits = hits

    print(
        f"\n  [phase3] Loaded {len(entities)} candidates from Phase 2 "
        f"({Path(phase2_discovery_path or '').name})",
        flush=True,
    )

    await _enrich_parents(state.entities, settings)

    if not skip_search_validation:
        print(
            f"  [phase3] Running validation gates (max {settings.max_validation_entities} entities)...",
            flush=True,
        )
        await run_validation(
            state,
            config,
            settings,
            search_router=router,
            fast_validation=fast_validation,
        )
    elif is_mock_run(settings):
        from vendor_intel.mock.fixtures import apply_mock_validation

        for ent in state.entities:
            apply_mock_validation(ent)

    validated = [_entity_validation_row(e) for e in state.entities]
    tier_a = [e for e in state.entities if e.tier == "A"]
    tier_b = [e for e in state.entities if e.tier == "B"]
    tier_c = [e for e in state.entities if e.tier == "C"]
    passed_ab = tier_a + tier_b

    api_status = await check_all_apis(settings, llm_responded_ok=None)

    manifest: dict[str, Any] = {
        "phase": 3,
        "query": query,
        "mock_mode": is_mock_run(settings),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "llm_provider": settings.llm_provider,
        "phase2_discovery_path": str(Path(phase2_discovery_path).resolve())
        if phase2_discovery_path
        else None,
        "scope": scope,
        "scope_source": scope.get("scope_source", "phase2"),
        "input_candidate_count": len(entities),
        "validated_entity_count": len(validated),
        "tier_a_count": len(tier_a),
        "tier_b_count": len(tier_b),
        "tier_c_count": len(tier_c),
        "company_list_ready_count": len(passed_ab),
        "max_validation_entities": settings.max_validation_entities,
        "fast_validation": fast_validation
        if fast_validation is not None
        else getattr(settings, "phase3_fast_validation", True),
        "parallel_workers": getattr(settings, "phase3_parallel_workers", 3),
        "agentic_validation": getattr(settings, "phase3_agentic_validation", True),
        "hybrid_validation": (config.scope or {}).get("_hybrid_validation_meta", {}),
        "gates": list(_DEFAULT_GATES.keys()),
        "api_status": api_status,
        "validated_entities": validated,
        "warnings": list(warnings),
    }

    out_dir = _project_root() / "output" / "phase3"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = _slug_from_scope(scope, query)
    out_path = out_dir / f"phase3_validation_{slug}.json"
    out_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest["output_path"] = str(out_path.resolve())

    return manifest


def run_phase3_sync(
    query: str | None = None,
    settings: Settings | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return asyncio.run(run_phase3(query, settings, **kwargs))


def print_phase3_summary(manifest: dict[str, Any]) -> None:
    mode = "MOCK" if manifest.get("mock_mode") else "LIVE"
    print(f"\n=== Phase 3 complete ({mode}) ===")
    print(f"  Output: {manifest.get('output_path')}")
    if manifest.get("phase2_discovery_path"):
        print(f"  From Phase 2: {manifest.get('phase2_discovery_path')}")
    scope = manifest.get("scope") or {}
    print(
        f"  Market: {scope.get('market', '?')} "
        f"| Geo: {(scope.get('geographies') or ['?'])[0]}"
    )
    print(
        f"  Candidates in: {manifest.get('input_candidate_count', 0)} "
        f"| Validated: {manifest.get('validated_entity_count', 0)}"
    )
    print(
        f"  Tiers — A: {manifest.get('tier_a_count', 0)} "
        f"| B: {manifest.get('tier_b_count', 0)} "
        f"| C: {manifest.get('tier_c_count', 0)} "
        f"| Ready for listing (A+B): {manifest.get('company_list_ready_count', 0)}"
    )
    hybrid = manifest.get("hybrid_validation") or {}
    if hybrid:
        print(
            f"  Hybrid — deterministic promoted: {hybrid.get('deterministic_promoted', 0)} "
            f"| agent reviewed: {hybrid.get('reviewed', 0)} "
            f"| agent updates: {hybrid.get('changed', 0)}",
            flush=True,
        )
    if manifest.get("tier_c_count", 0) > 0:
        print(
            "  Note: Tier C = junk names, weak evidence, or failed gates "
            "(not in your final company list).",
            flush=True,
        )
    print("  Gates: operational, geography, product, activity, ma")
    print("  Top validated (A/B):")
    rows = [
        e
        for e in (manifest.get("validated_entities") or [])
        if e.get("tier") in ("A", "B")
    ]
    for e in rows[:15]:
        gates = e.get("gate_pass") or {}
        ok = sum(1 for v in gates.values() if v)
        print(
            f"    [{e.get('tier')}] {e.get('canonical_name', '?')[:42]:42} "
            f"score={e.get('composite_score', 0)} gates_ok={ok}/4 "
            f"domains={e.get('distinct_domains', 0)}"
        )
    for w in manifest.get("warnings") or []:
        print(f"  Warning: {w}")
    print(
        "\n  Next: Phase 4 CSV — .venv\\Scripts\\python.exe run_cli.py --live "
        f"\"{manifest.get('query', '')}\""
    )

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vendor_intel.clients.news import fetch_company_news
from vendor_intel.clients.search_router import FreeSearchRouter, SearchResult
from vendor_intel.config import Settings
from vendor_intel.funnel.prompt_builder import refine_search_topic
from vendor_intel.mock.fixtures import apply_mock_validation, is_mock_run
from vendor_intel.models import EvidenceItem, RunConfig, RunState
from vendor_intel.placeholders.wikidata import lookup_parent_org
from vendor_intel.discovery.company_registry import (
    enrich_entity_domain,
    get_registry_scope,
    is_blocklisted_domain,
    is_registry_company,
    registry_domain_for_name,
    set_registry_scope,
)
from vendor_intel.discovery.entity_extract import (
    is_listicle_domain,
    is_validation_ready_name,
)
from vendor_intel.scraping.website import scrape_company_website
from vendor_intel.utils.domains import domain_from_url


@dataclass
class _GateStatus:
    operational: bool
    geography: bool
    product: bool
    activity: bool
    core_hits: int

    @property
    def core_satisfied(self) -> bool:
        return self.operational and (self.geography or self.product)

    @property
    def strong_enough(self) -> bool:
        return self.core_satisfied and self.core_hits >= 2


def _add_evidence(entity, gate: str, url: str, etype: str, snippet: str = "") -> None:
    dom = domain_from_url(url) or "unknown"
    entity.gates.setdefault(gate, []).append(
        EvidenceItem(url=url, domain=dom, type=etype, snippet=snippet[:500], gate=gate)
    )


def _distinct_domains(entity) -> set[str]:
    domains: set[str] = set()
    for items in entity.gates.values():
        for it in items:
            if it.domain and it.domain != "unknown":
                domains.add(it.domain)
    return domains


def _scope_market(scope: dict, query: str) -> str:
    raw = (
        scope.get("market")
        or scope.get("search_topic")
        or scope.get("industry_vertical")
        or ""
    )
    market = str(raw).strip()
    if market:
        return market[:80]
    return refine_search_topic(query, (scope.get("geographies") or ["global"])[0])


def _has_reusable_phase2_scrape(entity) -> bool:
    if len((entity.scraped_text or "").strip()) >= 150:
        return True
    for gate in ("operational", "product"):
        if entity.gates.get(gate):
            return True
    return False


def _gate_status(entity, min_gate_domains: int) -> _GateStatus:
    def gate_ok(gate: str) -> bool:
        items = entity.gates.get(gate, [])
        if not items:
            return False
        domains = {it.domain for it in items if it.domain != "unknown"}
        if len(domains) >= min_gate_domains:
            return True
        return min_gate_domains <= 1 and len(items) >= 1

    op_ok = gate_ok("operational")
    geo_ok = gate_ok("geography")
    prod_ok = gate_ok("product")
    return _GateStatus(
        operational=op_ok,
        geography=geo_ok,
        product=prod_ok,
        activity=bool(entity.gates.get("activity")),
        core_hits=sum((op_ok, geo_ok, prod_ok)),
    )


def _has_strong_company_evidence(entity) -> bool:
    """Real company signal: multi-domain evidence or live company-site scrape."""
    domains = _distinct_domains(entity)
    clean = {d for d in domains if not is_blocklisted_domain(d) and not is_listicle_domain(d)}
    if len(clean) >= 2:
        return True
    reg = enrich_entity_domain(entity.canonical_name, entity.primary_domain, clean)
    if reg and entity.primary_domain == reg and not is_blocklisted_domain(reg):
        if any(
            it.type in ("scrape", "official")
            for items in entity.gates.values()
            for it in items
            if it.domain == reg or it.domain == entity.primary_domain
        ):
            return True
    for items in entity.gates.values():
        for it in items:
            if it.type in ("scrape", "official") and it.domain and not is_listicle_domain(
                it.domain
            ) and not is_blocklisted_domain(it.domain):
                return True
    return False


def _assign_tier(
    entity,
    status: _GateStatus,
    *,
    market: str = "",
    geo: str = "",
) -> None:
    from vendor_intel.validation.scrape_signals import analyze_site_text

    text = (entity.scraped_text or entity.company_description or "").strip()
    # Pass the scraped URL so geo check can match /in/ path-based signals
    scraped_url = (entity.scraped_urls or [""])[0] if entity.scraped_urls else ""
    sig = analyze_site_text(text, market=market, scope=get_registry_scope(), url=scraped_url)
    op = status.operational or bool(sig["looks_like_company"])
    # CHANGED: dynamic geo gate — scope-driven geo_match (geo_india kept as legacy alias)
    geo_ok = status.geography or bool(sig.get("geo_match") or sig.get("geo_india"))
    prod_ok = status.product or bool(sig.get("market_relevant") or sig.get("pharma_relevant"))
    if sig["junk_site"]:
        op = status.operational
        prod_ok = status.product

    entity.gate_pass = {
        "operational": op,
        "geography": geo_ok,
        "product": prod_ok,
        "activity": status.activity,
    }
    status = _GateStatus(
        operational=op,
        geography=geo_ok,
        product=prod_ok,
        activity=status.activity,
        core_hits=sum((op, geo_ok, prod_ok)),
    )

    from vendor_intel.validation.site_kind import is_non_product_site

    from vendor_intel.discovery.entity_extract import (
        is_generic_category_name,
        is_generic_phrase_name,
        is_likely_real_company_name,
    )

    if is_generic_phrase_name(entity.canonical_name):
        entity.tier = "C"
        entity.composite_score = 0.1
        entity.suppression_reason = "generic_phrase_not_company"
        return

    if is_generic_category_name(entity.canonical_name):
        entity.tier = "C"
        entity.composite_score = 0.12
        entity.suppression_reason = "generic_category_not_company"
        return

    if not is_likely_real_company_name(
        entity.canonical_name, entity.primary_domain or ""
    ):
        entity.tier = "C"
        entity.composite_score = 0.15
        entity.suppression_reason = entity.suppression_reason or "junk_candidate_name"
        return

    # CHANGED: phase3 quality fix — reject media/blog/directory before tier scoring
    # Registry/seed companies are never demoted by site-kind heuristics
    if not is_registry_company(entity.canonical_name) and is_non_product_site(
        entity.primary_domain or "",
        text=text,
        name=entity.canonical_name,
    ):
        entity.tier = "C"
        entity.composite_score = 0.14
        entity.suppression_reason = "non_product_site"
        entity.gate_pass = {
            "operational": False,
            "geography": False,
            "product": False,
            "activity": status.activity,
        }
        return

    if is_blocklisted_domain(entity.primary_domain):
        entity.tier = "C"
        entity.composite_score = 0.18
        entity.suppression_reason = entity.suppression_reason or "blocklisted_domain"
        return

    registry_verified = (
        is_registry_company(entity.canonical_name)
        and status.operational
        and status.product
        and bool(registry_domain_for_name(entity.canonical_name))
    )

    from vendor_intel.scoring.metrics import apply_metrics_to_entity

    gp = {
        "operational": op,
        "geography": geo_ok,
        "product": prod_ok,
        "activity": status.activity,
    }
    if not _has_strong_company_evidence(entity):
        entity.tier = "C"
        entity.suppression_reason = entity.suppression_reason or "weak_evidence"
        apply_metrics_to_entity(
            entity,
            gate_pass=gp,
            market=market,
            verification_confidence=getattr(entity, "verification_confidence", None),
            respect_agent_tier=True,
        )
        return

    apply_metrics_to_entity(
        entity,
        gate_pass=gp,
        market=market,
        verification_confidence=getattr(entity, "verification_confidence", None),
        respect_agent_tier=True,
    )
    if registry_verified and entity.tier == "C" and op and prod_ok:
        entity.tier = "B"
        entity.suppression_reason = None
    if entity.tier == "C" and not entity.suppression_reason:
        entity.suppression_reason = "gates_insufficient"
    _cap_tier_for_role_and_quality(entity)


def _cap_tier_for_role_and_quality(entity) -> None:
    """Never keep Tier A for generic phrases or unknown role (non-seeds)."""
    if entity.tier not in ("A", "B"):
        return
    from vendor_intel.discovery.entity_extract import (
        is_generic_category_name,
        is_generic_phrase_name,
        is_likely_real_company_name,
    )

    name = entity.canonical_name or ""
    if is_generic_phrase_name(name) or is_generic_category_name(name):
        entity.tier = "C"
        entity.suppression_reason = "generic_phrase_not_company"
        return
    if not is_likely_real_company_name(name, entity.primary_domain or ""):
        entity.tier = "C"
        entity.suppression_reason = "not_real_company_name"
        return
    fn = (getattr(entity, "company_function", "") or "").strip().lower()
    if entity.tier == "A" and fn in ("", "unknown", "unclear") and not is_registry_company(
        entity.canonical_name
    ):
        entity.tier = "B"
        entity.suppression_reason = entity.suppression_reason or "unknown_company_function"


def _guess_domain(name: str) -> str:
    return f"{name.lower().replace(' ', '')}.com"


async def _run_searches(
    query: str,
    router: FreeSearchRouter,
    *,
    market: str,
    geo: str,
    search_topic: str,
    max_hits: int,
    validation_mode: bool = True,
) -> list[SearchResult]:
    rows = await router.search(
        query,
        market=market,
        geo=geo,
        search_topic=search_topic,
        discovery_mode=False,
        validation_mode=validation_mode,
    )
    return rows[:max_hits]


def _apply_search_hits(entity, rows: list[SearchResult], gates: tuple[str, ...]) -> None:
    for r in rows:
        for gate in gates:
            _add_evidence(entity, gate, r.link, "search", r.snippet)


def _needs_fresh_scrape(entity, settings: Settings, *, fast: bool) -> bool:
    text_len = len((entity.scraped_text or "").strip())
    if text_len >= 200:
        return False
    if getattr(settings, "phase3_always_scrape_registry", True) and is_registry_company(
        entity.canonical_name
    ):
        return True
    if not fast:
        return True
    return text_len < 80


def _apply_content_function_from_scrape(entity) -> None:
    """Re-classify from scraped /about /products text (no extra search)."""
    from vendor_intel.discovery.function_enrichment import (
        apply_function_to_entity,
        classify_from_text,
    )

    text = (entity.scraped_text or entity.company_description or "").strip()
    if len(text) < 150:
        return
    current = (getattr(entity, "company_function", "") or "unknown").strip().lower()
    if not current or current == "unknown":
        ct = (entity.company_type or "").strip().lower().replace(" ", "_")
        current = ct if ct else "unknown"
    fn, all_fns, conf = classify_from_text(
        text,
        current,
        min_confidence=0.78,
        company_name=getattr(entity, "canonical_name", "") or "",
        domain=getattr(entity, "primary_domain", "") or "",
    )
    if conf >= 0.78 or fn != current:
        apply_function_to_entity(
            entity, primary_function=fn, all_functions=all_fns, confidence=conf
        )


async def _maybe_scrape(
    entity,
    settings: Settings,
    *,
    fast: bool,
    geo_hint: str = "",
) -> None:
    evidence_domains = [
        it.domain for items in entity.gates.values() for it in items if it.domain != "unknown"
    ]
    entity.primary_domain = enrich_entity_domain(
        entity.canonical_name,
        entity.primary_domain or "",
        evidence_domains,
    )
    if not entity.primary_domain or "example-" in entity.primary_domain:
        entity.primary_domain = _guess_domain(entity.canonical_name)
    if is_blocklisted_domain(entity.primary_domain):
        entity.primary_domain = enrich_entity_domain(entity.canonical_name, "", evidence_domains)

    if not settings.web_fetch_enabled or not entity.primary_domain:
        return

    if fast and _has_reusable_phase2_scrape(entity) and not _needs_fresh_scrape(
        entity, settings, fast=fast
    ):
        return

    profile = await scrape_company_website(
        entity.primary_domain,
        mode="profile",
        geo_hint=geo_hint,
        enrich_subpages=True,
    )
    if profile.alive and profile.text:
        entity.scraped_urls = list({*entity.scraped_urls, profile.final_url})
        entity.scraped_text = profile.text or entity.scraped_text
        _add_evidence(entity, "operational", profile.final_url, "scrape", profile.text[:500])
        _add_evidence(entity, "product", profile.final_url, "scrape", profile.text[:400])
        _apply_content_function_from_scrape(entity)
        return

    if not fast:
        corp = await scrape_company_website(
            entity.primary_domain, mode="corporate", geo_hint=geo_hint
        )
        if corp.alive and corp.text:
            entity.scraped_urls = list({*entity.scraped_urls, corp.final_url})
            entity.scraped_text = corp.text or entity.scraped_text
            _add_evidence(entity, "ma", corp.final_url, "scrape_lead", corp.text[:600])
            _apply_content_function_from_scrape(entity)
            return

    if not _has_reusable_phase2_scrape(entity):
        full = await scrape_company_website(
            entity.primary_domain, mode="full", max_chars=4000, geo_hint=geo_hint
        )
        if full.alive:
            entity.scraped_urls = list({*entity.scraped_urls, full.final_url})
            if full.text:
                entity.scraped_text = full.text
                _add_evidence(
                    entity, "operational", full.final_url, "scrape", full.text[:500]
                )
                _add_evidence(
                    entity, "product", full.final_url, "scrape", full.text[:400]
                )
                _apply_content_function_from_scrape(entity)
            else:
                _add_evidence(entity, "operational", full.final_url, "official")


async def _validate_entity_live(
    entity,
    state: RunState,
    config: RunConfig,
    router: FreeSearchRouter,
    settings: Settings,
    geo_label: str,
    lookback: int,
    min_gate_domains: int,
    *,
    market: str,
    max_hits: int,
    fast: bool,
) -> None:
    name = entity.canonical_name
    scope = config.scope or {}
    search_topic = str(scope.get("search_topic") or market)

    if not is_validation_ready_name(name, entity.primary_domain):
        entity.tier = "C"
        entity.composite_score = 0.12
        entity.suppression_reason = "junk_candidate_name"
        entity.gate_pass = {
            "operational": False,
            "geography": False,
            "product": False,
            "activity": False,
        }
        return

    # Pass geo so the scraper tries country-specific URLs first (e.g. apple.com/in)
    _geos = list((config.scope or {}).get("geographies") or [])
    _geo_hint = _geos[0].strip().lower() if _geos else ""
    await _maybe_scrape(entity, settings, fast=fast, geo_hint=_geo_hint)

    # Layer 4: site:domain.com search when role still ambiguous (all industries)
    if not is_mock_run(settings) and entity.primary_domain:
        from vendor_intel.discovery.function_enrichment import enrich_entity_function

        try:
            await enrich_entity_function(
                entity,
                router,
                market=market,
                geo=geo_label,
                search_topic=search_topic,
                max_hits=3,
            )
        except Exception:
            pass

    status = _gate_status(entity, min_gate_domains)

    combined_q = f"{name} {market} {geo_label} company official website products"
    if not status.core_satisfied:
        rows = await _run_searches(
            combined_q,
            router,
            market=market,
            geo=geo_label,
            search_topic=search_topic,
            max_hits=max_hits,
        )
        _apply_search_hits(entity, rows, ("operational", "geography", "product"))
        entity.primary_domain = enrich_entity_domain(
            name,
            entity.primary_domain,
            [domain_from_url(r.link) for r in rows if r.link],
        )
        status = _gate_status(entity, min_gate_domains)

    if fast and status.strong_enough and entity.discovery_count >= 3:
        parent, wurl = await lookup_parent_org(name)
        if parent:
            entity.parent_group = parent
            entity.parent_company = parent
            if wurl:
                _add_evidence(entity, "ma", wurl, "registry", f"Parent: {parent}")
        entity.distinct_domains = len(_distinct_domains(entity))
        _assign_tier(entity, status, market=market, geo=geo_label)
        return

    if not status.operational:
        op_rows = await _run_searches(
            f"{name} official website {geo_label}",
            router,
            market=market,
            geo=geo_label,
            search_topic=search_topic,
            max_hits=max_hits,
        )
        _apply_search_hits(entity, op_rows, ("operational",))

    status = _gate_status(entity, min_gate_domains)

    if fast and status.core_satisfied and entity.discovery_count >= 2:
        parent, wurl = await lookup_parent_org(name)
        if parent:
            entity.parent_group = parent
            entity.parent_company = parent
            if wurl:
                _add_evidence(entity, "ma", wurl, "registry", f"Parent: {parent}")
        entity.distinct_domains = len(_distinct_domains(entity))
        _assign_tier(entity, status, market=market, geo=geo_label)
        return

    if not status.geography and not status.product:
        fallback_q = f"{name} {market} products {geo_label}"
        fb_rows = await _run_searches(
            fallback_q,
            router,
            market=market,
            geo=geo_label,
            search_topic=search_topic,
            max_hits=max_hits,
        )
        _apply_search_hits(entity, fb_rows, ("geography", "product"))

    status = _gate_status(entity, min_gate_domains)

    need_activity = not status.activity and (
        entity.discovery_count >= 4 or (status.core_satisfied and entity.discovery_count >= 2)
    )
    if need_activity:
        alert_path = None
        if settings.google_alerts_store_path:
            alert_path = Path(settings.google_alerts_store_path)
            if not alert_path.is_absolute():
                from vendor_intel.config import _project_root

                alert_path = _project_root() / alert_path
        try:
            articles = await fetch_company_news(
                name,
                geo=geo_label,
                days=lookback,
                max_articles=4 if fast else 8,
                settings=settings,
                alert_store_path=alert_path,
            )
        except Exception:
            articles = []
        for art in articles:
            _add_evidence(
                entity,
                "activity",
                art.url,
                "news",
                f"{art.title} — {art.snippet[:200]}",
            )
        status = _gate_status(entity, min_gate_domains)

    if not entity.gates.get("ma") and not fast:
        ma_rows = await _run_searches(
            f"{name} acquisition merger {geo_label}",
            router,
            market=market,
            geo=geo_label,
            search_topic=search_topic,
            max_hits=2,
        )
        _apply_search_hits(entity, ma_rows, ("ma",))

    parent, wurl = await lookup_parent_org(name)
    if parent:
        entity.parent_group = parent
        entity.parent_company = parent
        if wurl:
            _add_evidence(entity, "ma", wurl, "registry", f"Parent: {parent}")

    entity.distinct_domains = len(_distinct_domains(entity))
    _assign_tier(
        entity,
        _gate_status(entity, min_gate_domains),
        market=market,
        geo=geo_label,
    )


async def run_validation(
    state: RunState,
    config: RunConfig,
    settings: Settings,
    *,
    search_router: FreeSearchRouter,
    fast_validation: bool | None = None,
) -> None:
    scope = config.scope
    set_registry_scope(scope)
    geo_label = (scope.get("geographies") or ["global"])[0]
    market = _scope_market(scope, state.query)
    exclusions = {x.lower() for x in scope.get("explicit_exclusions", [])}
    min_gate_domains = settings.min_domains_per_gate
    lookback = config.freshness_policy.get("corporate_events_lookback_days", 7)

    fast = (
        fast_validation
        if fast_validation is not None
        else getattr(settings, "phase3_fast_validation", True)
    )
    parallel = max(1, int(getattr(settings, "phase3_parallel_workers", 3)))
    max_hits = max(2, int(getattr(settings, "phase3_max_hits_per_query", 3)))

    candidates = sorted(
        state.entities,
        key=lambda e: (
            0 if is_registry_company(e.canonical_name) else 1,
            -e.discovery_count,
            e.canonical_name,
        ),
    )
    live_cap = settings.max_validation_entities

    to_validate: list = []
    for entity in candidates:
        name = entity.canonical_name
        low = name.lower()
        if low in exclusions:
            entity.tier = "C"
            entity.suppression_reason = "out_of_scope"
            continue
        if "intel" in low and (
            "laptop" in state.query.lower() or "phone" in state.query.lower()
        ):
            entity.tier = "C"
            entity.suppression_reason = "component_vendor"
            continue
        if "croma" in low:
            entity.tier = "C"
            entity.suppression_reason = "retailer"
            continue
        if is_mock_run(settings):
            apply_mock_validation(entity)
            continue
        if len(to_validate) >= live_cap:
            entity.tier = "C"
            entity.suppression_reason = "validation_cap"
            continue
        to_validate.append(entity)

    if not to_validate or is_mock_run(settings):
        for entity in candidates:
            if scope.get("segment_conditions") and entity.canonical_name.lower() == "apple":
                entity.tier = "C"
                entity.suppression_reason = "budget_segment_exclusion"
        return

    mode = "fast" if fast else "full"
    print(
        f"  [phase3] Validating {len(to_validate)} entities ({mode}, "
        f"{parallel} parallel, max {max_hits} hits/search)",
        flush=True,
    )

    sem = asyncio.Semaphore(parallel)
    done = 0
    total = len(to_validate)
    lock = asyncio.Lock()

    async def _run_one(entity) -> None:
        nonlocal done
        async with sem:
            await _validate_entity_live(
                entity,
                state,
                config,
                search_router,
                settings,
                geo_label,
                lookback,
                min_gate_domains,
                market=market,
                max_hits=max_hits,
                fast=fast,
            )
            async with lock:
                done += 1
                print(
                    f"  [phase3] {done}/{total} {entity.canonical_name} → tier {entity.tier}",
                    flush=True,
                )

    await asyncio.gather(*[_run_one(e) for e in to_validate])

    hybrid_meta: dict[str, Any] = {}
    if not is_mock_run(settings):
        from vendor_intel.clients.claude import ClaudeClient
        from vendor_intel.validation.validation_agent import (
            final_quality_sweep,
            run_hybrid_post_validation,
        )

        hybrid_meta = await run_hybrid_post_validation(
            state.entities,
            query=state.query,
            market=market,
            geo=geo_label,
            claude=ClaudeClient(settings),
            settings=settings,
        )
        sweep = final_quality_sweep(state.entities)
        hybrid_meta["final_quality_demoted"] = sweep.get("demoted", 0)
        if sweep.get("demoted"):
            print(
                f"  [phase3] Final quality sweep: {sweep['demoted']} junk A/B demoted to C",
                flush=True,
            )
        if hybrid_meta.get("enabled") or hybrid_meta.get("deterministic_promoted"):
            print(
                f"  [phase3] Hybrid validation: "
                f"deterministic={hybrid_meta.get('deterministic_promoted', 0)}, "
                f"agent reviewed={hybrid_meta.get('reviewed', 0)}, "
                f"agent tier updates={hybrid_meta.get('changed', 0)}, "
                f"LLM batches={hybrid_meta.get('llm_calls', 0)}",
                flush=True,
            )
    state.config.scope["_hybrid_validation_meta"] = hybrid_meta

    solid_ab = sum(
        1
        for e in state.entities
        if e.tier in ("A", "B") and is_validation_ready_name(e.canonical_name, e.primary_domain)
    )
    print(
        f"  [phase3] Solid names (A/B after quality filter): {solid_ab}",
        flush=True,
    )

    for entity in candidates:
        if scope.get("segment_conditions") and entity.canonical_name.lower() == "apple":
            entity.tier = "C"
            entity.suppression_reason = "budget_segment_exclusion"

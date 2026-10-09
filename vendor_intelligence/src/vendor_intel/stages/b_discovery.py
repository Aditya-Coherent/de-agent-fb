from __future__ import annotations

import os

from vendor_intel.clients.claude import ClaudeClient
from vendor_intel.clients.search_router import FreeSearchRouter
from vendor_intel.config import Settings
from vendor_intel.discovery.company_registry import seed_discovery_hits, set_registry_scope
from vendor_intel.funnel.scope_schema import scope_summary
from vendor_intel.discovery.entity_extract import (
    hits_from_search_result,
    is_valid_company,
    to_discovery_hits,
)
from vendor_intel.discovery.discovery_query_engine import (
    QueryYieldTracker,
    infer_sub_sector,
    mutate_low_yield_query,
    query_cache_key,
    sub_sector_query_pool,
)
from vendor_intel.discovery.discovery_query_quality import (
    is_listicle_discovery_query,
    sanitize_discovery_query,
)
from vendor_intel.discovery.sector_tree import build_sector_tree_prompts
from vendor_intel.discovery.seed_expansion import build_seed_expansion_prompts
from vendor_intel.discovery.volume_prompts import build_volume_prompts
from vendor_intel.funnel.prompt_builder import build_widen_prompts
from vendor_intel.mock.fixtures import generate_mock_discovery_hits, is_mock_run
from vendor_intel.models import DiscoveryHit, RunConfig, RunState
from vendor_intel.pipeline.geo_limits import is_global_geography


def _sanitize_prompt_text(text: str) -> str:
    text = sanitize_discovery_query((text or "").strip())
    if not text or is_listicle_discovery_query(text):
        return ""
    return text


def _tag_prompt(p: dict[str, str]) -> dict[str, str]:
    text = str(p.get("text") or "")
    pid = str(p.get("id") or "")
    sector = str(p.get("sub_sector") or infer_sub_sector(text, pid))
    return {**p, "sub_sector": sector}


def _drop_already_searched_prompts(
    prompts: list[dict[str, str]],
    tracker: QueryYieldTracker,
    *,
    pass_label: str = "",
) -> list[dict[str, str]]:
    """Remove prompts whose exact query text already ran (common in widen pass)."""
    fresh: list[dict[str, str]] = []
    skipped = 0
    for p in prompts:
        text = (p.get("text") or "").strip()
        if not text:
            continue
        if query_cache_key(text) in tracker.seen_queries:
            skipped += 1
            continue
        fresh.append(p)
    if skipped:
        label = f" ({pass_label})" if pass_label else ""
        print(
            f"  [discovery] Skipped {skipped} repeat search(es){label} - "
            f"same query text already ran earlier (different roles can share one query)",
            flush=True,
        )
    return fresh


def all_search_prompts(config: RunConfig, settings: Settings | None = None) -> list[dict[str, str]]:
    """Funnel + discovery + sector tree + volume prompts (deduped)."""
    funnel = list(config.funnel_prompts or [])
    discovery = [p for p in (config.prompts or []) if p.get("id") not in {"L0", "L1", "L2"}]
    seen: set[str] = set()
    out: list[dict[str, str]] = []

    def _append_prompts(rows: list[dict[str, str]]) -> None:
        for p in rows:
            text = _sanitize_prompt_text((p.get("text") or "").strip())
            if not text:
                continue
            key = query_cache_key(text)
            if key in seen:
                continue
            seen.add(key)
            out.append(_tag_prompt({**p, "text": text}))

    scope = config.scope or {}
    geo_pre = (scope.get("geographies") or ["global"])[0]
    market_pre = str(scope.get("market") or "")
    from vendor_intel.discovery.tier1_registry import build_tier1_discovery_prompts
    from vendor_intel.funnel.market_understanding import build_prompts_from_market_map

    _append_prompts(
        build_tier1_discovery_prompts(scope, market_pre, str(geo_pre))
    )
    _append_prompts(
        build_prompts_from_market_map(scope, market_pre, geo=str(geo_pre), max_prompts=26)
    )
    _append_prompts(funnel + discovery)

    geo = (scope.get("geographies") or ["global"])[0]
    market = str(scope.get("market") or "")
    terms = scope.get("industry_terms") if isinstance(scope.get("industry_terms"), list) else []
    eco = scope.get("ecosystem_functions") if isinstance(scope.get("ecosystem_functions"), list) else []

    _append_prompts(
        build_sector_tree_prompts(
            market, geo, industry_terms=terms, max_prompts=16, scope=scope
        )
    )

    from vendor_intel.pipeline.geo_limits import is_global_geography, pipeline_limits

    if settings:
        lim = pipeline_limits(settings, recall=False, country=str(geo))
        vol_n = int(lim["volume_prompts"])
    else:
        vol_n = 22 if not is_global_geography(str(geo)) else 36
    _append_prompts(
        build_volume_prompts(
            market, geo, industry_terms=terms, ecosystem_functions=eco, max_prompts=vol_n
        )
    )
    return out


def _unique_company_count(hits: list[DiscoveryHit]) -> int:
    return len({h.name_raw.lower() for h in hits if h.name_raw})


def _discovery_parallel_batch_size() -> int:
    try:
        n = int(os.getenv("DISCOVERY_PARALLEL_BATCH", "4") or "4")
    except ValueError:
        n = 4
    return max(1, min(8, n))


def _valid_unique_count(hits: list[DiscoveryHit], scope: dict) -> int:
    names: set[str] = set()
    for h in hits:
        if not h.name_raw:
            continue
        dom = h.source_domain or ""
        if is_valid_company(h.name_raw, dom, scope=scope):
            names.add(h.name_raw.lower())
    return len(names)


def _mark_prompt_searched(p: dict, tracker: QueryYieldTracker) -> bool:
    """Register prompt query; return False if this exact text was already searched."""
    text = str(p.get("text") or "").strip()
    if not text:
        return False
    qkey = query_cache_key(text)
    pid = str(p.get("id") or "?")
    if qkey in tracker.seen_queries:
        first = tracker.searched_query_sources.get(qkey, "?")
        if first != pid:
            print(
                f"  [discovery] skip repeat search (already ran as {first}, now {pid}): "
                f"{text[:56]}",
                flush=True,
            )
        return False
    tracker.seen_queries.add(qkey)
    tracker.searched_query_sources[qkey] = pid
    return True


def _record_search_results(
    p: dict,
    results: list,
    *,
    scope: dict,
    hits: list[DiscoveryHit],
    tracker: QueryYieldTracker,
) -> float:
    text = str(p.get("text") or "").strip()
    pid = str(p.get("id", "P0"))
    sector = str(p.get("sub_sector") or infer_sub_sector(text, pid))
    uniq_before = _unique_company_count(hits)
    valid_before = _valid_unique_count(hits, scope)
    funnel_level = pid if pid in {"L0", "L1", "L2"} else ""
    for r in results:
        extracted = hits_from_search_result(
            r.title,
            r.link,
            r.snippet,
            prompt_id=pid,
            funnel_level=funnel_level,
            backend=r.backend,
            search_theme=text,
            scope=scope,
        )
        hits.extend(
            to_discovery_hits(
                extracted,
                prompt_id=pid,
                funnel_level=funnel_level,
                backend=r.backend,
                snippet=r.snippet,
                search_theme=text,
                scope=scope,
            )
        )
    uniq_after = _unique_company_count(hits)
    valid_after = _valid_unique_count(hits, scope)
    unique_added = max(0, uniq_after - uniq_before)
    raw_hits = len(results)
    ratio = tracker.record(
        query_id=pid,
        query=text,
        sub_sector=sector,
        raw_hits=raw_hits,
        unique_added=unique_added,
        validated_added=max(0, valid_after - valid_before),
    )
    print(
        f"  [discovery] {pid} ({sector}): {raw_hits} hits, "
        f"+{unique_added} companies, yield={ratio:.2f} "
        f"({uniq_after} unique)",
        flush=True,
    )
    return ratio


async def _run_prompt(
    router: FreeSearchRouter,
    p: dict,
    *,
    scope: dict,
    market: str,
    search_topic: str,
    geo: str,
    hits: list[DiscoveryHit],
    tracker: QueryYieldTracker,
) -> float | None:
    """Run one query; return yield ratio or None if skipped (cache hit)."""
    if not _mark_prompt_searched(p, tracker):
        return None
    text = str(p.get("text") or "").strip()
    results = await router.search(
        text,
        market=market,
        geo=geo,
        search_topic=search_topic,
        discovery_mode=True,
    )
    return _record_search_results(p, results, scope=scope, hits=hits, tracker=tracker)


async def _run_prompt_batch(
    router: FreeSearchRouter,
    prompts: list[dict],
    *,
    scope: dict,
    market: str,
    search_topic: str,
    geo: str,
    hits: list[DiscoveryHit],
    tracker: QueryYieldTracker,
) -> list[float | None]:
    """Run several discovery prompts in parallel (same filters as _run_prompt)."""
    runnable: list[dict] = []
    for p in prompts:
        if _mark_prompt_searched(p, tracker):
            runnable.append(p)
    if not runnable:
        return [None] * len(prompts)

    if len(runnable) == 1:
        p = runnable[0]
        results = await router.search(
            str(p.get("text") or ""),
            market=market,
            geo=geo,
            search_topic=search_topic,
            discovery_mode=True,
        )
        return [_record_search_results(p, results, scope=scope, hits=hits, tracker=tracker)]

    texts = [str(p.get("text") or "").strip() for p in runnable]
    print(
        f"  [discovery] Parallel search batch ({len(runnable)} prompts)",
        flush=True,
    )
    result_map = await router.search_batch(
        texts,
        market=market,
        geo=geo,
        search_topic=search_topic,
        discovery_mode=True,
    )
    ratios: list[float | None] = []
    for p in runnable:
        text = str(p.get("text") or "").strip()
        results = result_map.get(text) or []
        ratios.append(
            _record_search_results(p, results, scope=scope, hits=hits, tracker=tracker)
        )
    return ratios


async def _discover_live(
    router: FreeSearchRouter,
    prompts: list[dict],
    *,
    scope: dict,
    fallback_query: str,
    settings: Settings | None = None,
    tracker: QueryYieldTracker | None = None,
    allow_mutations: bool = True,
    initial_hits: list[DiscoveryHit] | None = None,
    pass_label: str = "",
) -> tuple[list[DiscoveryHit], QueryYieldTracker]:
    tracker = tracker or QueryYieldTracker()
    set_registry_scope(scope)
    if initial_hits is not None:
        hits = initial_hits
    else:
        hits = list(seed_discovery_hits(scope))
        if hits:
            n_co = len({h.name_raw for h in hits})
            print(
                f"  [discovery] LLM seed companies: {n_co} names, {len(hits)} hits "
                f"({scope_summary(scope)})",
                flush=True,
            )
    market = str(scope.get("market") or fallback_query)
    search_topic = str(scope.get("search_topic") or market)
    geo = (scope.get("geographies") or ["global"])[0]

    ordered = [p for p in prompts if p.get("id") in {"L0", "L1", "L2"}]
    ordered.sort(key=lambda p: p.get("id", ""))
    rest = [p for p in prompts if p not in ordered]
    prioritized = tracker.prioritize([_tag_prompt(p) for p in ordered + rest])
    prioritized = _drop_already_searched_prompts(
        prioritized, tracker, pass_label=pass_label or "pre-filter"
    )

    min_prompts_run = 12
    yield_min_unique = 120
    yield_threshold = 0.08
    yield_window = 8
    if settings:
        min_prompts_run = min(20, max(12, len(prioritized) // 3))
        yield_min_unique = int(getattr(settings, "discovery_yield_min_unique", 120))
        yield_threshold = float(getattr(settings, "yield_stop_threshold", 0.08))
        yield_window = int(getattr(settings, "low_yield_consecutive", 8))

    prompts_run = 0
    pending_expansion: list[dict[str, str]] = []
    batch_size = _discovery_parallel_batch_size()
    idx = 0
    # Batch-level saturation stop: once a whole parallel batch adds < this fraction of new
    # unique companies (relative to what we already have), discovery is saturated. 0 disables.
    batch_min_growth = float(getattr(settings, "discovery_batch_min_growth", 0.0) or 0.0) if settings else 0.0
    batch_min_unique = int(getattr(settings, "discovery_batch_stop_min_unique", 40) or 40) if settings else 40
    low_growth_batches = 0

    from vendor_intel.pipeline.cancel import check_cancelled
    from vendor_intel.pipeline.time_budget import get_budget

    while idx < len(prioritized):
        check_cancelled("discovery")
        if get_budget().discovery_should_stop():
            print(
                f"  [discovery] time budget: stopping {pass_label or 'discovery'} with "
                f"{_unique_company_count(hits)} unique ({len(prioritized) - idx} prompts skipped)",
                flush=True,
            )
            break
        while pending_expansion:
            if get_budget().discovery_should_stop():
                pending_expansion.clear()
                break
            ep = pending_expansion.pop(0)
            await _run_prompt(
                router, ep, scope=scope, market=market, search_topic=search_topic,
                geo=geo, hits=hits, tracker=tracker,
            )

        if get_budget().discovery_should_stop():
            break
        n_before_batch = _unique_company_count(hits)
        batch: list[dict] = []
        while idx < len(prioritized) and len(batch) < batch_size:
            p = prioritized[idx]
            idx += 1
            text = str(p.get("text") or "").strip()
            if not text:
                continue
            if query_cache_key(text) in tracker.seen_queries:
                continue
            batch.append(p)

        if not batch:
            continue

        if len(batch) == 1:
            ratios = [
                await _run_prompt(
                    router, batch[0], scope=scope, market=market,
                    search_topic=search_topic, geo=geo, hits=hits, tracker=tracker,
                )
            ]
            batch_prompts = batch
        else:
            ratios = await _run_prompt_batch(
                router, batch, scope=scope, market=market,
                search_topic=search_topic, geo=geo, hits=hits, tracker=tracker,
            )
            batch_prompts = batch

        if batch_min_growth > 0:
            n_after_batch = _unique_company_count(hits)
            growth = (n_after_batch - n_before_batch) / max(n_before_batch, 1)
            if n_after_batch >= batch_min_unique and prompts_run + len(batch_prompts) >= min(
                8, len(prioritized)
            ):
                low_growth_batches = low_growth_batches + 1 if growth < batch_min_growth else 0
                if low_growth_batches >= 2:
                    print(
                        f"  [discovery] Saturation stop: last 2 batches each added "
                        f"<{batch_min_growth:.0%} new companies ({n_after_batch} unique)",
                        flush=True,
                    )
                    prompts_run += len(batch_prompts)
                    idx = len(prioritized)
                    break

        for p, ratio in zip(batch_prompts, ratios):
            prompts_run += 1
            if ratio is not None and ratio >= 0.22:
                sector = str(p.get("sub_sector") or "general")
                for ep in tracker.expansion_prompts_for_sector(
                    sector, geo, seen=tracker.seen_queries, max_new=3, market=market
                ):
                    pending_expansion.append(ep)

            n_after = _unique_company_count(hits)
            if tracker.should_stop_on_yield(
                n_after,
                min_unique=yield_min_unique,
                threshold=yield_threshold,
                window=yield_window,
                min_prompts_run=min_prompts_run,
                prompts_run=prompts_run,
            ):
                print(
                    f"  [discovery] Yield stop: {n_after} unique, last {yield_window} prompts "
                    f"avg yield < {yield_threshold:.0%} — stopping duplicate discovery",
                    flush=True,
                )
                idx = len(prioritized)
                break

    # Drain high-yield expansion queue (bounded)
    if pending_expansion and not get_budget().discovery_should_stop() and not (
        batch_min_growth > 0 and low_growth_batches >= 2
    ):
        await _run_prompt_batch(
            router, pending_expansion[:6], scope=scope, market=market,
            search_topic=search_topic, geo=geo, hits=hits, tracker=tracker,
        )

    if (
        allow_mutations
        and (settings is None or not is_mock_run(settings))
        and not get_budget().discovery_should_stop()
        and not (batch_min_growth > 0 and low_growth_batches >= 2)
    ):
        avg_yield = (
            sum(tracker.recent_yields) / len(tracker.recent_yields)
            if tracker.recent_yields
            else 0.0
        )
        if avg_yield >= yield_threshold and len(tracker.recent_yields) >= 3:
            mutation_prompts: list[dict[str, str]] = []
            for m in tracker.low_yield_queries(max_items=2):
                mut_text = mutate_low_yield_query(m.query, 0)
                if mut_text and query_cache_key(mut_text) not in tracker.seen_queries:
                    mutation_prompts.append(
                        {
                            "id": f"MU{m.query_id[:6]}",
                            "level": "mutation",
                            "text": mut_text,
                            "sub_sector": m.sub_sector,
                        }
                    )
            for sector in tracker.undercovered_sectors(target=5)[:2]:
                for row in sub_sector_query_pool(sector, market, geo, count=1):
                    k = query_cache_key(row["text"])
                    if k not in tracker.seen_queries:
                        mutation_prompts.append(row)
            if mutation_prompts:
                print(
                    f"  [discovery] {len(mutation_prompts)} targeted mutations "
                    f"(avg yield {avg_yield:.2f})",
                    flush=True,
                )
                for mp in mutation_prompts[:4]:
                    await _run_prompt(
                        router, mp, scope=scope, market=market,
                        search_topic=search_topic, geo=geo, hits=hits, tracker=tracker,
                    )

    return hits, tracker


async def run_discovery(
    state: RunState,
    config: RunConfig,
    claude: ClaudeClient,
    settings: Settings,
    *,
    search_router: FreeSearchRouter,
) -> None:
    del claude
    scope = config.scope or {}
    prompts = all_search_prompts(config, settings)
    if not prompts:
        prompts = [{"id": "P1", "level": "discovery", "text": state.query, "sub_sector": "general"}]

    tracker = QueryYieldTracker()

    if is_mock_run(settings):
        funnel = config.funnel_prompts or [{"id": "L0", "level": "L0", "text": state.query}]
        hits = generate_mock_discovery_hits(state.query, funnel + prompts[:3], "mock")
    else:
        hits, tracker = await _discover_live(
            search_router,
            prompts,
            scope=scope,
            fallback_query=state.query,
            settings=settings,
            tracker=tracker,
        )

    geo = (scope.get("geographies") or ["global"])[0]
    market = str(scope.get("market") or state.query)
    eco = scope.get("ecosystem_functions") if isinstance(scope.get("ecosystem_functions"), list) else []
    max_widen = settings.widen_loop_max
    if _unique_company_count(hits) < 55:
        max_widen = max(max_widen, 5)
    widen_lt = settings.widen_if_unique_lt
    if not is_global_geography(geo):
        # Regional: widen until ~80 unique (same headroom as global signage-style runs)
        widen_lt = min(widen_lt, 85)

    force_widen_below = int(
        getattr(settings, "discovery_force_widen_below", 80) or 80
    )
    thin_target = int(getattr(settings, "target_unique_companies", 100) or 100)
    seed_expansion_cap = 18 if is_global_geography(geo) else 14

    # Widen while below target; if unique count is still low, widen even when yield is flat
    from vendor_intel.pipeline.time_budget import get_budget

    while (
        state.widen_loops < max_widen
        and _unique_company_count(hits) < min(widen_lt, settings.target_unique_companies)
    ):
        if get_budget().discovery_should_stop():
            break
        uniq_now = _unique_company_count(hits)
        avg_y = (
            sum(tracker.recent_yields) / len(tracker.recent_yields)
            if tracker.recent_yields
            else 1.0
        )
        if (
            avg_y < settings.yield_stop_threshold
            and len(tracker.recent_yields) >= 5
            and uniq_now >= force_widen_below
        ):
            print(
                f"  [discovery] Skip widen loop — low yield ({avg_y:.2f}), "
                f"{uniq_now} unique already",
                flush=True,
            )
            break
        if avg_y < settings.yield_stop_threshold and uniq_now < force_widen_below:
            print(
                f"  [discovery] Low yield ({avg_y:.2f}) but only {uniq_now} unique "
                f"(<{force_widen_below}) — running widen pass {state.widen_loops + 1}",
                flush=True,
            )
        state.widen_loops += 1
        anchor = (scope.get("anchor_company") or "").strip() or None
        extra = build_widen_prompts(market, geo, anchor_company=anchor)
        terms = scope.get("industry_terms") if isinstance(scope.get("industry_terms"), list) else []
        extra.extend(
            build_sector_tree_prompts(market, geo, industry_terms=terms, max_prompts=6)
        )
        if _unique_company_count(hits) < force_widen_below:
            extra.extend(
                build_seed_expansion_prompts(
                    scope, market, geo, max_prompts=min(12, seed_expansion_cap)
                )
            )
        extra = [_tag_prompt(p) for p in extra if _sanitize_prompt_text(p.get("text", ""))]
        if is_mock_run(settings):
            hits.extend(generate_mock_discovery_hits(state.query, extra, "widen"))
        else:
            await _discover_live(
                search_router,
                extra,
                scope=scope,
                fallback_query=state.query,
                settings=settings,
                tracker=tracker,
                allow_mutations=False,
                initial_hits=hits,
                pass_label="widen pass",
            )

    # Extra real web search when still thin — never fabricate companies in export
    if settings and not is_mock_run(settings) and not get_budget().discovery_should_stop():
        uniq_final = _unique_company_count(hits)
        supplemental_floor = force_widen_below
        if uniq_final < supplemental_floor:
            terms = (
                scope.get("industry_terms")
                if isinstance(scope.get("industry_terms"), list)
                else []
            )
            eco = (
                scope.get("ecosystem_functions")
                if isinstance(scope.get("ecosystem_functions"), list)
                else []
            )
            extra_vol = build_volume_prompts(
                market,
                geo,
                industry_terms=terms,
                ecosystem_functions=eco,
                max_prompts=16,
            )
            extra_vol.extend(
                build_seed_expansion_prompts(
                    scope, market, geo, max_prompts=seed_expansion_cap
                )
            )
            extra_vol = [
                _tag_prompt(p)
                for p in extra_vol
                if _sanitize_prompt_text(p.get("text", ""))
            ]
            extra_vol = _drop_already_searched_prompts(
                extra_vol, tracker, pass_label="supplemental pre-filter"
            )
            if extra_vol:
                print(
                    f"  [discovery] Supplemental web search: {len(extra_vol)} queries "
                    f"({uniq_final} unique companies so far — seed + product terms)",
                    flush=True,
                )
                await _discover_live(
                    search_router,
                    extra_vol,
                    scope=scope,
                    fallback_query=state.query,
                    settings=settings,
                    tracker=tracker,
                    allow_mutations=False,
                    initial_hits=hits,
                    pass_label="supplemental",
                )
            elif uniq_final < supplemental_floor:
                seed_only = _drop_already_searched_prompts(
                    [
                        _tag_prompt(p)
                        for p in build_seed_expansion_prompts(
                            scope, market, geo, max_prompts=seed_expansion_cap
                        )
                        if _sanitize_prompt_text(p.get("text", ""))
                    ],
                    tracker,
                    pass_label="seed-only pre-filter",
                )
                if seed_only:
                    print(
                        f"  [discovery] Seed competitor expansion: {len(seed_only)} queries "
                        f"({uniq_final} unique so far)",
                        flush=True,
                    )
                    await _discover_live(
                        search_router,
                        seed_only,
                        scope=scope,
                        fallback_query=state.query,
                        settings=settings,
                        tracker=tracker,
                        allow_mutations=False,
                        initial_hits=hits,
                        pass_label="seed expansion",
                    )

    state.discovery_hits = [h for h in hits if h.name_raw and h.source_url]
    state.query_yield_metrics = tracker.summary()
    state.query_sector_validated = dict(tracker.sector_validated)

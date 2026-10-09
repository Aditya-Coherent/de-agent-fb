"""Phase 1 — Foundation & free search (query plan + funnel L0–L2 + search smoke test)."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from vendor_intel.clients.claude import ClaudeClient
from vendor_intel.clients.search_router import FreeSearchRouter
from vendor_intel.config import Settings, _project_root
from vendor_intel.funnel.levels import FunnelLevel, funnel_level_order, merge_funnel_into_config
from vendor_intel.live_checks import validate_live_settings
from vendor_intel.mock.fixtures import is_mock_run
from vendor_intel.stages.a_compiler import compile_query


def load_export_column_spec() -> dict[str, list[str]]:
    path = _project_root() / "config" / "export_columns.yaml"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return {k: list(v) for k, v in data.items() if isinstance(v, list)}


async def run_phase1(query: str, settings: Settings | None = None) -> dict[str, Any]:
    """
    Phase 1 exit criteria:
    - Market understanding + query compiled (2 LLM calls in live, mock template in demo)
    - Funnel L0–L2 themes (3 levels for tagging breadth)
    - Up to 12 search queries (3 funnel + up to 9 discovery, incl. competitors)
    - Free search router exercised on each discovery prompt
    """
    from vendor_intel.placeholders.load_keys import apply_env_overrides

    apply_env_overrides()
    settings = settings or Settings.load()
    warnings = validate_live_settings(settings)

    claude = ClaudeClient(settings)
    router = FreeSearchRouter(settings)

    config = compile_query(query, claude, settings)
    config = merge_funnel_into_config(config, query)
    scope_after = config.scope or {}
    llm_ok = scope_after.get("scope_source") in ("llm", "llm_partial")

    funnel_prompts = config.funnel_prompts or []
    discovery_prompts = config.prompts or []
    search_prompts = list(funnel_prompts) + [
        p for p in discovery_prompts if p.get("id") not in {"L0", "L1", "L2"}
    ]
    search_smoke_test: dict[str, Any] = {}
    manifest_extra: dict[str, Any] = {}

    if is_mock_run(settings):
        for p in search_prompts:
            search_smoke_test[p["id"]] = {
                "query": p["text"],
                "level": p.get("level", ""),
                "result_count": 0,
                "backends": [],
                "sample": [],
                "note": "Mock mode — search skipped.",
            }
    else:
        from vendor_intel.clients.duckduckgo import (
            duckduckgo_backend_name,
            network_search_blocked,
            reset_network_search_state,
        )
        from vendor_intel.clients.network_check import check_internet_dns

        reset_network_search_state()
        from vendor_intel.clients.host_reachability import print_connectivity_report
        from vendor_intel.clients.searxng import searxng_ping_any, searxng_urls

        geo = (config.scope.get("geographies") or ["global"])[0]
        from vendor_intel.pipeline.geo_limits import is_global_geography, pipeline_limits

        lim = pipeline_limits(settings, recall=False, country=str(geo))
        smoke_cap = max(0, int(lim["smoke_prompts"]))
        if is_global_geography(str(geo)) and smoke_cap > 0:
            print(
                f"  [phase1] Global smoke test: up to {smoke_cap} prompts",
                flush=True,
            )
        if smoke_cap == 0:
            print(
                "  [phase1] Search smoke test skipped (PHASE1_SMOKE_MAX_PROMPTS=0) — plan only",
                flush=True,
            )
            search_prompts = []
        elif len(search_prompts) > smoke_cap:
            search_prompts = search_prompts[:smoke_cap]
            print(
                f"  [phase1] Smoke test: first {smoke_cap} prompts only "
                f"(PHASE1_SMOKE_MAX_PROMPTS=12 for full test)",
                flush=True,
            )
        else:
            print_connectivity_report()
        market = str(config.scope.get("market") or "")
        search_topic = str(config.scope.get("search_topic") or market)
        ddg_backend = duckduckgo_backend_name()

        net_ok, net_err = check_internet_dns()
        if not net_ok:
            warnings.append(
                "Search offline: no internet/DNS on this machine "
                f"({net_err[:120]}). Fix Wi‑Fi/VPN/DNS, then re-run. "
                "Query plan was still saved."
            )
        elif settings.searxng_base_url:
            searx_ok, searx_used = await searxng_ping_any(searxng_urls(settings.searxng_base_url))
            if not searx_ok:
                warnings.append(
                    f"SearXNG not reachable at {settings.searxng_base_url} — "
                    "run: docker compose up -d  (or set SKIP_BING_HTML=false)"
                )
            elif searx_used:
                manifest_extra["searxng_active_url"] = searx_used

        results_cap = settings.results_per_query

        for p in search_prompts:
            try:
                rows = await router.search(
                    p["text"],
                    market=market,
                    geo=geo,
                    search_topic=search_topic,
                    discovery_mode=True,
                )
            except Exception as exc:
                rows = []
                search_smoke_test[p["id"]] = {
                    "query": p["text"],
                    "level": p.get("level", ""),
                    "result_count": 0,
                    "results_target": results_cap,
                    "backends": [],
                    "results": [],
                    "sample": [],
                    "error": str(exc)[:200],
                }
                continue
            result_rows = [
                {
                    "title": r.title[:120],
                    "link": r.link,
                    "snippet": (r.snippet or "")[:200],
                    "backend": r.backend,
                }
                for r in rows[:results_cap]
            ]
            search_smoke_test[p["id"]] = {
                "query": p["text"],
                "level": p.get("level", ""),
                "result_count": len(rows),
                "results_target": results_cap,
                "backends": sorted({r.backend for r in rows}),
                "results": result_rows,
                "sample": result_rows,
            }
            if len(rows) == 0:
                print(
                    f"  [search] Prompt {p['id']}: 0 results after all backends "
                    f"— {p['text'][:60]!r}",
                    flush=True,
                )
        if ddg_backend:
            manifest_extra["duckduckgo_package"] = ddg_backend
        total_hits = sum(
            (search_smoke_test.get(pid) or {}).get("result_count", 0)
            for pid in search_smoke_test
        )
        if total_hits == 0 and not net_ok:
            manifest_extra["search_status"] = "offline_dns"
        elif total_hits == 0 and network_search_blocked():
            manifest_extra["search_status"] = "duckduckgo_unreachable"
        elif total_hits == 0:
            manifest_extra["search_status"] = "no_results"
        elif total_hits < 30:
            warnings.append(
                f"Low search volume in Phase 1 smoke test ({total_hits} total hits). "
                "Phase 2 will run more prompts; set SKIP_BING_HTML=false or start SearXNG if search is thin."
            )
            from vendor_intel.discovery.volume_prompts import build_volume_prompts

            terms = scope_after.get("industry_terms") if isinstance(scope_after.get("industry_terms"), list) else []
            extra = build_volume_prompts(market, geo, industry_terms=terms, max_prompts=6)
            for p in extra:
                if p.get("id") in search_smoke_test:
                    continue
                try:
                    rows = await router.search(
                        p["text"],
                        market=market,
                        geo=geo,
                        search_topic=search_topic,
                        discovery_mode=True,
                    )
                except Exception:
                    rows = []
                pid = p.get("id", "VX")
                result_rows = [
                    {
                        "title": r.title[:120],
                        "link": r.link,
                        "snippet": (r.snippet or "")[:200],
                        "backend": r.backend,
                    }
                    for r in rows[:results_cap]
                ]
                search_smoke_test[pid] = {
                    "query": p["text"],
                    "level": "volume_supplement",
                    "result_count": len(rows),
                    "results_target": results_cap,
                    "backends": sorted({r.backend for r in rows}),
                    "results": result_rows,
                    "sample": result_rows,
                    "note": "Auto supplement — low Phase 1 hit count",
                }
                total_hits += len(rows)
            manifest_extra["volume_supplement_searches"] = len(extra)

    from vendor_intel.clients.api_health import check_all_apis

    api_status = await check_all_apis(settings, llm_responded_ok=llm_ok)

    export_spec = load_export_column_spec()
    manifest = {
        "phase": 1,
        "query": query,
        "mock_mode": is_mock_run(settings),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "llm_provider": settings.llm_provider,
        "search_primary": settings.search_primary,
        "search_backup": settings.search_backup,
        "funnel_levels": [lv.value for lv in funnel_level_order()] + [FunnelLevel.L3.value],
        "scope": config.scope,
        "scope_source": (config.scope or {}).get("scope_source", "unknown"),
        "industry_vertical": (config.scope or {}).get("industry_vertical", ""),
        "results_per_query": settings.results_per_query,
        "api_status": api_status,
        "funnel_prompts": funnel_prompts,
        "discovery_prompts": discovery_prompts,
        "discovery_prompt_count": len(discovery_prompts),
        "search_prompt_count": len(search_prompts),
        "search_smoke_test": search_smoke_test,
        "export_column_spec": export_spec,
        "warnings": warnings,
        **manifest_extra,
    }

    out_dir = _project_root() / "output" / "phase1"
    out_dir.mkdir(parents=True, exist_ok=True)
    scope = config.scope or {}
    slug_base = f"{scope.get('market', query)}_{(scope.get('geographies') or [''])[0]}"
    slug = re.sub(r"[^a-z0-9]+", "_", slug_base.lower())[:56].strip("_") or "run"
    plan_path = out_dir / f"phase1_plan_{slug}.json"
    plan_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest["plan_path"] = str(plan_path.resolve())

    return manifest


def run_phase1_sync(query: str, settings: Settings | None = None) -> dict[str, Any]:
    return asyncio.run(run_phase1(query, settings))


def print_phase1_summary(manifest: dict[str, Any]) -> None:
    mode = "MOCK" if manifest.get("mock_mode") else "LIVE"
    print(f"\n=== Phase 1 complete ({mode}) ===")
    print(f"  Plan file: {manifest.get('plan_path')}")
    print(f"  Scope source: {manifest.get('scope_source', 'unknown')}")
    scope = manifest.get("scope") or {}
    print(
        f"  Market: {scope.get('market', '?')} "
        f"| Industry: {scope.get('industry_vertical') or manifest.get('industry_vertical') or '—'} "
        f"| Search topic: {scope.get('search_topic', '?')} "
        f"| Geo: {(scope.get('geographies') or ['?'])[0]}"
    )
    if scope.get("market_definition"):
        print(f"  Definition: {str(scope['market_definition'])[:160]}")
    from vendor_intel.funnel.market_understanding import market_map_summary

    chain = market_map_summary(scope)
    if chain:
        print(f"  Value chain: {chain[:200]}")
    print(f"  Results per prompt (target): {manifest.get('results_per_query', 15)}")
    print("  API status:")
    for name, info in (manifest.get("api_status") or {}).items():
        print(f"    {name}: {info.get('status', '?')} — {info.get('detail', '')[:60]}")
    print(
        f"  Funnel levels (L0–L2): {len(manifest.get('funnel_prompts') or [])} "
        f"| Discovery (incl. competitors): {manifest.get('discovery_prompt_count', 0)} "
        f"| Total searches: {manifest.get('search_prompt_count', 0)}"
    )
    for fp in manifest.get("funnel_prompts") or []:
        print(f"    [funnel {fp.get('id')}] {fp.get('text', '')[:68]}")
    print("  Discovery prompts (each is a different search):")
    for p in manifest.get("discovery_prompts") or []:
        pid = p.get("id", "?")
        if pid in {"L0", "L1", "L2"}:
            continue
        print(f"    [{pid}] {p.get('text', '')[:68]}")
    status = manifest.get("search_status")
    if status in ("offline_dns", "duckduckgo_unreachable"):
        print(f"  Search: OFFLINE ({status}) — fix network/DNS, then re-run.")
    print("  Search smoke test:")
    for pid, info in (manifest.get("search_smoke_test") or {}).items():
        if manifest.get("mock_mode"):
            print(f"    {pid}: skipped (mock)")
        else:
            target = info.get("results_target", 15)
            print(
                f"    {pid}: {info.get('result_count', 0)}/{target} results "
                f"via {info.get('backends', [])}"
            )
    print(f"  Export spec: {len(manifest.get('export_column_spec') or {})} CSV schemas defined")
    for w in manifest.get("warnings") or []:
        print(f"  Warning: {w}")
    print()

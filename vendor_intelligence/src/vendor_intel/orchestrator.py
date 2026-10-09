from __future__ import annotations

import asyncio

from vendor_intel.attribution.builder import apply_attribution
from vendor_intel.classification.company_type import classify_entity, filter_entities_by_intent
from vendor_intel.clients.claude import ClaudeClient
from vendor_intel.clients.search_router import FreeSearchRouter
from vendor_intel.config import Settings
from vendor_intel.funnel.levels import merge_funnel_into_config
from vendor_intel.live_checks import validate_live_settings
from vendor_intel.mock.fixtures import is_mock_run
from vendor_intel.models import PipelineResult, RunState
from vendor_intel.stages.a_compiler import compile_query
from vendor_intel.stages.b_discovery import run_discovery
from vendor_intel.stages.c_entity_graph import run_entity_graph
from vendor_intel.stages.d_validation import run_validation
from vendor_intel.stages.f_brand_classifier import run_brand_classifier
from vendor_intel.stages.g_output import apply_listing_and_select
from vendor_intel.stages.i_quality import run_quality_gate


async def run_pipeline(query: str, settings: Settings | None = None) -> PipelineResult:
    from vendor_intel.placeholders.load_keys import apply_env_overrides

    apply_env_overrides()
    settings = settings or Settings.load()
    validate_live_settings(settings)

    claude = ClaudeClient(settings)
    search_router = FreeSearchRouter(settings)

    state = RunState(query=query)
    config = compile_query(query, claude, settings)
    config = merge_funnel_into_config(config, query)
    state.config = config

    await run_discovery(state, config, claude, settings, search_router=search_router)
    await run_entity_graph(state, config, settings)
    await run_validation(state, config, settings, search_router=search_router)

    for entity in state.entities:
        entity.company_type = classify_entity(entity)
    state.entities = filter_entities_by_intent(state.entities, config)
    apply_attribution(state.entities, config)

    await run_brand_classifier(state, config, claude, settings)

    result = apply_listing_and_select(state, config, settings)
    passed, issues = run_quality_gate(result, settings)
    if not passed:
        result.warnings.extend(issues)

    result.audit_manifest.setdefault("apis_used", _api_manifest(settings))
    result.audit_manifest["funnel_levels"] = ["L0", "L1", "L2", "L3"]
    result.audit_manifest["search_primary"] = settings.search_primary

    if settings.csv_output_enabled:
        from pathlib import Path

        from vendor_intel.export_csv import export_pipeline_csv

        base = Path(settings.csv_output_dir)
        if not base.is_absolute():
            base = Path(__file__).resolve().parents[2] / base
        result.csv_paths = export_pipeline_csv(result, base_dir=base)
        result.audit_manifest["csv_paths"] = result.csv_paths

    return result


def _api_manifest(settings: Settings) -> dict:
    from vendor_intel.clients.duckduckgo import duckduckgo_available
    from vendor_intel.placeholders import llm, web_fetch, wikidata

    def ok(key: str) -> bool:
        return bool(key) and not str(key).startswith("YOUR_")

    return {
        "mock_mode": settings.mock_mode or settings.use_mock_data,
        "llm_provider": settings.llm_provider,
        "llm": llm.is_configured(),
        "anthropic": ok(llm.ANTHROPIC_API_KEY),
        "search_primary": settings.search_primary,
        "duckduckgo": duckduckgo_available(),
        "searxng_url": settings.searxng_base_url,
        "google_alerts_enabled": settings.google_alerts_enabled,
        "wikidata": wikidata.is_enabled(),
        "web_fetch": web_fetch.WEB_FETCH_ENABLED,
        "backend_scraping": web_fetch.WEB_FETCH_ENABLED,
    }


def run_pipeline_sync(query: str, settings: Settings | None = None) -> PipelineResult:
    return asyncio.run(run_pipeline(query, settings))

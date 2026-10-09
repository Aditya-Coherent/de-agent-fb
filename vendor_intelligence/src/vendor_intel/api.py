from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from vendor_intel.config import Settings
from vendor_intel.orchestrator import run_pipeline

app = FastAPI(title="Vendor Intelligence API", version="0.3.0")


class RunRequest(BaseModel):
    query: str = Field(..., min_length=3)
    mock_mode: bool | None = None
    csv_output_enabled: bool | None = None


class RunResponse(BaseModel):
    run_id: str
    query: str
    interpretation_summary: str = ""
    company_list: list[dict]
    parent_group_list: list[dict]
    suppressed_brands: list[dict]
    counts: dict
    audit_manifest: dict
    warnings: list[str]
    mock_mode: bool = False
    csv_paths: dict[str, str] = Field(default_factory=dict)


def _key_configured(key: str) -> bool:
    return bool(key) and not str(key).startswith("YOUR_")


@app.get("/health")
async def health():
    from vendor_intel.placeholders.load_keys import apply_env_overrides

    apply_env_overrides()
    from vendor_intel.clients.duckduckgo import duckduckgo_available
    from vendor_intel.placeholders import llm, web_fetch, wikidata

    s = Settings.load()
    return {
        "status": "ok",
        "use_mock_data": s.use_mock_data,
        "llm_provider": s.llm_provider,
        "search_primary": s.search_primary,
        "services": {
            "llm": llm.is_configured(),
            "anthropic": _key_configured(llm.ANTHROPIC_API_KEY),
            "duckduckgo": duckduckgo_available(),
            "searxng": bool(s.searxng_base_url),
            "google_alerts": s.google_alerts_enabled,
            "backend_scraping": web_fetch.WEB_FETCH_ENABLED,
            "wikidata": wikidata.is_enabled(),
        },
    }


@app.post("/v1/runs", response_model=RunResponse)
async def create_run(body: RunRequest):
    settings = Settings.load()
    if body.mock_mode is not None:
        settings = settings.model_copy(update={"mock_mode": body.mock_mode})
    if body.csv_output_enabled is not None:
        settings = settings.model_copy(update={"csv_output_enabled": body.csv_output_enabled})
    try:
        result = await run_pipeline(body.query, settings)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return RunResponse(
        run_id=result.run_id,
        query=result.query,
        interpretation_summary=result.interpretation_summary,
        company_list=[c.model_dump() for c in result.company_list],
        parent_group_list=[p.model_dump() for p in result.parent_group_list],
        suppressed_brands=[s.model_dump() for s in result.suppressed_brands],
        counts=result.counts,
        audit_manifest=result.audit_manifest,
        warnings=result.warnings,
        mock_mode=result.mock_mode,
        csv_paths=result.csv_paths,
    )

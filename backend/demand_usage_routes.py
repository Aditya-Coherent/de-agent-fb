"""FastAPI routes for the Demand-Side (Usage-Based) three-phase pipeline.

Thin adapter over `me_engine.demand_usage.*` (unchanged pipeline logic lives
there) -- mirrors the `/api/generate` SSE pattern already in `main.py` for
the long-running Phase 3 (sizing) step; Phases 1/2 are a handful of LLM
calls each, so they're plain request/response, not streamed.

Included into `main.py` via `app.include_router(...)` -- no Desk Research or
Supply-Side route/logic is touched by this file.
"""
from __future__ import annotations

import json
import math
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from me_engine.demand_usage import checkpoints, setup, taxonomy
from me_engine.demand_usage.buildup_writer import write_buildup_json, write_buildup_workbook
from me_engine.demand_usage.graph import build_sizing_graph, sizing_init_state
from me_engine.demand_usage.quality import compute_provenance_quality_flags
from me_engine.generation.config import GenConfig
from me_engine.generation.llm import GenLLM

ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / "output" / ".gen_cache.db"

router = APIRouter(prefix="/api/demand-usage", tags=["demand-usage"])

# In-memory run store, same pattern as backend/main.py's `_RUNS` -- one
# process, single user; a server restart loses in-flight runs, but the
# on-disk checkpoint files (via checkpoints.py) are unaffected, so a CLI
# `approve-*` call can still pick up where the API left off.
_DU_RUNS: dict[str, dict[str, Any]] = {}


def _json_safe(value):
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


class SetupApproveBody(BaseModel):
    market_name: str
    scope: str
    geography: list[str]
    base_year: int
    market_unit: str
    physical_unit: str
    grounded: bool = False


class TaxonomyApproveBody(BaseModel):
    market_name: str
    usage_buckets: list[dict[str, Any]]
    drivers: dict[str, Any]
    excluded_usage_buckets: list[dict[str, Any]] = []
    missing_usage_flags: list[dict[str, Any]] = []
    coverage_pct: float | None = None
    overlap_flags: list[str] = []


# --------------------------------------------------------------------------- #
# Phase 1 -- Market Setup
# --------------------------------------------------------------------------- #
@router.get("/setup/generate")
def generate_setup(market: str):
    cfg = GenConfig.load(ROOT)
    if not cfg.is_online:
        raise HTTPException(400, "No LLM API key found in .env")
    draft = setup.run_setup_phase(market, cfg, cache_path=CACHE_PATH)
    run_id = str(uuid.uuid4())
    _DU_RUNS[run_id] = {"market": market, "phase": "setup", "setup_draft": draft}
    return _json_safe({"run_id": run_id, "draft": draft})


@router.post("/setup/{run_id}/approve")
def approve_setup_route(run_id: str, body: SetupApproveBody):
    run = _DU_RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "Unknown run_id -- generate a setup draft first")
    try:
        approved = setup.approve_setup(body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    run["setup_approved"] = approved
    run["phase"] = "setup_approved"
    return _json_safe(approved)


# --------------------------------------------------------------------------- #
# Phase 2 -- Usage/Driver Taxonomy
# --------------------------------------------------------------------------- #
@router.get("/taxonomy/generate")
def generate_taxonomy(run_id: str):
    run = _DU_RUNS.get(run_id)
    if not run or "setup_approved" not in run:
        raise HTTPException(400, "Approve the market setup for this run_id first")
    cfg = GenConfig.load(ROOT)
    if not cfg.is_online:
        raise HTTPException(400, "No LLM API key found in .env")
    draft = taxonomy.run_taxonomy_phase(run["setup_approved"], cfg, cache_path=CACHE_PATH)
    run["taxonomy_draft"] = draft
    run["phase"] = "taxonomy"
    return _json_safe({"run_id": run_id, "draft": draft})


@router.post("/taxonomy/{run_id}/approve")
def approve_taxonomy_route(run_id: str, body: TaxonomyApproveBody):
    run = _DU_RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "Unknown run_id")
    try:
        approved = taxonomy.approve_taxonomy(body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    run["taxonomy_approved"] = approved
    run["phase"] = "taxonomy_approved"
    return _json_safe(approved)


# --------------------------------------------------------------------------- #
# Phase 3 -- Sizing (SSE, live log -- same shape as /api/generate)
# --------------------------------------------------------------------------- #
def _run_sizing_stream(run_id: str):
    try:
        yield from _run_sizing_stream_inner(run_id)
    except Exception as exc:  # noqa: BLE001
        yield {"event": "error", "data": json.dumps({"message": f"Sizing crashed: {exc}"})}


def _run_sizing_stream_inner(run_id: str):
    run = _DU_RUNS.get(run_id)
    if not run or "setup_approved" not in run or "taxonomy_approved" not in run:
        yield {"event": "error", "data": json.dumps({"message": "Approve setup and taxonomy for this run_id first"})}
        return

    cfg = GenConfig.load(ROOT)
    if not cfg.is_online:
        yield {"event": "error", "data": json.dumps({"message": "No LLM API key found in .env"})}
        return

    approved_setup = run["setup_approved"]
    approved_taxonomy = run["taxonomy_approved"]
    llm = GenLLM(cfg, cache_path=CACHE_PATH)
    graph = build_sizing_graph()
    init = sizing_init_state(approved_setup, approved_taxonomy, cfg, llm)

    last_len = 0
    final_state: dict[str, Any] = {}
    for step in graph.stream(init, config={"recursion_limit": 20}, stream_mode="values"):
        final_state = step
        log = step.get("log", []) or []
        for line in log[last_len:]:
            yield {"event": "log", "data": json.dumps({"line": line})}
        last_len = len(log)

    flags = compute_provenance_quality_flags(final_state)
    final_state["quality_flags"] = flags

    prefix = checkpoints.sizing_output_prefix(approved_setup["market_name"])
    json_path = write_buildup_json(final_state, prefix.with_suffix(".json"))
    xlsx_path = write_buildup_workbook(final_state, prefix.with_suffix(".xlsx"))

    run["sizing_state"] = final_state
    run["json_path"] = json_path
    run["xlsx_path"] = xlsx_path
    run["phase"] = "sized"

    summary = {
        "run_id": run_id,
        "market": approved_setup["market_name"],
        "total_market_value_usd_mn": final_state.get("total_market_value"),
        "related_demand_value_usd_mn": final_state.get("related_demand_value"),
        "validation": final_state.get("validation"),
        "quality_flags": flags,
    }
    yield {"event": "done", "data": json.dumps(_json_safe(summary))}


@router.get("/size/generate")
def generate_size(run_id: str):
    # GET (not POST) so the browser's native EventSource can consume this
    # directly -- EventSource only supports GET (same reasoning as /api/generate).
    return EventSourceResponse(_run_sizing_stream(run_id))


# --------------------------------------------------------------------------- #
# Downloads
# --------------------------------------------------------------------------- #
@router.get("/download/{run_id}/{fmt}")
def download(run_id: str, fmt: str):
    run = _DU_RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "Unknown run_id")
    if fmt == "json":
        path = run.get("json_path")
        media = "application/json"
    elif fmt == "xlsx":
        path = run.get("xlsx_path")
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        raise HTTPException(400, "fmt must be 'json' or 'xlsx'")
    if not path or not Path(path).exists():
        raise HTTPException(404, "Artifact not found -- run the sizing phase first")
    return FileResponse(path, filename=Path(path).name, media_type=media)

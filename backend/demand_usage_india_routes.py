"""FastAPI routes for the India-only Demand-Side (Usage-Based) three-phase
pipeline.

Thin adapter over `me_engine.demand_usage_india.*` (unchanged pipeline logic
lives there) -- mirrors `backend/demand_usage_routes.py`'s own SSE pattern
and "isolated router, own in-memory run store" precedent 1:1. Phase 2 here
bundles Major Use Case + India Actual Use Case + Demand Driver into ONE
draft/approval (per the user's confirmed decision), matching Phase 2's
existing bucket+driver bundling.

Included into `main.py` via `app.include_router(...)` -- no Desk Research,
Supply-Side, or non-India Demand-Usage route/logic is touched by this file.
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

from me_engine.demandUseCaseCountry_approach2B import run_lock
from me_engine.demandUseCaseCountry_approach2B.correctness_gate import run_pre_write_gate
from me_engine.demandUseCaseCountry_approach2B.graph import build_sizing_graph, sizing_init_state
from me_engine.demandUseCaseCountry_approach2B.progress import NODE_NAMES, ProgressRecorder
from me_engine.demandUseCaseCountry_approach2B.quality import compute_provenance_quality_flags
from me_engine.demand_usage_india import aggregation, checkpoints, setup, taxonomy
from me_engine.demand_usage_india.buildup_writer import write_buildup_json, write_buildup_workbook
from me_engine.generation.config import GenConfig
from me_engine.generation.llm import GenLLM

ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / "output" / ".gen_cache.db"

router = APIRouter(prefix="/api/demand-usage-india", tags=["demand-usage-india"])

# In-memory run store, own lifecycle -- same pattern as
# `demand_usage_routes.py`'s `_DU_RUNS`; a server restart loses in-flight
# runs, but the on-disk checkpoint files (via `checkpoints.py`) are
# unaffected, so a CLI `approve-*` call can still pick up where the API left off.
_DUI_RUNS: dict[str, dict[str, Any]] = {}

# The market-level sizing lock (`me_engine.demandUseCaseCountry_approach2B.run_lock`) is keyed
# purely by market NAME -- shared, on purpose, with the non-India pipeline's
# own lock file location. Suffixing the lock's own key keeps an India run
# from colliding with a concurrent non-India run (or vice versa) for the
# same market name, without touching `run_lock.py` itself.
_LOCK_SUFFIX = " (India)"


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
    # Accepted for schema symmetry with the non-India `SetupApproveBody` --
    # `setup.approve_setup` ignores/overrides this to `["India"]` regardless
    # of what's supplied here.
    geography: list[str] = ["India"]
    base_year: int
    market_unit: str
    physical_unit: str
    grounded: bool = False


class TaxonomyApproveBody(BaseModel):
    market_name: str
    major_use_cases: list[dict[str, Any]] = []
    actual_use_cases: list[dict[str, Any]]
    drivers: dict[str, Any]
    rejected_actual_use_case_candidates: list[dict[str, Any]] = []
    excluded_usage_buckets: list[dict[str, Any]] = []
    missing_usage_flags: list[dict[str, Any]] = []
    coverage_pct: float | None = None
    overlap_flags: list[str] = []
    scope_validation_flags: dict[str, Any] = {}
    scope_taxonomy_aggregate: dict[str, Any] = {}


# --------------------------------------------------------------------------- #
# Phase 1 -- India-locked Market Setup
# --------------------------------------------------------------------------- #
@router.get("/setup/generate")
def generate_setup(market: str):
    cfg = GenConfig.load(ROOT)
    if not cfg.is_online:
        raise HTTPException(400, "No LLM API key found in .env")
    draft = setup.run_setup_phase(market, cfg, cache_path=CACHE_PATH)
    run_id = str(uuid.uuid4())
    _DUI_RUNS[run_id] = {"market": market, "phase": "setup", "setup_draft": draft}
    return _json_safe({"run_id": run_id, "draft": draft})


@router.post("/setup/{run_id}/approve")
def approve_setup_route(run_id: str, body: SetupApproveBody):
    run = _DUI_RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "Unknown run_id -- generate a setup draft first")
    try:
        # `setup.approve_setup` itself forces geography to ["India"] --
        # whatever `body.geography` carries is rejected/ignored, never
        # trusted as-is (see that function's own docstring).
        approved = setup.approve_setup(body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    run["setup_approved"] = approved
    run["phase"] = "setup_approved"
    return _json_safe(approved)


# --------------------------------------------------------------------------- #
# Phase 2 -- Major Use Case + India Actual Use Case + Demand Driver Taxonomy
# --------------------------------------------------------------------------- #
@router.get("/taxonomy/generate")
def generate_taxonomy(run_id: str):
    run = _DUI_RUNS.get(run_id)
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
    run = _DUI_RUNS.get(run_id)
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
# Phase 3 -- Sizing (SSE, live log -- same shape as /api/demand-usage/size/generate)
# --------------------------------------------------------------------------- #
def _run_sizing_stream(run_id: str):
    try:
        yield from _run_sizing_stream_inner(run_id)
    except Exception as exc:  # noqa: BLE001
        yield {"event": "error", "data": json.dumps({"message": f"Sizing crashed: {exc}"})}


def _run_sizing_stream_inner(run_id: str):
    run = _DUI_RUNS.get(run_id)
    if not run or "setup_approved" not in run or "taxonomy_approved" not in run:
        yield {"event": "error", "data": json.dumps({"message": "Approve setup and taxonomy for this run_id first"})}
        return

    cfg = GenConfig.load(ROOT)
    if not cfg.is_online:
        yield {"event": "error", "data": json.dumps({"message": "No LLM API key found in .env"})}
        return

    approved_setup = run["setup_approved"]
    approved_taxonomy = run["taxonomy_approved"]
    market_name = approved_setup["market_name"]

    # Same cross-process market-level sizing lock the non-India pipeline
    # uses (see `run_lock.py`) -- keyed with a distinct suffix so an India
    # run never collides with a concurrent non-India run for the same
    # market name (or vice versa).
    try:
        lock = run_lock.acquire_size_lock(market_name + _LOCK_SUFFIX, source="backend", run_id=run_id)
    except run_lock.SizingLockError as exc:
        yield {"event": "error", "data": json.dumps({"message": str(exc)})}
        return

    llm = GenLLM(cfg, cache_path=CACHE_PATH)
    graph = build_sizing_graph()
    init = sizing_init_state(approved_setup, approved_taxonomy, cfg, llm)
    progress = ProgressRecorder(market_name, run_id=run_id, source="backend")
    init["progress"] = progress

    try:
        last_len = 0
        final_state: dict[str, Any] = {}
        for i, step in enumerate(graph.stream(init, config={"recursion_limit": 20}, stream_mode="values")):
            final_state = step
            if i > 0:
                node_idx = i - 1
                node_name = NODE_NAMES[node_idx] if node_idx < len(NODE_NAMES) else f"node_{node_idx}"
                progress.node_done(node_name)
            log = step.get("log", []) or []
            for line in log[last_len:]:
                yield {"event": "log", "data": json.dumps({"line": line})}
            last_len = len(log)

        # Never silently accept an incomplete/broken final state -- same
        # hard-vs-soft gate the non-India pipeline uses.
        hard_failures, gate_soft_flags = run_pre_write_gate(final_state)
        if not final_state.get("validation"):
            hard_failures.insert(0, "'validation' is missing from the final state.")
        if hard_failures:
            err = RuntimeError(
                "Sizing graph produced a broken/incomplete final state -- treating this as a FAILED "
                "run, not a successful sizing result: " + "; ".join(hard_failures)
            )
            progress.failed(err)
            raise err

        flags = compute_provenance_quality_flags(final_state) + gate_soft_flags

        # The one genuinely new step -- deterministic Major Use Case rollup,
        # merged onto the same final state (never replacing Phase 3's own keys).
        rollup = aggregation.rollup_major_use_cases(final_state)
        final_state.update(rollup)
        flags = flags + rollup.get("rollup_flags", [])
        final_state["quality_flags"] = flags

        prefix = checkpoints.sizing_output_prefix(market_name)
        json_path = write_buildup_json(final_state, prefix.with_suffix(".json"))
        xlsx_path = write_buildup_workbook(final_state, prefix.with_suffix(".xlsx"))

        run["sizing_state"] = final_state
        run["json_path"] = json_path
        run["xlsx_path"] = xlsx_path
        run["phase"] = "sized"
        progress.finished("completed")
    except Exception as exc:  # noqa: BLE001 -- record, then re-raise so the
        # outer `_run_sizing_stream` wrapper still emits its own "error" SSE
        # event exactly as before.
        progress.failed(exc)
        raise
    finally:
        lock.release()

    summary = {
        "run_id": run_id,
        "market": approved_setup["market_name"],
        "total_market_value_usd_mn": final_state.get("total_market_value"),
        "related_demand_value_usd_mn": final_state.get("related_demand_value"),
        "major_use_case_totals": final_state.get("major_use_case_totals"),
        "india_market_total": final_state.get("india_market_total"),
        "validation": final_state.get("validation"),
        "quality_flags": flags,
    }
    yield {"event": "done", "data": json.dumps(_json_safe(summary))}


@router.get("/size/generate")
def generate_size(run_id: str):
    # GET (not POST) so the browser's native EventSource can consume this
    # directly -- EventSource only supports GET.
    return EventSourceResponse(_run_sizing_stream(run_id))


# --------------------------------------------------------------------------- #
# Downloads
# --------------------------------------------------------------------------- #
@router.get("/download/{run_id}/{fmt}")
def download(run_id: str, fmt: str):
    run = _DUI_RUNS.get(run_id)
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

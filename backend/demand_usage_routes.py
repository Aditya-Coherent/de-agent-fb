"""FastAPI routes for the Demand-Side (Usage-Based) three-phase pipeline.

Thin adapter over `me_engine.demandUseCaseCountry_approach2B.*` (unchanged pipeline logic lives
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

from me_engine.demandUseCaseCountry_approach2B import checkpoints, run_lock, run_report, setup, taxonomy
from me_engine.demandUseCaseCountry_approach2B.buildup_writer import write_buildup_json, write_buildup_workbook
from me_engine.demandUseCaseCountry_approach2B.graph import build_sizing_graph, sizing_init_state
from me_engine.demandUseCaseCountry_approach2B.progress import NODE_NAMES, ProgressRecorder
from me_engine.demandUseCaseCountry_approach2B.quality import compute_provenance_quality_flags
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
    scope_validation_flags: dict[str, Any] = {}
    scope_taxonomy_aggregate: dict[str, Any] = {}
    # Phase 9 (DEMAND_SIDE_EVIDENCE_PLAN.md): {bucket_name: "<justification>"}
    # -- required to retain any bucket flagged out_of_scope; see
    # `taxonomy.approve_taxonomy`.
    scope_overrides: dict[str, str] = {}


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
    market_name = approved_setup["market_name"]

    # Runtime-hardening (2026-08-24): the SAME market-level sizing lock the
    # CLI uses (see `run_lock.py`) -- a backend `run_id` is per-browser-tab
    # and does NOT by itself prevent two tabs (or a tab + a concurrent CLI
    # run) from sizing the same market at once, so this is a hard,
    # cross-process, file-based check, not just an in-memory `_DU_RUNS` guard.
    try:
        lock = run_lock.acquire_size_lock(market_name, source="backend", run_id=run_id)
    except run_lock.SizingLockError as exc:
        yield {"event": "error", "data": json.dumps({"message": str(exc)})}
        return

    llm = GenLLM(cfg, cache_path=CACHE_PATH)
    graph = build_sizing_graph()

    # Additive audit/observability layer (`me_engine.audit`) -- same
    # optional, no-op-if-absent discipline as `progress` below; never blocks
    # a sizing run if the audit package can't be constructed for any reason.
    emitter = None
    try:
        from me_engine.audit.store import AuditEmitter

        emitter = AuditEmitter(run_id=run_id, approach="usage_based", market=market_name)
    except Exception:  # noqa: BLE001
        emitter = None

    init = sizing_init_state(approved_setup, approved_taxonomy, cfg, llm, emitter=emitter)
    progress = ProgressRecorder(market_name, run_id=run_id, source="backend")
    init["progress"] = progress

    try:
        last_len = 0
        final_state: dict[str, Any] = {}
        # Runtime-hardening fix (2026-08-25): `stream_mode="values"` yields
        # the UNTOUCHED initial state as item 0, then one item per node that
        # actually ran -- `zip(NODE_NAMES, graph.stream(...))` (the prior
        # version of this loop) stops at `len(NODE_NAMES)` items, silently
        # DROPPING the true last yield (the state produced by
        # `validation_node`, the actual last node) and leaving `final_state`
        # one node short, missing `state["validation"]` entirely. Enumerating
        # every yielded item (never truncating) is what guarantees
        # `final_state` always ends up as the real final state.
        for i, step in enumerate(graph.stream(init, config={"recursion_limit": 20}, stream_mode="values")):
            final_state = step
            if i > 0:
                node_idx = i - 1
                node_name = NODE_NAMES[node_idx] if node_idx < len(NODE_NAMES) else f"node_{node_idx}"
                progress.node_done(node_name)
                if emitter is not None:
                    emitter.emit(node_name, "stage_end", data={"node": node_name})
            log = step.get("log", []) or []
            for line in log[last_len:]:
                yield {"event": "log", "data": json.dumps({"line": line})}
            last_len = len(log)

        # Never silently accept an incomplete/broken final state -- same
        # hard-vs-soft gate the CLI path uses (correctness-audit addendum,
        # 2026-08-26, `CEMENT_FORENSIC_AUDIT.md`); see `correctness_gate.py`.
        from me_engine.demandUseCaseCountry_approach2B.correctness_gate import run_pre_write_gate

        hard_failures, gate_soft_flags = run_pre_write_gate(final_state)
        if not final_state.get("validation"):
            hard_failures.insert(0, "'validation' is missing from the final state.")
        if hard_failures:
            err = RuntimeError(
                "Sizing graph produced a broken/incomplete final state -- treating this as a FAILED "
                "run, not a successful sizing result: " + "; ".join(hard_failures)
            )
            progress.failed(err)
            if emitter is not None:
                emitter.emit("sizing", "stage_end", status="failed", error=str(err))
            raise err

        flags = compute_provenance_quality_flags(final_state) + gate_soft_flags
        final_state["quality_flags"] = flags

        prefix = checkpoints.sizing_output_prefix(market_name)
        json_path = write_buildup_json(final_state, prefix.with_suffix(".json"))
        xlsx_path = write_buildup_workbook(final_state, prefix.with_suffix(".xlsx"))

        run["sizing_state"] = final_state
        run["json_path"] = json_path
        run["xlsx_path"] = xlsx_path
        run["phase"] = "sized"
        progress.finished("completed")

        # Reporting addendum (2026-08-26): same consolidated, human-readable
        # SIZE_RUN_RESULT.md the CLI writes -- purely a reporting layer over
        # the same `final_state`/files already written above; no new SSE
        # event, no change to the response shape.
        run_report.write_success_report(
            final_state, market_name, json_path=json_path, xlsx_path=xlsx_path,
            worker_count=getattr(cfg, "evidence_workers", None),
        )
    except Exception as exc:  # noqa: BLE001 -- record, then re-raise so the
        # outer `_run_sizing_stream` wrapper still emits its own "error" SSE
        # event exactly as before; this only adds the progress-file record.
        progress.failed(exc)
        run_report.write_failure_report(market_name, error=str(exc))
        raise
    finally:
        lock.release()

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

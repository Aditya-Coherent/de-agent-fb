"""FastAPI backend for the DeAgent frontend (Next.js).

Wraps the existing me_engine.generation.* pipeline (unchanged) behind an HTTP
API — no generation logic lives here, this is a thin adapter so a proper
frontend can replace the Streamlit UI.

Run:  python backend/main.py
      (or: uvicorn backend.main:app --reload --port 8000, from repo root)
"""
from __future__ import annotations

import dataclasses
import json
import math
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))  # export_market_json.py / export_me_json.py live at repo root, not in src/
# This file's own directory -- needed for `from demand_usage_routes import ...` when
# launched as `uvicorn backend.main:app` (that invocation doesn't auto-add this dir
# to sys.path the way running `python backend/main.py` directly does).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from audit_routes import router as audit_router
from demand_usage_india_routes import router as demand_usage_india_router
from demand_usage_routes import router as demand_usage_router
from segmentation_demo_routes import router as segmentation_demo_router
from export_market_json import export_market_json
from export_me_json import export_me_json
from me_engine.generation.compare import compare, format_report
from me_engine.generation.config import GenConfig
from me_engine.generation.db_source import fetch_market_evidence
from me_engine.generation.dynamic_skeleton import load_skeleton_from_db
from me_engine.generation.graph import build_graph
from me_engine.generation.history import (
    append_history,
    me_workbook_history_entry,
    read_history,
    state_to_history_entry,
)
from me_engine.generation.llm import GenLLM
from me_engine.generation.me_runner import run_me_pipeline
from me_engine.generation import segment_definition as segdef
from me_engine.generation.sheet_writer import write_input_sheet
from me_engine.generation.skeleton import REGIONS, Skeleton, load_skeleton, load_skeleton_from_human_sheet

INPUTS_DIR = ROOT / "my_inputs"
OUT_DIR = ROOT / "agentGeneratedInputSheet"
ME_DIR = ROOT / "agentGeneratedMEWorkbook"
HISTORY_PATH = ROOT / "output" / "generation_history.jsonl"
CACHE_PATH = ROOT / "output" / ".gen_cache.db"

app = FastAPI(title="DeAgent API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)
# Demand-Side (Usage-Based) three-phase pipeline -- isolated router, own
# in-memory run store, no Desk Research/Supply-Side route or logic touched.
app.include_router(demand_usage_router)
# India-only Demand-Side (Usage-Based) variant -- separate isolated router,
# own in-memory run store; imports from `demand_usage_routes`'s own pipeline
# package but never modifies it.
app.include_router(demand_usage_india_router)
# Additive audit/observability layer -- read-only over on-disk checkpoints/
# progress/lock/audit_log.jsonl files; no pipeline logic touched.
app.include_router(audit_router)
# Standalone Segmentation Demo tab -- single generate-and-render endpoint,
# no run/checkpoint lifecycle at all; touches no other route/logic.
app.include_router(segmentation_demo_router)

# In-memory run store — one process, single user, mirrors what Streamlit's
# st.session_state did. Not persisted; a server restart loses in-flight runs
# (the generated files and history log on disk are unaffected).
_RUNS: dict[str, dict[str, Any]] = {}

# Desk Research Phase 0 gate (segment_definition.py) -- approved base-year
# segment definitions, keyed by market name (GET /api/generate has no run_id
# yet at call time; it mints one only after the graph finishes). Same
# in-memory, single-process discipline as `_RUNS`.
_SEGMENT_DEFS: dict[str, dict[str, Any]] = {}


def _json_safe(value):
    """Replace NaN/Infinity (not valid JSON) with None, recursively."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _find_human_sheet(market_name: str) -> Path | None:
    """A human-authored INPUT SHEET (my_inputs/) — used only to (a) borrow a
    skeleton's labels in "Reference sheet" mode, or (b) as the ground truth
    for the opt-in Baseline-compare tab (Input Sheet vs Input Sheet). Never
    used for ME workbook generation — that's Input Sheet -> ME workbook,
    nothing else; no reference lookup/comparison in that flow (which was a
    dev-only accuracy-testing aid, still available via
    `python run_pipeline.py --reference ...` for manual testing)."""
    slug = market_name.lower().replace("market", "").replace("global", "").strip()
    key = "".join(ch for ch in slug if ch.isalnum())
    for p in INPUTS_DIR.glob("*.xlsx"):
        stem = "".join(ch for ch in p.stem.lower() if ch.isalnum())
        if key and key in stem:
            return p
    return None


class RunIdBody(BaseModel):
    run_id: str


class SegmentDefBody(BaseModel):
    name: str
    region_shares: dict[str, float]


class DimensionDefBody(BaseModel):
    dimension_name: str
    segments: list[SegmentDefBody]


class SegmentDefinitionSubmitBody(BaseModel):
    market_name: str
    base_year: int
    dimensions: list[DimensionDefBody]


def _load_skeleton_for_market(market: str, source: str, skeleton_choice: str | None, cfg: GenConfig):
    """Returns (skeleton, human_sheet, skeleton_source, error_message) --
    error_message is None on success. Shared by the SSE generation stream
    (`_run_generation_stream_inner`) and the read-only segment-definition
    preview endpoint below, so both use the exact same DB/reference lookup,
    never a second, divergent one."""
    if source == "db":
        if not cfg.neon_url:
            return None, None, None, "NEON_DATABASE_URL not set in .env"
        skeleton, _ev = load_skeleton_from_db(market, cfg)
        if skeleton is None:
            return None, None, None, f"No DB segmentation found for '{market}'"
        return skeleton, _find_human_sheet(market), "Neon DB", None
    else:
        if not skeleton_choice:
            return None, None, None, "No reference sheet chosen"
        human_sheet = INPUTS_DIR / skeleton_choice
        skeleton = load_skeleton_from_human_sheet(human_sheet, market) or load_skeleton(human_sheet, market)
        return skeleton, human_sheet, "Reference sheet", None


# --------------------------------------------------------------------------- #
# Desk Research Phase 0 -- Base-Year Segment Definition (the gate before
# GET /api/generate is allowed to run at all). No LLM involved: validation
# and the segment_value calculation are pure, deterministic Python -- see
# `me_engine.generation.segment_definition`.
# --------------------------------------------------------------------------- #
@app.get("/api/segment-definition/regions")
def get_segment_definition_regions():
    return {"regions": list(segdef.FIXED_REGIONS)}


@app.get("/api/segment-definition/default-base-year")
def get_segment_definition_default_base_year():
    return {"base_year": segdef.default_base_year()}


@app.get("/api/segment-definition/preview")
def preview_segment_definition(market: str, source: str, skeleton_choice: str | None = None):
    """Read-only: pre-fills the frontend's segment-definition step -- labels
    only, no numbers, no LLM call.

    For `source == "db"`, this parses the DB's own "depth|label"
    `segmentation_tree` directly (`segdef.dimensions_from_segmentation_tree`)
    rather than going through the heavier LLM/regex Skeleton builder
    (`load_skeleton_from_db`) that the full generation run uses -- simpler,
    deterministic, and immune to that builder's occasional header-leaked-
    into-its-own-segments bug. Falls back to the Skeleton-derived nesting
    (same defensive same-label filter applied) only if the DB has no usable
    segmentation tree for this market.

    A market genuinely absent from the DB is not an error here -- the whole
    point of this gate is to let the user define segments by hand for a
    market the DB has never seen, so `source == "db"` returns an empty
    `dimensions: []` (never a 400) when neither the direct parse nor the
    Skeleton-builder fallback finds anything. `source == "reference"` still
    404/400s on a genuinely missing/unselected reference file -- that's a
    real usage error, not "no DB data"."""
    cfg = GenConfig.load(ROOT)
    dimensions: list[dict[str, Any]] = []
    if source == "db" and cfg.neon_url:
        try:
            ev = fetch_market_evidence(cfg.neon_url, market)
        except Exception:  # noqa: BLE001
            ev = None
        if ev is not None and ev.segmentation_tree:
            dimensions = segdef.dimensions_from_segmentation_tree(ev.segmentation_tree)

    if not dimensions:
        skeleton, _human_sheet, _src, err = _load_skeleton_for_market(market, source, skeleton_choice, cfg)
        if err:
            if source == "db":
                # Nothing in the DB for this market -- fine, hand the user
                # an empty tree to build from scratch rather than blocking
                # them (see docstring above).
                return {
                    "regions": list(segdef.FIXED_REGIONS),
                    "base_year": segdef.default_base_year(),
                    "dimensions": [],
                }
            raise HTTPException(400, err)
        candidate_dimensions = [
            {
                "dimension_name": dim,
                "segments": [
                    {"name": seg, "region_shares": {}}
                    for seg in tree.get("__root__", [])
                    # Same defensive filter as `dimensions_from_segmentation_tree`
                    # -- the dimension's own label must never appear as one of
                    # its own segments.
                    if segdef.normalize_label_for_comparison(seg) != segdef.normalize_label_for_comparison(dim)
                ],
            }
            for dim, tree in skeleton.nesting.items()
        ]
        if source == "db":
            # The nearest-neighbor DB fallback (`load_skeleton_from_db`'s
            # semantic search) can land on a completely unrelated report --
            # if what it found is actually THAT report's own geography tree
            # (region/country names), it must never be surfaced as if it
            # were product/application segmentation the user could
            # unknowingly approve.
            dimensions = [
                d for d in candidate_dimensions
                if not segdef.looks_like_geography_dimension(d["dimension_name"], [s["name"] for s in d["segments"]])
            ]
        else:
            dimensions = candidate_dimensions

    return {"regions": list(segdef.FIXED_REGIONS), "base_year": segdef.default_base_year(), "dimensions": dimensions}


@app.post("/api/segment-definition/submit")
def submit_segment_definition(body: SegmentDefinitionSubmitBody):
    try:
        approved = segdef.validate_segment_definition(body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    approved["approved"] = True
    _SEGMENT_DEFS[approved["market_name"]] = approved
    return _json_safe(approved)


@app.get("/api/segment-definition/{market}")
def get_segment_definition(market: str):
    approved = _SEGMENT_DEFS.get(market)
    if not approved:
        raise HTTPException(404, "No approved segment definition for this market")
    return _json_safe(approved)


# --------------------------------------------------------------------------- #
# Config / metadata
# --------------------------------------------------------------------------- #
@app.get("/api/config")
def get_config():
    cfg = GenConfig.load(ROOT)
    return {
        "model": cfg.model,
        "base_url": cfg.base_url,
        "enable_web": cfg.enable_web,
        "min_loop_iters": cfg.min_loop_iters,
        "max_loop_iters": cfg.max_loop_iters,
        "reward_threshold": cfg.reward_threshold,
        "per_region_shares": cfg.per_region_shares,
        "db_connected": bool(cfg.neon_url),
        "is_online": cfg.is_online,
    }


@app.get("/api/skeleton-files")
def get_skeleton_files():
    if not INPUTS_DIR.exists():
        return []
    return sorted(p.name for p in INPUTS_DIR.glob("*.xlsx"))


@app.get("/api/history")
def get_history(limit: int = 25):
    return read_history(path=HISTORY_PATH, limit=limit)


# --------------------------------------------------------------------------- #
# Generation (SSE — live log lines, then a final "done" event)
# --------------------------------------------------------------------------- #
def _run_generation_stream(market: str, source: str, skeleton_choice: str | None):
    try:
        yield from _run_generation_stream_inner(market, source, skeleton_choice)
    except Exception as exc:
        yield {"event": "error", "data": json.dumps({
            "message": f"Generation crashed: {exc}"
        })}


def _run_generation_stream_inner(market: str, source: str, skeleton_choice: str | None):
    cfg = GenConfig.load(ROOT)
    if not cfg.is_online:
        yield {"event": "error", "data": json.dumps({"message": "No LLM API key found in .env"})}
        return

    # Desk Research Phase 0 gate -- re-checked here too (the HTTP route below
    # also checks before opening the SSE stream) so a stream started just as
    # the definition was removed still fails loudly instead of silently
    # falling back to LLM-invented segments/shares.
    seg_def = _SEGMENT_DEFS.get(market)
    if seg_def is None:
        yield {"event": "error", "data": json.dumps({
            "message": "Submit and approve a base-year segment definition for this market first"
        })}
        return

    if source == "db":
        yield {"event": "log", "data": json.dumps({"line": f"Looking up past research for '{market}' in our database..."})}
    else:
        yield {"event": "log", "data": json.dumps({"line": f"Reading the market structure from {skeleton_choice}..."})}
    skeleton, human_sheet, skeleton_source, err = _load_skeleton_for_market(market, source, skeleton_choice, cfg)
    if err:
        # An approved segment definition already supplies everything a
        # Skeleton needs (nesting, regions, geographies, priced_dimension --
        # see the override below) -- a failed DB/reference lookup is fatal
        # only when there's NO segment definition to fall back on. With one
        # approved, `human_sheet`/`skeleton_source` (DB-lookup metadata, used
        # only for provenance/logging) are the only things genuinely lost.
        yield {"event": "log", "data": json.dumps({
            "line": f"{err} -- proceeding with your approved segment definition only."
        })}
        nesting = segdef.to_nesting(seg_def)
        skeleton = Skeleton(
            market_name=market,
            geographies=[c for members in REGIONS.values() for c in members],
            regions=dict(REGIONS),
            nesting=nesting,
            priced_dimension=next(iter(nesting)),
        )
        human_sheet, skeleton_source = None, "User-defined segments (no DB match)"
    else:
        # The user's approved segments REPLACE whatever dimensions the DB/LLM/
        # reference-sheet skeleton builder found, and the fixed 5-region list
        # REPLACES whatever region topology it derived -- see
        # `segment_definition.py` module docstring. `sk.nesting`/`shares_node`
        # is the exact slot `agents.py`'s LLM segmentation-share call used to
        # fill; nothing calls that LLM for these dimensions anymore.
        skeleton = dataclasses.replace(
            skeleton,
            nesting=segdef.to_nesting(seg_def),
            regions=dict(REGIONS),
            geographies=[c for members in REGIONS.values() for c in members],
            priced_dimension=next(iter(segdef.to_nesting(seg_def))),
        )

    yield {"event": "log", "data": json.dumps({
        "line": f"Structure ready: {len(skeleton.geographies)} countries/regions, "
                f"{len(skeleton.nesting)} segment categories to estimate"
    })}
    yield {"event": "log", "data": json.dumps({
        "line": f"Using the approved base-year ({seg_def['base_year']}) segment definition for "
                f"{len(seg_def['dimensions'])} dimension(s) -- the AI analyst team will not re-derive "
                "these segments' shares."
    })}
    yield {"event": "log", "data": json.dumps({
        "line": "Starting the AI analyst team: gathering evidence, then estimating market size, "
                "geography, and pricing -- with a review pass after each attempt..."
    })}

    llm = GenLLM(cfg, cache_path=CACHE_PATH)
    graph = build_graph()
    init = {
        "market_name": market, "skeleton": skeleton, "config": cfg, "llm": llm, "iter": 0, "log": [],
        "segment_definition": seg_def,
    }

    last_len = 0
    final_state: dict[str, Any] = {}
    for step in graph.stream(init, config={"recursion_limit": 80}, stream_mode="values"):
        final_state = step
        log = step.get("log", []) or []
        for line in log[last_len:]:
            yield {"event": "log", "data": json.dumps({"line": line})}
        last_len = len(log)

    out_path = OUT_DIR / f"Data File - {market}.xlsx"
    write_input_sheet(final_state, out_path)

    run_id = str(uuid.uuid4())
    _RUNS[run_id] = {
        "state": final_state,
        "out_path": out_path,
        "human_sheet": human_sheet,
        "market": market,
    }
    append_history(state_to_history_entry(
        market, final_state, run_id=run_id, skeleton_source=skeleton_source, out_path=str(out_path),
    ), path=HISTORY_PATH)

    regional = final_state.get("regional") or {}
    global_anchor = final_state.get("global_anchor_2026") or 0.0
    # region_total_market_value[region] = global_anchor_2026 * regional[region].value_share
    # -- the SAME anchor `market_size_node` already computes (agents.py:203),
    # never a second, competing source of regional totals.
    region_totals = {r: global_anchor * v.get("value_share", 0.0) for r, v in regional.items()}
    segment_value = segdef.compute_segment_values(region_totals, seg_def)

    summary = {
        "run_id": run_id,
        "market": market,
        "reward": final_state.get("reward"),
        "iter": final_state.get("iter"),
        "global_anchor_2026": final_state.get("global_anchor_2026"),
        "global_cagr": final_state.get("global_cagr"),
        "regional": final_state.get("regional"),
        "shares": final_state.get("shares"),
        "artifacts": final_state.get("artifacts"),
        "has_human_sheet": human_sheet is not None,
        "quality_flags": final_state.get("quality_flags") or [],
        "region_totals_usd_mn": region_totals,
        "segment_value_usd_mn": segment_value,
    }
    yield {"event": "done", "data": json.dumps(_json_safe(summary))}


@app.get("/api/generate")
def generate(market: str, source: str, skeleton_choice: str | None = None):
    # Desk Research Phase 0 hard gate (spec: "Reject GET /api/generate if no
    # approved segment definition exists for this run/market") -- checked
    # here, BEFORE the SSE stream opens, so this is a real 400 response, not
    # an SSE "error" event a plain HTTP client would never see.
    if market not in _SEGMENT_DEFS:
        raise HTTPException(400, "Submit and approve a base-year segment definition for this market first")
    # GET (not POST) so the browser's native EventSource can consume this
    # directly — EventSource only supports GET.
    return EventSourceResponse(_run_generation_stream(market, source, skeleton_choice))


# --------------------------------------------------------------------------- #
# ME workbook (one click)
# --------------------------------------------------------------------------- #
@app.post("/api/generate-me")
def generate_me(req: RunIdBody):
    run = _RUNS.get(req.run_id)
    if not run:
        raise HTTPException(404, "Unknown run_id — generate an Input Sheet first")

    state = run["state"]
    market = run["market"]
    out_path = Path(run["out_path"])
    me_input = out_path.with_name(out_path.stem + " [pipeline].xlsx")

    try:
        write_input_sheet(state, me_input, flat=True)
        me_output = ME_DIR / f"ME - {market}.xlsx"
        # Production flow: Input Sheet -> ME workbook, nothing else. No automatic
        # reference lookup/comparison here — that was a dev-only accuracy-testing
        # aid (still available via `python run_pipeline.py --reference ...` for
        # manual testing), not something the one-click flow should silently do.
        res = run_me_pipeline(me_input, me_output, reference=None, root=ROOT)
    except Exception as exc:
        raise HTTPException(500, f"ME workbook generation crashed: {exc}") from exc

    run["me_result"] = res

    if res.output_exists:
        append_history(me_workbook_history_entry(
            req.run_id, me_output_path=str(res.output_path),
            gate1_passed=res.gate1_passed, gate3_passed=res.gate3_passed,
        ), path=HISTORY_PATH)

    return _json_safe({
        "gate1_passed": res.gate1_passed,
        "gate3_passed": res.gate3_passed,
        "output_exists": res.output_exists,
        "log_tail": res.log_tail,
    })


# --------------------------------------------------------------------------- #
# Baseline compare
# --------------------------------------------------------------------------- #
@app.post("/api/compare")
def do_compare(req: RunIdBody):
    run = _RUNS.get(req.run_id)
    if not run or not run.get("human_sheet"):
        raise HTTPException(400, "No matching human sheet for this run — nothing to compare against")
    rep = compare(Path(run["human_sheet"]), Path(run["out_path"]))
    return _json_safe({"report_text": format_report(rep), "report": rep})


# --------------------------------------------------------------------------- #
# Downloads
# --------------------------------------------------------------------------- #
@app.get("/api/download/input-sheet/{run_id}")
def download_input_sheet(run_id: str):
    run = _RUNS.get(run_id)
    if not run or not Path(run["out_path"]).exists():
        raise HTTPException(404, "Not found")
    path = Path(run["out_path"])
    return FileResponse(path, filename=path.name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/download/me-workbook/{run_id}")
def download_me_workbook(run_id: str):
    run = _RUNS.get(run_id)
    res = run.get("me_result") if run else None
    if not res or not res.output_exists:
        raise HTTPException(404, "Not found")
    path = Path(res.output_path)
    return FileResponse(path, filename=path.name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/download/input-json/{run_id}")
def download_input_json(run_id: str):
    """Export the Input Sheet's own real numbers (hierarchical, by region/
    country/segment) as JSON -- reads the already-written .xlsx, no
    regeneration (see export_market_json.py)."""
    run = _RUNS.get(run_id)
    if not run or not Path(run["out_path"]).exists():
        raise HTTPException(404, "Not found")
    path = Path(run["out_path"])
    data = export_market_json(path)
    filename = path.stem + ".json"
    return JSONResponse(content=_json_safe(data),
                         headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/download/me-json/{run_id}")
def download_me_json(run_id: str):
    """Export the ME Workbook as JSON -- geography -> band -> segment -> year
    (see export_me_json.py). Requires the one-click ME workbook to have
    already been generated for this run."""
    run = _RUNS.get(run_id)
    res = run.get("me_result") if run else None
    if not res or not res.output_exists:
        raise HTTPException(404, "Not found — generate the ME workbook first")
    path = Path(res.output_path)
    data = export_me_json(path)
    filename = path.stem + ".json"
    return JSONResponse(content=_json_safe(data),
                         headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/download/file")
def download_by_path(path: str):
    """Re-download a file the History tab points to.

    History entries survive server restarts (they're just JSONL lines), but
    the in-memory `_RUNS` cache doesn't — so a past run's files must be
    served by path, not by `run_id`. Restricted to the two output
    directories so this can't be used to fetch arbitrary files off disk.
    """
    resolved = Path(path).resolve()
    allowed = [OUT_DIR.resolve(), ME_DIR.resolve()]
    if not any(resolved.is_relative_to(d) for d in allowed):
        raise HTTPException(403, "Path not allowed")
    if not resolved.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(resolved, filename=resolved.name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)

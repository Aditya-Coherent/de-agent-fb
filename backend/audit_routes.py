"""FastAPI routes exposing the additive audit/observability layer
(`me_engine.audit`) for the usage-based demand-side pipeline.

Read-only over whatever is already on disk (checkpoints, progress file,
lock file, `audit_log.jsonl`, buildup JSON artifact) -- never recomputes or
mutates pipeline state. Every endpoint tolerates partial/missing data: an
older run with no `audit_log.jsonl` yet still returns whatever IS available
(progress/lock file), with the rest marked `"not_captured": true` rather
than fabricated.

Included into `main.py` via `app.include_router(...)`, mirroring exactly how
`demand_usage_routes.router` is included -- see that file's own module
docstring.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from me_engine.audit import store as audit_store
from me_engine.demandUseCaseCountry_approach2B import checkpoints, run_lock

router = APIRouter(prefix="/api/audit", tags=["audit"])

# Overridable root for the demand-usage output tree (mirrors the `root`
# parameter every `me_engine.demandUseCaseCountry_approach2B.checkpoints`/`me_engine.audit.store`
# function already accepts) -- tests monkeypatch this module attribute to
# point every route at a temp directory instead of the real `output/` tree.
# `None` means "use the real, on-disk default" (unchanged behavior).
ROOT: Path | None = None

# Fixed, linear stage order for the timeline endpoint -- mirrors
# `me_engine.demandUseCaseCountry_approach2B.progress.NODE_NAMES` for Phase 3, prefixed with
# the two approval-gated phases that precede it (setup, taxonomy/
# taxonomy_approval) which are not LangGraph nodes and so carry no
# stage_start/stage_end events of their own today.
TIMELINE_STAGES: tuple[str, ...] = (
    "setup",
    "taxonomy",
    "taxonomy_approval",
    "demand_quantification",
    "pricing",
    "calculate",
    "driver_magnitude_validation",
    "triangulation",
    "magnitude_cross_validation",
    "validation",
)


def _json_safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _read_progress(market: str, root: Path | None = None) -> dict[str, Any] | None:
    from me_engine.demandUseCaseCountry_approach2B.progress import progress_path

    path = progress_path(market, root)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _read_lock(market: str, root: Path | None = None) -> dict[str, Any] | None:
    return run_lock._read_lock(run_lock.lock_path(market, root))


def _buildup_json(market: str, root: Path | None = None) -> dict[str, Any] | None:
    path = checkpoints.sizing_output_prefix(market, root).with_suffix(".json")
    return checkpoints.read_json(path)


def _compute_status(progress: dict[str, Any] | None, lock: dict[str, Any] | None) -> str:
    """Cross-references `progress.py`'s progress file (read-only consumer,
    per that module's own documented relationship) with `run_lock.py`'s
    lock-liveness check -- neither module's own logic is touched here."""
    lock_alive = bool(lock) and not run_lock._lock_is_stale(lock)
    if progress is None:
        return "running" if lock_alive else "unknown"
    status = progress.get("status")
    if status == "completed":
        return "completed"
    if status == "failed":
        return "interrupted"
    # status == "running" (or missing/unrecognized) -- distinguish a genuinely
    # live process from one that died without recording failure/completion.
    return "running" if lock_alive else "interrupted"


def _run_id_for(market: str, root: Path | None, progress: dict[str, Any] | None, lock: dict[str, Any] | None) -> str | None:
    for source in (progress, lock):
        if isinstance(source, dict) and source.get("run_id"):
            return source["run_id"]
    return None


@router.get("/runs")
def list_runs(approach: str = "usage_based") -> list[dict[str, Any]]:
    """`approach` is accepted for a stable, forward-compatible signature --
    only `"usage_based"` is implemented today (see `me_engine.audit.events`'s
    module docstring); any other value returns an empty list rather than
    fabricating data for a pipeline this layer doesn't yet instrument."""
    if approach != "usage_based":
        return []

    summaries: list[dict[str, Any]] = []
    for run in audit_store.list_runs(root=ROOT):
        market = run["market"]
        progress = _read_progress(market, ROOT)
        lock = _read_lock(market, ROOT)
        run_id = run.get("run_id") or _run_id_for(market, ROOT, progress, lock)
        status = _compute_status(progress, lock)
        buildup = _buildup_json(market, ROOT)
        summaries.append({
            "market": market,
            "run_id": run_id,
            "status": status,
            "started_at": (progress or {}).get("start_time"),
            "last_update": (progress or {}).get("last_update") or run.get("last_modified"),
            "current_stage": (progress or {}).get("phase"),
            "final_market_size": (buildup or {}).get("total_market_value_usd_mn"),
            "has_audit_log": run["has_audit_log"],
        })
    return _json_safe(summaries)


@router.get("/runs/{market}/{run_id}")
def run_detail(market: str, run_id: str) -> dict[str, Any]:
    progress = _read_progress(market, ROOT)
    lock = _read_lock(market, ROOT)
    events = audit_store.read_events(market, run_id, root=ROOT)
    status = _compute_status(progress, lock)

    counts_by_event_type: dict[str, int] = {}
    counts_by_status: dict[str, int] = {}
    countries: set[str] = set()
    buckets: set[str] = set()
    for e in events:
        counts_by_event_type[e.get("event_type", "")] = counts_by_event_type.get(e.get("event_type", ""), 0) + 1
        if e.get("status"):
            counts_by_status[e["status"]] = counts_by_status.get(e["status"], 0) + 1
        if e.get("country"):
            countries.add(e["country"])
        if e.get("bucket"):
            buckets.add(e["bucket"])

    buildup = _buildup_json(market, ROOT)
    totals = None
    if buildup:
        totals = {
            "total_market_value_usd_mn": buildup.get("total_market_value_usd_mn"),
            "related_demand_value_usd_mn": buildup.get("related_demand_value_usd_mn"),
            "quality_flags": buildup.get("quality_flags"),
        }

    result: dict[str, Any] = {
        "market": market,
        "run_id": run_id,
        "status": status,
        "progress": progress if progress is not None else {"not_captured": True},
        "lock": lock if lock is not None else {"not_captured": True},
        "audit_summary": {
            "total_events": len(events),
            "counts_by_event_type": counts_by_event_type,
            "counts_by_status": counts_by_status,
            "countries_touched": sorted(countries),
            "buckets_touched": sorted(buckets),
            "not_captured": not events,
        },
        "totals": totals if totals is not None else {"not_captured": True},
    }
    return _json_safe(result)


@router.get("/runs/{market}/{run_id}/events")
def run_events(
    market: str, run_id: str,
    stage: str | None = None, event_type: str | None = None,
    country: str | None = None, bucket: str | None = None, status: str | None = None,
    limit: int = 100, offset: int = 0,
) -> dict[str, Any]:
    events = audit_store.read_events(
        market, run_id, root=ROOT, stage=stage, event_type=event_type,
        country=country, bucket=bucket, status=status,
    )
    total = len(events)
    page = events[offset: offset + limit] if limit >= 0 else events[offset:]
    return _json_safe({"total": total, "limit": limit, "offset": offset, "events": page})


@router.get("/runs/{market}/{run_id}/timeline")
def run_timeline(market: str, run_id: str) -> dict[str, Any]:
    progress = _read_progress(market, ROOT)
    events = audit_store.read_events(market, run_id, root=ROOT)
    started_stages = {e["stage"] for e in events if e.get("event_type") == "stage_start"}
    ended_stages = {e["stage"] for e in events if e.get("event_type") == "stage_end"}
    current_phase = (progress or {}).get("phase") or ""

    timeline: list[dict[str, Any]] = []
    for stage_name in TIMELINE_STAGES:
        if stage_name in ended_stages:
            status = "done"
        elif stage_name in started_stages or current_phase.startswith(stage_name):
            status = "in_progress"
        else:
            status = "pending"
        timeline.append({"stage": stage_name, "status": status})
    return _json_safe({"market": market, "run_id": run_id, "timeline": timeline})


def _find_derivation_event(events: list[dict[str, Any]], output_id: str) -> dict[str, Any] | None:
    for e in events:
        if e.get("event_type") != "derivation":
            continue
        derivation = e.get("derivation") or {}
        if derivation.get("output_id") == output_id:
            return e
    return None


def _find_source_event(events: list[dict[str, Any]], bucket: str | None, country: str | None) -> dict[str, Any] | None:
    """Best-effort match of a sourced (non-derived) input fact to whichever
    `source_attempt`/`fact_validation` event most plausibly produced it --
    matched by bucket/country only (this layer does not carry a stronger
    per-fact key today), so absence of a match is common and expected, never
    an error."""
    for e in events:
        if e.get("event_type") not in ("source_attempt", "fact_validation"):
            continue
        if bucket and e.get("bucket") != bucket:
            continue
        if country and e.get("country") not in (country, None):
            continue
        if e.get("status") in ("success", "pass"):
            return e
    return None


def _build_lineage_node(
    name: str, fact: dict[str, Any], events: list[dict[str, Any]], *, _seen: set[str],
) -> dict[str, Any]:
    output_id = fact.get("output_id")
    node: dict[str, Any] = {
        "name": name,
        "value": fact.get("value"),
        "unit": fact.get("unit"),
        "geography": fact.get("geography"),
        "verification_status": fact.get("verification_status") or fact.get("evidence_type"),
        "source": fact.get("source"),
        "source_url": fact.get("source_url"),
    }
    if output_id and output_id not in _seen and _find_derivation_event(events, output_id):
        derivation_event = _find_derivation_event(events, output_id)
        derivation = derivation_event.get("derivation") or {}
        node["derivation_method"] = derivation.get("derivation_method")
        node["formula"] = derivation.get("formula")
        children = []
        for input_name, input_fact in (derivation.get("input_facts") or {}).items():
            children.append(_build_lineage_node(
                input_name, input_fact, events, _seen=_seen | {output_id},
            ))
        node["inputs"] = children
    else:
        # No further derivation for this fact -- an observed/sourced leaf.
        # Attach the closest matching evidence event, if any (best-effort).
        source_event = _find_source_event(events, node.get("bucket"), fact.get("geography"))
        node["is_leaf"] = True
        node["evidence_event"] = source_event
    return node


@router.get("/runs/{market}/{run_id}/lineage/{output_id}")
def run_lineage(market: str, run_id: str, output_id: str) -> dict[str, Any]:
    events = audit_store.read_events(market, run_id, root=ROOT)
    root_event = _find_derivation_event(events, output_id)
    if root_event is None:
        raise HTTPException(
            404,
            f"No derivation event with output_id={output_id!r} found for run "
            f"{market!r}/{run_id!r} -- lineage can only be reconstructed for a fact this pipeline "
            "actually recorded a derivation event for.",
        )
    derivation = root_event.get("derivation") or {}
    tree = _build_lineage_node(
        output_id, {**(root_event.get("output_data") or {}), "output_id": output_id}, events, _seen=set(),
    )
    tree["derivation_method"] = derivation.get("derivation_method")
    tree["formula"] = derivation.get("formula")
    tree["inputs"] = [
        _build_lineage_node(name, fact, events, _seen={output_id})
        for name, fact in (derivation.get("input_facts") or {}).items()
    ]
    return _json_safe({"market": market, "run_id": run_id, "output_id": output_id, "lineage": tree})

"""Append-only JSONL audit trail storage.

Directory convention is reused, not reinvented: every demand-usage run's
on-disk home is `me_engine.demandUseCaseCountry_approach2B.checkpoints.run_dir(market, root)`
(one directory per market, e.g. `output/demand_usage/<slug>/`) -- this
module's `audit_log.jsonl` lives right alongside `00_setup_approved.json`,
`01_taxonomy_approved.json`, `02_sizing.progress.json`, and `.size.lock` in
that SAME directory. `run_id` is carried on every event (and accepted by
every function here for a stable, forward-compatible signature) but does
NOT change the directory layout -- exactly one sizing run is ever active
per market at a time (enforced by `run_lock.py`), so one market directory
holds one run's audit trail at a time, matching every other checkpoint file
in that directory.

Write pattern mirrors `progress.py`'s `ProgressRecorder`: NOT a single
long-held file handle across the whole process, but a fresh open-append-close
per `emit()` call -- simplest safe approach for a single-writer process, and
it means a crash mid-run never corrupts previously-written lines (each JSON
line is already flushed to disk before the process can move on).

Runtime-optimization note (2026-08-26): Demand-Usage's bounded-parallel
driver-evidence fetch (see `agents.py::demand_quantification_node`) means
`emit()` is no longer guaranteed single-writer -- multiple worker threads can
call it concurrently for the same `AuditEmitter` instance. `self._seq += 1`
is a read-modify-write race, and unlocked concurrent appends risk
interleaved writes. A single `threading.Lock` around the increment+write is
the minimal fix -- it does not change the file format, event shape, or
ordering guarantees beyond "each event gets a unique, monotonically
assigned seq and one clean line," and it does not block anything else (each
critical section is just an int increment + one small file write).
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from me_engine.demandUseCaseCountry_approach2B import checkpoints

from .events import AuditEvent
from .redact import redact_secrets

_LLM_TEXT_FIELDS = ("prompt", "response", "error")
_SOURCE_TEXT_FIELDS = ("url",)


def run_audit_dir(market: str, run_id: str, root: Path | None = None) -> Path:
    """Reuses `checkpoints.run_dir` -- see module docstring. `run_id` is
    accepted for a stable signature/forward compatibility but does not
    affect the returned path today (one active run per market directory)."""
    return checkpoints.run_dir(market, root)


def audit_log_path(market: str, run_id: str, root: Path | None = None) -> Path:
    return run_audit_dir(market, run_id, root) / "audit_log.jsonl"


def _redact_nested(value: Any, fields: tuple[str, ...]) -> Any:
    if not isinstance(value, dict):
        return value
    out = dict(value)
    for f in fields:
        if isinstance(out.get(f), str):
            out[f] = redact_secrets(out[f])
    return out


def _redact_event_fields(event: AuditEvent) -> None:
    if isinstance(event.llm, dict):
        event.llm = _redact_nested(event.llm, _LLM_TEXT_FIELDS)
    if isinstance(event.source, dict):
        event.source = _redact_nested(event.source, _SOURCE_TEXT_FIELDS)


class AuditEmitter:
    """One per pipeline run. `emit(...)` builds an `AuditEvent`, redacts any
    LLM/source text fields, appends one JSON line to `audit_log.jsonl`
    (creating the file/directory if needed), and returns the event.
    Never rewrites or truncates the file -- append-only, always."""

    def __init__(self, run_id: str, approach: str, market: str, root: Path | None = None) -> None:
        self.run_id = run_id
        self.approach = approach
        self.market = market
        self.root = root
        self._seq = 0
        self._lock = threading.Lock()  # see module note on concurrent emit() (2026-08-26)
        self.path = audit_log_path(market, run_id, root)

    def emit(self, stage: str, event_type: str, **kwargs: Any) -> AuditEvent:
        with self._lock:
            self._seq += 1
            event = AuditEvent.create(
                run_id=self.run_id, approach=self.approach, market=self.market,
                stage=stage, event_type=event_type, seq=self._seq, **kwargs,
            )
            _redact_event_fields(event)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
            return event


def read_events(market: str, run_id: str, root: Path | None = None, **filters: Any) -> list[dict[str, Any]]:
    """Reads and parses every line of `audit_log.jsonl` for this run,
    applying simple equality filters (e.g. `stage="pricing"`,
    `event_type="llm_call"`, `country="India"`, `bucket="Fuel"`,
    `status="success"`). A missing file returns an empty list, never
    raises."""
    path = audit_log_path(market, run_id, root)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            # `audit_log.jsonl` lives at the MARKET directory level (see
            # module docstring) -- an event belonging to a different run_id
            # than requested (e.g. a stale file from a prior run at the same
            # market) is never returned as if it were this run's data.
            if data.get("run_id") != run_id:
                continue
            if all(data.get(k) == v for k, v in filters.items() if v is not None):
                events.append(data)
    return events


def list_runs(root: Path | None = None) -> list[dict[str, Any]]:
    """Scans the demand-usage output root for run directories. A directory
    counts as a "run" if it has an `audit_log.jsonl`, a `.size.lock`, or a
    `02_sizing.progress.json` -- older runs with none of those (pre-audit,
    pre-hardening) are not listed here; use `checkpoints`-level file listing
    for those if ever needed."""
    base = (Path(root) / checkpoints._OUTPUT_ROOT) if root else checkpoints._OUTPUT_ROOT
    if not base.exists():
        return []
    runs: list[dict[str, Any]] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        audit_path = entry / "audit_log.jsonl"
        progress_path = entry / "02_sizing.progress.json"
        lock_path = entry / ".size.lock"
        has_audit_log = audit_path.exists()
        has_progress = progress_path.exists()
        has_lock = lock_path.exists()
        if not (has_audit_log or has_progress or has_lock):
            continue

        run_id = None
        market = entry.name
        last_modified: float | None = None
        for p in (audit_path, progress_path, lock_path):
            if p.exists():
                mtime = p.stat().st_mtime
                last_modified = mtime if last_modified is None else max(last_modified, mtime)

        # A run_id, if known, comes from the progress file or the lock file
        # (both already carry it) -- audit events also carry it, but reading
        # the whole JSONL just to get a run_id would be wasteful here.
        for p in (progress_path, lock_path):
            if p.exists():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    data = {}
                if isinstance(data, dict) and data.get("run_id"):
                    run_id = data["run_id"]
                    market = data.get("market", market)
                    break

        # Neither progress nor lock carried a run_id -- this is the normal
        # case for a plain CLI run (no `--run-id` passed), not a missing
        # value. Every audit event already carries the real run_id used at
        # emission time (see `AuditEmitter`/`graph.py::run_sizing_phase`,
        # which use the market name itself), so fall back to reading ONLY
        # the first line of the JSONL -- one line, not the whole file --
        # rather than leaving `run_id: None` and breaking every
        # `/runs/{market}/{run_id}` route that depends on it.
        if run_id is None and audit_path.exists():
            try:
                with audit_path.open("r", encoding="utf-8") as f:
                    first_line = f.readline()
                if first_line.strip():
                    first_event = json.loads(first_line)
                    if isinstance(first_event, dict) and first_event.get("run_id"):
                        run_id = first_event["run_id"]
            except (OSError, json.JSONDecodeError):
                pass

        runs.append({
            "market": market,
            "run_id": run_id,
            "run_dir": str(entry),
            "has_audit_log": has_audit_log,
            "has_progress": has_progress,
            "has_lock": has_lock,
            "last_modified": last_modified,
        })
    return runs


# NOTE (read-only consumer relationship, not a change here): `progress.py`'s
# `ProgressRecorder`/`02_sizing.progress.json` and `run_lock.py`'s
# `.size.lock` are the two other on-disk signals a future run-detail/list API
# endpoint should cross-reference to compute a run's live/interrupted/
# completed status (e.g. `run_lock._is_pid_alive` + a progress file whose
# `status` is still `"running"` == likely interrupted if the pid is dead).
# Neither of those modules' logic is touched by this package.

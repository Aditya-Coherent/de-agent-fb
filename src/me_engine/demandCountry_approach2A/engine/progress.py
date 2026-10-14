"""Lightweight, incremental progress persistence for the Phase 3 (sizing)
run -- runtime-hardening addendum, post-regression-incident (2026-08-24): a
1-2 hour `size` run wrote NOTHING to disk until the very end
(`write_buildup_json`/`write_buildup_workbook` in `buildup_writer.py`), so a
killed/crashed process left zero trace of what had actually happened.

This module does not change what any node computes -- it only records a
small, cumulative SUMMARY (counts and milestone markers, never the full
400-500-cell result) to `<market run dir>/02_sizing.progress.json` at
meaningful checkpoints: run start, each usage-bucket's driver quantification
done, each country/bucket demand-quantity cell done, each intensity/price
done, each top-level graph node done, and final completion/failure. It is
deliberately NOT written on every individual network/evidence-acquisition
attempt (there are 400-500 of those per market) -- only once per completed
cell/bucket/phase, so the mechanism stays lightweight.

`ProgressRecorder` is injected into the sizing graph's state under the
`"progress"` key (see `graph.py::sizing_init_state`) and is optional
throughout -- every call site in `agents.py` checks `state.get("progress")`
and is a no-op when absent, so every existing test that builds a bare state
dict (no recorder) is completely unaffected.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import checkpoints
from .evidence import PROVENANCE_STATES

# Fixed, linear node order of the sizing graph (see `graph.py::build_sizing_graph`)
# -- used only to label graph-level milestones in the progress file; does not
# drive control flow.
NODE_NAMES = (
    "demand_quantification",
    "pricing",
    "calculate",
    "driver_magnitude_validation",
    "triangulation",
    "magnitude_cross_validation",
    "validation",
)


def progress_path(market_name: str, root: Path | None = None) -> Path:
    return checkpoints.run_dir(market_name, root) / "02_sizing.progress.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProgressRecorder:
    """Not thread/process-safe by design -- exactly one process is expected
    to hold the sizing lock (see `run_lock.py`) and own this recorder at a
    time. Each `_write` is a small, atomic (`os.replace`) JSON write."""

    def __init__(
        self,
        market_name: str,
        run_id: str | None = None,
        source: str = "cli",
        root: Path | None = None,
    ) -> None:
        self.market_name = market_name
        self.path = progress_path(market_name, root)
        self._t0 = time.time()
        start = _now_iso()
        self._data: dict[str, Any] = {
            "market": market_name,
            "run_id": run_id,
            "source": source,
            "start_time": start,
            "last_update": start,
            "elapsed_seconds": 0.0,
            "status": "running",
            "phase": "start",
            "completed_buckets": [],
            "completed_cells": 0,
            "evidence_attempts": {"driver": 0, "intensity": 0, "price": 0, "price_conversion": 0},
            "evidence_quality": dict.fromkeys(PROVENANCE_STATES, 0),
            "pricing": {"completed_buckets": [], "failures": []},
            "errors": [],
            # Reporting addendum (2026-08-26) -- cumulative elapsed_seconds
            # at the moment each top-level graph node finished, keyed by
            # NODE_NAMES entry. Real, measured wall-clock (not an estimate);
            # `run_report.py` diffs consecutive entries to get each node's
            # own duration.
            "phase_timings": {},
        }
        self._write()

    def _write(self) -> None:
        self._data["last_update"] = _now_iso()
        self._data["elapsed_seconds"] = round(time.time() - self._t0, 1)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
        # Atomic on the same filesystem. On Windows the rename fails with
        # "Access is denied" while another process (antivirus, indexer, an
        # editor's file watcher) briefly has the target open -- seen killing a
        # whole Olive Oil sizing run. This file is progress display only, so
        # retry briefly, then skip this update rather than fail the run.
        for attempt in range(5):
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:
                time.sleep(0.2 * (attempt + 1))

    # -- milestones -------------------------------------------------------- #

    def bucket_started(self, bucket_name: str) -> None:
        self._data["phase"] = f"demand_quantification:{bucket_name}"
        self._write()

    def driver_attempts_live(self, count: int) -> None:
        """Real-time visibility during bounded-parallel driver-evidence fetch
        (runtime optimization, 2026-08-26) -- called from the MAIN thread
        only, once per completed worker task as it lands (never from inside
        a worker thread), so this stays single-writer-safe with no locking
        needed in this class. Bumps only the raw attempt counter; the later
        `country_cell_done` call (made during the unchanged sequential
        fallback/assembly pass) must pass `attempts_count=0` for that same
        country to avoid double-counting."""
        if count:
            self._data["evidence_attempts"]["driver"] += count
            self._write()

    def country_cell_done(self, bucket_name: str, country: str, provenance: str, attempts_count: int) -> None:
        self._data["completed_cells"] += 1
        self._data["evidence_attempts"]["driver"] += attempts_count
        key = provenance if provenance in self._data["evidence_quality"] else "assumption"
        self._data["evidence_quality"][key] += 1
        self._write()

    def intensity_done(self, bucket_name: str, attempts_count: int) -> None:
        self._data["evidence_attempts"]["intensity"] += attempts_count
        self._write()

    def bucket_done(self, bucket_name: str) -> None:
        self._data["completed_buckets"].append(bucket_name)
        self._write()

    def price_done(self, bucket_name: str, attempts_count: int, call_failed: bool) -> None:
        self._data["pricing"]["completed_buckets"].append(bucket_name)
        self._data["evidence_attempts"]["price"] += attempts_count
        if call_failed:
            self._data["pricing"]["failures"].append(bucket_name)
        self._write()

    def price_conversion_attempts(self, attempts_count: int) -> None:
        self._data["evidence_attempts"]["price_conversion"] += attempts_count
        self._write()

    def node_done(self, node_name: str) -> None:
        self._data["phase"] = f"{node_name}_done"
        self._data["phase_timings"][node_name] = round(time.time() - self._t0, 1)
        self._write()

    def failed(self, error: BaseException | str) -> None:
        self._data["status"] = "failed"
        self._data["errors"].append(str(error))
        self._write()

    def finished(self, status: str = "completed") -> None:
        self._data["status"] = status
        self._data["phase"] = "done"
        self._write()

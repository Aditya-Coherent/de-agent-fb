"""Generation history — an append-only log of every Input Sheet generation run.

Purely for visibility/audit ("what did we generate, when, how well did it
score") — nothing here feeds back into generation itself. One JSON line per
run, so it's human-readable and trivially diffable; no DB/schema migration
needed for something this small.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

DEFAULT_HISTORY_PATH = Path("output") / "generation_history.jsonl"


def append_history(entry: dict[str, Any], path: Path | str = DEFAULT_HISTORY_PATH) -> None:
    """Append one run record. Best-effort — a logging failure must never
    interrupt an otherwise-successful generation."""
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"timestamp": datetime.now(timezone.utc).isoformat(), **entry}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def read_history(path: Path | str = DEFAULT_HISTORY_PATH, limit: int = 20) -> list[dict[str, Any]]:
    """Return the most recent `limit` runs, newest first.

    A run can produce more than one record over time — the Input Sheet
    generation appends one immediately, and a later one-click ME workbook
    generation appends another for the same `run_id` (see
    `me_workbook_history_entry`). Records sharing a `run_id` are merged here
    (later fields win, e.g. `me_output_path` gets added on top of the
    original `output_path`) so the caller sees one row per run, not two.
    Records with no `run_id` (older log entries) are kept as their own row.
    Empty list if the log doesn't exist yet; malformed lines are skipped.
    """
    path = Path(path)
    if not path.exists():
        return []
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = record.get("run_id") or f"_no_run_id_{i}"
        if key not in merged:
            merged[key] = {}
            order.append(key)
        merged[key].update(record)
    rows = [merged[k] for k in order]
    return list(reversed(rows[-limit:]))


def state_to_history_entry(market_name: str, state: Mapping[str, Any], *,
                           run_id: str, skeleton_source: str, out_path: str) -> dict[str, Any]:
    """Build a history record from a finished generation graph state."""
    return {
        "run_id": run_id,
        "market_name": market_name,
        "skeleton_source": skeleton_source,
        "reward": state.get("reward"),
        "iterations": state.get("iter"),
        "global_anchor_2026": state.get("global_anchor_2026"),
        "global_cagr": state.get("global_cagr"),
        "geographies": len(state.get("regional", {}) or {}),
        "db_evidence_used": state.get("db_evidence") is not None,
        "web_snippets": len(state.get("web_snippets") or []),
        "quality_flags": [f.get("code") for f in (state.get("quality_flags") or [])],
        "output_path": out_path,
    }


def me_workbook_history_entry(run_id: str, *, me_output_path: str,
                              gate1_passed: bool, gate3_passed: bool) -> dict[str, Any]:
    """Build the follow-up record appended once a one-click ME workbook
    finishes for an already-logged run (merged into that run's row by
    `read_history`, keyed on `run_id`)."""
    return {
        "run_id": run_id,
        "me_output_path": me_output_path,
        "me_gate1_passed": gate1_passed,
        "me_gate3_passed": gate3_passed,
    }

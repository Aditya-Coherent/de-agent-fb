"""Tests for incremental sizing progress persistence (`progress.py`) --
runtime-hardening addendum, post-regression-incident (2026-08-24): a 1-2 hour
`size` run wrote nothing to disk until the very end, so a killed process left
zero trace of what had actually happened.

Run with: pytest tests/test_demand_usage_progress.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from me_engine.demand_usage import agents
from me_engine.demand_usage.progress import ProgressRecorder, progress_path


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_progress_file_created_on_construction(tmp_path):
    rec = ProgressRecorder("Cement", source="cli", root=tmp_path)
    path = progress_path("Cement", root=tmp_path)
    assert path.exists()
    data = _read(path)
    assert data["market"] == "Cement"
    assert data["status"] == "running"
    assert data["phase"] == "start"
    assert data["completed_buckets"] == []
    assert data["completed_cells"] == 0


def test_progress_updates_at_bucket_and_cell_milestones(tmp_path):
    rec = ProgressRecorder("Cement", source="cli", root=tmp_path)
    path = progress_path("Cement", root=tmp_path)

    rec.bucket_started("Residential Construction")
    assert _read(path)["phase"] == "demand_quantification:Residential Construction"

    rec.country_cell_done("Residential Construction", "US", "verified_observed", 3)
    data = _read(path)
    assert data["completed_cells"] == 1
    assert data["evidence_attempts"]["driver"] == 3
    assert data["evidence_quality"]["verified_observed"] == 1

    rec.country_cell_done("Residential Construction", "China", "assumption", 4)
    data = _read(path)
    assert data["completed_cells"] == 2
    assert data["evidence_attempts"]["driver"] == 7
    assert data["evidence_quality"]["assumption"] == 1

    rec.bucket_done("Residential Construction")
    assert _read(path)["completed_buckets"] == ["Residential Construction"]

    rec.intensity_done("Residential Construction", 2)
    assert _read(path)["evidence_attempts"]["intensity"] == 2

    rec.price_done("Residential Construction", 5, call_failed=False)
    data = _read(path)
    assert data["pricing"]["completed_buckets"] == ["Residential Construction"]
    assert data["evidence_attempts"]["price"] == 5
    assert data["pricing"]["failures"] == []

    rec.price_done("Other Bucket", 1, call_failed=True)
    assert _read(path)["pricing"]["failures"] == ["Other Bucket"]

    rec.node_done("demand_quantification")
    assert _read(path)["phase"] == "demand_quantification_done"

    rec.finished("completed")
    data = _read(path)
    assert data["status"] == "completed"
    assert data["phase"] == "done"


def test_unknown_provenance_value_falls_back_to_assumption_bucket(tmp_path):
    rec = ProgressRecorder("Cement", source="cli", root=tmp_path)
    rec.country_cell_done("B", "US", "not_a_real_status", 1)
    data = _read(progress_path("Cement", root=tmp_path))
    assert data["evidence_quality"]["assumption"] == 1


def test_failed_marks_status_and_records_error(tmp_path):
    rec = ProgressRecorder("Cement", source="cli", root=tmp_path)
    rec.failed(RuntimeError("network exploded"))
    data = _read(progress_path("Cement", root=tmp_path))
    assert data["status"] == "failed"
    assert "network exploded" in data["errors"][0]


def test_progress_survives_across_multiple_writes_no_corruption(tmp_path):
    rec = ProgressRecorder("Cement", source="cli", root=tmp_path)
    for i in range(50):
        rec.country_cell_done("B", f"country-{i}", "assumption", 1)
    data = _read(progress_path("Cement", root=tmp_path))
    assert data["completed_cells"] == 50
    assert data["evidence_attempts"]["driver"] == 50


def test_demand_quantification_node_is_a_no_op_without_progress_key():
    """Every pre-existing test builds a bare state dict with no 'progress'
    key -- confirms that absence is silently safe, never a KeyError."""
    from me_engine.generation.llm import GenLLM

    class _StubConfig:
        enable_web = False

    class _StubLLM:
        def complete_json(self, system, user, **kwargs):
            return {"value": 1.0, "rationale": "stub"}

    state = {
        "config": _StubConfig(),
        "llm": _StubLLM(),
        "market_name": "Cement",
        "log": [],
        "usage_buckets": [{"name": "B", "description": "d", "bucket_category": "x"}],
        "drivers": {"B": {"driver_name": "d", "unit": "u", "final_demand_unit": "u"}},
        "countries": ["US"],
    }
    out = agents.demand_quantification_node(state)
    assert "quantities" in out  # ran to completion, no progress key required


def test_progress_recorder_injected_via_state_records_bucket_progress(tmp_path):
    """Same node, this time WITH a real ProgressRecorder injected -- confirms
    the hook actually fires without changing the node's own return value."""
    class _StubConfig:
        enable_web = False

    class _StubLLM:
        def complete_json(self, system, user, **kwargs):
            return {"value": 1.0, "rationale": "stub"}

    rec = ProgressRecorder("Cement", source="cli", root=tmp_path)
    state = {
        "config": _StubConfig(),
        "llm": _StubLLM(),
        "market_name": "Cement",
        "log": [],
        "usage_buckets": [{"name": "B", "description": "d", "bucket_category": "x"}],
        "drivers": {"B": {"driver_name": "d", "unit": "u", "final_demand_unit": "u"}},
        "countries": ["US", "China"],
        "progress": rec,
    }
    out = agents.demand_quantification_node(state)
    data = _read(progress_path("Cement", root=tmp_path))
    assert data["completed_buckets"] == ["B"]
    assert data["completed_cells"] == 2
    assert "quantities" in out

"""Tests for the additive audit/observability layer (`me_engine.audit`)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from me_engine.audit.events import AuditEvent
from me_engine.audit.redact import redact_secrets
from me_engine.audit.store import AuditEmitter, list_runs, read_events, run_audit_dir


def test_event_creation_and_serialization_round_trip():
    event = AuditEvent.create(
        run_id="run-1", approach="usage_based", market="Cement", stage="pricing",
        event_type="llm_call", seq=1, bucket="Ready-mix", llm={"prompt": "p", "response": "r"},
    )
    assert event.event_id
    assert event.timestamp
    as_dict = event.to_dict()
    assert as_dict["run_id"] == "run-1"
    assert as_dict["approach"] == "usage_based"
    assert as_dict["seq"] == 1

    restored = AuditEvent.from_dict(as_dict)
    assert restored == event

    # from_dict tolerates extra/unknown keys (forward compatible)
    as_dict["some_future_field"] = "x"
    restored2 = AuditEvent.from_dict(as_dict)
    assert restored2.event_id == event.event_id


def test_emit_appends_without_truncating(tmp_path: Path):
    emitter = AuditEmitter(run_id="run-1", approach="usage_based", market="Avocado Oil", root=tmp_path)
    emitter.emit("pricing", "llm_call", data={"n": 1})
    emitter.emit("pricing", "llm_call", data={"n": 2})

    path = run_audit_dir("Avocado Oil", "run-1", root=tmp_path) / "audit_log.jsonl"
    assert path.exists()
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    second = json.loads(lines[1])
    assert first["seq"] == 1
    assert second["seq"] == 2
    assert first["data"]["n"] == 1
    assert second["data"]["n"] == 2

    # A third emit must not rewrite/truncate the first two lines.
    emitter.emit("pricing", "llm_call", data={"n": 3})
    lines2 = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines2) == 3
    assert json.loads(lines2[0]) == first
    assert json.loads(lines2[1]) == second


def test_redact_secrets_strips_bearer_token_and_api_key():
    text = (
        "Authorization: Bearer abc123.def-456_ghi and api_key=sk_live_ABCDEFGHIJKLMNOPQRSTUVWX "
        "plus a raw sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ token"
    )
    redacted = redact_secrets(text)
    assert "abc123.def-456_ghi" not in redacted
    assert "sk_live_ABCDEFGHIJKLMNOPQRSTUVWX" not in redacted
    assert "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in redacted
    assert "[REDACTED]" in redacted

    normal = "The market size grew 5% year over year in barrels per day."
    assert redact_secrets(normal) == normal


def test_redact_secrets_handles_non_string_and_empty_input():
    assert redact_secrets("") == ""
    assert redact_secrets(None) is None  # type: ignore[arg-type]


def test_emitter_redacts_llm_and_source_fields(tmp_path: Path):
    emitter = AuditEmitter(run_id="run-2", approach="usage_based", market="Cement", root=tmp_path)
    event = emitter.emit(
        "evidence_search", "llm_call",
        llm={"prompt": "call with Bearer sekrit-token-value", "response": "ok"},
    )
    assert "sekrit-token-value" not in event.llm["prompt"]
    persisted = read_events("Cement", "run-2", root=tmp_path)
    assert "sekrit-token-value" not in persisted[0]["llm"]["prompt"]


def test_read_events_filters_by_stage_and_event_type(tmp_path: Path):
    emitter = AuditEmitter(run_id="run-3", approach="usage_based", market="Crude Oil", root=tmp_path)
    emitter.emit("pricing", "llm_call", data={"x": 1})
    emitter.emit("pricing", "source_attempt", status="success", data={"x": 2})
    emitter.emit("calculate", "source_attempt", status="failed", data={"x": 3})

    all_events = read_events("Crude Oil", "run-3", root=tmp_path)
    assert len(all_events) == 3

    pricing_only = read_events("Crude Oil", "run-3", root=tmp_path, stage="pricing")
    assert len(pricing_only) == 2

    source_attempts = read_events("Crude Oil", "run-3", root=tmp_path, event_type="source_attempt")
    assert len(source_attempts) == 2

    combined = read_events(
        "Crude Oil", "run-3", root=tmp_path, stage="calculate", event_type="source_attempt", status="failed",
    )
    assert len(combined) == 1
    assert combined[0]["data"]["x"] == 3


def test_read_events_missing_file_returns_empty(tmp_path: Path):
    assert read_events("Nonexistent Market", "run-x", root=tmp_path) == []


def test_list_runs_finds_run_with_audit_log_and_progress(tmp_path: Path):
    from me_engine.demandUseCaseCountry_approach2B import checkpoints

    market_dir = checkpoints.run_dir("Smartphones", root=tmp_path)
    (market_dir / "audit_log.jsonl").write_text(
        json.dumps({"run_id": "run-abc", "market": "Smartphones"}) + "\n", encoding="utf-8",
    )
    (market_dir / "02_sizing.progress.json").write_text(
        json.dumps({"market": "Smartphones", "run_id": "run-abc", "status": "running"}), encoding="utf-8",
    )

    runs = list_runs(root=tmp_path)
    assert len(runs) == 1
    run = runs[0]
    assert run["market"] == "Smartphones"
    assert run["run_id"] == "run-abc"
    assert run["has_audit_log"] is True
    assert run["has_progress"] is True
    assert run["has_lock"] is False
    assert run["last_modified"] is not None


def test_list_runs_empty_root_returns_empty_list(tmp_path: Path):
    assert list_runs(root=tmp_path / "does_not_exist") == []

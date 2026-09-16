"""Tests for `backend/audit_routes.py`, using FastAPI's TestClient against
`backend/main.py`'s app with a temp fixture run directory."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT / "src"), str(ROOT), str(ROOT / "backend")):
    if p not in sys.path:
        sys.path.insert(0, p)

import audit_routes  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from me_engine.demand_usage import checkpoints  # noqa: E402

client = TestClient(app)

MARKET = "Test Cement"
RUN_ID = "run-fixture-1"


@pytest.fixture()
def fixture_run(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_routes, "ROOT", tmp_path)

    run_dir = checkpoints.run_dir(MARKET, root=tmp_path)

    progress = {
        "market": MARKET, "run_id": RUN_ID, "source": "cli",
        "start_time": "2026-08-25T00:00:00+00:00", "last_update": "2026-08-25T00:05:00+00:00",
        "elapsed_seconds": 300.0, "status": "completed", "phase": "done",
        "completed_buckets": ["Ready-mix"], "completed_cells": 2,
        "evidence_attempts": {"driver": 2, "intensity": 1, "price": 1, "price_conversion": 0},
        "evidence_quality": {"verified_observed": 1, "derived": 1, "source_found_unverified": 0, "assumption": 0},
        "pricing": {"completed_buckets": ["Ready-mix"], "failures": []},
        "errors": [],
    }
    (run_dir / "02_sizing.progress.json").write_text(json.dumps(progress), encoding="utf-8")

    buildup = {
        "market_name": MARKET,
        "total_market_value_usd_mn": 12345.6,
        "related_demand_value_usd_mn": 100.0,
        "quality_flags": [],
    }
    (run_dir / "02_sizing.json").write_text(json.dumps(buildup), encoding="utf-8")

    events = [
        {
            "event_id": "e1", "run_id": RUN_ID, "approach": "usage_based", "market": MARKET,
            "stage": "demand_quantification", "event_type": "stage_start", "timestamp": "2026-08-25T00:00:01+00:00",
            "seq": 1, "country": None, "bucket": None, "llm": None, "query": None, "source": None,
            "raw_evidence": None, "validation": None, "derivation": None, "calculation": None,
            "input_data": None, "output_data": None, "status": None, "error": None, "data": {"node": "demand_quantification_node"},
        },
        {
            "event_id": "e2", "run_id": RUN_ID, "approach": "usage_based", "market": MARKET,
            "stage": "evidence_search", "event_type": "source_attempt", "timestamp": "2026-08-25T00:00:02+00:00",
            "seq": 2, "country": "India", "bucket": "Ready-mix", "llm": None,
            "query": {"search_query": "India cement production tonnes"},
            "source": {"name": "USGS", "url": "https://usgs.gov/report.pdf", "type": "pdf"},
            "raw_evidence": None, "validation": None, "derivation": None, "calculation": None,
            "input_data": None, "output_data": None, "status": "success", "error": None, "data": None,
        },
        {
            "event_id": "e3", "run_id": RUN_ID, "approach": "usage_based", "market": MARKET,
            "stage": "derivation", "event_type": "derivation", "timestamp": "2026-08-25T00:00:03+00:00",
            "seq": 3, "country": "India", "bucket": "Ready-mix", "llm": None, "query": None, "source": None,
            "raw_evidence": None, "validation": None,
            "derivation": {
                "derivation_method": "regional_share", "formula": "total(1000) * share(0.5)",
                "input_facts": {
                    "total": {
                        "value": 1000.0, "unit": "tonnes", "year": 2025, "geography": "global",
                        "source": "IEA", "source_url": "https://iea.org/report",
                        "verification_status": "verified_observed",
                    },
                    "population_share": {
                        "value": 0.5, "unit": "share", "year": None, "geography": "India",
                        "source": "reference_data.population_share", "source_url": None,
                        "verification_status": "derived",
                    },
                },
                "output_id": "Ready-mix:India:driver",
            },
            "calculation": None, "input_data": None,
            "output_data": {"value": 500.0, "unit": "tonnes", "verification_status": "derived"},
            "status": "success", "error": None, "data": None,
        },
        {
            "event_id": "e4", "run_id": RUN_ID, "approach": "usage_based", "market": MARKET,
            "stage": "demand_quantification", "event_type": "stage_end", "timestamp": "2026-08-25T00:00:04+00:00",
            "seq": 4, "country": None, "bucket": None, "llm": None, "query": None, "source": None,
            "raw_evidence": None, "validation": None, "derivation": None, "calculation": None,
            "input_data": None, "output_data": None, "status": None, "error": None,
            "data": {"node": "demand_quantification"},
        },
    ]
    audit_path = run_dir / "audit_log.jsonl"
    with open(audit_path, "w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")

    return {"run_dir": run_dir, "root": tmp_path}


def test_list_runs(fixture_run):
    resp = client.get("/api/audit/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    run = data[0]
    assert run["market"] == MARKET
    assert run["run_id"] == RUN_ID
    assert run["status"] == "completed"
    assert run["final_market_size"] == 12345.6
    assert run["has_audit_log"] is True


def test_list_runs_unsupported_approach_returns_empty(fixture_run):
    resp = client.get("/api/audit/runs?approach=desk_research")
    assert resp.status_code == 200
    assert resp.json() == []


def test_run_detail(fixture_run):
    resp = client.get(f"/api/audit/runs/{MARKET}/{RUN_ID}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["audit_summary"]["total_events"] == 4
    assert data["audit_summary"]["counts_by_event_type"]["derivation"] == 1
    assert "India" in data["audit_summary"]["countries_touched"]
    assert "Ready-mix" in data["audit_summary"]["buckets_touched"]
    assert data["totals"]["total_market_value_usd_mn"] == 12345.6


def test_run_detail_missing_run_tolerates_absence(fixture_run):
    resp = client.get(f"/api/audit/runs/{MARKET}/no-such-run")
    assert resp.status_code == 200
    data = resp.json()
    assert data["audit_summary"]["not_captured"] is True
    assert data["audit_summary"]["total_events"] == 0


def test_run_events_filtering(fixture_run):
    resp = client.get(f"/api/audit/runs/{MARKET}/{RUN_ID}/events", params={"event_type": "source_attempt"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["events"][0]["event_id"] == "e2"

    resp2 = client.get(f"/api/audit/runs/{MARKET}/{RUN_ID}/events", params={"bucket": "Ready-mix", "country": "India"})
    assert resp2.status_code == 200
    assert resp2.json()["total"] == 2  # source_attempt + derivation


def test_run_events_pagination(fixture_run):
    resp = client.get(f"/api/audit/runs/{MARKET}/{RUN_ID}/events", params={"limit": 2, "offset": 1})
    data = resp.json()
    assert data["total"] == 4
    assert len(data["events"]) == 2
    assert data["events"][0]["event_id"] == "e2"


def test_run_timeline_shape(fixture_run):
    resp = client.get(f"/api/audit/runs/{MARKET}/{RUN_ID}/timeline")
    assert resp.status_code == 200
    data = resp.json()
    stages = {t["stage"]: t["status"] for t in data["timeline"]}
    assert stages["demand_quantification"] == "done"
    assert stages["pricing"] == "pending"
    assert set(stages) == set(audit_routes.TIMELINE_STAGES)


def test_lineage_happy_path(fixture_run):
    resp = client.get(f"/api/audit/runs/{MARKET}/{RUN_ID}/lineage/Ready-mix:India:driver")
    assert resp.status_code == 200
    data = resp.json()
    lineage = data["lineage"]
    assert lineage["derivation_method"] == "regional_share"
    input_names = {i["name"] for i in lineage["inputs"]}
    assert input_names == {"total", "population_share"}
    total_input = next(i for i in lineage["inputs"] if i["name"] == "total")
    assert total_input["is_leaf"] is True
    assert total_input["verification_status"] == "verified_observed"


def test_lineage_404_for_unknown_output_id(fixture_run):
    resp = client.get(f"/api/audit/runs/{MARKET}/{RUN_ID}/lineage/does-not-exist")
    assert resp.status_code == 404

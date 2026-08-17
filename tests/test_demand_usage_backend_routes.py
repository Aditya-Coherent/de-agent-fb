"""Backend API route tests for the Demand-Side (Usage-Based) three-phase
pipeline (`backend/demand_usage_routes.py`).

Mounts ONLY this router on a fresh FastAPI app (not the full `backend/main.py`,
which pulls in the whole Desk Research stack) and monkeypatches the
pipeline-level functions it calls -- no real LLM/network call, no real
LangGraph run. Confirms the two approval gates are enforced at the HTTP
layer too, and that a run_id can't skip a gate.

Run with: pytest tests/test_demand_usage_backend_routes.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "backend"))

import demand_usage_routes as routes  # noqa: E402


@pytest.fixture()
def client(monkeypatch):
    # Fresh in-memory run store per test -- routes module keeps it at import scope.
    monkeypatch.setattr(routes, "_DU_RUNS", {})

    class _StubConfig:
        is_online = True
        enable_web = False

    monkeypatch.setattr(routes, "GenConfig", type("GenConfig", (), {"load": staticmethod(lambda root: _StubConfig())}))

    setup_draft = {
        "market_name": "Crude Oil", "scope": "s", "geography": ["US", "China"],
        "base_year": 2025, "market_unit": "USD Mn", "physical_unit": "Barrels", "grounded": True,
    }
    monkeypatch.setattr(routes.setup, "run_setup_phase", lambda market, cfg, cache_path=None: {**setup_draft, "market_name": market})

    taxonomy_draft = {
        "market_name": "Crude Oil",
        "usage_buckets": [{"name": "Transportation Fuels", "description": "x", "bucket_category": "physical_consumption"}],
        "drivers": {"Transportation Fuels": {"driver_name": "Fuel consumption", "unit": "barrels", "final_demand_unit": "barrels"}},
        "excluded_usage_buckets": [], "missing_usage_flags": [], "coverage_pct": 80.0, "overlap_flags": [],
    }
    monkeypatch.setattr(routes.taxonomy, "run_taxonomy_phase", lambda approved_setup, cfg, cache_path=None: dict(taxonomy_draft))

    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def test_setup_generate_returns_editable_draft_and_run_id(client):
    res = client.get("/api/demand-usage/setup/generate", params={"market": "Crude Oil"})
    assert res.status_code == 200
    body = res.json()
    assert "run_id" in body
    assert body["draft"]["market_name"] == "Crude Oil"
    assert body["draft"]["physical_unit"] == "Barrels"


def test_taxonomy_generate_requires_setup_approval_first(client):
    gen = client.get("/api/demand-usage/setup/generate", params={"market": "Crude Oil"})
    run_id = gen.json()["run_id"]
    res = client.get("/api/demand-usage/taxonomy/generate", params={"run_id": run_id})
    assert res.status_code == 400  # setup not approved yet -- gate enforced


def test_full_setup_then_taxonomy_approval_flow(client):
    gen = client.get("/api/demand-usage/setup/generate", params={"market": "Crude Oil"})
    run_id = gen.json()["run_id"]
    draft = gen.json()["draft"]

    approve = client.post(f"/api/demand-usage/setup/{run_id}/approve", json=draft)
    assert approve.status_code == 200
    assert approve.json()["approved"] is True

    tax_gen = client.get("/api/demand-usage/taxonomy/generate", params={"run_id": run_id})
    assert tax_gen.status_code == 200
    tax_draft = tax_gen.json()["draft"]

    tax_approve = client.post(f"/api/demand-usage/taxonomy/{run_id}/approve", json=tax_draft)
    assert tax_approve.status_code == 200
    assert tax_approve.json()["approved"] is True
    assert routes._DU_RUNS[run_id]["phase"] == "taxonomy_approved"


def test_taxonomy_approve_rejects_bucket_with_no_driver(client):
    gen = client.get("/api/demand-usage/setup/generate", params={"market": "Crude Oil"})
    run_id = gen.json()["run_id"]
    client.post(f"/api/demand-usage/setup/{run_id}/approve", json=gen.json()["draft"])

    bad_draft = {
        "market_name": "Crude Oil",
        "usage_buckets": [{"name": "No Driver Bucket", "description": "x", "bucket_category": "physical_consumption"}],
        "drivers": {},
    }
    res = client.post(f"/api/demand-usage/taxonomy/{run_id}/approve", json=bad_draft)
    assert res.status_code == 422


def test_size_generate_rejects_when_taxonomy_not_approved(client):
    gen = client.get("/api/demand-usage/setup/generate", params={"market": "Crude Oil"})
    run_id = gen.json()["run_id"]
    client.post(f"/api/demand-usage/setup/{run_id}/approve", json=gen.json()["draft"])
    # Skip taxonomy approval -- try to jump straight to sizing.
    with client.stream("GET", "/api/demand-usage/size/generate", params={"run_id": run_id}) as res:
        body = b"".join(res.iter_bytes()).decode()
    assert "Approve setup and taxonomy" in body


def test_unknown_run_id_is_rejected_everywhere(client):
    assert client.post("/api/demand-usage/setup/does-not-exist/approve", json={
        "market_name": "X", "scope": "s", "geography": ["US"], "base_year": 2025,
        "market_unit": "USD Mn", "physical_unit": "Units", "grounded": False,
    }).status_code == 404
    assert client.get("/api/demand-usage/taxonomy/generate", params={"run_id": "does-not-exist"}).status_code == 400
    assert client.get("/api/demand-usage/download/does-not-exist/json").status_code == 404


def test_download_before_sizing_is_404(client):
    gen = client.get("/api/demand-usage/setup/generate", params={"market": "Crude Oil"})
    run_id = gen.json()["run_id"]
    assert client.get(f"/api/demand-usage/download/{run_id}/json").status_code == 404

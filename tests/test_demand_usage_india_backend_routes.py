"""Backend API route tests for the India-only Demand-Side (Usage-Based)
pipeline (`backend/demand_usage_india_routes.py`).

Same conventions as `tests/test_demand_usage_backend_routes.py`: mounts ONLY
this router on a fresh FastAPI app, monkeypatches the pipeline-level
phase-generation functions (no real LLM/network call, no real LangGraph
run), and redirects this package's own `checkpoints._OUTPUT_ROOT` to
`tmp_path` so running this file never clobbers a real on-disk approved
checkpoint. `setup.approve_setup`/`taxonomy.approve_taxonomy` are the REAL
functions -- only `run_setup_phase`/`run_taxonomy_phase` are monkeypatched.

Run with: pytest tests/test_demand_usage_india_backend_routes.py
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

import demand_usage_india_routes as routes  # noqa: E402
from me_engine.demand_usage import checkpoints as non_india_checkpoints  # noqa: E402
from me_engine.demand_usage import run_lock  # noqa: E402
from me_engine.demand_usage_india import checkpoints  # noqa: E402


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(routes, "_DUI_RUNS", {})
    monkeypatch.setattr(checkpoints, "_OUTPUT_ROOT", tmp_path / "demand_usage_india")
    # `run_lock`'s lock file location is keyed off the NON-India
    # `checkpoints.run_dir` (see `demand_usage_india_routes.py`'s own
    # docstring on the shared-tree lock-suffix decision) -- redirect it too,
    # so this test file never touches a real on-disk `.size.lock` file.
    monkeypatch.setattr(non_india_checkpoints, "_OUTPUT_ROOT", tmp_path / "demand_usage")

    class _StubConfig:
        is_online = True
        enable_web = False

    monkeypatch.setattr(routes, "GenConfig", type("GenConfig", (), {"load": staticmethod(lambda root: _StubConfig())}))

    setup_draft = {
        "market_name": "Vinegar", "scope": "s", "geography": ["India"],
        "base_year": 2025, "market_unit": "USD Mn", "physical_unit": "Tonnes", "grounded": True,
    }
    monkeypatch.setattr(routes.setup, "run_setup_phase", lambda market, cfg, cache_path=None: {**setup_draft, "market_name": market})

    taxonomy_draft = {
        "market_name": "Vinegar",
        "major_use_cases": [{"name": "Food Preservation", "description": "x", "bucket_category": "physical_consumption"}],
        "actual_use_cases": [{
            "name": "Household Pickle Preservation", "description": "x",
            "bucket_category": "physical_consumption", "major_use_case": "Food Preservation",
        }],
        "drivers": {"Household Pickle Preservation": {"driver_name": "Pickle volume", "unit": "tonnes", "final_demand_unit": "tonnes"}},
        "excluded_usage_buckets": [], "missing_usage_flags": [], "coverage_pct": 80.0, "overlap_flags": [],
    }
    monkeypatch.setattr(routes.taxonomy, "run_taxonomy_phase", lambda approved_setup, cfg, cache_path=None: dict(taxonomy_draft))

    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def test_setup_generate_returns_editable_draft_and_run_id(client):
    res = client.get("/api/demand-usage-india/setup/generate", params={"market": "Vinegar"})
    assert res.status_code == 200
    body = res.json()
    assert "run_id" in body
    assert body["draft"]["market_name"] == "Vinegar"
    assert body["draft"]["geography"] == ["India"]


def test_setup_approve_forces_geography_to_india_even_if_client_sends_something_else(client):
    gen = client.get("/api/demand-usage-india/setup/generate", params={"market": "Vinegar"})
    run_id = gen.json()["run_id"]
    draft = {**gen.json()["draft"], "geography": ["United States", "China"]}
    res = client.post(f"/api/demand-usage-india/setup/{run_id}/approve", json=draft)
    assert res.status_code == 200
    assert res.json()["geography"] == ["India"]


def test_taxonomy_generate_requires_setup_approval_first(client):
    gen = client.get("/api/demand-usage-india/setup/generate", params={"market": "Vinegar"})
    run_id = gen.json()["run_id"]
    res = client.get("/api/demand-usage-india/taxonomy/generate", params={"run_id": run_id})
    assert res.status_code == 400


def test_full_setup_then_taxonomy_approval_flow(client):
    gen = client.get("/api/demand-usage-india/setup/generate", params={"market": "Vinegar"})
    run_id = gen.json()["run_id"]
    draft = gen.json()["draft"]

    approve = client.post(f"/api/demand-usage-india/setup/{run_id}/approve", json=draft)
    assert approve.status_code == 200
    assert approve.json()["approved"] is True

    tax_gen = client.get("/api/demand-usage-india/taxonomy/generate", params={"run_id": run_id})
    assert tax_gen.status_code == 200
    tax_draft = tax_gen.json()["draft"]

    tax_approve = client.post(f"/api/demand-usage-india/taxonomy/{run_id}/approve", json=tax_draft)
    assert tax_approve.status_code == 200
    assert tax_approve.json()["approved"] is True
    assert tax_approve.json()["actual_use_cases"][0]["major_use_case"] == "Food Preservation"
    assert routes._DUI_RUNS[run_id]["phase"] == "taxonomy_approved"


def test_taxonomy_approve_rejects_actual_use_case_with_no_driver(client):
    gen = client.get("/api/demand-usage-india/setup/generate", params={"market": "Vinegar"})
    run_id = gen.json()["run_id"]
    client.post(f"/api/demand-usage-india/setup/{run_id}/approve", json=gen.json()["draft"])

    bad_draft = {
        "market_name": "Vinegar",
        "actual_use_cases": [{
            "name": "No Driver Use Case", "description": "x",
            "bucket_category": "physical_consumption", "major_use_case": "Food Preservation",
        }],
        "drivers": {},
    }
    res = client.post(f"/api/demand-usage-india/taxonomy/{run_id}/approve", json=bad_draft)
    assert res.status_code == 422


def test_size_generate_rejects_when_taxonomy_not_approved(client):
    gen = client.get("/api/demand-usage-india/setup/generate", params={"market": "Vinegar"})
    run_id = gen.json()["run_id"]
    client.post(f"/api/demand-usage-india/setup/{run_id}/approve", json=gen.json()["draft"])
    with client.stream("GET", "/api/demand-usage-india/size/generate", params={"run_id": run_id}) as res:
        body = b"".join(res.iter_bytes()).decode()
    assert "Approve setup and taxonomy" in body


def test_unknown_run_id_is_rejected_everywhere(client):
    assert client.post("/api/demand-usage-india/setup/does-not-exist/approve", json={
        "market_name": "X", "scope": "s", "geography": ["India"], "base_year": 2025,
        "market_unit": "USD Mn", "physical_unit": "Units", "grounded": False,
    }).status_code == 404
    assert client.get("/api/demand-usage-india/taxonomy/generate", params={"run_id": "does-not-exist"}).status_code == 400
    assert client.get("/api/demand-usage-india/download/does-not-exist/json").status_code == 404


def test_download_before_sizing_is_404(client):
    gen = client.get("/api/demand-usage-india/setup/generate", params={"market": "Vinegar"})
    run_id = gen.json()["run_id"]
    assert client.get(f"/api/demand-usage-india/download/{run_id}/json").status_code == 404


# --------------------------------------------------------------------------- #
# Market-level sizing lock -- shared with the non-India pipeline's own lock
# FILE LOCATION (both keyed via `me_engine.demand_usage.checkpoints.run_dir`),
# but suffixed distinctly (" (India)") so an India run never collides with a
# concurrent non-India run for the same market name, or vice versa.
# --------------------------------------------------------------------------- #
def _approve_setup_and_taxonomy(client) -> str:
    gen = client.get("/api/demand-usage-india/setup/generate", params={"market": "Vinegar"})
    run_id = gen.json()["run_id"]
    client.post(f"/api/demand-usage-india/setup/{run_id}/approve", json=gen.json()["draft"])
    tax_gen = client.get("/api/demand-usage-india/taxonomy/generate", params={"run_id": run_id})
    client.post(f"/api/demand-usage-india/taxonomy/{run_id}/approve", json=tax_gen.json()["draft"])
    return run_id


def test_size_generate_blocked_when_another_india_process_holds_the_market_lock(client):
    run_id = _approve_setup_and_taxonomy(client)
    other = run_lock.acquire_size_lock("Vinegar (India)", source="cli")
    try:
        with client.stream("GET", "/api/demand-usage-india/size/generate", params={"run_id": run_id}) as res:
            body = b"".join(res.iter_bytes()).decode()
        assert "already in progress" in body
        assert "error" in body
    finally:
        other.release()


def test_size_generate_not_blocked_by_a_non_india_lock_for_the_same_market_name(client):
    """A lock held by the NON-India pipeline for the same market name must
    never block the India route -- confirms the lock key is genuinely
    distinct (suffixed), not shared."""
    run_id = _approve_setup_and_taxonomy(client)
    other = run_lock.acquire_size_lock("Vinegar", source="cli")  # non-India lock, same market name
    try:
        acquired = run_lock.acquire_size_lock("Vinegar (India)", source="cli")
        acquired.release()
    finally:
        other.release()

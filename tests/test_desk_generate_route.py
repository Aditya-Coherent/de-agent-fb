"""Backend route tests for the Desk Research Phase 0 gate
(`backend/main.py`'s `/api/segment-definition/*` endpoints and the new hard
gate on `GET /api/generate`).

Imports the real `backend.main` app (not an isolated router, unlike
`demand_usage_routes.py`'s tests) since the segment-definition endpoints and
the `/api/generate` gate both live directly in `main.py`. Monkeypatches
`GenConfig.load` and the DB/skeleton lookup so no real LLM/network/DB call
happens; the module-level `_SEGMENT_DEFS` store is reset per test.

Run with: pytest tests/test_desk_generate_route.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

import main as backend_main  # noqa: E402
from me_engine.generation.skeleton import Skeleton  # noqa: E402

_MARKET = "Global Process Analyzer Market"

_VALID_BODY = {
    "market_name": _MARKET,
    "base_year": 2025,
    "dimensions": [
        {
            "dimension_name": "By Installation",
            "segments": [
                {"name": "Flooring", "region_shares": {
                    "North America": 60, "Europe": 55, "Asia Pacific": 50,
                    "Latin America": 50, "Middle East & Africa": 50,
                }},
                {"name": "Wall Cladding", "region_shares": {
                    "North America": 40, "Europe": 45, "Asia Pacific": 50,
                    "Latin America": 50, "Middle East & Africa": 50,
                }},
            ],
        },
    ],
}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(backend_main, "_SEGMENT_DEFS", {})

    class _StubConfig:
        is_online = True
        neon_url = "postgres://stub"
        enable_web = False

    monkeypatch.setattr(backend_main, "GenConfig", type("GenConfig", (), {"load": staticmethod(lambda root: _StubConfig())}))

    stub_skeleton = Skeleton(
        market_name=_MARKET,
        geographies=["U.S.", "Germany"],
        regions={"North America": ["U.S."], "Europe": ["Germany"]},
        nesting={"By Installation": {"__root__": ["Flooring", "Wall Cladding"]}},
        priced_dimension="By Installation",
    )
    monkeypatch.setattr(backend_main, "load_skeleton_from_db", lambda market, cfg: (stub_skeleton, None))

    return TestClient(backend_main.app)


# --------------------------------------------------------------------------- #
# Phase 0 -- Base-Year Segment Definition
# --------------------------------------------------------------------------- #
def test_fixed_regions_endpoint_returns_the_five_regions(client):
    res = client.get("/api/segment-definition/regions")
    assert res.status_code == 200
    assert res.json()["regions"] == [
        "North America", "Europe", "Asia Pacific", "Latin America", "Middle East & Africa",
    ]


def test_preview_returns_dimensions_from_the_db_skeleton(client):
    res = client.get("/api/segment-definition/preview", params={"market": _MARKET, "source": "db"})
    assert res.status_code == 200
    body = res.json()
    assert body["dimensions"] == [
        {"dimension_name": "By Installation", "segments": [
            {"name": "Flooring", "region_shares": {}}, {"name": "Wall Cladding", "region_shares": {}},
        ]},
    ]
    assert body["regions"] == ["North America", "Europe", "Asia Pacific", "Latin America", "Middle East & Africa"]


def test_submit_segment_definition_succeeds_and_persists(client):
    res = client.post("/api/segment-definition/submit", json=_VALID_BODY)
    assert res.status_code == 200
    body = res.json()
    assert body["approved"] is True
    assert body["base_year"] == 2025
    assert backend_main._SEGMENT_DEFS[_MARKET]["approved"] is True


def test_submit_segment_definition_rejects_shares_that_dont_reconcile(client):
    bad = {
        "market_name": _MARKET,
        "base_year": 2025,
        "dimensions": [{
            "dimension_name": "By Installation",
            "segments": [
                {"name": "Flooring", "region_shares": {
                    "North America": 60, "Europe": 55, "Asia Pacific": 50,
                    "Latin America": 50, "Middle East & Africa": 50,
                }},
                {"name": "Wall Cladding", "region_shares": {
                    # North America now sums to 60+30=90, outside +/-1% tolerance
                    "North America": 30, "Europe": 45, "Asia Pacific": 50,
                    "Latin America": 50, "Middle East & Africa": 50,
                }},
            ],
        }],
    }
    res = client.post("/api/segment-definition/submit", json=bad)
    assert res.status_code == 422
    assert "North America" in res.json()["detail"]


def test_get_segment_definition_404_when_not_submitted(client):
    assert client.get(f"/api/segment-definition/{_MARKET}").status_code == 404


def test_get_segment_definition_returns_what_was_submitted(client):
    client.post("/api/segment-definition/submit", json=_VALID_BODY)
    res = client.get(f"/api/segment-definition/{_MARKET}")
    assert res.status_code == 200
    assert res.json()["market_name"] == _MARKET


# --------------------------------------------------------------------------- #
# GET /api/generate -- hard-gated on an approved segment definition
# --------------------------------------------------------------------------- #
def test_generate_rejects_when_no_segment_definition_submitted(client):
    res = client.get("/api/generate", params={"market": _MARKET, "source": "db"})
    assert res.status_code == 400
    assert "segment definition" in res.json()["detail"]


def test_generate_rejects_a_different_unsubmitted_market(client):
    client.post("/api/segment-definition/submit", json=_VALID_BODY)
    res = client.get("/api/generate", params={"market": "Some Other Market", "source": "db"})
    assert res.status_code == 400


def test_generate_proceeds_past_the_gate_once_segment_definition_is_approved(client, monkeypatch):
    client.post("/api/segment-definition/submit", json=_VALID_BODY)
    # Stub out the actual generation stream entirely -- this test only
    # proves `generate()`'s hard gate lets the request through once a
    # segment definition is approved; the real LangGraph run is exercised
    # separately by the agents.py LLM-bypass tests.
    monkeypatch.setattr(backend_main, "_run_generation_stream", lambda market, source, choice: iter(
        [{"event": "log", "data": "{\"line\": \"stub\"}"}]
    ))
    with client.stream("GET", "/api/generate", params={"market": _MARKET, "source": "db"}) as res:
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")


# --------------------------------------------------------------------------- #
# Regression: an approved segment definition must let the run proceed even
# when the market has no DB match at all -- the definition's own
# nesting/regions fully replace whatever the DB lookup would have produced,
# so a failed DB lookup is not fatal once one is approved.
# --------------------------------------------------------------------------- #
class _StubGraph:
    """Mimics `build_graph().compile()`'s `.stream()` just enough for
    `_run_generation_stream_inner` to reach its `done` event -- no real
    LangGraph/agents/LLM node runs."""

    def stream(self, init, config=None, stream_mode=None):
        final = {
            **init,
            "reward": 1.0, "iter": 1, "global_anchor_2025": 100.0, "global_cagr": 0.1,
            "regional": {r: {"value_share": 1.0 / 5, "cagr": 0.1} for r in
                         ["North America", "Europe", "Asia Pacific", "Latin America", "Middle East & Africa"]},
            "shares": {"By Installation": {"__global__": {"__root__": {"Flooring": 0.6, "Wall Cladding": 0.4}}}},
            "asp": {}, "artifacts": [], "quality_flags": [], "log": ["stub run complete"],
        }
        yield final


def test_generate_proceeds_when_market_has_no_db_match_but_segments_are_approved(client, monkeypatch, tmp_path):
    unmatched_market = "A Market The DB Has Never Heard Of"
    # Simulate the real "no DB segmentation found" failure mode -- see
    # `dynamic_skeleton.load_skeleton_from_db`'s contract: `(None, None)`.
    monkeypatch.setattr(backend_main, "load_skeleton_from_db", lambda market, cfg: (None, None))
    monkeypatch.setattr(backend_main, "fetch_market_evidence", lambda neon_url, market: None)
    monkeypatch.setattr(backend_main, "GenLLM", lambda cfg, cache_path=None: object())
    monkeypatch.setattr(backend_main, "build_graph", lambda: _StubGraph())
    monkeypatch.setattr(backend_main, "write_input_sheet", lambda state, out_path, flat=False: out_path)
    monkeypatch.setattr(backend_main, "OUT_DIR", tmp_path)
    monkeypatch.setattr(backend_main, "HISTORY_PATH", tmp_path / "history.jsonl")

    body = {**_VALID_BODY, "market_name": unmatched_market}
    submit = client.post("/api/segment-definition/submit", json=body)
    assert submit.status_code == 200

    with client.stream("GET", "/api/generate", params={"market": unmatched_market, "source": "db"}) as res:
        assert res.status_code == 200
        events = list(res.iter_lines())

    body_text = "\n".join(events)
    # No error event at all -- the missing DB match is informational (a log
    # line), never fatal, once a segment definition is approved.
    assert "event: error" not in body_text
    assert "proceeding with your approved segment definition only" in body_text
    assert "event: done" in body_text


# --------------------------------------------------------------------------- #
# The preview endpoint must never hard-fail for a market genuinely absent
# from the DB -- the whole point of the gate is to support markets the DB
# has never seen.
# --------------------------------------------------------------------------- #
def test_preview_returns_empty_dimensions_instead_of_404_for_an_unmatched_db_market(client, monkeypatch):
    monkeypatch.setattr(backend_main, "fetch_market_evidence", lambda neon_url, market: None)
    monkeypatch.setattr(backend_main, "load_skeleton_from_db", lambda market, cfg: (None, None))
    res = client.get("/api/segment-definition/preview", params={"market": "Nowhere In The DB", "source": "db"})
    assert res.status_code == 200
    body = res.json()
    assert body["dimensions"] == []
    assert body["regions"] == ["North America", "Europe", "Asia Pacific", "Latin America", "Middle East & Africa"]


def test_preview_never_surfaces_a_geography_tree_from_an_unrelated_nearest_neighbor_match(client, monkeypatch):
    """The nearest-neighbor DB fallback can land on a completely unrelated
    report -- if what it returns is literally that report's own REGIONS
    geography tree, it must never be shown as if it were real segmentation
    the user could unknowingly approve."""
    monkeypatch.setattr(backend_main, "fetch_market_evidence", lambda neon_url, market: None)
    geo_skeleton = Skeleton(
        market_name="Some Unrelated Report",
        geographies=["U.S.", "Germany", "Brazil"],
        regions={"North America": ["U.S."], "Europe": ["Germany"], "Latin America": ["Brazil"]},
        nesting={"By Region": {"__root__": [
            "North America", "Europe", "Asia Pacific", "Latin America", "Middle East & Africa",
        ]}},
        priced_dimension="By Region",
    )
    monkeypatch.setattr(backend_main, "load_skeleton_from_db", lambda market, cfg: (geo_skeleton, None))

    res = client.get("/api/segment-definition/preview", params={"market": "A Market With No Real DB Match", "source": "db"})
    assert res.status_code == 200
    body = res.json()
    assert body["dimensions"] == []


def test_preview_keeps_real_segmentation_dimensions_alongside_a_filtered_geography_one(client, monkeypatch):
    """A mix of a real segmentation dimension and a geography-look-alike one
    -- only the geography one is dropped."""
    monkeypatch.setattr(backend_main, "fetch_market_evidence", lambda neon_url, market: None)
    mixed_skeleton = Skeleton(
        market_name="Some Unrelated Report",
        geographies=["U.S.", "Germany", "Brazil"],
        regions={"North America": ["U.S."], "Europe": ["Germany"], "Latin America": ["Brazil"]},
        nesting={
            "By Installation": {"__root__": ["Flooring", "Wall Cladding"]},
            "By Region": {"__root__": ["North America", "Europe", "Asia Pacific", "Latin America", "Middle East & Africa"]},
        },
        priced_dimension="By Installation",
    )
    monkeypatch.setattr(backend_main, "load_skeleton_from_db", lambda market, cfg: (mixed_skeleton, None))

    res = client.get("/api/segment-definition/preview", params={"market": "A Market With No Real DB Match", "source": "db"})
    assert res.status_code == 200
    body = res.json()
    assert len(body["dimensions"]) == 1
    assert body["dimensions"][0]["dimension_name"] == "By Installation"


def test_preview_drops_by_middle_east_and_by_africa_on_the_nearest_neighbor_fallback_path(client, monkeypatch):
    """Regression: live case found while re-testing the previous round's
    fix -- standalone sub-region dimensions ("By Middle East", "By Africa")
    with country children not in `skeleton.REGIONS`'s coarse template list
    must still be filtered, not just the combined "By Region" case."""
    monkeypatch.setattr(backend_main, "fetch_market_evidence", lambda neon_url, market: None)
    geo_skeleton = Skeleton(
        market_name="Some Unrelated Report",
        geographies=["U.S.", "Germany"],
        regions={"North America": ["U.S."], "Europe": ["Germany"]},
        nesting={
            "By Middle East": {"__root__": ["GCC Countries", "Israel", "Rest of Middle East"]},
            "By Africa": {"__root__": ["South Africa", "North Africa", "Central Africa"]},
        },
        priced_dimension="By Middle East",
    )
    monkeypatch.setattr(backend_main, "load_skeleton_from_db", lambda market, cfg: (geo_skeleton, None))

    res = client.get("/api/segment-definition/preview", params={"market": "A Market With No Real DB Match", "source": "db"})
    assert res.status_code == 200
    assert res.json()["dimensions"] == []

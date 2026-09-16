"""Phase 1 (Market Setup Draft) tests -- the FIRST user approval gate.

No real LLM/network calls: `market_setup_draft_node` only touches
`state["llm"].complete_json(...)` and `state["config"].enable_web`, so a
duck-typed stub stands in for both (same style as `test_units_contract.py`'s
fabricated state for `calculate_node`).

Run with: pytest tests/test_demand_usage_setup.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from me_engine.demand_usage import setup


class _StubLLM:
    def __init__(self, response: dict):
        self.response = response
        self.calls = 0

    def complete_json(self, system, user):
        self.calls += 1
        return self.response


class _StubConfig:
    enable_web = False


def _draft_state(response: dict) -> dict:
    return {"market_name": "Crude Oil", "config": _StubConfig(), "llm": _StubLLM(response), "log": []}


# --------------------------------------------------------------------------- #
# market_setup_draft_node -- LLM proposes, every field stays editable
# --------------------------------------------------------------------------- #
def test_draft_node_returns_every_required_field_from_llm_response():
    response = {
        "scope": "Crude oil sold/consumed as feedstock and fuel input; excludes refined products.",
        "geography": ["United States", "China", "Rest of World"],
        "base_year": 2025,
        "market_unit": "USD Mn",
        "physical_unit": "Barrels",
        "grounded": True,
    }
    out = setup.market_setup_draft_node(_draft_state(response))
    assert out["market_name"] == "Crude Oil"
    assert out["scope"] == response["scope"]
    assert out["geography"] == response["geography"]
    assert out["base_year"] == 2025
    assert out["market_unit"] == "USD Mn"
    assert out["physical_unit"] == "Barrels"
    assert out["grounded"] is True


def test_draft_node_falls_back_safely_when_llm_returns_nothing_usable():
    out = setup.market_setup_draft_node(_draft_state({}))
    assert out["geography"]  # non-empty fallback list, never crashes
    assert isinstance(out["base_year"], int)
    assert out["market_unit"] == "USD Mn"
    assert out["physical_unit"] == "Units"
    assert out["grounded"] is False


def test_draft_node_survives_llm_call_failure():
    class _ThrowingLLM:
        def complete_json(self, system, user):
            raise RuntimeError("network down")

    state = {"market_name": "Crude Oil", "config": _StubConfig(), "llm": _ThrowingLLM(), "log": []}
    out = setup.market_setup_draft_node(state)
    assert out["scope"]  # still produces a usable draft, never raises
    assert any("LLM call failed" in line for line in out["log"])


# --------------------------------------------------------------------------- #
# approve_setup -- the actual gate: incomplete/invalid drafts must be rejected
# --------------------------------------------------------------------------- #
def _valid_draft(**overrides) -> dict:
    base = {
        "market_name": "Crude Oil",
        "scope": "Some scope",
        "geography": ["United States", "China"],
        "base_year": 2025,
        "market_unit": "USD Mn",
        "physical_unit": "Barrels",
        "grounded": True,
    }
    base.update(overrides)
    return base


def test_approve_setup_writes_checkpoint_and_returns_normalized_dict(tmp_path):
    approved = setup.approve_setup(_valid_draft(), root=tmp_path)
    assert approved["approved"] is True
    assert approved["geography"] == ["United States", "China"]
    assert setup.load_approved_setup("Crude Oil", root=tmp_path) == approved


def test_approve_setup_rejects_missing_market_name(tmp_path):
    with pytest.raises(ValueError):
        setup.approve_setup(_valid_draft(market_name=""), root=tmp_path)


def test_approve_setup_rejects_empty_geography(tmp_path):
    with pytest.raises(ValueError):
        setup.approve_setup(_valid_draft(geography=[]), root=tmp_path)


def test_approve_setup_rejects_non_integer_base_year(tmp_path):
    with pytest.raises(ValueError):
        setup.approve_setup(_valid_draft(base_year="not a year"), root=tmp_path)


def test_load_approved_setup_raises_when_never_approved(tmp_path):
    with pytest.raises(FileNotFoundError):
        setup.load_approved_setup("Never Approved Market", root=tmp_path)


def test_user_edits_to_draft_survive_into_the_approved_checkpoint(tmp_path):
    """The whole point of the gate: a human-edited value (not the LLM's
    original proposal) is what gets locked in and read downstream."""
    draft = _valid_draft(physical_unit="Barrels", base_year=2025)
    edited = {**draft, "physical_unit": "Tonnes", "base_year": 2024}  # user overrides
    approved = setup.approve_setup(edited, root=tmp_path)
    assert approved["physical_unit"] == "Tonnes"
    assert approved["base_year"] == 2024


# --------------------------------------------------------------------------- #
# Additive audit instrumentation -- optional emitter, never load-bearing
# --------------------------------------------------------------------------- #
class _StubEmitter:
    def __init__(self):
        self.events: list[dict] = []

    def emit(self, stage, event_type, **kwargs):
        event = {"stage": stage, "event_type": event_type, **kwargs}
        self.events.append(event)
        return event


def test_run_setup_phase_emits_stage_start_llm_call_and_stage_end_with_real_content(tmp_path, monkeypatch):
    response = {
        "scope": "Crude oil sold/consumed as feedstock and fuel input; excludes refined products.",
        "geography": ["United States", "China", "Rest of World"],
        "base_year": 2025,
        "market_unit": "USD Mn",
        "physical_unit": "Barrels",
        "grounded": True,
    }

    captured: dict = {}

    class _StubAuditEmitter(_StubEmitter):
        def __init__(self, run_id, approach, market, root=None):
            super().__init__()
            captured["run_id"] = run_id
            captured["approach"] = approach
            captured["market"] = market

    import me_engine.audit.store as audit_store

    monkeypatch.setattr(audit_store, "AuditEmitter", _StubAuditEmitter)

    class _StubGenLLM:
        def __init__(self, config, cache_path=None):
            pass

        def complete_json(self, system, user):
            return response

    monkeypatch.setattr(setup, "GenLLM", _StubGenLLM)

    draft = setup.run_setup_phase("Crude Oil", config=_StubConfig(), cache_path=None, root=tmp_path)

    assert draft["scope"] == response["scope"]
    # run_id must be the market name itself for this pipeline (per graph.py's
    # run_sizing_phase convention).
    assert captured["run_id"] == "Crude Oil"
    assert captured["approach"] == "usage_based"
    assert captured["market"] == "Crude Oil"


def test_market_setup_draft_node_emits_events_via_stub_emitter():
    response = {
        "scope": "Some scope",
        "geography": ["United States"],
        "base_year": 2025,
        "market_unit": "USD Mn",
        "physical_unit": "Barrels",
        "grounded": True,
    }
    emitter = _StubEmitter()
    state = {
        "market_name": "Crude Oil",
        "config": _StubConfig(),
        "llm": _StubLLM(response),
        "log": [],
        "emitter": emitter,
    }
    setup.market_setup_draft_node(state)

    event_types = [e["event_type"] for e in emitter.events]
    assert event_types == ["stage_start", "llm_call", "stage_end"]
    assert all(e["stage"] == "setup" for e in emitter.events)

    llm_event = emitter.events[1]
    assert "Crude Oil" in llm_event["llm"]["prompt"]
    assert llm_event["llm"]["error"] is None
    assert "Some scope" in llm_event["llm"]["response"]
    assert llm_event["status"] == "success"

    stage_end_event = emitter.events[2]
    assert stage_end_event["output_data"]["scope"] == "Some scope"
    assert stage_end_event["output_data"]["base_year"] == 2025
    assert stage_end_event["output_data"]["market_unit"] == "USD Mn"
    assert stage_end_event["output_data"]["physical_unit"] == "Barrels"
    assert stage_end_event["output_data"]["grounded"] is True


def test_market_setup_draft_node_emits_failed_llm_call_on_exception():
    class _ThrowingLLM:
        def complete_json(self, system, user):
            raise RuntimeError("network down")

    emitter = _StubEmitter()
    state = {
        "market_name": "Crude Oil",
        "config": _StubConfig(),
        "llm": _ThrowingLLM(),
        "log": [],
        "emitter": emitter,
    }
    setup.market_setup_draft_node(state)

    llm_event = next(e for e in emitter.events if e["event_type"] == "llm_call")
    assert llm_event["status"] == "failed"
    assert "network down" in llm_event["llm"]["error"]


def test_approve_setup_stamps_approved_at_and_approved_by(tmp_path):
    approved = setup.approve_setup(_valid_draft(), root=tmp_path, approved_by="rajeev@coherentmarketinsights.com")
    assert approved["approved_by"] == "rajeev@coherentmarketinsights.com"
    assert approved["approved_at"]  # real UTC timestamp, non-empty


def test_approve_setup_defaults_approved_by_to_none_when_not_supplied(tmp_path):
    approved = setup.approve_setup(_valid_draft(), root=tmp_path)
    assert approved["approved_by"] is None
    assert approved["approved_at"]

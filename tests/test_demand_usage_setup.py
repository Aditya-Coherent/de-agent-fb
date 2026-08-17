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

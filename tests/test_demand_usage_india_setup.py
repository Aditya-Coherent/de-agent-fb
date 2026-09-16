"""Phase 1 (India-locked Market Setup Draft) tests -- the FIRST user approval
gate for the India-only variant.

Same duck-typed stub pattern as `tests/test_demand_usage_setup.py`; the only
India-specific behavior under test is that `geography` is ALWAYS forced to
`["India"]`, regardless of what the (reused, unchanged)
`market_setup_draft_node` LLM call proposes.

Run with: pytest tests/test_demand_usage_india_setup.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from me_engine.demand_usage_india import setup


class _StubLLM:
    def __init__(self, response: dict):
        self.response = response

    def complete_json(self, system, user):
        return self.response


class _StubConfig:
    enable_web = False


def test_run_setup_phase_forces_geography_to_india_regardless_of_llm_output(tmp_path, monkeypatch):
    response = {
        "scope": "Vinegar sold/consumed as a food-grade acidulant and preservative.",
        # LLM proposes a multi-country list -- must be overridden anyway.
        "geography": ["United States", "China", "Germany", "Rest of World"],
        "base_year": 2025,
        "market_unit": "USD Mn",
        "physical_unit": "Tonnes",
        "grounded": True,
    }

    class _StubGenLLM:
        def __init__(self, config, cache_path=None):
            pass

        def complete_json(self, system, user):
            return response

    monkeypatch.setattr(setup, "GenLLM", _StubGenLLM)

    draft = setup.run_setup_phase("Vinegar", config=_StubConfig(), cache_path=None, root=tmp_path)

    assert draft["geography"] == ["India"]
    assert draft["scope"] == response["scope"]
    assert any("India" in line for line in draft["log"])


def test_run_setup_phase_leaves_india_untouched_when_llm_already_proposed_it(tmp_path, monkeypatch):
    response = {
        "scope": "s", "geography": ["India"], "base_year": 2025,
        "market_unit": "USD Mn", "physical_unit": "Tonnes", "grounded": True,
    }

    class _StubGenLLM:
        def __init__(self, config, cache_path=None):
            pass

        def complete_json(self, system, user):
            return response

    monkeypatch.setattr(setup, "GenLLM", _StubGenLLM)
    draft = setup.run_setup_phase("Vinegar", config=_StubConfig(), cache_path=None, root=tmp_path)
    assert draft["geography"] == ["India"]


def _valid_draft(**overrides) -> dict:
    base = {
        "market_name": "Vinegar",
        "scope": "Some scope",
        "geography": ["United States", "China"],  # deliberately not India -- must still be forced
        "base_year": 2025,
        "market_unit": "USD Mn",
        "physical_unit": "Tonnes",
        "grounded": True,
    }
    base.update(overrides)
    return base


def test_approve_setup_forces_geography_to_india_even_if_edited_draft_says_otherwise(tmp_path):
    approved = setup.approve_setup(_valid_draft(), root=tmp_path)
    assert approved["approved"] is True
    assert approved["geography"] == ["India"]
    assert setup.load_approved_setup("Vinegar", root=tmp_path) == approved


def test_approve_setup_rejects_missing_market_name(tmp_path):
    with pytest.raises(ValueError):
        setup.approve_setup(_valid_draft(market_name=""), root=tmp_path)


def test_approve_setup_rejects_non_integer_base_year(tmp_path):
    with pytest.raises(ValueError):
        setup.approve_setup(_valid_draft(base_year="not a year"), root=tmp_path)


def test_load_approved_setup_raises_when_never_approved(tmp_path):
    with pytest.raises(FileNotFoundError):
        setup.load_approved_setup("Never Approved Market", root=tmp_path)


def test_approve_setup_stamps_approved_at_and_approved_by(tmp_path):
    approved = setup.approve_setup(_valid_draft(), root=tmp_path, approved_by="rajeev@coherentmarketinsights.com")
    assert approved["approved_by"] == "rajeev@coherentmarketinsights.com"
    assert approved["approved_at"]

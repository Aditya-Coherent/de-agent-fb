"""Confirms `agents.py::_shares_global` / `agents.py::shares_node` REPLACE
their own LLM segmentation-share call with the user's approved base-year
segment definition, for any dimension it covers -- never running both.

No real LLM/network call: a stub LLM whose `complete_json` raises if it is
ever invoked proves the call path was skipped entirely (not just ignored
after the fact).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from me_engine.generation import agents, segment_definition  # noqa: E402
from me_engine.generation.skeleton import Skeleton  # noqa: E402


class _ExplodingLLM:
    """Raises if called -- proves the segmentation-share LLM call was
    skipped for a user-defined dimension, not merely overwritten after."""

    def complete_json(self, system, user):
        raise AssertionError("LLM must not be called for a user-defined dimension")


class _StubConfig:
    per_region_shares = False
    enable_web = False


def _skeleton_with_one_dimension() -> Skeleton:
    return Skeleton(
        market_name="Global Process Analyzer Market",
        geographies=["U.S.", "Germany"],
        regions={
            "North America": ["U.S."], "Europe": ["Germany"], "Asia Pacific": [],
            "Latin America": [], "Middle East & Africa": [],
        },
        nesting={"By Installation": {"__root__": ["Flooring", "Wall Cladding"]}},
        priced_dimension="By Installation",
    )


def _approved_definition() -> dict:
    return segment_definition.validate_segment_definition({
        "market_name": "Global Process Analyzer Market",
        "base_year": 2025,
        "dimensions": [{
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
        }],
    })


def test_shares_node_global_mode_bypasses_the_llm_for_a_user_defined_dimension():
    sk = _skeleton_with_one_dimension()
    state = {
        "skeleton": sk, "llm": _ExplodingLLM(), "config": _StubConfig(),
        "segment_definition": _approved_definition(), "log": [],
    }
    out = agents.shares_node(state)
    shares = out["shares"]["By Installation"]
    assert shares["North America"]["__root__"]["Flooring"] == 0.6
    assert shares["North America"]["__root__"]["Wall Cladding"] == 0.4
    assert shares["Europe"]["__root__"]["Flooring"] == 0.55


def test_shares_node_per_region_mode_bypasses_the_llm_for_a_user_defined_dimension():
    sk = _skeleton_with_one_dimension()

    class _PerRegionConfig(_StubConfig):
        per_region_shares = True

    state = {
        "skeleton": sk, "llm": _ExplodingLLM(), "config": _PerRegionConfig(),
        "segment_definition": _approved_definition(), "log": [],
    }
    out = agents.shares_node(state)
    shares = out["shares"]["By Installation"]
    assert shares["North America"]["__root__"]["Flooring"] == 0.6
    assert shares["Asia Pacific"]["__root__"]["Flooring"] == 0.5


def test_shares_node_still_calls_the_llm_when_no_segment_definition_is_present():
    """Sanity check: the bypass is opt-in per run, not a permanent change to
    existing (non-gated) behavior."""
    sk = _skeleton_with_one_dimension()

    class _StubLLM:
        def __init__(self):
            self.calls = 0

        def complete_json(self, system, user):
            self.calls += 1
            return {"__root__": {"Flooring": 0.7, "Wall Cladding": 0.3}}

    llm = _StubLLM()
    state = {"skeleton": sk, "llm": llm, "config": _StubConfig(), "log": []}
    agents.shares_node(state)
    assert llm.calls == 1

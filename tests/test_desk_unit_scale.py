"""Desk Research market_size_node: billions-vs-millions guard.

Regression for the Olive Oil example run (2026-10-03): the LLM returned
global_value_2025 = 16 (meaning USD 16 billion) with no cited figure, so the
cited-figure check never ran and the sheet said $16M.
"""
from __future__ import annotations

import pytest

from me_engine.generation import agents
from me_engine.generation.skeleton import Skeleton


def test_fix_unit_scale_rescales_a_value_given_in_billions():
    value, note = agents._fix_unit_scale(16.5, [14_000.0, 15_900.0, 18_000.0])
    assert value == pytest.approx(16_500.0)
    assert "billions" in note


def test_fix_unit_scale_rescales_a_value_1000x_too_large():
    value, note = agents._fix_unit_scale(16_500_000.0, [15_900.0])
    assert value == pytest.approx(16_500.0)
    assert note


@pytest.mark.parametrize("anchor", [5_000.0, 40_000.0, 160.0])
def test_fix_unit_scale_leaves_real_estimation_gaps_alone(anchor):
    # 3x, 2.5x and 100x gaps are genuine disagreements, not unit slips.
    value, note = agents._fix_unit_scale(anchor, [16_000.0])
    assert value == anchor and note == ""


def test_fix_unit_scale_without_evidence_is_a_no_op():
    assert agents._fix_unit_scale(16.5, []) == (16.5, "")


def test_evidence_values_parsed_from_snippets():
    state = {"web_snippets": [
        "Olive Oil Market size was valued at USD 15.9 billion in 2024.",
        "Extra virgin segment reached $900 million.",
        "No figure here.",
    ]}
    assert sorted(agents._evidence_values_mn(state)) == [900.0, 15_900.0]


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.user = ""

    def complete_json(self, system, user, *, retries=3):
        self.user = user
        return self.payload


def _state(llm):
    sk = Skeleton(market_name="Olive Oil", geographies=["Europe"], regions={"Europe": ["Spain"]},
                  nesting={}, priced_dimension="")
    return {
        "config": object(), "skeleton": sk, "llm": llm, "log": [],
        "web_snippets": ["Olive Oil Market size was valued at USD 15.9 billion in 2024 and will grow 4.6%."],
    }


def test_market_size_node_rescales_ungrounded_billions_answer():
    llm = _FakeLLM({"global_value_2025": 16.5, "global_cagr": 0.046, "grounded": False, "cited_figure": "",
                    "regions": {"Europe": {"value_share": 1.0, "cagr": 0.04}}})
    out = agents.market_size_node(_state(llm))
    assert out["global_anchor_2025"] == pytest.approx(16_500.0)
    assert "US$ MILLIONS" in llm.user


def test_market_size_node_keeps_a_correct_millions_answer():
    llm = _FakeLLM({"global_value_2025": 16_500.0, "global_cagr": 0.046, "grounded": False, "cited_figure": "",
                    "regions": {"Europe": {"value_share": 1.0, "cagr": 0.04}}})
    out = agents.market_size_node(_state(llm))
    assert out["global_anchor_2025"] == pytest.approx(16_500.0)

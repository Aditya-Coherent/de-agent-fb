"""Desk Research best-pass selection (agents.select_best_pass / reward_node / finalize_node).

Regression for the Televisions example run: four ungrounded passes gave
7.8 Bn, 258 Bn, 245 Bn and 245 Bn, and the reviewer kept 7.8 Bn because it
scored 64% vs 60%. All LLM calls are fakes.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from me_engine.generation import agents
from me_engine.generation.quality import compute_demand_quality_flags
from me_engine.generation.skeleton import Skeleton


def _pass(i, anchor, reward, grounded):
    return {"iter": i, "reward": reward, "grounded": grounded, "global_anchor_2025": anchor,
            "snapshot": {"global_anchor_2025": anchor, "market_size_grounded": grounded, "regional": {}}}


def test_grounded_pass_beats_higher_scoring_ungrounded_pass():
    history = [_pass(1, 50_000, 0.95, False), _pass(2, 64_000, 0.60, True), _pass(3, 61_000, 0.90, False)]
    assert agents.select_best_pass(history)["index"] == 1


def test_reward_breaks_ties_within_grounded_group():
    history = [_pass(1, 64_000, 0.70, True), _pass(2, 64_000, 0.80, True), _pass(3, 9_000, 0.99, False)]
    assert agents.select_best_pass(history)["index"] == 1


def test_stable_ungrounded_passes_use_reward():
    history = [_pass(1, 15_000, 0.80, False), _pass(2, 16_500, 0.88, False), _pass(3, 12_500, 0.82, False)]
    choice = agents.select_best_pass(history)
    assert choice == {"index": 1, "anchor_override": None, "unstable": False, "values": [15_000, 16_500, 12_500]}


def test_unstable_ungrounded_values_never_pick_the_outlier():
    # Televisions: the 7.8 Bn pass had the highest reward.
    history = [_pass(1, 7_800, 0.64, False), _pass(2, 258_000, 0.60, False),
               _pass(3, 245_000, 0.53, False), _pass(4, 245_000, 0.60, False)]
    choice = agents.select_best_pass(history)
    assert choice["unstable"] is True
    assert choice["index"] == 3            # median 245,000; best-scoring pass at the median
    assert choice["anchor_override"] is None


def test_unstable_median_between_passes_overrides_the_value():
    history = [_pass(1, 1_000, 0.9, False), _pass(2, 5_000, 0.5, False)]
    choice = agents.select_best_pass(history)
    assert choice["unstable"] and choice["anchor_override"] == pytest.approx(3_000.0)


def test_finalize_uses_median_and_flags_unstable_market_size():
    history = [_pass(1, 7_800, 0.64, False), _pass(2, 258_000, 0.60, False),
               _pass(3, 245_000, 0.53, False), _pass(4, 230_000, 0.60, False)]
    state = {"pass_history": history, "reward": 0.60, "log": [], "web_snippets": ["x"]}
    out = agents.finalize_node(state)
    assert out["global_anchor_2025"] == pytest.approx(237_500.0)
    codes = [f["code"] for f in out["quality_flags"]]
    assert "market_size_unstable" in codes and "ungrounded_market_size" in codes
    assert any("unstable across passes" in line for line in state["log"])


def test_finalize_restores_grounded_pass_and_logs_why():
    history = [_pass(1, 64_000, 0.60, True), _pass(2, 7_800, 0.80, False)]
    state = {"pass_history": history, "reward": 0.80, "log": []}
    out = agents.finalize_node(state)
    assert out["global_anchor_2025"] == 64_000 and out["market_size_grounded"] is True
    assert any("backed by a cited figure" in line for line in state["log"])


def test_quality_flag_for_unstable_market_size():
    flags = compute_demand_quality_flags({"market_size_unstable": {"values": [7_800, 245_000], "median": 126_400}})
    assert [f["code"] for f in flags if f["code"] == "market_size_unstable"]


class _ReviewerLLM:
    def complete_json(self, system, user, *, retries=3):
        return {"market_size": {"score": 0.9, "critique": ""}, "asp": {"score": 0.9, "critique": ""},
                "dimensions": {}, "regional_critique": ""}


def _reward_state(grounded):
    sk = Skeleton(market_name="Televisions", geographies=["U.S."], regions={"North America": ["U.S."]},
                  nesting={}, priced_dimension="")
    return {"config": SimpleNamespace(per_region_shares=False), "skeleton": sk, "llm": _ReviewerLLM(),
            "log": [], "regional": {"North America": {"value_share": 1.0, "cagr": 0.03}},
            "global_anchor_2025": 64_000.0, "market_size_grounded": grounded}


def test_reward_penalises_an_ungrounded_market_size():
    grounded = agents.reward_node(_reward_state(True))
    ungrounded = agents.reward_node(_reward_state(False))
    assert grounded["reward"] - ungrounded["reward"] == pytest.approx(agents.UNGROUNDED_PENALTY)
    assert "not backed by any cited figure" in ungrounded["market_critique"]


def test_reward_node_records_every_pass_and_keeps_grounded_best():
    state = _reward_state(True)
    first = agents.reward_node(state)
    state2 = {**_reward_state(False), "pass_history": first["pass_history"], "iter": 1}
    second = agents.reward_node(state2)
    assert [p["grounded"] for p in second["pass_history"]] == [True, False]
    assert second["best_state"]["market_size_grounded"] is True

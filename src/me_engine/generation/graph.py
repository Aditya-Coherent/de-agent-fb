"""LangGraph StateGraph for the synthetic Input-Sheet generator.

Flow:
    structure -> market_size -> shares -> reward -> (loop back to shares | asp)
    asp -> brain -> END

The reward -> shares edge is the evidence-refinement ("RL") loop: the reward
signal + critique feed back into the segmentation policy until the reward clears
the threshold or the iteration budget is spent.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from . import agents
from .config import GenConfig
from .llm import GenLLM
from .skeleton import Skeleton


class GenState(TypedDict, total=False):
    market_name: str
    skeleton: Skeleton
    config: GenConfig
    llm: GenLLM
    db_evidence: Any
    web_snippets: list[str]
    regional: dict[str, dict[str, float]]
    global_anchor_2026: float
    global_cagr: float
    geo: dict[str, dict[str, dict[str, float]]]
    shares: dict[str, dict[str, dict[str, float]]]
    asp: dict[str, float]
    artifacts: list[dict[str, Any]]
    reward: float
    critique: str
    dim_critique: dict[str, str]
    best_reward: float
    best_shares: dict[str, Any]
    iter: int
    log: list[str]


def _should_refine(state: GenState) -> str:
    cfg: GenConfig = state["config"]
    reward = float(state.get("reward", 0.0))
    it = int(state.get("iter", 0))
    if it >= cfg.max_loop_iters:
        return "accept"
    if it < cfg.min_loop_iters:
        return "refine"
    return "accept" if reward >= cfg.reward_threshold else "refine"


def build_graph():
    g = StateGraph(GenState)
    g.add_node("structure", agents.structure_node)
    g.add_node("market_size", agents.market_size_node)
    g.add_node("geo", agents.geo_node)
    g.add_node("shares", agents.shares_node)
    g.add_node("reward", agents.reward_node)
    g.add_node("finalize", agents.finalize_node)
    g.add_node("asp", agents.asp_node)
    g.add_node("brain", agents.brain_node)

    g.set_entry_point("structure")
    g.add_edge("structure", "market_size")
    g.add_edge("market_size", "geo")
    g.add_edge("geo", "shares")
    g.add_edge("shares", "reward")
    g.add_conditional_edges("reward", _should_refine, {"refine": "shares", "accept": "finalize"})
    g.add_edge("finalize", "asp")
    g.add_edge("asp", "brain")
    g.add_edge("brain", END)
    return g.compile()


def run_generation(market_name: str, skeleton: Skeleton, config: GenConfig,
                   cache_path: Path | None = None) -> GenState:
    llm = GenLLM(config, cache_path=cache_path)
    graph = build_graph()
    init: GenState = {
        "market_name": market_name,
        "skeleton": skeleton,
        "config": config,
        "llm": llm,
        "iter": 0,
        "log": [],
    }
    # recursion_limit must exceed nodes * loop iterations
    final = graph.invoke(init, config={"recursion_limit": 50})
    return final

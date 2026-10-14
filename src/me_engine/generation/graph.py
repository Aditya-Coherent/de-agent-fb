"""LangGraph StateGraph for the synthetic Input-Sheet generator.

Flow:
    structure -> market_size -> geo -> shares -> asp -> reward
                     ^                                    |
                     +---------- refine (loop back) -------+
    reward -> (accept) -> finalize -> brain -> END

The reward -> market_size edge is the evidence-refinement ("RL") loop: the
reward signal + per-family critique (shares, market size/regional, asp) feed
back into all four decision nodes for another attempt, until the reward clears
the threshold or the iteration budget is spent. finalize restores whichever
full iteration scored best, not just the last one.
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
    dim_snippets: dict[str, list[str]]
    # Desk Research Phase 0 gate (segment_definition.py) -- when present,
    # `shares_node`/`_shares_global` (agents.py) use it verbatim for every
    # dimension it covers instead of calling the LLM for that dimension.
    segment_definition: dict[str, Any]
    regional: dict[str, dict[str, float]]
    global_anchor_2025: float
    global_cagr: float
    market_size_grounded: bool
    # scope.py: which end-use segments the cited figure covers, and the
    # segment market sizes added for the rest (cached per cited text)
    scope_reconciliation: dict[str, Any] | None
    geo: dict[str, dict[str, dict[str, float]]]
    shares: dict[str, dict[str, dict[str, float]]]
    # evidence trail per decision: numbered evidence items + the LLM's reasons
    shares_trace: dict[str, Any]
    market_trace: dict[str, Any]
    geo_trace: dict[str, Any]
    asp_trace: dict[str, Any]
    asp: dict[str, float]
    artifacts: list[dict[str, Any]]
    reward: float
    critique: str
    dim_critique: dict[str, str]
    market_critique: str
    asp_critique: str
    dim_score: dict[str, float]
    market_score: float | None
    asp_score: float | None
    best_reward: float
    best_state: dict[str, Any]
    # Market-size grounding. These keys must be declared here: LangGraph drops
    # any node update whose key is not in the state schema (market_size_grounded
    # and quality_flags were silently lost before they were listed).
    market_definition: str
    market_search_name: str
    evidence_sources: list[list[str]]
    web_sources: list[dict[str, Any]]
    cited_anchor: dict[str, Any]
    market_size_grounded: bool
    market_size_source: str
    market_size_unstable: dict[str, Any]
    pass_history: list[dict[str, Any]]
    quality_flags: list[dict[str, str]]
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
    g.add_edge("shares", "asp")
    g.add_edge("asp", "reward")
    g.add_conditional_edges("reward", _should_refine, {"refine": "market_size", "accept": "finalize"})
    g.add_edge("finalize", "brain")
    g.add_edge("brain", END)
    return g.compile()


def run_generation(market_name: str, skeleton: Skeleton, config: GenConfig,
                   cache_path: Path | None = None, market_definition: str = "",
                   market_search_name: str = "") -> GenState:
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
    if market_definition:
        init["market_definition"] = market_definition
    if market_search_name:
        init["market_search_name"] = market_search_name
    # recursion_limit must exceed nodes * loop iterations (5 nodes/iteration now
    # that market_size/geo/asp are refined too, not just shares)
    final = graph.invoke(init, config={"recursion_limit": 80})
    return final

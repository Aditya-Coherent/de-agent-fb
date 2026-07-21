"""Agent node functions for the synthetic Input-Sheet generator.

Every agent emits *judgements* (shares, growth rates, prices) grounded in DB +
web evidence. Deterministic helpers normalise vectors and expand base vectors to
per-country columns. A brain agent attaches justification artifacts. A reward
agent scores the number set and drives the refinement loop.
"""
from __future__ import annotations

import json
from typing import Any

from .db_source import DBEvidence, fetch_market_evidence
from .llm import GenLLM
from .skeleton import Skeleton
from .web_evidence import gather


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _normalize(vector: dict[str, float]) -> dict[str, float]:
    clean = {k: max(0.0, float(v)) for k, v in vector.items()}
    total = sum(clean.values())
    if total <= 0:
        n = len(clean) or 1
        return {k: round(1.0 / n, 4) for k in clean}
    out = {k: round(v / total, 4) for k, v in clean.items()}
    # fix rounding residue on the largest element
    residue = round(1.0 - sum(out.values()), 4)
    if out:
        top = max(out, key=out.get)
        out[top] = round(out[top] + residue, 4)
    return out


def _evidence_brief(state: dict[str, Any]) -> str:
    db: DBEvidence | None = state.get("db_evidence")
    parts: list[str] = []
    if db is not None:
        if db.raw_narrative:
            parts.append("DB NARRATIVE (market size / drivers):\n" + db.raw_narrative[:2200])
        if db.segmentation_tree:
            tree = "\n".join("  " * int(l.split("|", 1)[0]) + l.split("|", 1)[1] for l in db.segmentation_tree[:60])
            parts.append("DB SEGMENTATION TREE:\n" + tree)
    snippets = state.get("web_snippets") or []
    if snippets:
        parts.append("WEB EVIDENCE:\n" + "\n".join(f"- {s}" for s in snippets[:10]))
    return "\n\n".join(parts) if parts else "(no external evidence available)"


# --------------------------------------------------------------------------- #
# nodes
# --------------------------------------------------------------------------- #
def structure_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg = state["config"]
    sk: Skeleton = state["skeleton"]
    db_ev = None
    if cfg.neon_url:
        try:
            db_ev = fetch_market_evidence(cfg.neon_url, sk.market_name)
        except Exception as exc:  # noqa: BLE001
            state.setdefault("log", []).append(f"DB fetch failed: {exc}")
    snippets: list[str] = []
    if cfg.enable_web:
        queries = [
            f"{sk.market_name} size 2026 2033 CAGR forecast",
            f"{sk.market_name} regional share North America Europe Asia Pacific",
            f"{sk.market_name} market segmentation components pricing",
        ]
        try:
            snippets = gather(queries)
        except Exception as exc:  # noqa: BLE001
            state.setdefault("log", []).append(f"web gather failed: {exc}")
    log = state.setdefault("log", [])
    log.append(f"structure: db={'yes' if db_ev else 'no'} web_snippets={len(snippets)}")
    return {"db_evidence": db_ev, "web_snippets": snippets, "log": log}


def market_size_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg = state["config"]
    sk: Skeleton = state["skeleton"]
    llm: GenLLM = state["llm"]
    regions = list(sk.regions.keys())
    system = (
        "You are a market-sizing analyst. From the evidence, decide the GLOBAL market "
        "value for the base year 2026 (US$ Mn), the overall value CAGR (2026-2033, as a "
        "decimal), and for each region its share of global value (decimals summing to 1) "
        "and its own value CAGR (decimal). Decide independently from evidence; if the "
        "evidence gives a global size or CAGR, you may adopt or adjust it with reasoning."
    )
    user = (
        f"Market: {sk.market_name}\nRegions: {regions}\n\n{_evidence_brief(state)}\n\n"
        'Return JSON: {"global_value_2026": number, "global_cagr": number, '
        '"regions": {"<region>": {"value_share": number, "cagr": number}}}'
    )
    data = llm.complete_json(system, user)
    regional_raw = data.get("regions", {}) or {}
    shares = {r: float(regional_raw.get(r, {}).get("value_share", 0.0) or 0.0) for r in regions}
    shares = _normalize(shares)
    regional = {}
    for r in regions:
        cagr = float(regional_raw.get(r, {}).get("cagr", data.get("global_cagr", 0.1)) or 0.1)
        regional[r] = {"value_share": shares[r], "cagr": round(min(0.45, max(0.01, cagr)), 4)}
    global_anchor = float(data.get("global_value_2026", 0.0) or 0.0) or 1000.0
    global_cagr = round(min(0.45, max(0.01, float(data.get("global_cagr", 0.1) or 0.1))), 4)
    state.setdefault("log", []).append(
        f"market_size: anchor2026={global_anchor:.1f} cagr={global_cagr:.3f}"
    )
    return {"regional": regional, "global_anchor_2026": global_anchor, "global_cagr": global_cagr}


def _shares_global(state, sk, llm, regions, critique, prior):
    """One global vector per sibling group (default). Best when shares are
    near-uniform across geographies; every region is set to the same vector."""
    dim_critique = state.get("dim_critique", {}) or {}
    all_shares: dict[str, dict[str, dict[str, dict[str, float]]]] = {}
    for dim in sk.nesting:
        groups = sk.sibling_groups(dim)
        group_spec = {parent: children for parent, children in groups}
        system = (
            "You are a market segmentation analyst. For each sibling group, assign each "
            "segment a share of its immediate parent (decimals that SUM TO 1.0 within the "
            "group). Base the split on evidence and domain reasoning. Parent '__root__' "
            "means shares of the whole dimension."
        )
        refine = ""
        dcrit = dim_critique.get(dim) or critique
        if dcrit and prior.get(dim, {}).get("__global__"):
            refine = (
                f"\nPREVIOUS ATTEMPT: {json.dumps(prior[dim]['__global__'])}\n"
                f"REVIEWER CRITIQUE for this dimension: {dcrit}\n"
                "Apply the critique concretely — shift the named segments in the stated "
                "direction; do not return the same numbers."
            )
        user = (
            f"Market: {sk.market_name}\nDimension: {dim}\n"
            f"Sibling groups (parent -> segments): {json.dumps(group_spec, ensure_ascii=False)}\n\n"
            f"{_evidence_brief(state)}{refine}\n\n"
            'Return JSON: {"<parent>": {"<segment>": share, ...}, ...} for every group.'
        )
        data = llm.complete_json(system, user)
        glob: dict[str, dict[str, float]] = {}
        for parent, children in groups:
            raw = data.get(parent, {}) or {}
            vec = {seg: float(raw.get(seg, 0.0) or 0.0) for seg in children}
            glob[parent] = _normalize(vec)
        dim_out = {"__global__": glob}
        for region in regions:
            dim_out[region] = {p: dict(v) for p, v in glob.items()}
        all_shares[dim] = dim_out
    state.setdefault("log", []).append(f"shares(global): {len(all_shares)} dimensions")
    return {"shares": all_shares}


def _group_global_mean(groups, present_regions):
    """Mean vector per group across regions that supplied it (fallback for gaps)."""
    glob: dict[str, dict[str, float]] = {}
    for parent, children in groups:
        acc = {seg: 0.0 for seg in children}
        cnt = 0
        for reg_map in present_regions.values():
            if parent in reg_map:
                for seg in children:
                    acc[seg] += reg_map[parent].get(seg, 0.0)
                cnt += 1
        glob[parent] = _normalize(acc) if cnt else _normalize({seg: 1.0 for seg in children})
    return glob


def geo_node(state: dict[str, Any]) -> dict[str, Any]:
    """Split each region's value across its member countries (share-of-region + CAGR).

    Produces the per-country anchor/CAGR the ME pipeline's sizing block requires:
    country_value = global_anchor * region_value_share * country_share_of_region.
    """
    sk: Skeleton = state["skeleton"]
    llm: GenLLM = state["llm"]
    regional = state.get("regional", {})
    system = (
        "You are a market geography analyst. For each region, split its value across the "
        "listed member countries: give each country a share of its region (decimals that "
        "SUM TO 1.0 per region) and a plausible value CAGR (decimal). Larger economies "
        "get larger shares; reflect real market maturity."
    )
    user = (
        f"Market: {sk.market_name}\n"
        f"Regions and members: {json.dumps(sk.regions, ensure_ascii=False)}\n"
        f"Region value shares/CAGRs: {json.dumps(regional, ensure_ascii=False)}\n\n"
        f"{_evidence_brief(state)}\n\n"
        'Return JSON: {"<region>": {"<country>": {"share_of_region": number, "cagr": number}}}'
    )
    try:
        data = llm.complete_json(system, user)
    except Exception as exc:  # noqa: BLE001
        data = {}
        state.setdefault("log", []).append(f"geo failed: {exc}")

    geo: dict[str, dict[str, dict[str, float]]] = {}
    for region, members in sk.regions.items():
        rraw = data.get(region, {}) or {}
        shares = {c: float((rraw.get(c, {}) or {}).get("share_of_region", 0.0) or 0.0) for c in members}
        shares = _normalize(shares)
        region_cagr = regional.get(region, {}).get("cagr", state.get("global_cagr", 0.1))
        geo[region] = {}
        for c in members:
            cagr = float((rraw.get(c, {}) or {}).get("cagr", region_cagr) or region_cagr)
            geo[region][c] = {"share_of_region": shares[c], "cagr": round(min(0.5, max(0.01, cagr)), 4)}
    state.setdefault("log", []).append(f"geo: split {sum(len(v) for v in geo.values())} countries")
    return {"geo": geo}


def shares_node(state: dict[str, Any]) -> dict[str, Any]:
    """Decide per-REGION share vectors for each sibling group (not one global vector).

    Output shape: shares[dim][region][parent] = {segment: share}. A synthetic
    "__global__" region holds the cross-region mean and fills any gaps.
    """
    sk: Skeleton = state["skeleton"]
    llm: GenLLM = state["llm"]
    cfg = state["config"]
    regions = list(sk.regions.keys())
    critique = state.get("critique", "")
    prior = state.get("shares", {})

    if not cfg.per_region_shares:
        return _shares_global(state, sk, llm, regions, critique, prior)

    all_shares: dict[str, dict[str, dict[str, dict[str, float]]]] = {}

    for dim in sk.nesting:
        groups = sk.sibling_groups(dim)
        group_spec = {parent: children for parent, children in groups}
        system = (
            "You are a market segmentation analyst. For EACH region, assign each segment a "
            "share of its immediate parent (decimals that SUM TO 1.0 within each sibling "
            "group). Regions genuinely differ (e.g. RFID/AI adoption is higher in "
            "developed markets, cash usage higher in emerging ones) — reflect that with "
            "distinct per-region splits, not copies. Parent '__root__' = shares of the "
            "whole dimension."
        )
        refine = ""
        dcrit = (state.get("dim_critique", {}) or {}).get(dim) or critique
        if dcrit and prior.get(dim, {}).get("__global__"):
            refine = (
                f"\nPREVIOUS GLOBAL-MEAN ATTEMPT: {json.dumps(prior[dim]['__global__'])}\n"
                f"REVIEWER CRITIQUE: {dcrit}\nProduce an improved, better-differentiated split."
            )
        user = (
            f"Market: {sk.market_name}\nDimension: {dim}\nRegions: {regions}\n"
            f"Sibling groups (parent -> segments): {json.dumps(group_spec, ensure_ascii=False)}\n\n"
            f"{_evidence_brief(state)}{refine}\n\n"
            'Return JSON: {"<region>": {"<parent>": {"<segment>": share, ...}, ...}, ...} '
            "for EVERY region and EVERY group."
        )
        data = llm.complete_json(system, user)

        present_regions: dict[str, dict[str, dict[str, float]]] = {}
        for region in regions:
            rraw = data.get(region, {}) or {}
            reg_map: dict[str, dict[str, float]] = {}
            for parent, children in groups:
                praw = rraw.get(parent, {}) or {}
                vec = {seg: float(praw.get(seg, 0.0) or 0.0) for seg in children}
                if sum(vec.values()) > 0:
                    reg_map[parent] = _normalize(vec)
            if reg_map:
                present_regions[region] = reg_map

        glob = _group_global_mean(groups, present_regions)
        dim_out: dict[str, dict[str, dict[str, float]]] = {"__global__": glob}
        for region in regions:
            reg_map = present_regions.get(region, {})
            for parent, _children in groups:
                if parent not in reg_map:
                    reg_map[parent] = dict(glob[parent])
            dim_out[region] = reg_map
        all_shares[dim] = dim_out

    filled = sum(1 for d in all_shares.values() for r in d if r != "__global__")
    state.setdefault("log", []).append(
        f"shares: {len(all_shares)} dimensions x {len(regions)} regions ({filled} region-maps)"
    )
    return {"shares": all_shares}


def finalize_node(state: dict[str, Any]) -> dict[str, Any]:
    """After the loop, restore the best-scoring shares seen across iterations."""
    best = state.get("best_shares")
    best_reward = float(state.get("best_reward", -1.0))
    cur_reward = float(state.get("reward", -1.0))
    if best is not None and best_reward > cur_reward:
        state.setdefault("log", []).append(
            f"finalize: restored best shares (reward {best_reward} > final {cur_reward})"
        )
        return {"shares": best, "reward": best_reward}
    return {}


def asp_node(state: dict[str, Any]) -> dict[str, Any]:
    sk: Skeleton = state["skeleton"]
    llm: GenLLM = state["llm"]
    products = sk.ordered_segments(sk.priced_dimension)
    system = (
        "You are a pricing analyst. Assign an average selling price (ASP) in US$ per unit "
        "for each product below, reflecting realistic relative pricing in this market. "
        "Return a positive number for EVERY product (including roll-up categories)."
    )
    user = (
        f"Market: {sk.market_name}\nPriced products ({sk.priced_dimension}): "
        f"{json.dumps(products, ensure_ascii=False)}\n\n{_evidence_brief(state)}\n\n"
        'Return JSON: {"<product>": asp_number, ...}'
    )
    data = llm.complete_json(system, user)
    asp = {}
    for leaf in products:
        try:
            val = float(data.get(leaf, 0.0) or 0.0)
        except (TypeError, ValueError):
            val = 0.0
        asp[leaf] = round(val if val > 0 else 100.0, 2)
    state.setdefault("log", []).append(f"asp: priced {len(asp)} products")
    return {"asp": asp}


def brain_node(state: dict[str, Any]) -> dict[str, Any]:
    sk: Skeleton = state["skeleton"]
    llm: GenLLM = state["llm"]
    regional = state.get("regional", {})
    system = (
        "You are a 'brain' analyst that interrogates the numbers. Pose the key WHY "
        "questions about this market's growth, regional split, and pricing, then answer "
        "each with a concise justification citing drivers and CURRENT EVENTS where "
        "relevant (e.g. supply shocks, regulation, conflicts). Keep each answer 1-3 "
        "sentences."
    )
    user = (
        f"Market: {sk.market_name}\n"
        f"Global 2026 value: {state.get('global_anchor_2026')} US$ Mn, CAGR {state.get('global_cagr')}\n"
        f"Regional split: {json.dumps(regional, ensure_ascii=False)}\n\n{_evidence_brief(state)}\n\n"
        'Return JSON: {"artifacts": [{"topic": str, "question": str, "answer": str, '
        '"drivers": [str], "current_events": [str]}]}'
    )
    try:
        data = llm.complete_json(system, user)
        artifacts = data.get("artifacts", []) or []
    except Exception as exc:  # noqa: BLE001
        artifacts = []
        state.setdefault("log", []).append(f"brain failed: {exc}")
    state.setdefault("log", []).append(f"brain: {len(artifacts)} artifacts")
    return {"artifacts": artifacts}


def reward_node(state: dict[str, Any]) -> dict[str, Any]:
    """Deterministic consistency + LLM plausibility -> reward in [0,1] + critique."""
    sk: Skeleton = state["skeleton"]
    llm: GenLLM = state["llm"]
    shares = state.get("shares", {})
    regional = state.get("regional", {})

    # 1) deterministic consistency (across every region vector)
    regions = list(sk.regions.keys())
    checks: list[float] = []
    problems: list[str] = []
    for dim in sk.nesting:
        for parent, children in sk.sibling_groups(dim):
            for region in regions:
                vec = shares.get(dim, {}).get(region, {}).get(parent, {})
                s = sum(vec.values())
                ok = abs(s - 1.0) < 0.02 and len(vec) == len(children)
                checks.append(1.0 if ok else 0.0)
                if not ok:
                    problems.append(f"{dim}/{parent}@{region} sums to {s:.3f}")
                if len(children) > 2 and max(vec.values(), default=0) > 0.97:
                    problems.append(f"{dim}/{parent}@{region} degenerate (one segment dominates)")
    rshare = sum(r["value_share"] for r in regional.values()) if regional else 0.0
    region_ok = 1.0 if abs(rshare - 1.0) < 0.02 else 0.0
    if region_ok < 1.0:
        problems.append(f"region shares sum to {rshare:.3f}")
    consistency = (sum(checks) / len(checks) if checks else 0.0) * 0.7 + region_ok * 0.3

    # 2) LLM plausibility vote — scored PER DIMENSION so critique can be targeted
    plaus = 0.6
    dim_critique: dict[str, str] = {}
    global_shares = {dim: shares.get(dim, {}).get("__global__", {}) for dim in sk.nesting}
    it = int(state.get("iter", 0)) + 1
    try:
        system = (
            "You are a decisive, skeptical market reviewer. For EACH dimension, judge how "
            "plausible its segmentation split is for THIS specific market and rate it 0-1 "
            "(use the full range — be critical; a generic or evenly-spread split should "
            "score low). Give a concrete, actionable critique naming which segment is over- "
            "or under-weighted and the direction to change it. Also rate the regional split "
            "and growth. Vary your scores; do not give everything the same number."
        )
        user = (
            f"Market: {sk.market_name}\nIteration: {it}\nGlobal CAGR: {state.get('global_cagr')}\n"
            f"Regional: {json.dumps(regional, ensure_ascii=False)}\n"
            f"Global-mean shares per dimension: {json.dumps(global_shares, ensure_ascii=False)[:5000]}\n\n"
            'Return JSON: {"overall": number, "dimensions": {"<dim>": {"score": number, '
            '"critique": string}}, "regional_critique": string}'
        )
        data = llm.complete_json(system, user)
        dims = data.get("dimensions", {}) or {}
        scores = []
        for dim in sk.nesting:
            entry = dims.get(dim, {}) or {}
            sc = entry.get("score")
            if isinstance(sc, (int, float)):
                scores.append(min(1.0, max(0.0, float(sc))))
            crit = str(entry.get("critique", "")).strip()
            if crit:
                dim_critique[dim] = crit
        plaus = (sum(scores) / len(scores)) if scores else min(1.0, max(0.0, float(data.get("overall", 0.6) or 0.6)))
    except Exception as exc:  # noqa: BLE001
        state.setdefault("log", []).append(f"reward LLM failed: {exc}")

    reward = round(0.4 * consistency + 0.6 * plaus, 4)
    # global critique string (consistency problems + regional note) as a fallback
    fallback = "; ".join(problems[:4])
    reg_crit = ""
    try:
        reg_crit = str(data.get("regional_critique", ""))  # type: ignore[name-defined]
    except Exception:  # noqa: BLE001
        pass
    critique = " | ".join(x for x in [fallback, reg_crit] if x)

    # keep-best across iterations
    best_reward = float(state.get("best_reward", -1.0))
    update = {"reward": reward, "critique": critique, "dim_critique": dim_critique, "iter": it}
    if reward > best_reward:
        update["best_reward"] = reward
        update["best_shares"] = shares
    state.setdefault("log", []).append(
        f"reward#{it}: reward={reward} consistency={consistency:.2f} plausibility={plaus:.2f} "
        f"(best={max(best_reward, reward):.2f})"
    )
    return update

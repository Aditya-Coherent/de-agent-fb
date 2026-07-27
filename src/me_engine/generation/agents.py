"""Agent node functions for the synthetic Input-Sheet generator.

Every agent emits *judgements* (shares, growth rates, prices) grounded in DB +
web evidence. Deterministic helpers normalise vectors and expand base vectors to
per-country columns. A brain agent attaches justification artifacts. A reward
agent scores the number set and drives the refinement loop.
"""
from __future__ import annotations

import json
from typing import Any

from .db_source import (
    DBEvidence,
    fetch_market_evidence,
    fetch_market_evidence_semantic,
    verify_market_match,
)
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
        top = max(out, key=out.__getitem__)
        out[top] = round(out[top] + residue, 4)
    return out


def _evidence_brief(state: dict[str, Any], dim: str | None = None) -> str:
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
    if dim is not None:
        dim_snippets = (state.get("dim_snippets") or {}).get(dim) or []
        if dim_snippets:
            parts.append(f"DIMENSION-SPECIFIC EVIDENCE ({dim}):\n" + "\n".join(f"- {s}" for s in dim_snippets))
    return "\n\n".join(parts) if parts else "(no external evidence available)"


# --------------------------------------------------------------------------- #
# nodes
# --------------------------------------------------------------------------- #
def structure_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg = state["config"]
    sk: Skeleton = state["skeleton"]
    llm: GenLLM = state["llm"]
    log = state.setdefault("log", [])
    db_ev = None
    if cfg.neon_url:
        try:
            db_ev = fetch_market_evidence(cfg.neon_url, sk.market_name)
        except Exception as exc:  # noqa: BLE001
            log.append(f"DB fetch failed: {exc}")

        if db_ev is not None and cfg.is_online:
            try:
                if not verify_market_match(sk.market_name, db_ev.raw_narrative, llm):
                    log.append(
                        f"DB evidence rejected: narrative did not match '{sk.market_name}' — "
                        "trying semantic search for a second opinion"
                    )
                    db_ev = None
                    # A LIKE hit can land on a generic word (e.g. "retail") that
                    # matches an unrelated report's category tag. Before giving
                    # up, retry with a genuine meaning-based lookup.
                    try:
                        alt = fetch_market_evidence_semantic(cfg.neon_url, sk.market_name)
                        if alt is not None and verify_market_match(sk.market_name, alt.raw_narrative, llm):
                            db_ev = alt
                            log.append(f"semantic retry matched: '{alt.keyword}'")
                        elif alt is not None:
                            log.append("semantic retry also rejected — no DB evidence used")
                    except Exception as exc:  # noqa: BLE001
                        log.append(f"semantic retry failed: {exc}")
            except Exception as exc:  # noqa: BLE001
                log.append(f"DB match verification failed (keeping evidence): {exc}")

    snippets: list[str] = []
    dim_snippets: dict[str, list[str]] = {}
    if cfg.enable_web:
        queries = [
            f"{sk.market_name} size 2026 2033 CAGR forecast",
            f"{sk.market_name} regional share North America Europe Asia Pacific",
            f"{sk.market_name} market segmentation components pricing",
        ]
        try:
            snippets = gather(queries)
        except Exception as exc:  # noqa: BLE001
            log.append(f"web gather failed: {exc}")

        # Per-dimension queries: the broad/flat dimensions (many near-equal
        # segments) are the ones the shares agent gets most wrong when it only
        # has generic market-level evidence to work from.
        for dim, tree in sk.nesting.items():
            root = tree.get("__root__", [])
            label = dim[3:] if dim.startswith("By ") else dim
            dim_queries = [f"{sk.market_name} {label} share breakdown percentage"]
            if len(root) >= 5:
                dim_queries.append(f"{sk.market_name} {', '.join(root[:4])} market share")
            try:
                dim_snippets[dim] = gather(dim_queries, per_query=4, cap=6)
            except Exception as exc:  # noqa: BLE001
                log.append(f"web gather failed for {dim}: {exc}")

    log.append(
        f"structure: db={'yes' if db_ev else 'no'} web_snippets={len(snippets)} "
        f"dim_snippets={sum(len(v) for v in dim_snippets.values())}"
    )
    return {"db_evidence": db_ev, "web_snippets": snippets, "dim_snippets": dim_snippets, "log": log}


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
    refine = ""
    critique = state.get("market_critique", "")
    prior_anchor = state.get("global_anchor_2026")
    if critique and prior_anchor is not None:
        refine = (
            f"\nPREVIOUS ATTEMPT: global_value_2026={prior_anchor}, "
            f"global_cagr={state.get('global_cagr')}, regions={json.dumps(state.get('regional', {}), ensure_ascii=False)}\n"
            f"REVIEWER CRITIQUE: {critique}\n"
            "Apply the critique concretely — adjust the named number(s) in the stated "
            "direction; do not just return the same numbers."
        )
    user = (
        f"Market: {sk.market_name}\nRegions: {regions}\n\n{_evidence_brief(state)}{refine}\n\n"
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
            f"{_evidence_brief(state, dim)}{refine}\n\n"
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
            f"{_evidence_brief(state, dim)}{refine}\n\n"
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
    """After the loop, restore the best-scoring full snapshot seen across iterations.

    Covers shares AND market_size/geo/asp, since all four are now refined inside
    the reward loop (see reward_node) — a later, worse-scoring iteration should
    not overwrite an earlier, better one for any of them.
    """
    best = state.get("best_state")
    best_reward = float(state.get("best_reward", -1.0))
    cur_reward = float(state.get("reward", -1.0))
    if best is not None and best_reward > cur_reward:
        state.setdefault("log", []).append(
            f"finalize: restored best snapshot (reward {best_reward} > final {cur_reward})"
        )
        return {**best, "reward": best_reward}
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
    refine = ""
    critique = state.get("asp_critique", "")
    prior_asp = state.get("asp")
    if critique and prior_asp:
        refine = (
            f"\nPREVIOUS ATTEMPT: {json.dumps(prior_asp, ensure_ascii=False)}\n"
            f"REVIEWER CRITIQUE: {critique}\n"
            "Apply the critique concretely — reprice the named product(s) in the stated "
            "direction; do not just return the same numbers."
        )
    user = (
        f"Market: {sk.market_name}\nPriced products ({sk.priced_dimension}): "
        f"{json.dumps(products, ensure_ascii=False)}\n\n{_evidence_brief(state)}{refine}\n\n"
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


def _regional_spread(shares: dict, sk: Skeleton, regions: list[str], top_n: int = 5) -> dict[str, list[str]]:
    """Per-dimension summary of how much a segment's share actually varies across
    regions (min-max). Lets the reviewer judge whether per-region differentiation
    looks realistic (not just check the global-mean shape, which never sees this)."""
    out: dict[str, list[str]] = {}
    for dim in sk.nesting:
        dim_map = shares.get(dim, {})
        rows: list[tuple[float, str]] = []
        for parent, children in sk.sibling_groups(dim):
            for seg in children:
                vals = [dim_map.get(r, {}).get(parent, {}).get(seg) for r in regions]
                vals = [v for v in vals if isinstance(v, (int, float))]
                if len(vals) >= 2:
                    spread = max(vals) - min(vals)
                    if spread > 0.03:
                        rows.append((spread, f"{parent}/{seg}: {min(vals):.2f}-{max(vals):.2f}"))
        rows.sort(key=lambda r: r[0], reverse=True)
        if rows:
            out[dim] = [r[1] for r in rows[:top_n]]
    return out


def reward_node(state: dict[str, Any]) -> dict[str, Any]:
    """Deterministic consistency + LLM plausibility -> reward in [0,1] + critique.

    Scores (and produces targeted critiques for) shares, market size/regional
    split, AND asp — all four are refined by the loop, not just shares.
    """
    cfg = state["config"]
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

    # 2) LLM plausibility vote — scored PER DIMENSION, plus market size and asp,
    #    so critique can be targeted at whichever family of numbers is weakest.
    plaus = 0.6
    dim_critique: dict[str, str] = {}
    market_crit = ""
    asp_crit = ""
    reg_crit = ""
    global_shares = {dim: shares.get(dim, {}).get("__global__", {}) for dim in sk.nesting}
    spread = _regional_spread(shares, sk, regions) if cfg.per_region_shares else {}
    it = int(state.get("iter", 0)) + 1
    try:
        system = (
            "You are a decisive, skeptical market reviewer. For EACH dimension, judge how "
            "plausible its segmentation split is for THIS specific market and rate it 0-1 "
            "(use the full range — be critical; a generic or evenly-spread split should "
            "score low). Give a concrete, actionable critique naming which segment is over- "
            "or under-weighted and the direction to change it. Also rate: the regional value "
            "split and growth; the global market size + CAGR; and the ASP (pricing) set. If a "
            "per-region variation summary is given, judge whether that differentiation looks "
            "realistic for this market (too flat/uniform for a market where regions should "
            "genuinely differ, or too extreme, should score low). Vary your scores; do not "
            "give everything the same number."
        )
        spread_block = (
            f"Regional variation (min-max share across regions, notable spreads only): "
            f"{json.dumps(spread, ensure_ascii=False)[:2000]}\n" if spread else ""
        )
        user = (
            f"Market: {sk.market_name}\nIteration: {it}\n"
            f"Global value 2026: {state.get('global_anchor_2026')} US$ Mn, "
            f"CAGR: {state.get('global_cagr')}\n"
            f"Regional: {json.dumps(regional, ensure_ascii=False)}\n"
            f"ASP: {json.dumps(state.get('asp', {}), ensure_ascii=False)[:2000]}\n"
            f"Global-mean shares per dimension: {json.dumps(global_shares, ensure_ascii=False)[:5000]}\n"
            f"{spread_block}\n"
            'Return JSON: {"overall": number, "dimensions": {"<dim>": {"score": number, '
            '"critique": string}}, "regional_critique": string, '
            '"market_size": {"score": number, "critique": string}, '
            '"asp": {"score": number, "critique": string}}'
        )
        data = llm.complete_json(system, user)
        dims = data.get("dimensions", {}) or {}
        scores = []
        dim_score: dict[str, float] = {}
        for dim in sk.nesting:
            entry = dims.get(dim, {}) or {}
            sc = entry.get("score")
            if isinstance(sc, (int, float)):
                clipped = min(1.0, max(0.0, float(sc)))
                scores.append(clipped)
                dim_score[dim] = clipped
            crit = str(entry.get("critique", "")).strip()
            if crit:
                dim_critique[dim] = crit

        market_entry = data.get("market_size", {}) or {}
        m_sc = market_entry.get("score")
        market_score = None
        if isinstance(m_sc, (int, float)):
            market_score = min(1.0, max(0.0, float(m_sc)))
            scores.append(market_score)
        market_crit = str(market_entry.get("critique", "")).strip()

        asp_entry = data.get("asp", {}) or {}
        a_sc = asp_entry.get("score")
        asp_score = None
        if isinstance(a_sc, (int, float)):
            asp_score = min(1.0, max(0.0, float(a_sc)))
            scores.append(asp_score)
        asp_crit = str(asp_entry.get("critique", "")).strip()

        reg_crit = str(data.get("regional_critique", ""))
        plaus = (sum(scores) / len(scores)) if scores else min(1.0, max(0.0, float(data.get("overall", 0.6) or 0.6)))
    except Exception as exc:  # noqa: BLE001
        state.setdefault("log", []).append(f"reward LLM failed: {exc}")
        dim_score, market_score, asp_score = {}, None, None

    reward = round(0.4 * consistency + 0.6 * plaus, 4)
    # global critique string (consistency problems + regional note) as a fallback
    fallback = "; ".join(problems[:4])
    critique = " | ".join(x for x in [fallback, reg_crit] if x)

    # keep-best across iterations — snapshot EVERYTHING the loop refines, not
    # just shares, so finalize can restore the best full state.
    best_reward = float(state.get("best_reward", -1.0))
    update = {
        "reward": reward, "critique": critique, "dim_critique": dim_critique,
        "market_critique": market_crit, "asp_critique": asp_crit, "iter": it,
        "dim_score": dim_score, "market_score": market_score, "asp_score": asp_score,
    }
    if reward > best_reward:
        update["best_reward"] = reward
        update["best_state"] = {
            "shares": shares,
            "regional": regional,
            "global_anchor_2026": state.get("global_anchor_2026"),
            "global_cagr": state.get("global_cagr"),
            "geo": state.get("geo"),
            "asp": state.get("asp"),
            "dim_score": dim_score,
            "market_score": market_score,
            "asp_score": asp_score,
        }
    state.setdefault("log", []).append(
        f"reward#{it}: reward={reward} consistency={consistency:.2f} plausibility={plaus:.2f} "
        f"(best={max(best_reward, reward):.2f})"
    )
    return update

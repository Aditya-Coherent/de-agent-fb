"""Estimator agent: estimates a missing number from facts already gathered in
the same domain (vertical), never from thin air.

    facts library  output/facts/<vertical-slug>.jsonl -- every verified number
                   the 2A signal model collects (trade anchors, web anchors,
                   customs prices, ceiling constants, worldwide totals), each
                   with its source, across all markets of the vertical.
    retrieval      for a target (market, country, metric): the same metric in
                   peer countries (closest GDP per capita), other products in
                   the same country, and the nearest facts by embedding
                   similarity (fastembed / sentence-transformers).
    reasoning      the LLM must answer with a formula over named inputs, each
                   either a library fact (by id) or a labelled assumption.
    verification   code checks every cited fact id exists and that the value
                   used matches the library, then evaluates the formula
                   itself (safe arithmetic only). Numbers the LLM didn't take
                   from the library count as assumptions and cap confidence.

The result is labelled `estimated_from_facts`, never `verified`.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import operator
import statistics
import re
import threading
from pathlib import Path
from typing import Any

from me_engine.generation.llm import GenLLM

_FACTS_DIR = Path("output") / "facts"
_lock = threading.Lock()
MAX_FACTS = 40
ASSUMPTION_CONFIDENCE_CAP = 0.5


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def facts_path(vertical: str, root: Path | None = None) -> Path:
    return (Path(root) / _FACTS_DIR if root else _FACTS_DIR) / f"{_slug(vertical)}.jsonl"


# --------------------------------------------------------------------------- #
# facts library
# --------------------------------------------------------------------------- #
def fact_id(f: dict[str, Any]) -> str:
    key = f"{f['market']}|{f.get('country') or 'World'}|{f['metric']}|{f.get('year')}|{f['source']}"
    return hashlib.sha1(key.lower().encode("utf-8")).hexdigest()[:10]


def load_facts(vertical: str, root: Path | None = None) -> list[dict[str, Any]]:
    path = facts_path(vertical, root)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def add_facts(vertical: str, facts: list[dict[str, Any]], root: Path | None = None) -> int:
    """Upsert by id (same market/country/metric/year/source replaces). Returns count added or replaced."""
    with _lock:
        existing = {f["id"]: f for f in load_facts(vertical, root)}
        for f in facts:
            f = {**f, "vertical": vertical}
            f["id"] = fact_id(f)
            existing[f["id"]] = f
        path = facts_path(vertical, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(f, ensure_ascii=False) + "\n" for f in existing.values()), encoding="utf-8")
    return len(facts)


def fact_text(f: dict[str, Any]) -> str:
    return (f"{f['market']} | {f.get('country') or 'World'} | {f['metric']} = {f['value']:,.4g} {f.get('unit', '')} "
            f"({f.get('year')}, {f.get('kind')}, {f.get('source', '')[:60]})")


# --------------------------------------------------------------------------- #
# retrieval
# --------------------------------------------------------------------------- #
def _embed_rank(query: str, facts: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    try:
        import numpy as np

        from me_engine.generation.semantic_search import available, embed, embed_many
        if not available() or not facts:
            return []
        q = np.array(embed(query))
        vecs = np.array(embed_many([fact_text(f) for f in facts]))
        order = np.argsort(-(vecs @ q))[:k]
        return [facts[i] for i in order]
    except Exception:  # noqa: BLE001 -- retrieval quality degrades, never breaks
        return []


def retrieve(facts: list[dict[str, Any]], *, market: str, country: str | None, metric: str,
             gdp_of: dict[str, float], k: int = MAX_FACTS) -> list[dict[str, Any]]:
    """Same metric in peer countries (by GDP per capita), other products in the
    same country, then nearest by embedding -- deduplicated, capped at k."""
    picked: dict[str, dict[str, Any]] = {}

    def take(items: list[dict[str, Any]], n: int) -> None:
        for f in items:
            if len(picked) >= k or n <= 0:
                return
            if f["id"] not in picked:
                picked[f["id"]] = f
                n -= 1

    target_gdp = gdp_of.get(country or "")
    same_metric = [f for f in facts if f["metric"] == metric and f.get("country") != country]
    if target_gdp:
        same_metric.sort(key=lambda f: abs(math.log((gdp_of.get(f.get("country") or "") or target_gdp) / target_gdp)))
    same_market = [f for f in same_metric if f["market"] == market]
    take(same_market, 15)
    take([f for f in same_metric if f["market"] != market], 8)
    take([f for f in facts if country and f.get("country") == country], 8)
    take([f for f in facts if f.get("country") is None and f["market"] == market], 5)
    take(_embed_rank(f"{market} {country or 'World'} {metric}", facts, k), k)
    return list(picked.values())


# --------------------------------------------------------------------------- #
# safe formula evaluation
# --------------------------------------------------------------------------- #
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"sqrt": math.sqrt, "log": math.log, "exp": math.exp, "min": min, "max": max}


def evaluate(formula: str, names: dict[str, float]) -> float:
    """Arithmetic over named inputs only (no attributes, no calls except a few math functions)."""
    def ev(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in names:
                raise ValueError(f"unknown input '{node.id}'")
            return float(names[node.id])
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
            return float(_FUNCS[node.func.id](*[ev(a) for a in node.args]))
        raise ValueError(f"unsupported expression: {ast.dump(node)[:80]}")

    return ev(ast.parse(formula, mode="eval"))


# --------------------------------------------------------------------------- #
# the agent
# --------------------------------------------------------------------------- #
_UNIT_SCALES = (1.0, 0.01, 100.0, 1e-3, 1e3, 1e-6, 1e6)


def _match_scaled(used: float, source: float) -> tuple[float, bool]:
    """(value to use, matched). A unit conversion of the source (% -> fraction,
    millions -> units) counts as a match; otherwise the source value is used as-is."""
    for scale in _UNIT_SCALES:
        if math.isclose(used, source * scale, rel_tol=0.02):
            return source * scale, True
    return source, False

def estimate(llm: GenLLM, facts: list[dict[str, Any]], *, market: str, country: str | None, metric: str,
             unit: str, context: dict[str, Any] | None = None, gdp_of: dict[str, float] | None = None,
             sample: int = 0) -> dict[str, Any] | None:
    """{value, confidence, formula, inputs, fact_ids, n_assumptions, reasoning} or None."""
    pool = retrieve(facts, market=market, country=country, metric=metric, gdp_of=gdp_of or {})
    if not pool:
        return None
    by_id = {f["id"]: f for f in pool}
    lines = "\n".join(f"[{f['id']}] {fact_text(f)}" for f in pool)
    target = f"{metric} of {market} in {country or 'the World'}, in {unit}"
    data = llm.complete_json(
        "You are an estimator. Estimate ONE missing number using the facts given -- by analogy (same metric in "
        "comparable countries, scaled per capita / per household / by income), by ratios between products in "
        "the same domain, or by simple arithmetic. Every number you use must be either a fact (cite its id and "
        "copy its value exactly) or an explicit assumption (with a reason). Prefer facts; keep assumptions few.",
        f"Target: {target}\nContext about the target: {json.dumps(context or {}, ensure_ascii=False, default=str)}\n\n"
        f"Facts:\n{lines}\n\n"
        "Each input is EITHER a fact (fact_id, value copied from it -- you may convert units, e.g. 25 % -> 0.25), "
        "OR a numeric context field (context_key, value copied from the context), OR an assumption.\n"
        'Return JSON: {"formula": "<arithmetic over input names, e.g. a / b * c>", '
        '"inputs": {"<name>": {"value": <number>, "fact_id": "<id or null>", "context_key": "<key or null>", '
        '"assumption_reason": "<if neither>"}}, '
        '"estimate": <number>, "confidence": <0..1>, "reasoning": "<2-3 sentences>"}'
        + (f" (independent estimate #{sample + 1})" if sample else ""),
    )
    inputs = data.get("inputs") or {}
    ctx = {k: float(v) for k, v in (context or {}).items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    names: dict[str, float] = {}
    fact_ids, n_assumptions, mismatched = [], 0, []
    for name, inp in inputs.items():
        if not re.fullmatch(r"[A-Za-z_]\w*", str(name)) or not isinstance(inp, dict):
            return None
        try:
            value = float(inp.get("value"))
        except (TypeError, ValueError):
            return None
        fid, ckey = str(inp.get("fact_id") or "").strip("[] "), inp.get("context_key")
        if fid in by_id:
            value, ok = _match_scaled(value, float(by_id[fid]["value"]))
            if not ok:
                mismatched.append(name)  # replaced by the library's number, never the LLM's copy
            fact_ids.append(fid)
        elif ckey in ctx:
            value, ok = _match_scaled(value, ctx[ckey])
            if not ok:
                mismatched.append(name)
        else:
            n_assumptions += 1
        names[str(name)] = value
    try:
        value = evaluate(str(data.get("formula") or ""), names)
    except Exception:  # noqa: BLE001 -- unparseable or unsafe formula
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        confidence = 0.0
    if n_assumptions or mismatched:
        confidence = min(confidence, ASSUMPTION_CONFIDENCE_CAP)
    if not fact_ids:
        confidence = min(confidence, 0.2)
    return {"value": value, "confidence": confidence, "formula": data.get("formula"), "inputs": names,
            "fact_ids": fact_ids, "facts_used": [fact_text(by_id[i]) for i in fact_ids],
            "n_assumptions": n_assumptions, "corrected_inputs": mismatched,
            "llm_estimate": data.get("estimate"), "reasoning": str(data.get("reasoning") or "")[:500],
            "kind": "estimated_from_facts"}


def estimate_consensus(llm: GenLLM, facts: list[dict[str, Any]], samples: int = 3, **kw: Any) -> dict[str, Any] | None:
    """Self-consistency: `samples` independent estimates, median value. Keeps
    the derivation of the estimate closest to the median, and the spread."""
    outs = [o for k in range(samples) if (o := estimate(llm, facts, sample=k, **kw))]
    if not outs:
        return None
    med = statistics.median(o["value"] for o in outs)
    best = min(outs, key=lambda o: abs(math.log(o["value"] / med)))
    return {**best, "value": med, "samples": [o["value"] for o in outs],
            "confidence": statistics.median(o["confidence"] for o in outs)}

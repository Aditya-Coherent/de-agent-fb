"""Estimation strategies (the assumptor's actions). Each predicts the target
country's PER-CAPITA value of one metric from the countries where it is known,
in log space. Deterministic strategies are free; the LLM prior costs a call.

    peer_income    median per-capita of the k countries closest in ln GDP pc
    peer_region    median per-capita of known countries in the same WB region
    elasticity     ln(pc) = a + b ln(GDP pc) + c urban, least squares (n >= 5)
    global_median  median per-capita of every known country (the fallback)
    llm_prior      the LLM brain's direct estimate (product knowledge + peers + lessons)
    llm_analog     the LLM brain CHOOSES comparable countries + a bounded adjustment;
                   code takes their median (one call feeds both LLM strategies)
"""
from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

DETERMINISTIC = ("peer_income", "peer_region", "elasticity", "global_median")
LLM = ("llm_prior", "llm_analog")
ALL = DETERMINISTIC + LLM


@dataclass
class Known:
    country: str
    cov: dict[str, Any]       # covariates: population, gdp_pc_ppp, urban_pct, region
    per_capita: float         # metric / population (> 0)


def _ln(x: float) -> float:
    return math.log(max(x, 1e-12))


def peer_income(target: dict[str, Any], known: list[Known], k: int = 5) -> float | None:
    if not known:
        return None
    g = _ln(target["gdp_pc_ppp"])
    peers = sorted(known, key=lambda p: abs(_ln(p.cov["gdp_pc_ppp"]) - g))[:k]
    return statistics.median(_ln(p.per_capita) for p in peers)


def peer_region(target: dict[str, Any], known: list[Known]) -> float | None:
    peers = [p for p in known if p.cov.get("region") == target.get("region")]
    if len(peers) < 2:
        return None
    return statistics.median(_ln(p.per_capita) for p in peers)


def elasticity(target: dict[str, Any], known: list[Known]) -> float | None:
    if len(known) < 5:
        return None
    use_urban = all(p.cov.get("urban_pct") is not None for p in known) and target.get("urban_pct") is not None
    X = np.array([[1.0, _ln(p.cov["gdp_pc_ppp"])] + ([p.cov["urban_pct"] / 100.0] if use_urban else [])
                  for p in known])
    y = np.array([_ln(p.per_capita) for p in known])
    if np.linalg.matrix_rank(X) < X.shape[1]:
        return None
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    x0 = np.array([1.0, _ln(target["gdp_pc_ppp"])] + ([target["urban_pct"] / 100.0] if use_urban else []))
    pred = float(x0 @ coef)
    lo, hi = float(y.min()), float(y.max())
    return min(max(pred, lo - 1.0), hi + 1.0)  # no extrapolation beyond e (2.7x) outside the observed range


def global_median(target: dict[str, Any], known: list[Known]) -> float | None:
    return statistics.median(_ln(p.per_capita) for p in known) if known else None


ADJUST_BOUNDS = (0.2, 5.0)


def llm_brain(llm: Any, *, product: str, metric: str, unit: str, target_name: str, target: dict[str, Any],
              known: list[Known], lessons: list[str], display_scale: float = 1.0) -> dict[str, float]:
    """One LLM call -> {"llm_prior": ln pc, "llm_analog": ln pc} (either may be missing).

    llm_prior  : the model's own realistic estimate.
    llm_analog : the model only CHOOSES comparable countries (by diet, climate,
                 production, culture -- not just income) and one bounded
                 adjustment factor; code computes median(analogs) x adjustment.
    `display_scale` puts per-capita into a human-sized unit for the prompt
    (tonnes -> kg: 1000); models misjudge tiny numbers like 0.0098 t/person."""
    peers = sorted(known, key=lambda p: abs(_ln(p.cov["gdp_pc_ppp"]) - _ln(target["gdp_pc_ppp"])))[:25]
    lines = "\n".join(f"- {p.country}: {p.per_capita * display_scale:.4g} {unit}/person (GDP pc PPP "
                      f"{p.cov['gdp_pc_ppp']:,.0f}, {p.cov.get('region', '')})" for p in peers)
    lesson_text = "\n".join(f"- {x}" for x in lessons[-8:]) or "- (none yet)"
    try:
        data = llm.complete_json(
            "You are a market researcher estimating a missing statistic REALISTICALLY, not cautiously. Think "
            "about how this product is actually produced and consumed in the target country: diet and cuisine, "
            "climate, domestic production, religion and culture, prices, income. A country that is a known "
            "heavy (or near-zero) consumer must be estimated as such, even if that is far from the peer "
            "average. Then pick the known countries that are genuinely comparable FOR THIS PRODUCT. Return json.",
            f"Product: {product}\nMetric: {metric}, per person per year, in {unit}\nTarget: {target_name} "
            f"(population {target['population']:,.0f}, GDP pc PPP {target['gdp_pc_ppp']:,.0f}, "
            f"{target.get('region', '')})\n\nKnown countries:\n{lines or '- none'}\n\n"
            f"Lessons from past estimation errors:\n{lesson_text}\n\n"
            'Return JSON: {"per_capita": <your estimate>, '
            '"analogs": ["<names copied exactly from the known list, 1-5 most comparable for this product>"], '
            f'"adjustment": <target / analog median, between {ADJUST_BOUNDS[0]} and {ADJUST_BOUNDS[1]}>, '
            '"reason": "<one sentence>"}',
        )
    except Exception:  # noqa: BLE001 -- the LLM arms are optional
        return {}
    out: dict[str, float] = {}
    try:
        v = float(data.get("per_capita"))
        if v > 0 and math.isfinite(v):
            out["llm_prior"] = _ln(v / display_scale)
    except (TypeError, ValueError):
        pass
    by_name = {p.country.lower(): p for p in known}
    chosen = [by_name[str(n).strip().lower()] for n in data.get("analogs") or [] if str(n).strip().lower() in by_name]
    if chosen:
        try:
            adj = float(data.get("adjustment") or 1.0)
        except (TypeError, ValueError):
            adj = 1.0
        adj = min(max(adj, ADJUST_BOUNDS[0]), ADJUST_BOUNDS[1]) if math.isfinite(adj) and adj > 0 else 1.0
        out["llm_analog"] = statistics.median(_ln(p.per_capita) for p in chosen) + math.log(adj)
    return out


def predict_all(target: dict[str, Any], known: list[Known],
                llm_call: Callable[[], dict[str, float]] | None = None) -> dict[str, float]:
    """{strategy: ln per-capita} for every strategy that can run."""
    out: dict[str, float] = {}
    for name, fn in (("peer_income", peer_income), ("peer_region", peer_region), ("elasticity", elasticity),
                     ("global_median", global_median)):
        v = fn(target, known)
        if v is not None and math.isfinite(v):
            out[name] = v
    if llm_call is not None:
        out.update({k: v for k, v in (llm_call() or {}).items() if math.isfinite(v)})
    return out


def describe(preds: dict[str, float]) -> str:
    return json.dumps({k: round(math.exp(v), 6) for k, v in preds.items()})

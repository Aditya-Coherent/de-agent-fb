"""The assumptor: a realistic, calibrated estimate for a number no source
gives, from the countries where it is known.

    1. per-capita in the known countries (covariates from the World Bank)
    2. every strategy predicts the target's per-capita (strategies.py)
    3. the learned policy bias-corrects and weights them, and sets an 80%
       interval whose width is calibrated on held-out truths (policy.py)
    4. realism guard: the estimate must sit within 3x of the peer range and
       under the physical per-capita ceiling; otherwise it is clipped and
       flagged -- never silently passed through
    5. the result carries its own track record: how accurate this kind of
       assumption has been in training (episodes, typical error, coverage)

Label: `estimated_from_facts` (built from other countries' observed numbers),
never `verified_observed`.
"""
from __future__ import annotations

import math
import random
from typing import Any

from . import strategies as S
from .covariates import Covariates
from .policy import Policy, richness

REALISM_FACTOR = 3.0


def assume(*, product: str, metric: str, unit: str, country: str, known: dict[str, float], cov: Covariates,
           policy: Policy, domain: str, llm: Any | None = None, ceiling_per_capita: float | None = None,
           rng: random.Random | None = None, display_unit: str | None = None,
           display_scale: float = 1.0) -> dict[str, Any] | None:
    """known: {country: total value of the metric}. Returns the estimate
    (total, per-capita, P10/P90, weights, flags, track record) or None."""
    target = cov.get(country)
    if target is None:
        return None
    peers = []
    for c, v in known.items():
        cv = cov.get(c)
        if cv and v and v > 0 and c != country:
            peers.append(S.Known(c, cv, v / cv["population"]))
    if not peers:
        return None
    rich = richness(len(peers))
    lessons = policy.lessons.get(domain, [])
    llm_call = None
    if llm is not None and policy.want_llm(domain, rich, rng):
        def llm_call() -> dict[str, float]:
            return S.llm_brain(llm, product=product, metric=metric, unit=display_unit or unit, target_name=country,
                               target=target, known=peers, lessons=lessons, display_scale=display_scale)
    preds = S.predict_all(target, peers, llm_call)
    res = policy.combine(domain, rich, preds)

    flags = []
    lo_peer = min(math.log(p.per_capita) for p in peers) - math.log(REALISM_FACTOR)
    hi_peer = max(math.log(p.per_capita) for p in peers) + math.log(REALISM_FACTOR)
    mu = res["mu"]
    if not lo_peer <= mu <= hi_peer:
        flags.append(f"estimate outside {REALISM_FACTOR:g}x of the peer range -- clipped")
        mu = min(max(mu, lo_peer), hi_peer)
    if ceiling_per_capita and mu > math.log(ceiling_per_capita):
        flags.append(f"estimate above the physical ceiling {ceiling_per_capita:g}/person -- clipped")
        mu = math.log(ceiling_per_capita)
    shift = mu - res["mu"]
    pop = target["population"]
    track = policy.track_record(domain, rich)
    out = {
        "value": math.exp(mu) * pop, "p10": math.exp(res["lo"] + shift) * pop, "p90": math.exp(res["hi"] + shift) * pop,
        "per_capita": math.exp(mu), "population": pop, "n_peers": len(peers), "richness": rich,
        "strategies": {k: math.exp(v) for k, v in preds.items()}, "weights": res["weights"], "bias": res["bias"],
        "flags": flags, "track_record": track, "policy_version": policy.version, "label": "estimated_from_facts",
        "used_llm": any(k in preds for k in S.LLM),
    }
    top = sorted(res["weights"].items(), key=lambda kv: -kv[1])[:3]
    out["reason"] = (f"assumptor (policy v{policy.version}): per-capita {out['per_capita']:.4g} {unit}/person from "
                     f"{len(peers)} peer countries; strategies " + ", ".join(f"{k} {w:.0%}" for k, w in top)
                     + f"; 80% interval {out['p10']:,.0f}–{out['p90']:,.0f}"
                     + (f"; track record in {track['context']}: typical error x{track['typical_error_x']}, "
                        f"80% coverage {track['coverage_80']:.0%} over {track['episodes']} episodes"
                        if track["context"] else "; no training history for this context yet")
                     + (f"; {'; '.join(flags)}" if flags else ""))
    return out

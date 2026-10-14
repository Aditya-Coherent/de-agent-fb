"""Plausibility score 0-10 for one agent pass (`src/newinfo/system.md` §8).

| Component             | Weight | Measure                                                     |
|-----------------------|--------|-------------------------------------------------------------|
| Reviewer rubric mean  | 0.40   | mean of the reviewer's five 1-10 scores                     |
| Physical plausibility | 0.20   | share of countries with per-capita inside [floor, ceiling]   |
| Evidence coverage     | 0.20   | share of global value whose weakest input is verified        |
| Soft-flag count       | 0.10   | 10 - penalty per soft flag                                  |
| Mix sanity            | 0.10   | no use case above the dominance threshold without reviewer   |
|                       |        | justification                                               |

Distance to published market sizes is deliberately NOT a component, so the
loop cannot learn to copy consensus. A component that can't be measured
(e.g. no per-capita band for the market) is dropped and the remaining
weights are renormalised -- reported as such, never silently scored 10.
"""
from __future__ import annotations

from typing import Any


def score(cfg: dict[str, Any], rubric_mean: float, per_capita: dict[str, float], band: dict[str, Any] | None,
          verified_share: float, soft_flags: list[dict[str, Any]], mix_rubric: float | None) -> dict[str, Any]:
    a = cfg["agent"]
    w = a["weights"]
    comps: dict[str, float | None] = {
        "reviewer": max(0.0, min(10.0, rubric_mean)),
        "physical": (10.0 * sum(1 for v in per_capita.values() if band["floor"] <= v <= band["ceiling"])
                     / len(per_capita)) if band and per_capita else None,
        "coverage": 10.0 * max(0.0, min(1.0, verified_share)),
        "soft_flags": max(0.0, 10.0 - a["soft_flag_penalty"] * len(soft_flags)),
    }
    dominated = any(f["check"] == "use_case_dominance" for f in soft_flags)
    comps["mix"] = 10.0 if not dominated else (5.0 if (mix_rubric or 0.0) >= 7.0 else 0.0)
    used = {k: v for k, v in comps.items() if v is not None}
    total_w = sum(w[k] for k in used)
    value = sum(w[k] * v for k, v in used.items()) / total_w if total_w else 0.0
    return {"score": round(value, 3), "components": comps,
            "dropped": [k for k, v in comps.items() if v is None], "weights": w}


def accepted(cfg: dict[str, Any], plaus: dict[str, Any], hard_flags: list[dict[str, Any]]) -> bool:
    """Score >= min_score, no hard flag, and the reviewer itself not below
    `min_reviewer` -- otherwise strong evidence coverage can outvote a
    reviewer who rejects the build (seen: olive oil R001, rubric 2.8 -> 7.02)."""
    return (plaus["score"] >= cfg["agent"]["min_score"] and not hard_flags
            and plaus["components"]["reviewer"] >= cfg["agent"].get("min_reviewer", 0.0))


def best_pass(passes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Highest plausibility; ties -> higher verified-value share. Passes with
    hard flags lose to any pass without."""
    sized = [p for p in passes if p.get("plausibility")]
    if not sized:
        return None
    return max(sized, key=lambda p: (not p.get("hard_flags"), p["plausibility"]["score"],
                                     p["plausibility"]["components"]["coverage"]))

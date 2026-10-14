"""The assumptor's learned policy, updated from rewards (the RL loop).

State, per context (domain x data richness):
    per strategy   running stats of its log error  e = ln(pred) - ln(truth):
                   n, mean (-> bias correction), M2 (-> error spread sigma)
    scale          interval width multiplier, tuned so that the stated
                   80% interval contains the truth 80% of the time
    lessons        short notes the LLM wrote about its own big misses

Action = how to weight the strategies (softmax over each strategy's robust
learned error, after bias correction) and how wide to make the interval. Reward per episode =
-(log interval score at 80%): sharp AND calibrated estimates score best.
Exploration: Thompson sampling decides whether to pay for the LLM arm.

Priors (n0 pseudo-observations, sigma0) keep a context with little history
close to cautious defaults; contexts back off to `domain|*`, then `*|richness`,
then `*|*` until they have their own history.
"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

Z80 = 1.2816          # two-sided 80% interval
ALPHA = 0.2
N0 = 3.0              # prior pseudo-observations per strategy
SIGMA0 = 1.0          # prior log-error spread (~ x2.7)
SCALE_LR = 0.05       # calibration step
MIN_N_OWN = 20        # observations before a context stops backing off
TAU = 0.15            # weight temperature: a strategy 0.15 worse in typical log error gets 1/e the weight
ROBUST = 1.2533       # mean |e| -> sigma for a normal; robust to the heavy tails of country data


def richness(n_known: int) -> str:
    return "n<=3" if n_known <= 3 else "n4-7" if n_known <= 7 else "n8-15" if n_known <= 15 else "n16+"


def interval_score(lo: float, hi: float, y: float, alpha: float = ALPHA) -> float:
    """Winkler interval score in log space (lower is better)."""
    return (hi - lo) + (2 / alpha) * max(0.0, lo - y) + (2 / alpha) * max(0.0, y - hi)


class Policy:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        data = data or {}
        self.contexts: dict[str, dict[str, Any]] = data.get("contexts", {})
        self.lessons: dict[str, list[str]] = data.get("lessons", {})
        self.history: list[dict[str, Any]] = data.get("history", [])
        self.version: int = data.get("version", 0)

    # ---- persistence ---------------------------------------------------- #
    @staticmethod
    def path(root: Path | None = None) -> Path:
        return (Path(root) if root else Path(".")) / "output" / "assumptor" / "policy.json"

    @classmethod
    def load(cls, root: Path | None = None) -> "Policy":
        p = cls.path(root)
        return cls(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else cls()

    def save(self, root: Path | None = None) -> Path:
        p = self.path(root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"version": self.version, "contexts": self.contexts, "lessons": self.lessons,
                                 "history": self.history[-200:]}, indent=1), encoding="utf-8")
        return p

    # ---- lookup with back-off --------------------------------------------- #
    def _ctx(self, key: str) -> dict[str, Any]:
        return self.contexts.setdefault(key, {"strategies": {}, "scale": 1.0, "n": 0, "covered": 0})

    def resolve(self, domain: str, rich: str) -> list[str]:
        """Context keys from most to least specific."""
        return [f"{domain}|{rich}", f"{domain}|*", f"*|{rich}", "*|*"]

    def _stats(self, domain: str, rich: str, strategy: str) -> tuple[float, float, float]:
        """(n, bias, sigma) for a strategy, from the most specific context with
        enough history, shrunk toward the prior."""
        for key in self.resolve(domain, rich):
            s = self.contexts.get(key, {}).get("strategies", {}).get(strategy)
            if s and (s["n"] >= MIN_N_OWN or key == "*|*"):
                break
        else:
            s = None
        if not s or s["n"] == 0:
            return 0.0, 0.0, SIGMA0
        n = s["n"]
        bias = s["mean"] * n / (n + N0)
        abs_mean = s.get("abs", math.sqrt(s["m2"] / n) / ROBUST)  # older policies: fall back to the SD
        sigma = ROBUST * (n * abs_mean + N0 * SIGMA0 / ROBUST) / (n + N0)
        return n, bias, sigma

    def scale(self, domain: str, rich: str) -> float:
        for key in self.resolve(domain, rich):
            c = self.contexts.get(key)
            if c and (c["n"] >= MIN_N_OWN or key == "*|*"):
                return c["scale"]
        return 1.0

    # ---- action ---------------------------------------------------------- #
    def combine(self, domain: str, rich: str, preds: dict[str, float], learned: bool = True) -> dict[str, Any]:
        """Bias-corrected, inverse-variance ensemble in log space + calibrated 80% interval.
        learned=False is the untrained baseline (equal weights, no correction, scale 1)."""
        if not preds:
            raise ValueError("no strategy could run")
        parts = {}
        for s, v in preds.items():
            n, bias, sigma = self._stats(domain, rich, s) if learned else (0.0, 0.0, SIGMA0)
            parts[s] = {"pred": v, "corrected": v - bias, "sigma": sigma, "n": n, "bias": bias}
        best = min(p["sigma"] for p in parts.values())
        for p in parts.values():  # learned selection: softmax over robust error (equal weights when untrained)
            p["w"] = math.exp(-(p["sigma"] - best) / TAU) if learned else 1.0
        tw = sum(p["w"] for p in parts.values())
        mu = sum(p["w"] * p["corrected"] for p in parts.values()) / tw
        # spread: ensemble disagreement + the best single strategy's learned error, never below either
        disagreement = math.sqrt(sum(p["w"] * (p["corrected"] - mu) ** 2 for p in parts.values()) / tw)
        sigma = max(min(p["sigma"] for p in parts.values()), disagreement)
        half = Z80 * sigma * (self.scale(domain, rich) if learned else 1.0)
        return {"mu": mu, "lo": mu - half, "hi": mu + half, "sigma": sigma,
                "weights": {s: p["w"] / tw for s, p in parts.items()},
                "bias": {s: p["bias"] for s, p in parts.items()}}

    def want_llm(self, domain: str, rich: str, rng: random.Random | None = None) -> bool:
        """Thompson sampling: draw a plausible error spread for the LLM arm and
        for the best free strategy; pay for the LLM when its draw is smaller."""
        rng = rng or random
        def draw(strategy: str) -> float:
            n, _, sigma = self._stats(domain, rich, strategy)
            return sigma * math.sqrt((n + N0) / max(rng.gammavariate((n + N0) / 2, 2.0), 1e-9))
        free = min(draw(s) for s in ("peer_income", "peer_region", "elasticity", "global_median"))
        return min(draw("llm_prior"), draw("llm_analog")) < free

    # ---- learning (the RL update) --------------------------------------- #
    def update(self, domain: str, rich: str, preds: dict[str, float], result: dict[str, Any], truth: float) -> float:
        """One episode: update every strategy's error stats in the specific and
        back-off contexts, nudge the interval scale toward 80% coverage, and
        return the reward."""
        covered = result["lo"] <= truth <= result["hi"]
        for key in self.resolve(domain, rich):
            c = self._ctx(key)
            for s, v in preds.items():
                st = c["strategies"].setdefault(s, {"n": 0, "mean": 0.0, "m2": 0.0, "abs": 0.0})
                e = v - truth
                st["n"] += 1
                d = e - st["mean"]
                st["mean"] += d / st["n"]
                st["m2"] += d * (e - st["mean"])
                st["abs"] = st.get("abs", 0.0) + (abs(e - st["mean"]) - st.get("abs", 0.0)) / st["n"]
            c["n"] += 1
            c["covered"] += int(covered)
            # recent behaviour, not the lifetime average (early episodes are pre-learning)
            c["cov_ema"] = 0.97 * c.get("cov_ema", 1 - ALPHA) + 0.03 * float(covered)
            c["abs_err_ema"] = 0.97 * c.get("abs_err_ema", SIGMA0) + 0.03 * abs(result["mu"] - truth)
            # stochastic approximation toward 80% coverage: widen on a miss, shrink on a hit
            c["scale"] = max(0.3, min(5.0, c["scale"] * math.exp(SCALE_LR * ((0 if covered else 1) - ALPHA) / ALPHA)))
        self.version += 1
        return -interval_score(result["lo"], result["hi"], truth)

    def add_lesson(self, domain: str, lesson: str, cap: int = 30) -> None:
        items = self.lessons.setdefault(domain, [])
        if lesson and lesson not in items:
            items.append(lesson)
            del items[:-cap]

    def record(self, entry: dict[str, Any]) -> None:
        self.history.append({"at": datetime.now(timezone.utc).isoformat(), "version": self.version, **entry})

    def track_record(self, domain: str, rich: str) -> dict[str, Any]:
        for key in self.resolve(domain, rich):
            c = self.contexts.get(key)
            if c and c["n"] >= MIN_N_OWN:
                return {"context": key, "episodes": c["n"], "coverage_80": round(c.get("cov_ema", 0.0), 3),
                        "typical_error_x": round(math.exp(c.get("abs_err_ema", SIGMA0)), 2)}
        return {"context": None, "episodes": 0, "coverage_80": None, "typical_error_x": None}

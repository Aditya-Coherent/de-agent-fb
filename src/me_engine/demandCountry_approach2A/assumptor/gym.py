"""Training environment for the assumptor (the RL loop).

Episodes are built from real, official per-country statistics (the local
FAOSTAT cache: ~116 food items x ~180 countries, 23 fertilizers x ~180):

    1. pick a product and a target country; its real value is HIDDEN
    2. reveal only n other countries (n = 3, 5, 10, 20 or all), sampled with
       a bias toward large countries -- the realistic "thin data" situation
    3. the assumptor estimates the target (strategies -> policy ensemble)
    4. reward = -(log interval score): accurate AND honest about uncertainty
    5. the policy updates (bias, spread, weights, interval scale); big LLM
       misses become written lessons fed back into later prompts

Products are split by hash into train / test, so evaluation always uses
products the policy never trained on. `evaluate(learned=False)` is the
untrained baseline on the same episodes.
"""
from __future__ import annotations

import hashlib
import math
import random
import statistics
from dataclasses import dataclass
from typing import Any, Callable

from . import strategies as S
from .covariates import Covariates
from .policy import Policy, interval_score, richness

N_CHOICES = (3, 5, 10, 20, 0)          # 0 = every other country
MIN_COUNTRIES = 15


@dataclass
class Task:
    domain: str
    product: str
    unit: str
    values: dict[str, float]           # ISO3 -> level (latest 3-year mean)

    @property
    def split(self) -> str:
        h = int(hashlib.sha1(f"{self.domain}|{self.product}".encode()).hexdigest(), 16)
        return "test" if h % 5 == 0 else "train"


def faostat_tasks(cov: Covariates) -> list[Task]:
    from ..topdown import official_data as od

    cache = od.load_cache()
    tasks = []
    for ds, domain, unit in (("fbs", "food", "t"), ("rfb", "fertilizer", "t")):
        for item, areas in (cache.get(ds) or {}).items():
            values = {}
            for area, series in areas.items():
                iso = cov.iso3(area)
                if not iso or not cov.get(iso):
                    continue
                pts = [v for _, v in sorted(series.items(), key=lambda kv: int(kv[0])) if v and v > 0][-3:]
                if pts:
                    values[iso] = sum(pts) / len(pts)
            if len(values) >= MIN_COUNTRIES:
                tasks.append(Task(domain, item, unit, values))
    return tasks


def _episode(task: Task, cov: Covariates, rng: random.Random) -> tuple[str, dict[str, float], float] | None:
    target = rng.choice(list(task.values))
    others = [c for c in task.values if c != target]
    n = rng.choice(N_CHOICES) or len(others)
    n = min(n, len(others))
    weights = [math.sqrt(cov.get(c)["population"]) for c in others]
    known: dict[str, float] = {}
    while len(known) < n:
        c = rng.choices(others, weights)[0]
        known[c] = task.values[c]
    return target, known, task.values[target]


def run_episode(policy: Policy, task: Task, cov: Covariates, rng: random.Random, *, learn: bool, learned: bool = True,
                llm: Any | None = None, lessons: bool = False) -> dict[str, Any] | None:
    ep = _episode(task, cov, rng)
    if ep is None:
        return None
    target_iso, known_raw, truth_total = ep
    target = cov.get(target_iso)
    peers = [S.Known(c, cov.get(c), v / cov.get(c)["population"]) for c, v in known_raw.items()]
    rich = richness(len(peers))
    truth = math.log(truth_total / target["population"])
    llm_call = None
    if llm is not None and learned and policy.want_llm(task.domain, rich, rng):
        def llm_call() -> dict[str, float]:
            return S.llm_brain(llm, product=task.product, metric="apparent consumption", unit="kg",
                               target_name=target["name"], target=target, known=peers,
                               lessons=policy.lessons.get(task.domain, []), display_scale=1000.0)
    preds = S.predict_all(target, peers, llm_call)
    if not preds:
        return None
    res = policy.combine(task.domain, rich, preds, learned=learned)
    score = interval_score(res["lo"], res["hi"], truth)
    out = {"product": task.product, "country": target["name"], "richness": rich, "abs_log_err": abs(res["mu"] - truth),
           "covered": res["lo"] <= truth <= res["hi"], "interval_score": score, "used_llm": any(k in preds for k in S.LLM)}
    if learn:
        out["reward"] = policy.update(task.domain, rich, preds, res, truth)
        if lessons and llm is not None and "llm_prior" in preds and abs(preds["llm_prior"] - truth) > math.log(2.5):
            _write_lesson(llm, policy, task, target, preds["llm_prior"], truth)
    return out


def _write_lesson(llm: Any, policy: Policy, task: Task, target: dict[str, Any], pred: float, truth: float) -> None:
    try:
        data = llm.complete_json(
            "You review your own estimation mistake and write ONE general, reusable lesson (not about this "
            "single number). Return json.",
            f"Product: {task.product}. Country: {target['name']} ({target.get('region')}, GDP pc PPP "
            f"{target['gdp_pc_ppp']:,.0f}). You estimated {1000 * math.exp(pred):.4g} kg per person per year; the "
            f"real value was {1000 * math.exp(truth):.4g} kg ({math.exp(pred - truth):.2f}x).\n"
            'Return JSON: {"lesson": "<one sentence, general rule for similar products/countries>"}',
        )
        policy.add_lesson(task.domain, str(data.get("lesson") or "").strip()[:240])
    except Exception:  # noqa: BLE001 -- lessons are optional
        pass


def summarise(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return {"episodes": 0}
    errs = sorted(r["abs_log_err"] for r in results)
    return {"episodes": len(results), "typical_error_x": round(math.exp(statistics.median(errs)), 3),
            "p90_error_x": round(math.exp(errs[int(0.9 * (len(errs) - 1))]), 3),
            "within_2x": round(sum(e <= math.log(2) for e in errs) / len(errs), 3),
            "coverage_80": round(sum(r["covered"] for r in results) / len(results), 3),
            "interval_score": round(statistics.mean(r["interval_score"] for r in results), 3),
            "llm_share": round(sum(r["used_llm"] for r in results) / len(results), 3)}


def evaluate(policy: Policy, tasks: list[Task], cov: Covariates, episodes: int, seed: int = 7,
             learned: bool = True, llm: Any | None = None) -> dict[str, Any]:
    """Frozen evaluation on the given tasks (no learning). Same seed -> same episodes."""
    rng = random.Random(seed)
    res = []
    for _ in range(episodes):
        r = run_episode(policy, rng.choice(tasks), cov, rng, learn=False, learned=learned, llm=llm)
        if r:
            res.append(r)
    out = summarise(res)
    out["by_richness"] = {k: summarise([r for r in res if r["richness"] == k])
                          for k in ("n<=3", "n4-7", "n8-15", "n16+")}
    return out


def train(policy: Policy, tasks: list[Task], cov: Covariates, episodes: int, seed: int = 1, llm: Any | None = None,
          llm_budget: int = 0, lessons: bool = False, say: Callable[[str], None] = print, every: int = 500) -> dict[str, Any]:
    rng = random.Random(seed)
    res = []
    budget = llm_budget
    for i in range(1, episodes + 1):
        use_llm = llm if budget > 0 else None
        r = run_episode(policy, rng.choice(tasks), cov, rng, learn=True, llm=use_llm, lessons=lessons)
        if r:
            res.append(r)
            budget -= int(r["used_llm"])
        if i % every == 0:
            s = summarise(res[-every:])
            say(f"[train] {i}/{episodes}: typical error x{s['typical_error_x']}, within 2x {s['within_2x']:.0%}, "
                f"80% coverage {s['coverage_80']:.0%}, interval score {s['interval_score']:.2f}")
    return summarise(res)

"""Train the assumptor's policy on hidden official statistics (the RL loop).

    python train_assumptor.py --episodes 20000                 # free strategies only
    python train_assumptor.py --episodes 4000 --llm-budget 300 --lessons   # + DeepSeek arm and lessons
    python train_assumptor.py --fresh ...                      # start from an untrained policy

Evaluates the untrained baseline and the trained policy on the SAME episodes
from held-out products (never trained on), then saves
output/assumptor/policy.json and output/assumptor/training_report.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from me_engine.demandCountry_approach2A.assumptor import gym  # noqa: E402
from me_engine.demandCountry_approach2A.assumptor.covariates import Covariates  # noqa: E402
from me_engine.demandCountry_approach2A.assumptor.policy import Policy  # noqa: E402


def _row(name: str, s: dict) -> str:
    return (f"| {name} | {s['episodes']} | x{s['typical_error_x']} | x{s['p90_error_x']} | {s['within_2x']:.0%} | "
            f"{s['coverage_80']:.0%} | {s['interval_score']:.2f} |")


def main() -> int:
    p = argparse.ArgumentParser(description="Train the assumptor policy (reward = log interval score on hidden truths).")
    p.add_argument("--episodes", type=int, default=20000)
    p.add_argument("--eval-episodes", type=int, default=3000)
    p.add_argument("--llm-budget", type=int, default=0, help="max DeepSeek calls during training (0 = free strategies only)")
    p.add_argument("--lessons", action="store_true", help="let the LLM write lessons from its big misses")
    p.add_argument("--fresh", action="store_true", help="start from an untrained policy")
    p.add_argument("--llm-eval", type=int, default=300, help="held-out episodes evaluated WITH the LLM brain (needs --llm-budget)")
    p.add_argument("--model", default="deepseek-chat")
    p.add_argument("--cache", default="output/.gen_cache.db")
    args = p.parse_args()

    cov = Covariates.load(ROOT)
    tasks = gym.faostat_tasks(cov)
    train_tasks = [t for t in tasks if t.split == "train"]
    test_tasks = [t for t in tasks if t.split == "test"]
    print(f"[gym] {len(tasks)} products ({len(train_tasks)} train / {len(test_tasks)} held-out test), "
          f"{sum(len(t.values) for t in tasks):,} country truths")

    llm = None
    if args.llm_budget:
        from demand_country_2a_agent import check_llm, deepseek_config
        from me_engine.generation.llm import GenLLM
        cfg = deepseek_config(args.model)
        if (err := check_llm(cfg)):
            print(f"ERROR: DeepSeek not reachable: {err}")
            return 2
        llm = GenLLM(cfg, cache_path=ROOT / args.cache)

    policy = Policy() if args.fresh else Policy.load(ROOT)
    start_version = policy.version
    baseline = gym.evaluate(Policy(), test_tasks, cov, args.eval_episodes, learned=False)
    before = gym.evaluate(policy, test_tasks, cov, args.eval_episodes)
    print(f"[eval] untrained baseline: typical error x{baseline['typical_error_x']}, coverage {baseline['coverage_80']:.0%}")
    print(f"[eval] policy v{policy.version} before: typical error x{before['typical_error_x']}, coverage {before['coverage_80']:.0%}")

    train_stats = gym.train(policy, train_tasks, cov, args.episodes, llm=llm, llm_budget=args.llm_budget,
                            lessons=args.lessons, every=max(500, args.episodes // 10))
    after = gym.evaluate(policy, test_tasks, cov, args.eval_episodes)
    print(f"[eval] policy v{policy.version} after: typical error x{after['typical_error_x']}, coverage {after['coverage_80']:.0%}")
    with_llm = None
    if llm is not None and args.llm_eval:
        with_llm = gym.evaluate(policy, test_tasks, cov, args.llm_eval, seed=13, llm=llm)
        free_same = gym.evaluate(policy, test_tasks, cov, args.llm_eval, seed=13)
        print(f"[eval] same {args.llm_eval} episodes -- free strategies: typical error x{free_same['typical_error_x']}, "
              f"coverage {free_same['coverage_80']:.0%} | with LLM brain: x{with_llm['typical_error_x']}, "
              f"coverage {with_llm['coverage_80']:.0%} (LLM used in {with_llm['llm_share']:.0%})")
    policy.record({"episodes": args.episodes, "llm_budget": args.llm_budget, "baseline": baseline,
                   "before": before, "after": after})
    path = policy.save(ROOT)

    report = ROOT / "output" / "assumptor" / "training_report.md"
    L = [f"# Assumptor training report — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", "",
         f"Policy v{start_version} → v{policy.version} after {args.episodes:,} training episodes "
         f"({len(train_tasks)} training products). Evaluation: {args.eval_episodes:,} episodes on "
         f"{len(test_tasks)} held-out products the policy never saw, same episodes for every row.", "",
         "Typical error = median of max(est/true, true/est); interval = stated 80% range.", "",
         "| Policy | Episodes | Typical error | 90th pct error | Within 2× | 80% coverage | Interval score |",
         "|---|---|---|---|---|---|---|",
         _row("Untrained baseline (equal weights, no correction)", baseline),
         _row(f"Before this training (v{start_version})", before), _row(f"After (v{policy.version})", after)]
    if with_llm:
        L += [_row(f"After, free strategies only ({args.llm_eval} LLM-eval episodes)", free_same),
              _row(f"After, with the LLM brain (same {args.llm_eval} episodes)", with_llm)]
    L += ["",
         "## After, by how much data was available", "",
         "| Known countries | Episodes | Typical error | 90th pct error | Within 2× | 80% coverage | Interval score |",
         "|---|---|---|---|---|---|---|"]
    L += [_row(k, s) for k, s in after["by_richness"].items() if s.get("episodes")]
    L += ["", "## Learned strategy bias and spread (`*|*` context)", "", "| Strategy | n | Bias (×) | Spread σ (log) |",
          "|---|---|---|---|"]
    for s in ("peer_income", "peer_region", "elasticity", "global_median", "llm_prior", "llm_analog"):
        n, bias, sigma = policy._stats("*", "*", s)
        if n:
            L.append(f"| {s} | {int(n)} | ×{2.718281828 ** bias:.2f} | {sigma:.2f} |")
    if any(policy.lessons.values()):
        L += ["", "## Lessons", ""] + [f"- [{d}] {x}" for d, xs in policy.lessons.items() for x in xs]
    report.write_text("\n".join(L), encoding="utf-8")
    print(f"[save] {path}\n[report] {report}")
    print(json.dumps({"baseline": {k: v for k, v in baseline.items() if k != "by_richness"},
                      "after": {k: v for k, v in after.items() if k != "by_richness"}}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

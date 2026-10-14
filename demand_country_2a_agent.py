"""Demand-Side 2A, autonomous: one command per market, DeepSeek as the brain.

Runs every 2A phase end to end (setup -> driver -> size -> review loop ->
segments -> report) without the human approval gates; see
`me_engine.demandCountry_approach2A.agent`. For the step-by-step,
human-approved flow use `demand_country_2a_pipeline.py`.

Usage:
    python demand_country_2a_agent.py --market "Olive Oil"
    python demand_country_2a_agent.py --market "Laptops" --market "Televisions" --max-passes 3
    # -> output/demand_country_2a/<slug>/04_market.xlsx + 05_agent_summary.json

The actual 2025 size in approaches/examples/COMPARISON_VS_ACTUAL.md, when
listed, is printed next to the result for comparison only -- the agent never
sees it.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from me_engine.demandCountry_approach2A.agent import run_agent  # noqa: E402
from me_engine.generation.config import GenConfig  # noqa: E402
from me_engine.generation.llm import GenLLM  # noqa: E402

_ACTUALS_MD = ROOT / "approaches" / "examples" / "COMPARISON_VS_ACTUAL.md"


def load_actuals(path: Path = _ACTUALS_MD) -> dict[str, float]:
    """{market name (lower case): actual 2025 US$ Mn} from the comparison table."""
    actuals: dict[str, float] = {}
    if not path.exists():
        return actuals
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        try:
            actuals[cells[0].lower()] = float(cells[3].replace(",", ""))
        except ValueError:
            continue
    return actuals


def deepseek_config(model: str) -> GenConfig:
    cfg = GenConfig.load(ROOT)
    if "deepseek" not in cfg.base_url:
        cfg = dataclasses.replace(cfg, base_url="https://api.deepseek.com")
    return dataclasses.replace(cfg, model=model)


def check_llm(cfg: GenConfig) -> str | None:
    """None when the LLM answers, else the error (e.g. an invalid API key)."""
    if not cfg.is_online:
        return "no API key in .env (DEEPSEEK_API_KEY)"
    try:
        GenLLM(cfg).complete_json("Health check.", 'Return {"ok": true}', retries=1)
    except Exception as exc:  # noqa: BLE001
        return str(exc)
    return None


def print_result(summary: dict, actual: float | None) -> None:
    total = summary.get("global_usd_mn") or 0.0
    print(f"\n=== {summary['market_name']} (2A, {summary['model']}, run {summary.get('run_id')}) ===")
    print(f"Global 2025 (raw bottom-up): ${total:,.1f}M" + (
        f"   vs actual ${actual:,.0f}M ({(total - actual) / actual:+.1%})" if actual else ""))
    if summary.get("publisher"):
        pub = summary["publisher"]
        print(f"Publisher: ${pub['value_usd_mn']:,.1f}M (k = {pub['k']:.3f})")
    print(f"Status: {summary.get('status')} | verified value {summary.get('verified_value_share', 0):.0%} | "
          f"hard {len(summary.get('hard_flags') or [])} soft {len(summary.get('soft_flags') or [])}")
    for f in (summary.get("hard_flags") or []) + (summary.get("soft_flags") or []):
        print(f"  [{f['severity']}] {f['check']}: {f['message']}")
    print(f"Kept pass {summary['kept_pass']} of {len(summary['passes'])} -- "
          + ("accepted by review" if summary["accepted"] else "best available, NOT accepted by review"))
    for p in summary["passes"]:
        drv = (p.get("driver") or {}).get("driver_name")
        if p["status"] == "rejected_before_sizing":
            print(f"  pass {p['pass']}: rejected before sizing -- {drv}")
        else:
            print(f"  pass {p['pass']}: ${p['global_usd_mn']:,.1f}M, plausibility {p['score']:.2f}/10, {p['status']} -- {drv}")
    for region, value in (summary.get("regions_usd_mn") or {}).items():
        print(f"  {region}: ${value:,.1f}M")
    critical = [f for f in summary.get("quality_flags") or [] if f.get("severity") == "critical"]
    if critical:
        print("  Critical flags: " + ", ".join(f.get("code", "") for f in critical))
    for key in ("report_xlsx", "documentation"):
        if summary["outputs"].get(key):
            print(f"  {key}: {summary['outputs'][key]}")
    print(f"  Run folder: output/demand_country_2a/<slug>/runs/{summary.get('run_id')}/ (summary.md, ledger, progress.mermaid)")


def main() -> int:
    p = argparse.ArgumentParser(description="Autonomous Demand-Side 2A agent (DeepSeek).")
    p.add_argument("--market", action="append", required=True, help="Market name; repeat for several")
    p.add_argument("--max-passes", type=int, default=None, help="default: agent.max_passes in the design config (3)")
    p.add_argument("--publisher", action="store_true", help="blend with the published consensus (system.md §12)")
    p.add_argument("--model", default="deepseek-chat")
    p.add_argument("--cache", default="output/.gen_cache.db", help="LLM cache path (shared with the other pipelines)")
    args = p.parse_args()

    cfg = deepseek_config(args.model)
    error = check_llm(cfg)
    if error:
        print(f"ERROR: DeepSeek is not reachable with the configured key/model ({cfg.model}): {error}")
        return 2

    actuals = load_actuals()
    results = []
    for market in args.market:
        try:
            summary = run_agent(market, cfg, cache_path=ROOT / args.cache, max_passes=args.max_passes,
                                design={"publisher": {"enabled": True}} if args.publisher else None)
        except Exception as exc:  # noqa: BLE001 -- one market failing must not stop the others
            print(f"\nERROR: {market}: {exc}")
            results.append({"market": market, "error": str(exc)})
            continue
        print_result(summary, actuals.get(market.lower()))
        actual = actuals.get(market.lower())
        results.append({"market": market, "global_usd_mn": summary["global_usd_mn"], "actual_usd_mn": actual,
                        "pct_diff": round((summary["global_usd_mn"] - actual) / actual * 100, 1) if actual else None,
                        "accepted": summary["accepted"]})

    if len(results) > 1:
        print("\n=== All markets ===")
        print(json.dumps(results, indent=2))
    return 0 if all("error" not in r for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

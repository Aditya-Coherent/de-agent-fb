"""2A signal-model experiment: per-country demand from a sigmoid under a
physical ceiling, over hard data + LLM-scored qualitative evidence + an
embedding signal, calibrated on countries with a figure
(see `me_engine.demandCountry_approach2A.signal_model`).

Usage:
    python demand_country_2a_signal.py --market "Televisions"
    python demand_country_2a_signal.py --market "Televisions" --countries 40 --fresh
    python demand_country_2a_signal.py --market "Televisions" --runs 3 --ablations
    # -> output/demand_country_2a/<slug>/signal_model/run_<k>/07_result.json
    #    + signal_model/08_runs_summary.json

--runs N: run 1 uses the cached LLM; runs 2..N call the LLM uncached, so the
LLM's judgments (scores, anchors, knowledge estimates, ceiling, price) are
re-sampled while search results and hard data stay fixed. The reported
answer is the MEDIAN across runs, with the min-max spread.
--ablations: refits run 1 with one component switched off at a time
(qualitative scores, embedding signal, LLM-knowledge anchors, hard data) --
no new LLM calls -- to show what each component contributes.

Steps are cached in that folder; --fresh deletes the cache first. The actual
size in approaches/examples/COMPARISON_VS_ACTUAL.md is printed for
comparison only -- the model never sees it.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from demand_country_2a_agent import check_llm, deepseek_config, load_actuals  # noqa: E402
import statistics  # noqa: E402

from me_engine.demandCountry_approach2A.signal_model import (  # noqa: E402
    HARD_FEATURES, RUBRIC, checkpoints, run_signal_model, signal_dir,
)
from me_engine.generation.llm import GenLLM  # noqa: E402


def _m(units: float | None) -> str:
    return f"{units / 1e6:,.2f}" if units else "-"


def market_vertical(market: str) -> str | None:
    """Vertical from the comparison table (column 2), e.g. 'Consumer Electronics'."""
    path = ROOT / "approaches" / "examples" / "COMPARISON_VS_ACTUAL.md"
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) > 2 and cells[0].lower() == market.lower():
            return cells[1]
    return None


def _vs(value: float, actual: float | None) -> str:
    return f"   vs actual ${actual:,.0f}M ({(value - actual) / actual:+.1%})" if actual else ""


def main() -> int:
    p = argparse.ArgumentParser(description="2A signal model (sigmoid under a physical ceiling).")
    p.add_argument("--market", required=True)
    p.add_argument("--countries", type=int, default=40)
    p.add_argument("--model", default="deepseek-chat")
    p.add_argument("--fresh", action="store_true", help="Delete cached steps and rerun everything")
    p.add_argument("--cache", default="output/.gen_cache.db")
    p.add_argument("--runs", type=int, default=1, help="Repeat runs (2..N re-sample the LLM uncached)")
    p.add_argument("--ablations", action="store_true", help="Refit run 1 with each component switched off")
    p.add_argument("--vertical", default=None, help="Domain for the facts library (default: from the comparison table)")
    args = p.parse_args()

    cfg = deepseek_config(args.model)
    error = check_llm(cfg)
    if error:
        print(f"ERROR: DeepSeek is not reachable: {error}")
        return 2
    if args.fresh:
        shutil.rmtree(signal_dir(args.market), ignore_errors=True)

    cached_llm = GenLLM(cfg, cache_path=ROOT / args.cache)
    vertical = args.vertical or market_vertical(args.market) or "General"
    r = run_signal_model(args.market, cached_llm, n_countries=args.countries, vertical=vertical)
    actual = load_actuals().get(args.market.lower())

    print(f"\n=== {args.market}: 2A signal model v5 ({vertical}) ===")
    print(f"Calibration: {r['n_trade_anchors']} trade + {r['n_mass_balance_anchors']} mass-balance + "
          f"{r['n_anchors']} web-evidence anchors + "
          f"{r['n_estimator_points']} estimator + {r['n_knowledge_points']} LLM-knowledge points | "
          f"leave-one-out median error per country: "
          f"{r['loo_median_abs_pct_error']}% (hard anchors only: {r['loo_median_abs_pct_error_hard_anchors']}%) | "
          f"embedding signal {'on' if r['embedding_signal_used'] else 'OFF'}")
    c = r["ceiling"]
    print(f"Ceiling: {c['items_per_household']['value']:g} per household / {c['replacement_years']['value']:g}-year "
          f"replacement ({'evidence' if c['items_per_household']['verified_quote'] else 'estimate'} / "
          f"{'evidence' if c['replacement_years']['verified_quote'] else 'estimate'})")
    print("Weights: " + ", ".join(f"{k} {v:+.2f}" for k, v in r["params"]["weights"].items()))
    pr = r["price"]
    print(f"Price: unit-weighted average ${pr['avg_usd_per_unit']:,.0f}/unit -- {pr['basis']}")

    print(f"\n{'Country':<24}{'Units (M)':>10}{'Anchor':>9}{'LLM':>8}{'LOO':>8}{'Price':>7}  Basis")
    for row in r["countries"]:
        print(f"{row['country'][:23]:<24}{row['units'] / 1e6:>10,.2f}{_m(row['anchor_units']):>9}"
              f"{_m(row['knowledge_units']):>8}{_m(row['loo_units']):>8}{row['price_usd']:>7,.0f}  {row['basis']}")
    rw = r["rest_of_world"]
    print(f"{'Rest of World (' + str(rw['n_countries']) + ')':<24}{rw['units'] / 1e6:>10,.2f}  model, hard data only")

    print(f"\nGlobal units: {r['global_units'] / 1e6:,.1f}M", end="")
    w = r.get("worldwide_units_crosscheck")
    print(f"   vs published worldwide {w['units'] / 1e6:,.1f}M ({w['gap_pct']:+.1f}%, {w['year']}) -- info only"
          if w else "   (no sourced worldwide unit total found)")
    lv = pr.get("level") or {}
    if lv:
        print(f"Pricing agent: market defined at '{lv['definition']['level']}' level "
              f"({lv['definition']['reason'][:120] or 'no statement found'}) -> using {lv['chosen_level']}, "
              f"factor x{lv['factor']:.2f}{' (clipped)' if lv['clipped'] else ''}")
        if lv["vendor_asp_points"]:
            print("  vendor ASP facts: " + ", ".join(f"${v:,.0f}" for v in lv["vendor_asp_points"]))
        if lv["retail_ratios"]:
            print("  retail/landed by country: " + ", ".join(f"{x['country']} {x['ratio']:.2f}" for x in lv["retail_ratios"]))
    print(f"Global value at the landed (customs) level: ${r['global_usd_mn_landed']:,.0f}M" + _vs(r["global_usd_mn_landed"], actual))
    print(f"Global value (headline, {lv.get('chosen_level', 'landed')} level): ${r['global_usd_mn']:,.0f}M"
          + _vs(r["global_usd_mn"], actual))

    baseline = signal_dir(args.market) / "05_result_v1_baseline.json"
    if baseline.exists():
        b = json.loads(baseline.read_text(encoding="utf-8"))
        print(f"\nBaseline (v1): units {b['global_units'] / 1e6:,.1f}M, price ${b['price']['usd_per_unit']:,.0f}, "
              f"value ${b['global_usd_mn']:,.0f}M" + _vs(b["global_usd_mn"], actual))
    for version in ("v2", "v3", "v4"):
        prior = signal_dir(args.market) / f"08_runs_summary_{version}.json"
        if prior.exists():
            b = json.loads(prior.read_text(encoding="utf-8"))
            print(f"{version} (median of {len(b['runs'])} runs): ${b['median_usd_mn']:,.0f}M" + _vs(b["median_usd_mn"], actual))
    runs = [r]
    for k in range(2, args.runs + 1):
        print(f"\n[run {k}/{args.runs}] re-sampling the LLM (uncached)...")
        try:
            runs.append(run_signal_model(args.market, GenLLM(cfg, cache_path=None), n_countries=args.countries,
                                         run_id=f"run_{k}", say=lambda s: print("  " + s), vertical=vertical))
        except Exception as exc:  # noqa: BLE001 -- keep the runs that worked
            print(f"  run {k} failed: {exc}")
    summary: dict = {"market": args.market, "actual_usd_mn": actual}
    if len(runs) > 1:
        values = [x["global_usd_mn"] for x in runs]
        med = statistics.median(values)
        print(f"\n=== {len(runs)} runs ===")
        for x in runs:
            print(f"  {x['run_id']}: units {x['global_units'] / 1e6:,.1f}M x ${x['price']['avg_usd_per_unit']:,.0f} "
                  f"= ${x['global_usd_mn']:,.0f}M" + _vs(x["global_usd_mn"], actual)
                  + f" | landed ${x['global_usd_mn_landed']:,.0f}M")
        spread = (max(values) - min(values)) / med
        print(f"Median ${med:,.0f}M (range ${min(values):,.0f}M - ${max(values):,.0f}M, "
              f"spread {spread:.0%} of median)" + _vs(med, actual))
        print(f"Stability target (spread < 10%): {'PASS' if spread < 0.10 else 'FAIL'}"
              + (f" | accuracy target (within +-10%): {'PASS' if abs(med - actual) / actual <= 0.10 else 'FAIL'}"
                 if actual else ""))
        summary["runs"] = [{"run_id": x["run_id"], "global_units": x["global_units"], "price": x["price"]["avg_usd_per_unit"],
                            "global_usd_mn": x["global_usd_mn"], "landed_usd_mn": x["global_usd_mn_landed"]} for x in runs]
        summary["median_usd_mn"] = med
        summary["spread"] = spread

    if args.ablations:
        variants = {
            "without qualitative scores": dict(exclude_features=tuple(RUBRIC)),
            "without embedding signal": dict(exclude_features=("embedding_signal",)),
            "without LLM-knowledge anchors": dict(use_knowledge=False),
            "without hard data": dict(exclude_features=tuple(HARD_FEATURES)),
            "without trade data": dict(use_trade=False),
            "without estimator (raw LLM knowledge)": dict(use_estimator=False),
            "with worldwide-units constraint": dict(world_constraint=True),
            "landed price level (no pricing factor)": dict(price_level="landed"),
        }
        print(f"\n=== Ablations (run 1 data, refit only) ===\n  {'full model':<32}${r['global_usd_mn']:>9,.0f}M  "
              f"LOO {r['loo_median_abs_pct_error']}%" + _vs(r["global_usd_mn"], actual))
        summary["ablations"] = {}
        for name, kw in variants.items():
            try:
                a = run_signal_model(args.market, cached_llm, n_countries=args.countries, say=lambda s: None,
                                     vertical=vertical, **kw)
            except Exception as exc:  # noqa: BLE001
                print(f"  {name:<32}not possible: {exc}")
                continue
            print(f"  {name:<32}${a['global_usd_mn']:>9,.0f}M  LOO {a['loo_median_abs_pct_error']}%"
                  + _vs(a["global_usd_mn"], actual))
            summary["ablations"][name] = {"global_usd_mn": a["global_usd_mn"], "loo": a["loo_median_abs_pct_error"]}

    checkpoints.write_json(signal_dir(args.market) / "08_runs_summary.json", summary)
    print(f"\nSaved: {signal_dir(args.market)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

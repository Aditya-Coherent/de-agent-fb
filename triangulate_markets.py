"""Triangulate demand-side estimates (2A, 2B) with the consensus of published
market sizes, and score them against the reference sizes.

    final = inverse-variance blend in log space of
            the approach's bottom-up estimate (sigma from its evidence quality)
            and the median of published global market sizes (sigma from publisher spread)
    (see `me_engine.consensus`)

Bottom-up sources:
    2A signal model  output/demand_country_2a/<slug>/signal_model/run_1/07_result.json
    2A original      approaches/examples/<slug>/result.json  ["2A_demand_country_total"]
    2B               approaches/examples/<slug>/result.json  ["2B_demand_usecase_country"]

Usage:
    python triangulate_markets.py
    python triangulate_markets.py --market "Televisions" --market "Olive Oil"
    # -> output/consensus/<slug>.json (published sizes, cached) + output/consensus/TRIANGULATION.md

The reference sizes are used ONLY to score the result, never as an input.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from demand_country_2a_agent import check_llm, deepseek_config, load_actuals  # noqa: E402
from me_engine.consensus import adapters, combine, published  # noqa: E402
from me_engine.demandCountry_approach2A import checkpoints  # noqa: E402
from me_engine.demandCountry_approach2A.signal_model import _search, page_windows  # noqa: E402
from me_engine.generation.llm import GenLLM  # noqa: E402

MARKETS = ["Televisions", "Smartphones", "Laptops", "Tablets", "Olive Oil", "Disposable Medical Gloves",
           "Watches", "Smartwatches"]
TUNED_ON = {"Televisions"}  # the signal model was developed on Televisions
OUT = ROOT / "output" / "consensus"


def bottom_up_estimates(market: str) -> list[tuple[str, float, float, dict]]:
    """[(approach, value_usd_mn, sigma_b, sigma_detail)]"""
    slug = checkpoints.slugify(market)
    out = []
    sm = ROOT / "output" / "demand_country_2a" / slug / "signal_model" / "run_1" / "07_result.json"
    if sm.exists():
        r = json.loads(sm.read_text(encoding="utf-8"))
        sigma, detail = adapters.sigma_signal_model(r)
        out.append(("2A signal model", r["global_usd_mn"], sigma, detail))
    ex = ROOT / "approaches" / "examples" / slug / "result.json"
    if ex.exists():
        r = json.loads(ex.read_text(encoding="utf-8"))
        for key, label in (("2A_demand_country_total", "2A original"), ("2B_demand_usecase_country", "2B")):
            sec = r.get(key) or {}
            if sec.get("global_usd_mn"):
                sigma, detail = adapters.sigma_evidence_mix(sec)
                out.append((label, float(sec["global_usd_mn"]), sigma, detail))
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Triangulate 2A/2B estimates with published market sizes.")
    p.add_argument("--market", action="append", default=None)
    p.add_argument("--base-year", type=int, default=2025)
    p.add_argument("--model", default="deepseek-chat")
    p.add_argument("--cache", default="output/.gen_cache.db")
    args = p.parse_args()

    cfg = deepseek_config(args.model)
    error = check_llm(cfg)
    if error:
        print(f"ERROR: DeepSeek is not reachable: {error}")
        return 2
    llm = GenLLM(cfg, cache_path=ROOT / args.cache)
    actuals = load_actuals()
    rows = []
    for market in args.market or MARKETS:
        path = OUT / f"{checkpoints.slugify(market)}.json"
        pub = checkpoints.read_json(path)
        if pub is None:
            print(f"[published] searching {market}...")
            pub = published.gather(llm, market, args.base_year, search=lambda q: _search(q, max_results=6),
                                   pages=page_windows)
            checkpoints.write_json(path, pub)
        cons = published.consensus(pub["figures"], args.base_year)
        actual = actuals.get(market.lower())
        print(f"\n=== {market} ===  actual ${actual:,.0f}M" if actual else f"\n=== {market} ===")
        if cons:
            print(f"  published consensus ${cons['value_usd_mn']:,.0f}M from {cons['n_publishers']} publisher(s), "
                  f"sigma {cons['sigma']:.2f}: " + ", ".join(f"{f['publisher'][:20]} ${f['value_base_year_usd_mn']:,.0f}M"
                                                              for f in cons["used"])
                  + (f" | dropped: {', '.join(f['publisher'][:20] for f in cons['dropped'])}" if cons["dropped"] else ""))
        else:
            print("  no verified published sizes found")
        for approach, value, sigma_b, detail in bottom_up_estimates(market):
            res = combine.blend(value, sigma_b, cons)
            err_b = (value - actual) / actual if actual else None
            err_f = (res["value_usd_mn"] - actual) / actual if actual else None
            rows.append({"market": market, "approach": approach, "bottom_up_usd_mn": value, "sigma_b": sigma_b,
                         "sigma_detail": detail, "published": cons and {k: cons[k] for k in ("value_usd_mn", "sigma", "n_publishers")},
                         **res, "actual_usd_mn": actual, "err_bottom_up": err_b, "err_final": err_f,
                         "tuned_on": market in TUNED_ON and approach == "2A signal model"})
            print(f"  {approach:<16} bottom-up ${value:>10,.0f}M (sigma {sigma_b:.2f}) -> final ${res['value_usd_mn']:>10,.0f}M "
                  f"[w_published {res['weight_published']:.2f}]"
                  + (f"  error {err_b:+.1%} -> {err_f:+.1%}" if actual else "")
                  + (f"  FLAG: {res['flag']}" if res["flag"] else ""))

    print("\n=== Median absolute error vs reference ===")
    summary = {}
    for approach in ("2A signal model", "2A original", "2B"):
        sel = [r for r in rows if r["approach"] == approach and r["err_final"] is not None]
        held = [r for r in sel if not r["tuned_on"]]
        if not sel:
            continue
        mb = statistics.median(abs(r["err_bottom_up"]) for r in sel)
        mf = statistics.median(abs(r["err_final"]) for r in sel)
        within = sum(1 for r in sel if abs(r["err_final"]) <= 0.10)
        line = (f"  {approach:<16} n={len(sel)}  bottom-up {mb:.1%} -> triangulated {mf:.1%}  "
                f"({within}/{len(sel)} within +-10%)")
        if len(held) != len(sel) and held:
            line += f"  | held-out only: {statistics.median(abs(r['err_final']) for r in held):.1%}"
        print(line)
        summary[approach] = {"n": len(sel), "median_abs_err_bottom_up": mb, "median_abs_err_final": mf, "within_10pct": within}
    checkpoints.write_json(OUT / "triangulation_results.json", {"rows": rows, "summary": summary})
    print(f"\nSaved: {OUT / 'triangulation_results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Publish a documented global market estimate from a 2A signal-model run.

    python demand_country_2a_signal.py --market "Televisions"   # bottom-up build (cached)
    python publish_market.py --market "Televisions"
    # -> output/reports/<slug>/<Market> - Methodology & Sources.md
    #    output/reports/<slug>/<Market> - Market Report.xlsx / .json
    #    output/reports/<slug>/<Market> - Segments.xlsx

See `me_engine.demandCountry_approach2A.publisher`. The reference sizes in
approaches/examples/COMPARISON_VS_ACTUAL.md are printed for comparison only.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from demand_country_2a_agent import check_llm, deepseek_config, load_actuals  # noqa: E402
from me_engine.demandCountry_approach2A.publisher import publish  # noqa: E402
from me_engine.generation.llm import GenLLM  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Publish a documented market estimate (2A signal model + reconciliation).")
    p.add_argument("--market", action="append", required=True)
    p.add_argument("--no-segments", action="store_true")
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
    for market in args.market:
        rep = publish(market, llm, cfg, segments=not args.no_segments)
        resp, actual = rep["respectability"], actuals.get(market.lower())
        print(f"\n=== {market} ===")
        print(f"Final ${rep['global_usd_mn']:,.0f}M | {rep['global_units'] / 1e6:,.1f}M units | avg ${rep['avg_price_usd']:,.0f}"
              f" | bottom-up ${rep['bottom_up_usd_mn']:,.0f}M x{rep['reconciliation']['price_level_factor']:.2f}")
        print(f"Respectability: {resp['verdict']}")
        print(f"Documented: {rep['documentation']['value_share_on_sourced_units']:.0%} of value on sourced country units")
        if actual:
            print(f"(reference ${actual:,.0f}M: {(rep['global_usd_mn'] - actual) / actual:+.1%})")
        for f in rep["flags"]:
            print(f"FLAG: {f}")
        print(f"Report: output/reports/{rep['market'].lower().replace(' ', '-')}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

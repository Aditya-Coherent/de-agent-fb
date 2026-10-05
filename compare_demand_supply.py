"""Supply-side market-size pipeline -- an independent methodology alongside
the Desk Research agent fleet (backend/main.py, run_pipeline.py).
Runs entirely on its own; its estimation logic lives in src/me_engine/supply/,
which only reuses generic company sourcing/verification utilities, not
anything from the Desk Research pipeline.

"Supply-side" and "bottom-up" are not two separate methodologies -- bottom-up
(sum real, ground-truth data upward) IS how a supply-side estimate gets
produced. Two techniques implement it, selectable via --method:

  evidence  (default) -- src/me_engine/supply/evidence_estimator.py. No LLM
            call anywhere, no company list needed. Gathers real market-report
            snippets (ddgs), keeps only the ones an embedding model confirms
            are actually about this market, extracts explicitly-cited
            size/CAGR figures with regex (no LLM), aggregates them by
            median, and nudges the CAGR with a real Google Trends signal.
            Only works when the market has some existing public coverage to
            find, and gives one aggregate number -- no per-company detail.

  company   -- src/me_engine/supply/graph.py. Sources named companies,
            estimates each one's revenue per country. Only hard-capped to a
            real number when a company has yfinance/InsightSentry financial
            data (public companies); for private companies (most of them, in
            practice) the LLM estimates revenue from web snippets with no
            real ceiling -- confirmed to swing 2x between identical runs for
            a market with no public-company coverage. Opt-in only: use this
            when you need the per-company/per-country breakdown, or the
            market is dominated by public companies with real financial data.

  both      -- runs both techniques independently and prints/saves all
            figures side by side for comparison, alongside the Desk Research
            total when a matching history entry exists.

Example:
    python compare_demand_supply.py --market "Global Avocado Oil Market"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from me_engine.generation.config import GenConfig
from me_engine.generation.history import read_history
from me_engine.supply.companies import load_companies, save_companies_csv
from me_engine.supply.discovery import discover_companies
from me_engine.supply.evidence_estimator import estimate_from_evidence
from me_engine.supply.graph import run_supply_side
from me_engine.supply.quality import compute_company_quality_flags, compute_evidence_quality_flags
from me_engine.supply.verify import verify_companies


def _print_flags(flags: list[dict[str, str]]) -> None:
    for f in flags:
        tag = {"critical": "[FLAG-CRITICAL]", "warning": "[FLAG-WARNING]", "info": "[FLAG-INFO]"}.get(
            f.get("severity", "info"), "[FLAG]"
        )
        print(f"{tag} {f.get('message', '')}")

DEFAULT_HISTORY_PATH = ROOT / "output" / "generation_history.jsonl"
DEFAULT_CACHE_PATH = ROOT / "output" / ".supply_cache.db"
COMPANIES_DIR = ROOT / "my_companies"
REPORTS_DIR = ROOT / "output" / "supply_side"


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _lookup_desk_research_value(market: str, history_path: Path) -> float | None:
    rows = read_history(path=history_path, limit=1000)
    market_l = market.strip().lower()
    for row in rows:  # newest first
        # Rows written before 2026-10-05 carry a 2026 anchor under the old key.
        value = row.get("global_anchor_2025", row.get("global_anchor_2026"))
        if str(row.get("market_name", "")).strip().lower() == market_l and value is not None:
            return float(value)
    return None


def _run_company_method(args, config, countries) -> dict | None:
    if args.discover:
        print(f"Discovering companies for '{args.market}' ...")
        companies = discover_companies(args.market, config, cache_path=DEFAULT_CACHE_PATH)
        if not companies:
            print("[ERROR] Discovery returned no companies.")
            return None
        if not args.no_verify:
            print(f"Verifying {len(companies)} companies' websites ...")
            companies = verify_companies(companies)
        saved_to = COMPANIES_DIR / f"{_slugify(args.market)}_discovered.csv"
        save_companies_csv(companies, saved_to)
        print(f"Discovered {len(companies)} companies -> saved to {saved_to}")
    elif args.companies:
        companies = load_companies(args.companies)
        if not companies:
            print(f"[ERROR] No companies found in {args.companies}")
            return None
    else:
        print("[ERROR] --method company (or both) needs either --companies <file> or --discover.")
        return None

    print(f"Running supply-side agent graph for '{args.market}' from {len(companies)} companies "
          + (f"across {len(countries)} given countries ..." if countries else
             "(geography agent will propose the countries) ..."))
    state = run_supply_side(args.market, companies, config, countries=countries, cache_path=DEFAULT_CACHE_PATH)
    for line in state.get("log", []):
        print(f"  {line}")
    country_breakdown = state["country_breakdown"]
    grand_total = state["grand_total_usd_mn"]
    per_source = state.get("per_company_source", {})

    grounded = sum(1 for s in per_source.values() if s in ("financial_api", "web_evidence"))
    print()
    print(f"Data grounding: {grounded}/{len(per_source)} companies backed by real data "
          f"({sum(1 for s in per_source.values() if s == 'financial_api')} financial records, "
          f"{sum(1 for s in per_source.values() if s == 'web_evidence')} web evidence, "
          f"{sum(1 for s in per_source.values() if s == 'no_real_data')} no real data found)")

    print()
    print(f"{'Country':25s} {'Named (USD Mn)':>15s}  {'Coverage':>8s}  {'Country total':>15s}")
    for cb in country_breakdown:
        print(f"{cb.country:25s} {cb.named_total_usd_mn:15,.1f}  {cb.coverage_fraction:8.0%}  "
              f"{cb.country_total_usd_mn:15,.1f}")
    print()
    print(f"Company-based supply-side total (bottom-up, country-summed): {grand_total:,.1f} USD Mn")

    company_flags = compute_company_quality_flags(
        per_source, grand_total, country_breakdown, state.get("per_company_notes", {})
    )
    _print_flags(company_flags)

    return {
        "countries": state["countries"],
        "total_usd_mn": grand_total,
        "log": state.get("log", []),
        "company_data_sources": per_source,
        "excluded_companies": state.get("excluded_companies", []),
        "merged_companies": state.get("merged_companies", []),
        "quality_flags": company_flags,
        "country_breakdown": [
            {
                "country": cb.country,
                "named_total_usd_mn": cb.named_total_usd_mn,
                "coverage_fraction": cb.coverage_fraction,
                "country_total_usd_mn": cb.country_total_usd_mn,
                "company_shares": cb.company_shares,
            }
            for cb in country_breakdown
        ],
    }


def _run_evidence_method(args) -> dict | None:
    print(f"\nGathering real market evidence for '{args.market}' "
          "(web search -> relevance filter -> regex extraction -> Google Trends) ...")
    est = estimate_from_evidence(args.market)
    print(f"Relevant real-data snippets found: {len(est.relevant_snippets)}")
    print(f"Real figures extracted: {len(est.figures)}")
    if est.has_real_data:
        print(f"Evidence-based supply-side total (median across real sources): {est.total_usd_mn:,.1f} USD Mn")
        print(f"Evidence-based CAGR: {est.cagr:.1%}" if est.cagr is not None else "Evidence-based CAGR: n/a")
    else:
        print("[WARN] No real cited figures found in gathered evidence -- evidence-based estimate unavailable.")
    print(f"Google Trends signal: {est.trends_note}")

    evidence_flags = compute_evidence_quality_flags(est)
    _print_flags(evidence_flags)

    return {
        "total_usd_mn": est.total_usd_mn,
        "cagr": est.cagr,
        "has_real_data": est.has_real_data,
        "relevant_snippets": est.relevant_snippets,
        "figures": [
            {"snippet": f.snippet, "value_usd_mn": f.value_usd_mn, "cagr_pct": f.cagr_pct}
            for f in est.figures
        ],
        "trends_note": est.trends_note,
        "quality_flags": evidence_flags,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--market", required=True, help="Market name (must match a Desk Research run's market name)")
    ap.add_argument("--companies", default=None, help="Path to a .csv, .xlsx, .json, or .txt company list")
    ap.add_argument("--discover", action="store_true",
                    help="Skip --companies -- run the miniature LLM-based discovery instead (seconds, "
                         "not the full vendor-intelligence pipeline)")
    ap.add_argument("--no-verify", action="store_true", help="Skip website verification with --discover")
    ap.add_argument("--countries", default=None,
                    help="Comma-separated country list, e.g. \"United States,India,Brazil,Germany,China\". "
                         "Omit to let the company-based method propose its own geography breakdown (own "
                         "LLM call -- never reads the Desk Research pipeline's files). Unused by --method evidence.")
    ap.add_argument("--method", choices=["company", "evidence", "both"], default="evidence",
                    help="Supply-side technique to run: 'evidence' (default, zero-LLM real market-report "
                         "snippets -> regex extraction -> median, no company list needed), 'company' "
                         "(per-company LLM-estimated revenue sum -- needs --companies or --discover), "
                         "or 'both' to compare them.")
    ap.add_argument("--desk-research-value", type=float, default=None,
                    help="Override: Desk Research total market value (USD Mn) instead of history lookup")
    ap.add_argument("--save-report", default=None,
                     help="Path to save the JSON report (per-company shares, per-country breakdown, grand "
                          "total). Defaults to output/supply_side/<market-slug>.json -- always saved, not opt-in.")
    args = ap.parse_args()

    config = GenConfig.load(ROOT)
    if not config.is_online:
        print("[ERROR] No LLM API key found in .env -- cannot run the supply-side estimator.")
        return 1

    countries = [c.strip() for c in args.countries.split(",") if c.strip()] if args.countries else None

    company_result = None
    if args.method in ("company", "both"):
        company_result = _run_company_method(args, config, countries)
        if company_result is None:
            return 1

    evidence_result = None
    if args.method in ("evidence", "both"):
        evidence_result = _run_evidence_method(args)

    desk_research_value = args.desk_research_value
    if desk_research_value is None:
        desk_research_value = _lookup_desk_research_value(args.market, DEFAULT_HISTORY_PATH)
    if desk_research_value is None:
        print(f"\n[WARN] No Desk Research run found for '{args.market}' -- comparisons against it will be skipped.")

    print()
    if desk_research_value is not None:
        print(f"Desk Research total:            {desk_research_value:,.1f} USD Mn")
    if company_result is not None:
        print(f"Company-based supply-side total: {company_result['total_usd_mn']:,.1f} USD Mn")
    if evidence_result is not None and evidence_result["has_real_data"]:
        print(f"Evidence-based supply-side total: {evidence_result['total_usd_mn']:,.1f} USD Mn")

    report: dict = {"market": args.market, "method": args.method}
    if desk_research_value is not None:
        report["desk_research_total_usd_mn"] = desk_research_value

    # Mentor's own accuracy bar for a real-data-grounded bottom-up estimate is
    # ~30-40% relative error -- treat exceeding that as worth a human's attention,
    # while being explicit that it can reflect a genuine scope/definition
    # difference between sources (confirmed real case: "Global Coffee Market"
    # published estimates alone range $74B-$489B depending on whether retail,
    # foodservice, or green-bean scope is used) rather than either side being wrong.
    _DISAGREEMENT_THRESHOLD_PCT = 40.0

    if company_result is not None:
        report["company_based"] = company_result
        if desk_research_value is not None:
            rel_error_pct = abs(desk_research_value - company_result["total_usd_mn"]) / desk_research_value * 100
            print(f"Company-based relative error:   {rel_error_pct:.1f}%")
            company_result["relative_error_vs_desk_research_pct"] = rel_error_pct
            if rel_error_pct > _DISAGREEMENT_THRESHOLD_PCT:
                flag = {
                    "code": "disagrees_with_desk_research",
                    "severity": "warning",
                    "message": f"Company-based total differs from the Desk Research total by {rel_error_pct:.0f}% "
                               "-- beyond the ~30-40% margin a real-data-grounded estimate should land within. "
                               "Could be a genuine scope/definition difference between sources, or a real error "
                               "on one side -- worth a human check either way.",
                }
                company_result.setdefault("quality_flags", []).append(flag)
                _print_flags([flag])

    if evidence_result is not None:
        report["evidence_based"] = evidence_result
        if desk_research_value is not None and evidence_result["has_real_data"]:
            rel_error_pct = abs(desk_research_value - evidence_result["total_usd_mn"]) / desk_research_value * 100
            print(f"Evidence-based relative error:  {rel_error_pct:.1f}%")
            evidence_result["relative_error_vs_desk_research_pct"] = rel_error_pct
            if rel_error_pct > _DISAGREEMENT_THRESHOLD_PCT:
                flag = {
                    "code": "disagrees_with_desk_research",
                    "severity": "warning",
                    "message": f"Evidence-based total differs from the Desk Research total by {rel_error_pct:.0f}% "
                               "-- beyond the ~30-40% margin a real-data-grounded estimate should land within. "
                               "Could be a genuine scope/definition difference between sources, or a real error "
                               "on one side -- worth a human check either way.",
                }
                evidence_result.setdefault("quality_flags", []).append(flag)
                _print_flags([flag])

    out = Path(args.save_report) if args.save_report else REPORTS_DIR / f"{_slugify(args.market)}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved report to {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

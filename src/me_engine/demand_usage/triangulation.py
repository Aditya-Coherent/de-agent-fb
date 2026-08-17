"""External benchmark triangulation (spec section 11).

Generalizes the old "compare to the Desk Research history entry only" check
into a real sweep across independent benchmark categories -- published
market-size estimates, production, consumption, trade, and installed
capacity. Every benchmark found here is INFORMATIONAL ONLY:

    Demand-Side Estimate  vs.  External Benchmark  vs.  Physical Indicator

None of these values are ever used to adjust, override, or backfill the
bottom-up total -- this node runs strictly AFTER `calculate_node` and only
READS its output (`total_market_value`); it never writes back into it. A
large gap is reported with its likely-reason context, never silently
resolved by tuning the bottom-up number toward the benchmark.
"""
from __future__ import annotations

from typing import Any

from me_engine.generation.llm import GenLLM
from me_engine.generation.web_evidence import gather

from . import evidence

_BENCHMARK_CATEGORIES = (
    "published market-size estimate",
    "production volume",
    "consumption volume",
    "trade volume",
    "installed capacity",
)
_LARGE_GAP_PCT = 40.0


def triangulation_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg = state["config"]
    llm: GenLLM = state["llm"]
    market = state["market_name"]
    log = state.setdefault("log", [])
    total = state.get("total_market_value", 0.0) or 0.0
    market_unit = state.get("market_unit") or "USD Mn"

    benchmarks: list[dict[str, Any]] = []
    for category in _BENCHMARK_CATEGORIES:
        snippets: list[str] = []
        if cfg.enable_web:
            try:
                snippets = gather(
                    [f"{market} {category} global", f"{market} {category} report statistics"],
                    per_query=3, cap=6,
                )
            except Exception as exc:  # noqa: BLE001
                log.append(f"[triangulation] could not fetch evidence for '{category}': {exc}")
                continue

        unit = market_unit if category == "published market-size estimate" else "physical units (as reported)"
        fact = evidence.extract_structured_fact(
            llm, snippets, unit=unit,
            context=(
                f"Independent benchmark figure ({category}) for {market} -- used ONLY as a sanity-check "
                "reference against a separately-built bottom-up estimate, never to adjust it"
            ),
        )
        if fact is None:
            continue

        entry: dict[str, Any] = {
            "category": category,
            "value": fact.get("value"),
            "unit": fact.get("unit"),
            "year": fact.get("year"),
            "source": fact.get("source"),
            "citation": fact.get("citation"),
            "evidence_type": fact.get("evidence_type"),
            "confidence": fact.get("confidence"),
        }
        if category == "published market-size estimate" and fact.get("value"):
            entry["demand_side_estimate_usd_mn"] = total
            entry["gap_pct"] = round((total - fact["value"]) / fact["value"] * 100.0, 2)
        benchmarks.append(entry)

    large_gap_flags = [
        f"'{b['category']}' benchmark ({b.get('value')} {b.get('unit', '')}, source: {b.get('source') or 'n/a'}) "
        f"vs. Demand-Side estimate ({total:,.1f} {market_unit}) -- gap {b['gap_pct']:+.1f}%"
        for b in benchmarks
        if b.get("gap_pct") is not None and abs(b["gap_pct"]) >= _LARGE_GAP_PCT
    ]
    log.append(
        f"Triangulation: {len(benchmarks)} independent benchmark(s) found across "
        f"{len(_BENCHMARK_CATEGORIES)} categories"
        + (f"; {len(large_gap_flags)} large-gap flag(s) (informational only)" if large_gap_flags else "")
    )
    return {"triangulation": benchmarks, "triangulation_large_gap_flags": large_gap_flags}

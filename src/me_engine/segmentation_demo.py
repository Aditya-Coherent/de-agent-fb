"""LLM-fetched data for the standalone "Segmentation Demo" UI tab.

NOT part of the production demand_usage / demand_usage_india pipelines --
this is a lightweight, market-agnostic-in-shape generator that reproduces
the traditional GVR-style report table of contents the user supplied as a
reference:

    Market
      -> Product / Source / Flavor / Application / Distribution Channel /
         Sales Channel views (each a % breakdown that sums back to the
         SAME total -- independent cross-cuts, not a nested nesting tree)
      -> Region -> Country, each of which repeats the same six views
         against its OWN total.

BOTTOM-UP by design (2026-09-11 rework, replacing an earlier top-down
version that asked the LLM for one global total + % splits): the LLM is
only ever asked for a DIRECT DOLLAR VALUE (USD Mn), never a percentage or a
parent total. For every country, it estimates all six views' category
values independently. The "application" view is treated as the canonical
one (it IS the real demand/usage pathway -- Food & Beverages, Healthcare,
etc.); that country's OWN total is the deterministic SUM of its application
-view category values, and the other five views (which describe the same
underlying total from a different angle) are proportionally rescaled to
also sum to that same country total, so all six views agree with each
other by construction. Region totals and the Global total are then just
plain sums of their children's totals -- never a top-down allocation. No
arithmetic is ever trusted from the LLM's own output.

Five calls total (one per region, each covering all of that region's
countries) -- reusing `GenLLM`'s existing on-disk cache so a repeat request
for the same market is instant and free.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from me_engine.demandUseCaseCountry_approach2B.checkpoints import slugify
from me_engine.generation.config import GenConfig
from me_engine.generation.llm import GenLLM

# Where the full result (including every step's request/response) is
# persisted -- reuses `demandUseCaseCountry_approach2B.checkpoints.slugify` (read-only import,
# never modifies that module) purely for a consistent market-name-to-slug
# convention across the app.
_OUTPUT_DIR = Path("output") / "segmentation_demo"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _current_base_year() -> int:
    """The last FULLY COMPLETED calendar year -- market-sizing reports are
    written about a year that has already happened, never the current
    (still in progress) year."""
    return datetime.now().year - 1


def result_path(market_name: str, root: Path | None = None) -> Path:
    base = (Path(root) / _OUTPUT_DIR) if root else _OUTPUT_DIR
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{slugify(market_name)}.json"


def write_result_json(result: dict[str, Any], root: Path | None = None) -> Path:
    path = result_path(result["market_name"], root)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


# Fixed category taxonomy, taken verbatim from the reference report's TOC --
# the LLM is asked to size THESE categories, never to invent its own, so the
# demo stays aligned with the structure the user asked to reproduce.
SEGMENT_VIEWS: dict[str, list[str]] = {
    "product": ["Balsamic Vinegar", "Red Wine Vinegar", "Cider Vinegar", "White Vinegar", "Rice Vinegar", "Others"],
    "source": ["Organic", "Synthetic"],
    "flavor": ["Apple", "Garlic", "Lemon", "Other"],
    "application": [
        "Food and Beverages", "Healthcare Industry", "Cleaning Industry", "Agriculture Industry", "Others",
    ],
    "distribution_channel": [
        "Supermarkets & Hypermarkets", "Convenience Stores", "Specialty Stores", "Online", "Other",
    ],
    "sales_channel": ["B2B", "B2C"],
}

VIEW_LABELS: dict[str, str] = {
    "product": "Product",
    "source": "Source",
    "flavor": "Flavor",
    "application": "Application",
    "distribution_channel": "Distribution Channel",
    "sales_channel": "Sales Channel",
}

# The demand/usage view -- this one view's category values are what actually
# DEFINE a country's total (Food & Beverages + Healthcare + ... = the whole
# market in that country). Every other view is a different lens on that
# same total and gets rescaled to agree with it.
CANONICAL_VIEW = "application"

REGIONS: dict[str, list[str]] = {
    "North America": ["U.S.", "Canada", "Mexico"],
    "Europe": ["Germany", "U.K.", "France", "Italy", "Spain"],
    "Asia Pacific": ["China", "India", "Japan", "South Korea", "Australia"],
    "Central & South America": ["Brazil", "Argentina"],
    "Middle East & Africa": ["South Africa", "Turkey"],
}


def _views_prompt_block() -> str:
    lines = []
    for key, cats in SEGMENT_VIEWS.items():
        lines.append(f'- "{key}" ({VIEW_LABELS[key]}): {", ".join(cats)}')
    return "\n".join(lines)


def _safe_float(v: Any) -> float:
    try:
        return max(0.0, float(v))
    except (TypeError, ValueError):
        return 0.0


def _rescale_to_total(raw: dict[str, Any], categories: list[str], target_total: float) -> dict[str, float]:
    """Deterministic reconciliation -- takes the LLM's raw dollar estimates
    for one view's categories and rescales them (preserving their relative
    proportions) so they sum to EXACTLY `target_total`. If the LLM gave no
    usable numbers at all, falls back to an equal split of the target so the
    view is still fully populated rather than silently zero."""
    vals = {c: _safe_float(raw.get(c)) for c in categories}
    raw_total = sum(vals.values())
    if raw_total <= 0 or target_total <= 0:
        equal = target_total / len(categories) if categories else 0.0
        return {c: round(equal, 2) for c in categories}
    return {c: round(v / raw_total * target_total, 2) for c, v in vals.items()}


def _fetch_region_leaf_values(
    llm: GenLLM, market_name: str, base_year: int, region: str, countries: list[str],
) -> tuple[str, str, dict[str, Any]]:
    """The ONLY kind of LLM call this module makes: for one region, ask for
    a direct USD Million estimate (never a percentage) for every
    segmentation-view category, for every one of that region's countries."""
    system = (
        "You are a market-sizing analyst. You will be given a fixed list of segmentation views (each with a "
        "fixed category list) and a fixed list of countries within one world region. For EACH country, "
        "estimate a REAL DOLLAR VALUE in USD MILLION (never a percentage, never a placeholder round number) "
        "for every category in every view -- your best honest estimate of how much of this market's revenue "
        "in that country that category represents, in the given base year. All six views describe the SAME "
        "underlying country total from different angles (e.g. the Application view and the Product view both "
        "describe the whole country's market) -- give each view its own honest independent estimate, they will "
        "be reconciled deterministically afterwards, so do not force them to match each other yourself. Give "
        "every country its own distinct estimate (do not copy one country's numbers to another). Return ONLY "
        "JSON, no commentary. Never invent categories or countries beyond what is given."
    )
    user = (
        f"Market: {market_name}\n"
        f"Base year: {base_year}\n"
        f"Region: {region}\n"
        f"Countries in this region: {', '.join(countries)}\n\n"
        f"Segmentation views (view_key: categories):\n{_views_prompt_block()}\n\n"
        f"Also estimate, for each country, the average realised PRICE PER LITRE in US$ for each of these "
        f"products: {', '.join(SEGMENT_VIEWS['product'])}. Prices differ by product (premium vs commodity) "
        "and by country (income level, import dependence).\n\n"
        'Return JSON: {"country_views": {"<country>": {"<view_key>": {"<category>": <usd_million_value>, '
        '...}, ...}, ...}, "country_prices": {"<country>": {"<product>": <usd_per_litre>, ...}, ...}}'
    )
    return system, user, llm.complete_json(system, user)


def _fetch_product_application_affinity(
    llm: GenLLM, market_name: str, base_year: int,
) -> tuple[str, str, dict[str, Any]]:
    """One market-wide LLM call for the Product x Application AFFINITY prior
    -- the relative propensity of each product to serve each application
    (0 = never, 1 = its primary use). This is only the SEED for the IPF
    balancing below: it shapes how a country's total is spread across the
    joint cells, but can never change either marginal, both of which are
    already fixed by the per-country estimates."""
    system = (
        "You are a market-sizing analyst. For the given market, judge how strongly each PRODUCT is associated "
        "with each APPLICATION -- a relative propensity on a 0 to 1 scale, where 1 means this application is a "
        "primary use for that product and values near 0 mean the product is essentially never used that way. "
        "These are relative affinities, NOT percentages and NOT dollar values; they do not need to sum to "
        "anything. Judge each product independently. Return ONLY JSON, no commentary."
    )
    user = (
        f"Market: {market_name}\n"
        f"Base year: {base_year}\n\n"
        f"Products: {', '.join(SEGMENT_VIEWS['product'])}\n"
        f"Applications: {', '.join(SEGMENT_VIEWS['application'])}\n\n"
        'Return JSON: {"affinity": {"<product>": {"<application>": <0..1>, ...}, ...}}'
    )
    return system, user, llm.complete_json(system, user)


def _ipf(
    seed: list[list[float]], row_marginals: list[float], col_marginals: list[float],
    iterations: int = 200, tol: float = 1e-9,
) -> list[list[float]]:
    """Iterative Proportional Fitting (IPF / RAS) -- the standard method for
    recovering a joint matrix that is simultaneously consistent with two
    known marginals, given a seed of relative affinities. Alternately rescales
    rows to hit `row_marginals` and columns to hit `col_marginals` until both
    hold. Purely deterministic: the LLM supplies only the seed's shape, never
    the resulting cell values, and the marginals it must reproduce were fixed
    upstream."""
    n_rows, n_cols = len(row_marginals), len(col_marginals)
    m = [[max(1e-9, float(seed[i][j])) for j in range(n_cols)] for i in range(n_rows)]
    row_target_total = sum(row_marginals)
    if row_target_total <= 0 or sum(col_marginals) <= 0:
        return [[0.0] * n_cols for _ in range(n_rows)]
    for _ in range(iterations):
        for i in range(n_rows):
            s = sum(m[i])
            f = (row_marginals[i] / s) if s > 0 else 0.0
            m[i] = [v * f for v in m[i]]
        for j in range(n_cols):
            s = sum(m[i][j] for i in range(n_rows))
            f = (col_marginals[j] / s) if s > 0 else 0.0
            for i in range(n_rows):
                m[i][j] *= f
        row_err = max(abs(sum(m[i]) - row_marginals[i]) for i in range(n_rows))
        col_err = max(abs(sum(m[i][j] for i in range(n_rows)) - col_marginals[j]) for j in range(n_cols))
        if row_err < tol and col_err < tol:
            break
    return m


def _fetch_market_narrative(
    llm: GenLLM, market_name: str, base_year: int, global_total: float, region_totals: dict[str, float],
) -> tuple[str, str, dict[str, Any]]:
    """One additional, purely qualitative LLM call -- grounded in the
    already-computed (deterministic, bottom-up) totals -- producing a short
    market-intelligence narrative: what's shifting, why, and what to watch.
    Deliberately NO numeric CAGR/forecast here (that would need a genuine
    second time-point/growth-rate estimate, out of scope for a single-
    base-year snapshot) -- qualitative direction only."""
    system = (
        "You are a senior market-intelligence analyst writing a short, sharp briefing for executives. You will "
        "be given a market's computed base-year revenue by region. Write a grounded narrative about current "
        "shifts and expected near-term movements in this market -- demand drivers, structural shifts (e.g. "
        "shifting consumer preference, channel shifts, regulatory changes, input-cost pressure), and which "
        "regions/segments look set to gain or lose share. Do NOT invent a CAGR or forecast number -- describe "
        "DIRECTION and DRIVERS qualitatively only. Return ONLY JSON, no commentary outside the JSON."
    )
    region_lines = "\n".join(f"- {name}: ${total:,.1f}M" for name, total in region_totals.items())
    user = (
        f"Market: {market_name}\n"
        f"Base year: {base_year}\n"
        f"Computed base-year global total: ${global_total:,.1f}M\n"
        f"Computed base-year total by region:\n{region_lines}\n\n"
        'Return JSON: {"headline": "<one punchy sentence>", "key_shifts": ["<shift 1>", "<shift 2>", '
        '"<shift 3>"], "growth_drivers": ["<driver 1>", "<driver 2>", "<driver 3>"], "regions_to_watch": '
        '[{"region": "<region>", "note": "<why this region is notable right now>"}], "outlook_summary": '
        '"<2-3 sentence qualitative outlook, no numbers>"}'
    )
    return system, user, llm.complete_json(system, user)


def generate_segmentation_demo(
    market_name: str, config: GenConfig, base_year: int | None = None, cache_path: Any = None,
    root: Path | None = None, persist: bool = True,
) -> dict[str, Any]:
    """Runs the bottom-up LLM calls described in this module's docstring
    and returns one fully-computed tree: Global -> Region -> Country, each
    node carrying its own total (USD Mn) and its own six-view breakdown --
    every dollar figure derived from summing (never top-down splitting)
    the LLM's per-country, per-category dollar estimates.

    Also returns a `steps` list -- one entry per pipeline stage (market name
    input, each region's LLM call's exact system/user prompt + raw response,
    the deterministic rollup/validation, and the market-intelligence
    narrative) -- so a UI can walk through and inspect exactly what was
    asked and what came back, and so the full run can be written to
    `output/segmentation_demo/<slug>.json` for later review/editing
    (`persist=True`, the default)."""
    base_year = base_year if base_year is not None else _current_base_year()
    llm = GenLLM(config, cache_path=cache_path)
    log: list[str] = []
    steps: list[dict[str, Any]] = []

    steps.append({
        "step_id": "market_name",
        "kind": "input",
        "title": "Market Name",
        "description": "The only user input this demo takes -- everything else is derived from it.",
        "data": {"market_name": market_name, "base_year": base_year},
    })

    # Product x Application affinity prior -- fetched ONCE for the whole
    # market (not per country), because it describes the products themselves,
    # not any geography. Used only as the IPF seed below.
    products = SEGMENT_VIEWS["product"]
    applications = SEGMENT_VIEWS[CANONICAL_VIEW]
    try:
        aff_system, aff_user, aff_raw = _fetch_product_application_affinity(llm, market_name, base_year)
    except Exception as exc:  # noqa: BLE001
        log.append(f"[segmentation_demo] LLM & Web affinity call failed: {exc}")
        aff_system, aff_user, aff_raw = "", "", {}
    aff_in = aff_raw.get("affinity") if isinstance(aff_raw, dict) else None
    aff_in = aff_in if isinstance(aff_in, dict) else {}
    # A missing/unusable affinity degrades to a uniform seed -- IPF still
    # reproduces both marginals exactly, the split across cells just carries
    # no product-specific prior.
    affinity = [
        [_safe_float((aff_in.get(p) or {}).get(a)) or (1.0 if not aff_in else 1e-3) for a in applications]
        for p in products
    ]
    steps.append({
        "step_id": "llm_affinity",
        "kind": "llm_call",
        "title": "Fetch Product x Application Affinity Prior",
        "description": (
            "One market-wide LLM & Web call: how strongly each product is associated with each application "
            "(0-1 relative propensity, not percentages). This is only the SEED for the IPF balancing -- it "
            "shapes how each country's total spreads across joint cells, but can never change either marginal."
        ),
        "request": {"system": aff_system, "user": aff_user},
        "response": aff_raw,
        "computed": {"affinity": {p: dict(zip(applications, affinity[i])) for i, p in enumerate(products)}},
    })

    region_names = list(REGIONS.keys())
    regions_out: list[dict[str, Any]] = []

    for region in region_names:
        countries = REGIONS[region]
        try:
            region_system, region_user, region_raw = _fetch_region_leaf_values(
                llm, market_name, base_year, region, countries,
            )
        except Exception as exc:  # noqa: BLE001
            log.append(f"[segmentation_demo] LLM & Web call failed for region '{region}': {exc}")
            region_system, region_user, region_raw = "", "", {}

        country_views_raw = region_raw.get("country_views") if isinstance(region_raw, dict) else None
        country_views_raw = country_views_raw if isinstance(country_views_raw, dict) else {}
        country_prices_raw = region_raw.get("country_prices") if isinstance(region_raw, dict) else None
        country_prices_raw = country_prices_raw if isinstance(country_prices_raw, dict) else {}

        countries_out = []
        for country in countries:
            raw_for_country = country_views_raw.get(country) or {}

            # The canonical view's RAW dollar values, summed, ARE this
            # country's total -- not an LLM-given number, a derived one.
            canonical_raw = raw_for_country.get(CANONICAL_VIEW) or {}
            country_total = round(
                sum(_safe_float(canonical_raw.get(c)) for c in SEGMENT_VIEWS[CANONICAL_VIEW]), 2,
            )
            if country_total <= 0:
                log.append(
                    f"[segmentation_demo] no usable '{CANONICAL_VIEW}' estimate for {country} "
                    f"({region}) -- its total defaults to 0"
                )

            views_out = {
                key: _rescale_to_total(raw_for_country.get(key) or {}, cats, country_total)
                for key, cats in SEGMENT_VIEWS.items()
            }

            # Product x Application joint matrix -- IPF-balanced against the
            # two marginals this country already has, so it reproduces both
            # exactly and adds no new dollars. Volume = value / price, so
            # each cell also carries litres and a $/L unit economics figure.
            prices_for_country = country_prices_raw.get(country) or {}
            prices = {p: _safe_float(prices_for_country.get(p)) for p in products}
            row_marg = [views_out["product"][p] for p in products]
            col_marg = [views_out[CANONICAL_VIEW][a] for a in applications]
            joint = _ipf(affinity, row_marg, col_marg)
            matrix_cells = []
            for i, p in enumerate(products):
                price = prices.get(p, 0.0)
                for j, a in enumerate(applications):
                    value = round(joint[i][j], 3)
                    matrix_cells.append({
                        "product": p,
                        "application": a,
                        "value_usd_mn": value,
                        "price_usd_per_l": round(price, 2),
                        # litres are DERIVED (value / price), never asked of
                        # the LLM -- a null price means volume is unknowable,
                        # never silently zero.
                        "volume_mn_l": round(value / price, 3) if price > 0 else None,
                    })
            total_vol = sum(c["volume_mn_l"] or 0.0 for c in matrix_cells)
            countries_out.append({
                "name": country,
                "total_usd_mn": country_total,
                "views": views_out,
                "product_application": {
                    "products": products,
                    "applications": applications,
                    "cells": matrix_cells,
                    "prices_usd_per_l": {p: round(prices.get(p, 0.0), 2) for p in products},
                    "total_volume_mn_l": round(total_vol, 3),
                    "blended_price_usd_per_l": round(country_total / total_vol, 3) if total_vol > 0 else None,
                },
            })

        region_total = round(sum(c["total_usd_mn"] for c in countries_out), 2)
        # Region-level views are a plain deterministic sum of their
        # (already-rescaled) countries' category values -- never a separate
        # LLM elicitation, so they agree with `region_total` by construction.
        region_views: dict[str, dict[str, float]] = {}
        for key, cats in SEGMENT_VIEWS.items():
            region_views[key] = {
                cat: round(sum(c["views"][key].get(cat, 0.0) for c in countries_out), 2) for cat in cats
            }

        regions_out.append({"name": region, "total_usd_mn": region_total, "views": region_views, "countries": countries_out})

        steps.append({
            "step_id": f"llm_region_{slugify(region)}",
            "kind": "llm_call",
            "title": f"Fetch {region}: Direct Dollar Estimates per Country",
            "description": (
                f"One LLM & Web call for {region}: for each of its {len(countries)} countries, estimate a direct "
                "USD Million value for every category in all six views (never a percentage). Each country's "
                f"total is then computed here as the SUM of its '{VIEW_LABELS[CANONICAL_VIEW]}' view values "
                "(the actual demand/usage pathway) -- the other five views are rescaled to agree with that "
                "same total."
            ),
            "request": {"system": region_system, "user": region_user},
            "response": region_raw,
            "computed": {
                "region_total_usd_mn": region_total,
                "region_views": region_views,
                "countries": countries_out,
            },
        })

    global_total = round(sum(r["total_usd_mn"] for r in regions_out), 2)
    global_views: dict[str, dict[str, float]] = {}
    for key, cats in SEGMENT_VIEWS.items():
        global_views[key] = {cat: round(sum(r["views"][key].get(cat, 0.0) for r in regions_out), 2) for cat in cats}

    # Deterministic rollup/validation step -- every check below re-derives
    # a parent total from its own children and compares, purely for
    # transparency (this is always true by construction given the sums
    # above; nothing here can "fail" other than floating-point rounding).
    region_sum = round(sum(r["total_usd_mn"] for r in regions_out), 2)
    checks = [{
        "check": "Sum of region totals == global total",
        "expected": global_total,
        "actual": region_sum,
        "ok": abs(region_sum - global_total) < 0.5,
    }]
    for r in regions_out:
        country_sum = round(sum(c["total_usd_mn"] for c in r["countries"]), 2)
        checks.append({
            "check": f"Sum of {r['name']} country totals == {r['name']} total",
            "expected": r["total_usd_mn"],
            "actual": country_sum,
            "ok": abs(country_sum - r["total_usd_mn"]) < 0.5,
        })
    steps.append({
        "step_id": "aggregate",
        "kind": "aggregate",
        "title": "Deterministic Rollup & Validation",
        "description": (
            "No LLM & Web call here -- pure arithmetic. Every country total above was the SUM of its own "
            f"'{VIEW_LABELS[CANONICAL_VIEW]}' view's dollar values; every region/global total is a plain sum "
            "of its children. This step re-derives each parent from its children and confirms they match."
        ),
        "checks": checks,
    })

    region_totals_by_name = {r["name"]: r["total_usd_mn"] for r in regions_out}
    try:
        narrative_system, narrative_user, narrative_raw = _fetch_market_narrative(
            llm, market_name, base_year, global_total, region_totals_by_name,
        )
    except Exception as exc:  # noqa: BLE001
        log.append(f"[segmentation_demo] market narrative LLM & Web call failed: {exc}")
        narrative_system, narrative_user, narrative_raw = "", "", {}

    narrative = {
        "headline": str(narrative_raw.get("headline") or "").strip(),
        "key_shifts": [str(s).strip() for s in (narrative_raw.get("key_shifts") or []) if str(s).strip()],
        "growth_drivers": [str(s).strip() for s in (narrative_raw.get("growth_drivers") or []) if str(s).strip()],
        "regions_to_watch": [
            {"region": str(r.get("region", "")).strip(), "note": str(r.get("note", "")).strip()}
            for r in (narrative_raw.get("regions_to_watch") or [])
            if isinstance(r, dict) and str(r.get("region", "")).strip()
        ],
        "outlook_summary": str(narrative_raw.get("outlook_summary") or "").strip(),
    }
    steps.append({
        "step_id": "market_narrative",
        "kind": "narrative",
        "title": "AI Market Intelligence — Tracing Shifts & Movements",
        "description": (
            "One qualitative LLM & Web call, grounded in the deterministic totals computed above -- no CAGR/forecast "
            "number (that needs a real second time-point estimate); direction and drivers only."
        ),
        "request": {"system": narrative_system, "user": narrative_user},
        "response": narrative_raw,
        "computed": {"narrative": narrative},
    })

    result = {
        "market_name": market_name,
        "base_year": base_year,
        "generated_at": _now_iso(),
        "view_labels": VIEW_LABELS,
        "canonical_view": CANONICAL_VIEW,
        "global_total_usd_mn": global_total,
        "global_views": global_views,
        "regions": regions_out,
        "narrative": narrative,
        "steps": steps,
        "log": log,
        "grounded": global_total > 0,
    }
    if persist:
        try:
            saved_path = write_result_json(result, root=root)
            result["saved_path"] = str(saved_path)
        except Exception as exc:  # noqa: BLE001 -- persistence never blocks returning the result
            log.append(f"[segmentation_demo] could not write result JSON to disk: {exc}")
    return result

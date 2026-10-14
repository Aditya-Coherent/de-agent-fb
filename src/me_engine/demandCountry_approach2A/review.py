"""Review checks for the 2A agent (`agent.py`).

Three layers, cheapest first:

1. `product_gate` -- before sizing: does the driver measure THIS market's own
   product? Deterministic word check first; only when that fails does the LLM
   get a yes/no say (it knows "TV" = "Television", code doesn't). Catches the
   cross-market leak seen in the example runs (Laptops sized with "National
   smartphone shipments", Televisions with "National watch consumption").
2. `deterministic_issues` -- after sizing: hard facts about the result
   (critical quality flags, empty countries, Rest of World or one country
   swallowing the total).
3. `llm_review` -- after sizing: a market-researcher read of the build-up
   (driver, per-country quantities, price) that diagnoses root causes and
   writes feedback for the next pass.

Published market sizes (triangulation) are shown to the reviewer as context
only. Nothing here ever scales the bottom-up number toward them; a redo only
re-derives the driver / quantities / price with the reviewer's notes.
"""
from __future__ import annotations

import json
import re
from typing import Any

from me_engine.generation.llm import GenLLM

from .price_curve import tonnes_factor

# Words that describe how a product is counted, not which product it is.
_GENERIC_WORDS = frozenset({
    "and", "the", "for", "global", "market", "markets", "total", "demand", "consumption", "apparent",
    "national", "annual", "volume", "volumes", "unit", "units", "shipped", "shipment", "shipments", "sold",
    "sale", "sales", "number", "count", "piece", "pieces", "single", "pair", "pairs", "tonne", "tonnes",
    "ton", "tons", "metric", "kilogram", "kilograms", "litre", "litres", "liter", "liters", "product",
    "products", "solution", "solutions", "system", "systems", "device", "devices", "per", "year",
})

CRITICAL_ROW_SHARE = 0.5
CRITICAL_COUNTRY_SHARE = 0.6
ACCEPT_PLAUSIBILITY = 0.75
CRITICAL_PENALTY = 0.2


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("ches", "shes", "xes", "sses")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def product_stems(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z]+", (text or "").lower()) if len(w) > 2 and w not in _GENERIC_WORDS}


def product_gate(driver: dict[str, Any], market_name: str, llm: GenLLM | None = None) -> str | None:
    """None when the driver measures `market_name`'s own product, else the
    reason it doesn't. Official-statistics drivers (FAOSTAT etc.) are matched
    to the market by item code upstream and always pass."""
    if driver.get("library_status") == "official_data":
        return None
    target = product_stems(market_name)
    if not target:
        return None
    described = " ".join(str(driver.get(k) or "") for k in ("driver_name", "unit", "final_demand_unit"))
    if target <= product_stems(described):
        return None

    reason = (f"driver '{driver.get('driver_name')}' (unit '{driver.get('final_demand_unit') or driver.get('unit')}') "
              f"does not name the market's product ('{market_name}')")
    if llm is None:
        return reason
    system = (
        "You check whether a demand-sizing methodology measures the RIGHT product. Synonyms and "
        "abbreviations of the same product are fine (TV = television, EV = electric vehicle). A broader or "
        "adjacent driver is fine ONLY when its conversion explicitly narrows it to the target product. A "
        "different product is never fine (smartphones for a laptop market, watches for a smartwatch market)."
    )
    user = (
        f"Target market: {market_name}\nDriver: {json.dumps({k: driver.get(k) for k in ('driver_name', 'unit', 'consumption_unit', 'final_demand_unit', 'conversion_formula', 'rationale')}, ensure_ascii=False)}\n\n"
        'Return JSON: {"same_product": <true|false>, "reason": "<one sentence>"}'
    )
    try:
        verdict = llm.complete_json(system, user)
    except Exception as exc:  # noqa: BLE001
        return f"{reason}; product check LLM call failed ({exc})"
    if verdict.get("same_product") is True:
        return None
    return f"{reason}: {str(verdict.get('reason') or '').strip()}"


def _is_rest_of_world(country: str) -> bool:
    return bool(re.match(r"^rest of (the )?world$", country.strip(), re.IGNORECASE))


def deterministic_issues(state: dict[str, Any]) -> list[dict[str, str]]:
    """[{severity, code, message}] -- severity 'critical' blocks acceptance."""
    issues: list[dict[str, str]] = []
    totals: dict[str, float] = state.get("country_totals") or {}
    total = float(state.get("total_market_value") or 0.0)
    if total <= 0:
        return [{"severity": "critical", "code": "zero_total", "message": "The global total is zero."}]

    for f in state.get("quality_flags") or []:
        if f.get("severity") == "critical":
            issues.append({"severity": "critical", "code": f.get("code", ""), "message": f.get("message", "")})

    empty = [c for c, v in totals.items() if not v]
    if empty:
        issues.append({"severity": "warning", "code": "empty_countries",
                       "message": f"{len(empty)} country/ies sized at $0: {', '.join(empty)}."})

    for country, value in totals.items():
        share = value / total
        if _is_rest_of_world(country) and share > CRITICAL_ROW_SHARE:
            issues.append({"severity": "critical", "code": "rest_of_world_dominates",
                           "message": f"Rest of World is {share:.0%} of the global total -- the listed countries "
                                      "are undersized or Rest of World is double counting them."})
        elif not _is_rest_of_world(country) and share > CRITICAL_COUNTRY_SHARE:
            issues.append({"severity": "critical", "code": "single_country_dominates",
                           "message": f"{country} is {share:.0%} of the global total."})
    return issues


def _build_up(state: dict[str, Any]) -> dict[str, Any]:
    """Compact, reviewer-readable view of the sizing state."""
    bucket = next(iter(state.get("drivers") or {}), None)
    driver = (state.get("drivers") or {}).get(bucket, {})
    quantities = (state.get("quantities") or {}).get(bucket, {})
    price = (state.get("prices") or {}).get(bucket, {})
    table = state.get("country_prices") or {}
    countries = {
        c: {
            "quantity": q.get("value"),
            "evidence": q.get("evidence_type") or q.get("provenance"),
            "source": str(q.get("source") or q.get("note") or "")[:80],
            "value_usd_mn": (state.get("country_totals") or {}).get(c),
        }
        for c, q in quantities.items()
    }
    for c, p in (table.get("prices") or {}).items():
        if c in countries:
            countries[c].update(price_usd_per_tonne=round(p["usd_per_t"]), price_method=p["method"])
            pop = ((table.get("population") or {}).get((table.get("iso3") or {}).get(c, "")) or {}).get("value")
            f = tonnes_factor(driver.get("final_demand_unit") or driver.get("unit") or "")
            if pop and f is not None and isinstance(countries[c]["quantity"], (int, float)):
                countries[c]["kg_per_person"] = round(countries[c]["quantity"] * f * 1000.0 / pop, 2)
    return {
        "driver": {k: driver.get(k) for k in ("driver_name", "unit", "final_demand_unit", "conversion_formula", "rationale")},
        "price": ({"per_country": True, "value_basis": table.get("basis"), "basis_note": table.get("basis_note"),
                   "hs_codes": (table.get("hs") or {}).get("codes"), "curve": table.get("curve")} if table else
                  {"usd_per_unit": price.get("asp_usd_per_unit"), "priced_unit": price.get("priced_unit"),
                   "evidence": price.get("evidence_type"), "basis": str(price.get("rationale") or "")[:160]}),
        "countries": countries,
        "global_total_usd_mn": state.get("total_market_value"),
        "published_benchmarks_context_only": [
            {k: b.get(k) for k in ("category", "value", "unit", "year", "source")}
            for b in state.get("triangulation") or []
        ],
    }


RUBRIC = (
    ("driver", "Does each driver measure this product and only this product?"),
    ("per_capita", "Are per-capita quantities physically plausible for each country?"),
    ("mix", "Does the use-case / segment mix match how the product is actually consumed?"),
    ("price_basis", "Is the price basis consistent across countries (one value basis, right level)?"),
    ("evidence", "What share of value rests on verified evidence (10 = nearly all)?"),
)
REDO_STAGES = ("driver", "quantities", "price", "segments", "none")


def llm_review(llm: GenLLM, approved_setup: dict[str, Any], state: dict[str, Any],
               issues: list[dict[str, str]]) -> dict[str, Any]:
    """Market-researcher rubric (system.md §2.3): five 1-10 scores, issues,
    the stage to redo, and redo instructions. Also returns the legacy keys
    {plausibility 0-1 (= rubric mean / 10), verdict, problems, feedback}."""
    system = (
        "You are a senior market researcher reviewing a BOTTOM-UP demand-side market size: one demand "
        "line per country, value = annual quantity x price, countries summed to global. Find ROOT CAUSES "
        "of error: wrong product measured; installed base or users counted instead of annual purchases; "
        "unit mix-ups (thousands vs millions, kg vs tonnes); retail instead of manufacturer price; "
        "implausible per-capita quantities for a country; countries or Rest of World missing or double "
        "counted.\n"
        "Score each rubric item 1-10:\n"
        + "\n".join(f"({i + 1}) {key}: {q}" for i, (key, q) in enumerate(RUBRIC))
        + "\nName concrete issues and the stage that should be redone. Do NOT score on closeness to "
        "published market sizes -- they are context only, and you must never recommend scaling or "
        "calibrating the total to match them. Feedback must be concrete instructions for re-deriving the "
        "driver, quantities or price (e.g. 'use annual unit shipments, not installed base'; 'India "
        "quantity looks 10x too high'). Return json."
    )
    user = (
        f"Market: {approved_setup['market_name']}\nScope: {approved_setup.get('scope', '')}\n"
        f"Physical unit: {approved_setup.get('physical_unit', '')}\nBase year: {approved_setup.get('base_year')}\n\n"
        f"Build-up:\n{json.dumps(_build_up(state), ensure_ascii=False, default=str)}\n\n"
        f"Automatic checks:\n{json.dumps(issues, ensure_ascii=False)}\n\n"
        'Return JSON: {"rubric": {' + ", ".join(f'"{k}": <1-10>' for k, _ in RUBRIC) + '}, '
        '"verdict": "<accept|redo>", "problems": ["<root cause>", ...], '
        '"redo_stage": "<' + "|".join(REDO_STAGES) + '>", "feedback": "<instructions for the next pass>"}'
    )
    try:
        data = llm.complete_json(system, user)
    except Exception as exc:  # noqa: BLE001
        return {"plausibility": 0.0, "rubric": {}, "rubric_mean": 0.0, "verdict": "redo", "redo_stage": "none",
                "problems": [f"review LLM call failed: {exc}"], "feedback": ""}
    rubric: dict[str, float] = {}
    for key, _ in RUBRIC:
        try:
            rubric[key] = min(10.0, max(1.0, float((data.get("rubric") or {}).get(key))))
        except (TypeError, ValueError, AttributeError):
            continue
    if len(rubric) == len(RUBRIC):
        mean = sum(rubric.values()) / len(rubric)
    else:  # legacy / malformed answer: a 0-1 plausibility, else nothing trusted
        try:
            mean = 10.0 * min(1.0, max(0.0, float(data.get("plausibility"))))
        except (TypeError, ValueError):
            mean = 0.0
        rubric = {}
    verdict = "accept" if str(data.get("verdict") or "").strip().lower() == "accept" else "redo"
    stage = str(data.get("redo_stage") or "none").strip().lower()
    problems = [str(p).strip() for p in data.get("problems") or [] if str(p).strip()]
    return {"plausibility": round(mean / 10.0, 4), "rubric": rubric, "rubric_mean": round(mean, 3),
            "verdict": verdict, "redo_stage": stage if stage in REDO_STAGES else "none", "problems": problems,
            "feedback": str(data.get("feedback") or "").strip()}


def score(review: dict[str, Any], issues: list[dict[str, str]]) -> float:
    n_critical = sum(1 for i in issues if i["severity"] == "critical")
    return round(max(0.0, review["plausibility"] - CRITICAL_PENALTY * n_critical), 3)


def accepted(review: dict[str, Any], issues: list[dict[str, str]]) -> bool:
    return (review["verdict"] == "accept" and review["plausibility"] >= ACCEPT_PLAUSIBILITY
            and not any(i["severity"] == "critical" for i in issues))


def feedback_for_next_pass(review: dict[str, Any], issues: list[dict[str, str]]) -> str:
    parts = [review.get("feedback") or ""]
    parts += [f"[{i['code']}] {i['message']}" for i in issues if i["severity"] == "critical"]
    return " ".join(p for p in parts if p).strip()

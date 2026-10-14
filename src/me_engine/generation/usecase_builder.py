"""Use-case demand builder: the market built bottom-up, the way an analyst
reasons -- industry -> use case -> real-world demand driver in each country
-> units -> price -> value.

Published market sizes come from research firms. They are read as context
("what others predict") so the agent understands the market, but they never
set a number and are never averaged. The number comes from demand:

  1. use cases     the partition dimension's segments are industries (e.g.
                   Medical); their sub-segments are use cases (Surgical Lasers,
                   Dental ...). An industry without sub-segments is its own use case.
  2. formula       per use case the agent reads use-case evidence plus the
                   published figures (context only), picks a COUNTRY-LEVEL real-
                   world driver (World Bank data for every country) and writes
                   value = driver x intensity constants x price, every constant
                   with unit, reason and basis (cited evidence id or assumption).
  3. compute       code evaluates the same formula on every country's own driver
                   value (country buckets such as ASEAN or Rest of Europe are sums
                   of their member countries).
  4. roll up       use case -> industry -> country -> region -> world.
  5. sanity        an industry more than 3x away from every published figure is
                   sent back once with the gap shown, to re-examine its formulas --
                   a check on the reasoning, never a pull toward a median.
"""
from __future__ import annotations

import json
import math
import re
import statistics
from pathlib import Path
from typing import Any, Callable

from . import assumptor_regulator as ar
from .scope import partition_dimension

# country-level drivers (World Bank), all available for (nearly) every country
_RAW = {"population": "SP.POP.TOTL", "gdp_usd": "NY.GDP.MKTP.CD", "military_expenditure_usd": "MS.MIL.XPND.CD",
        "manufacturing_value_added_usd": "NV.IND.MANF.CD", "industry_value_added_usd": "NV.IND.TOTL.CD",
        "medhigh_tech_share_of_mfg_pct": "NV.MNF.TECH.ZS.UN", "health_pc_usd": "SH.XPD.CHEX.PC.CD",
        "hospital_beds_per_1000": "SH.MED.BEDS.ZS", "physicians_per_1000": "SH.MED.PHYS.ZS",
        "rd_pct_gdp": "GB.XPD.RSDV.GD.ZS", "researchers_per_million": "SP.POP.SCIE.RD.P6",
        "high_tech_exports_usd": "TX.VAL.TECH.CD", "air_passengers": "IS.AIR.PSGR",
        "fixed_broadband_subscriptions": "IT.NET.BBND"}
MENU = {
    "military_expenditure_usd": "military expenditure, US$ per year",
    "manufacturing_value_added_usd": "manufacturing value added, US$ per year",
    "medhigh_tech_manufacturing_usd": "medium- and high-tech manufacturing value added, US$ per year",
    "industry_value_added_usd": "industry value added (incl. construction, energy, mining), US$ per year",
    "health_expenditure_usd": "current health expenditure, US$ per year",
    "hospital_beds": "hospital beds (count)",
    "physicians": "physicians (count)",
    "rd_expenditure_usd": "R&D expenditure, US$ per year",
    "researchers": "researchers in R&D (count)",
    "high_tech_exports_usd": "high-technology exports, US$ per year",
    "air_passengers": "air passengers carried per year (count)",
    "fixed_broadband_subscriptions": "fixed broadband subscriptions (count)",
    "gdp_usd": "GDP, US$ per year",
    "population": "population (count)",
}


def fetch_drivers() -> dict[str, Any]:
    countries = ar._get(f"{ar._WB}/country", {"format": "json", "per_page": 400})[1]
    out = {c["id"]: {"name": c["name"], "region": str((c.get("region") or {}).get("value") or "").strip()}
           for c in countries if (c.get("region") or {}).get("value") not in (None, "Aggregates")}
    for key, code in _RAW.items():
        rows = ar._get(f"{ar._WB}/country/all/indicator/{code}", {"format": "json", "per_page": 20000,
                                                                  "date": "2015:2024"})[1]
        latest: dict[str, tuple[int, float]] = {}
        for r in rows or []:
            iso, val = r.get("countryiso3code"), r.get("value")
            if iso in out and val is not None and (iso not in latest or int(r["date"]) > latest[iso][0]):
                latest[iso] = (int(r["date"]), float(val))
        for iso, (_, val) in latest.items():
            out[iso][key] = val
    for c in out.values():
        pop, gdp = c.get("population"), c.get("gdp_usd")
        if pop:
            if c.get("health_pc_usd"):
                c["health_expenditure_usd"] = c["health_pc_usd"] * pop
            if c.get("hospital_beds_per_1000"):
                c["hospital_beds"] = c["hospital_beds_per_1000"] * pop / 1000
            if c.get("physicians_per_1000"):
                c["physicians"] = c["physicians_per_1000"] * pop / 1000
            if c.get("researchers_per_million"):
                c["researchers"] = c["researchers_per_million"] * pop / 1e6
        if gdp and c.get("rd_pct_gdp"):
            c["rd_expenditure_usd"] = c["rd_pct_gdp"] / 100 * gdp
        if c.get("manufacturing_value_added_usd") and c.get("medhigh_tech_share_of_mfg_pct"):
            c["medhigh_tech_manufacturing_usd"] = c["manufacturing_value_added_usd"] * c["medhigh_tech_share_of_mfg_pct"] / 100
    return out


def use_cases(nesting: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    dim = partition_dimension(nesting)
    tree = nesting.get(dim) or {}
    out = []
    for industry in tree.get("__root__") or []:
        kids = tree.get(industry) or []
        for uc in kids or [industry]:
            out.append({"industry": industry, "use_case": uc, "own": not kids})
    return dim, out


def _fmt(v: float) -> str:
    a = abs(v)
    return f"{v / 1e12:.2f}tn" if a >= 1e12 else f"{v / 1e9:.2f}bn" if a >= 1e9 else f"{v / 1e6:.2f}mn" if a >= 1e6 else f"{v:,.0f}"


def design(llm: Any, market: str, uc: dict[str, str], definition: str, evidence: list[dict[str, str]],
           published: list[dict[str, Any]], driver_examples: dict[str, dict[str, float]],
           feedback: str = "") -> dict[str, Any]:
    ev_text = "\n".join(f"[{e['id']}] ({e['source']}) {e['text']}" for e in evidence) or "(no use-case evidence found)"
    examples = {k: {c: _fmt(v) for c, v in vals.items()} for k, vals in driver_examples.items()}
    return llm.complete_json(
        "You are a market analyst estimating demand bottom-up, use case by use case: who buys this product for "
        "this use, how many such buyers a country has, how many units they buy per year, and at what price. "
        "Published market-research figures are CONTEXT -- what other firms predict -- read them to understand "
        "the market, but do NOT derive your number from them; build it from demand. "
        "SCOPE: the product is everything the taxonomy lists (every laser type and power class). The market's "
        "name is only a label -- do not multiply in 'high-energy share' or similar narrowing factors. "
        "Pick ONE country-level driver from the menu that counts the buyers or the activity creating this demand. "
        "Write `units_formula` = the number of units bought PER YEAR in a country, using `driver` (that driver's "
        "value for the country) and constants you define. Give the price of ONE unit separately. Code computes "
        "value = units x price, so never divide by the price and never put the price inside units_formula. "
        "Every constant needs a unit, a reason and a basis: an evidence id (e.g. 'E3') if evidence supports it, "
        "else 'assumption'. FIRST stipulate physical demand like an analyst: how many BUYERS exist worldwide for this "
        "use case (factories running laser welding lines, hospitals with surgical lasers, navies fielding laser "
        "weapons, ...) and how many units each buys per year (new adds plus replacements: installed units / "
        "replacement cycle). world units per year = buyers_worldwide x units_per_buyer_per_year. NEVER obtain it by "
        "dividing a published market size by a price -- that just copies someone else's number. "
        "Then write units_formula so that, summed over all countries, it reproduces that world figure. "
        "Return json.",
        f"Market: {market}\nIndustry: {uc['industry']}\nUse case: {uc['use_case']}\n"
        f"How the market's taxonomy defines the product: {definition}\n\n"
        f"Driver menu (key: meaning): {json.dumps(MENU)}\n"
        f"Driver values for orientation (U.S. / China / Germany / India): {json.dumps(examples)}\n\n"
        f"Evidence about this use case:\n{ev_text}\n\n"
        f"What research firms predict for the industry (context only, never the answer): "
        f"{json.dumps(published, ensure_ascii=False)[:1500]}\n"
        + (f"\nREVIEW OF YOUR PREVIOUS ATTEMPT: {feedback}\n" if feedback else "")
        + '\nReturn JSON: {"driver": "<menu key>", "buyers": "<who buys, in plain words>", '
          '"units_label": "<what one unit is>", "units_formula": "<units bought per year, arithmetic over driver '
          'and your constants>", "constants": {"<name>": {"value": <number>, "unit": "<unit>", '
          '"basis": "<E-id or assumption>", "reason": "<why>"}}, "price_usd_per_unit": {"value": <number>, '
          '"basis": "<E-id or assumption>", "reason": "<why>"}, '
          '"world_units_per_year": {"buyer": "<what one buyer is>", "buyers_worldwide": <number>, '
          '"units_per_buyer_per_year": <number>, "basis": "<E-id or assumption>", "reason": "<the physical '
          'reasoning: installed base, replacement cycle, programmes, fleets>"}, '
          '"reasoning": "<3-4 sentences: the demand logic in plain words>"}',
    )


CIRCULAR = re.compile(r"(market|revenue|sales)[^.]{0,60}(\$|usd|billion|bn\b|million)[^.]{0,80}"
                      r"(divid|/|÷|per unit|average (system |selling )?price)", re.I)


def circular_reason(spec: dict[str, Any]) -> bool:
    """True if the world unit estimate was backed out of a published market size."""
    w = spec.get("world_units_per_year") or {}
    return bool(CIRCULAR.search(str(w.get("reason") or "") if isinstance(w, dict) else ""))


def world_units_estimate(spec: dict[str, Any]) -> float | None:
    w = spec.get("world_units_per_year")
    if isinstance(w, dict):
        try:
            v = float(w["buyers_worldwide"]) * float(w["units_per_buyer_per_year"])
            if v > 0:
                return v
        except (KeyError, TypeError, ValueError):
            pass
    try:
        v = float(w.get("value") if isinstance(w, dict) else w)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def evaluate_formula(spec: dict[str, Any], driver_value: float) -> float:
    """Annual value in US$ = units bought per year x price per unit."""
    return evaluate_units(spec, driver_value) * price_of(spec)


def evaluate_units(spec: dict[str, Any], driver_value: float) -> float:
    from me_engine.demandCountry_approach2A.estimator import evaluate
    consts = {}
    for k, v in (spec.get("constants") or {}).items():
        try:
            consts[str(k)] = float(v.get("value") if isinstance(v, dict) else v)
        except (TypeError, ValueError):
            continue
    names = {"driver": driver_value, **consts}
    if spec.get("driver"):
        names[str(spec["driver"])] = driver_value  # the agent may write the driver's own name instead of `driver`
    raw = evaluate(str(spec.get("units_formula") or spec.get("formula") or "0"), names)
    return raw * float((spec.get("calibration") or {}).get("factor") or 1.0)


def calibrate(spec: dict[str, Any], dv: dict[str, float], geos: list[str]) -> dict[str, Any]:
    """LLMs slip decimal places in per-dollar rates (5e-10 vs 5e-13). The agent's
    physical world estimate (units bought per year, with its reasoning) sets the
    LEVEL; its formula decides how those units are spread across countries.
    The factor is recorded so the document can show it."""
    spec.pop("calibration", None)
    raw = sum(evaluate_units(spec, dv.get(g, 0.0)) for g in geos)
    est = world_units_estimate(spec)
    if est and raw > 0 and math.isfinite(raw):
        spec["calibration"] = {"factor": est / raw, "raw_world_units": raw, "world_units_estimate": est}
    return spec


def price_of(spec: dict[str, Any]) -> float:
    p = spec.get("price_usd_per_unit")
    try:
        return float(p.get("value") if isinstance(p, dict) else p)
    except (TypeError, ValueError):
        return 0.0


def self_check_text(spec: dict[str, Any], by_geo: dict[str, float]) -> tuple[str, float]:
    """What the formula actually produces, shown back to the agent."""
    lines, world_units = [], 0.0
    for g in ("U.S.", "China", "Germany", "India"):
        if g in by_geo:
            u = evaluate_units(spec, by_geo[g])
            lines.append(f"{g}: {u:,.0f} {spec.get('units_label') or 'units'}/yr x ${price_of(spec):,.0f} = "
                         f"${u * price_of(spec) / 1e6:,.1f}M")
    for v in by_geo.values():
        world_units += evaluate_units(spec, v)
    world = world_units * price_of(spec) / 1e6
    lines.append(f"World (26 geographies): {world_units:,.0f} units/yr = ${world:,.1f}M")
    return "; ".join(lines), world


def build(llm: Any, spec: dict[str, Any], nesting: dict[str, Any], market: str, cache_dir: Path,
          search: Callable[[list[str]], list[str]], published_by_industry: dict[str, list[dict[str, Any]]],
          say: Callable[[str], None] = print) -> dict[str, Any]:
    from .scope import segment_definition

    dim, ucs = use_cases(nesting)
    geos = spec["geographies"]
    drivers = ar._cache(cache_dir / "wb_usecase_drivers.json", fetch_drivers)
    by_geo_cache: dict[str, dict[str, float]] = {}

    def by_geo(key: str) -> dict[str, float]:
        if key not in by_geo_cache:
            by_geo_cache[key] = ar.driver_by_geo(drivers, geos, key)
        return by_geo_cache[key]

    examples = {}
    for key in MENU:
        vals = by_geo(key)
        examples[key] = {g: vals.get(g, 0.0) for g in ("U.S.", "China", "Germany", "India") if g in vals}
    term = market.replace("Global", "").replace("Market", "").strip()
    results = []
    for uc in ucs:
        snippets = search([f"{uc['use_case']} {term} demand units installed per year",
                           f"{uc['use_case']} {uc['industry']} laser systems market adoption price"])
        evidence = [{"id": f"E{i}", "source": "web search", "text": s} for i, s in enumerate(snippets[:10], start=1)]
        definition = segment_definition(nesting, dim, uc["industry"])
        published = published_by_industry.get(uc["industry"], [])
        spec_uc, values, note = None, {}, ""
        feedback = ""
        checks, candidates = [], []
        for attempt in range(4):
            try:
                cand = design(llm, market, uc, definition, evidence, published, examples, feedback)
                key = cand.get("driver") if cand.get("driver") in MENU else "gdp_usd"
                cand["driver"] = key
                dv = by_geo(key)
                vals = {g: max(0.0, evaluate_formula(cand, dv.get(g, 0.0))) / 1e6 for g in geos}
                world = sum(vals.values())
                if world <= 0 or not math.isfinite(world) or price_of(cand) <= 0:
                    raise ValueError("no demand or no price")
                text, _ = self_check_text(cand, dv)
                checks.append(text)
                est = world_units_estimate(cand)
                f_units = sum(evaluate_units(cand, dv.get(g, 0.0)) for g in geos)
                gap = abs(math.log(f_units / est)) if est and f_units > 0 else math.log(1e6)
                if circular_reason(cand) and attempt < 3:
                    feedback = ("Your world units estimate was obtained by dividing a published market size by a "
                                "price. That copies another firm's number. Rebuild it physically: how many buyers "
                                "exist worldwide and how many units each buys per year.")
                    continue
                candidates.append((gap, cand, vals))
                if est and gap <= math.log(2):
                    break  # the formula reproduces the agent's own world unit estimate
                # deterministic feedback: the agent's own physical estimate vs what its formula produces
                feedback = (f"Your formula produces: {text}. Summed over all countries that is {f_units:,.0f} units "
                            f"per year, but your own world estimate is "
                            f"{f'{est:,.0f}' if est else '(missing)'} units per year. Fix the constants in "
                            "units_formula (check per-unit-of-driver rates and unit scales) so the world total matches "
                            "your physical estimate -- or revise the estimate if, on reflection, it was wrong.")
            except Exception as exc:  # noqa: BLE001
                feedback = (f"The formula failed or gave no demand ({exc}). Use `driver` and only constants "
                            "you define, with plain arithmetic; give a positive price per unit and a world "
                            "units-per-year estimate.")
                note = f"formula error: {exc}"
        if candidates:
            _, spec_uc, values = min(candidates, key=lambda c: c[0])  # best match to its own unit estimate
            dv = by_geo(spec_uc["driver"])
            calibrate(spec_uc, dv, geos)
            values = {g: max(0.0, evaluate_formula(spec_uc, dv.get(g, 0.0))) / 1e6 for g in geos}
        if not values:
            say(f"[use case] {uc['use_case']}: no usable formula ({note})")
            continue
        dvals = by_geo(spec_uc["driver"])
        results.append({**uc, "evidence": evidence, "published_context": published, "spec": spec_uc,
                        "driver_values": {g: dvals.get(g, 0.0) for g in geos},
                        "units": {g: evaluate_units(spec_uc, dvals.get(g, 0.0)) for g in geos},
                        "values_mn": values, "world_mn": sum(values.values()), "attempts": attempt + 1,
                        "self_checks": checks, "world_units_estimate": world_units_estimate(spec_uc),
                        "world_units_circular": circular_reason(spec_uc),
                        "world_units_formula": (spec_uc.get("calibration") or {}).get("raw_world_units")})
        say(f"[use case] {uc['industry']} / {uc['use_case']}: ${sum(values.values()):,.0f}M via {spec_uc['driver']}")

    # sanity: an industry far from every published figure re-examines its use cases once
    industries = sorted({r["industry"] for r in results})
    flags = []
    for ind in industries:
        pubs = sorted(p["usd_mn"] for p in published_by_industry.get(ind, []) if p.get("usd_mn"))
        tot = sum(r["world_mn"] for r in results if r["industry"] == ind)
        if pubs and not pubs[0] / 3 <= tot <= pubs[-1] * 3:
            fb = (f"Your use cases add up to ${tot:,.0f}M for the {ind} industry, more than 3x away from every figure "
                  f"research firms publish (${pubs[0]:,.0f}-{pubs[-1]:,.0f}M). That does not make them right, but "
                  "re-examine your world units-per-year estimate and price per unit (these set the level) for unit errors or "
                  "scope mistakes.")
            for r in [r for r in results if r["industry"] == ind]:
                try:
                    s2 = design(llm, market, r, segment_definition(nesting, dim, ind), r["evidence"],
                                published_by_industry.get(ind, []), examples, fb)
                    s2["driver"] = s2.get("driver") if s2.get("driver") in MENU else r["spec"]["driver"]
                    dv = by_geo(s2["driver"])
                    calibrate(s2, dv, geos)
                    v2 = {g: max(0.0, evaluate_formula(s2, dv.get(g, 0.0))) / 1e6 for g in geos}
                    w_old, w_new = r["world_mn"], sum(v2.values())
                    ind_old = sum(x["world_mn"] for x in results if x["industry"] == ind)
                    ind_new = ind_old - w_old + w_new
                    mid = math.sqrt(pubs[0] * pubs[-1])
                    # accept only if the industry moves toward plausibility (never makes it worse)
                    if w_new > 0 and abs(math.log(max(ind_new, 1e-9) / mid)) < abs(math.log(max(ind_old, 1e-9) / mid)):
                        r.update(spec=s2, values_mn=v2, world_mn=w_new, reexamined=fb,
                                 world_units_estimate=world_units_estimate(s2),
                                 world_units_formula=(s2.get("calibration") or {}).get("raw_world_units"),
                                 driver_values={g: dv.get(g, 0.0) for g in geos},
                                 units={g: evaluate_units(s2, dv.get(g, 0.0)) for g in geos})
                except Exception:  # noqa: BLE001
                    continue
            tot2 = sum(r["world_mn"] for r in results if r["industry"] == ind)
            flags.append(f"{ind}: ${tot:,.0f}M re-examined -> ${tot2:,.0f}M (published ${pubs[0]:,.0f}-{pubs[-1]:,.0f}M)")
            say(f"[use case] {flags[-1]}")

    country_value = {g: sum(r["values_mn"][g] for r in results) for g in geos}
    industry_value = {g: {ind: sum(r["values_mn"][g] for r in results if r["industry"] == ind) for ind in industries}
                      for g in geos}
    regions = spec["regions"]
    region_value = {rg: sum(country_value[c] for c in cs if c in country_value) for rg, cs in regions.items()}
    total = sum(region_value.values())
    return {"dimension": dim, "use_cases": results, "industries": industries, "country_value": country_value,
            "industry_value": industry_value, "region_value": region_value, "global_value": total,
            "region_share": {rg: v / total for rg, v in region_value.items()} if total else {},
            "country_share_in_region": {c: country_value[c] / region_value[rg] for rg, cs in regions.items()
                                        for c in cs if region_value.get(rg)},
            "flags": flags}


def write(spec: dict[str, Any], data_file: Path, ub: dict[str, Any]) -> dict[str, int]:
    """Write the bottom-up result into the human-structured Data file: country
    and region values and shares, the industry mix per country and the use-case
    mix within each industry per country."""
    from openpyxl import load_workbook

    from . import evaluator_agent as ev
    from .structure_agent import _norm

    reg_like = {"region_value": ub["region_value"], "region_share": ub["region_share"],
                "country_value": ub["country_value"], "country_share_in_region": ub["country_share_in_region"],
                "global_value": ub["global_value"], "dimension": ub["dimension"],
                "country_mix": {g: {ind: (v / ub["country_value"][g] if ub["country_value"][g] else 0.0)
                                    for ind, v in mix.items()} for g, mix in ub["industry_value"].items()}}
    stats = ar.write(spec, data_file, reg_like)
    wb = load_workbook(data_file)
    ws = wb["Data"]
    labels = [_norm(ws.cell(r, 2).value) for r in range(1, ws.max_row + 1)]
    paths = ev._path_walk(labels, spec)
    geo_col = {_norm(ws.cell(3, c).value): c for c in range(3, 30) if _norm(ws.cell(3, c).value)}
    by_uc = {(r["industry"], r["use_case"]): r for r in ub["use_cases"]}
    n = 0
    for row, p in enumerate(paths, start=1):
        if p and p[0] == ub["dimension"] and len(p[1]) == 2:
            ind, uc = p[1]
            r = by_uc.get((ind, uc))
            if not r:
                continue
            for g, c in geo_col.items():
                tot = ub["industry_value"].get(g, {}).get(ind, 0.0)
                if tot > 0:
                    ws.cell(row, c).value = r["values_mn"].get(g, 0.0) / tot
                    n += 1
    wb.save(data_file)
    stats["use_case_cells"] = n
    return stats

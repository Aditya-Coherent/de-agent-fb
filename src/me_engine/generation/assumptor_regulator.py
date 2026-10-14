"""Assumptor regulation of the Input Sheet: published numbers are artifacts,
real-world data decides.

Market-research firms publish conflicting numbers for the same market. Here
they are evidence the assumptor reads, never the answer:

  1. Real-world drivers -- World Bank data for every country: military
     expenditure, manufacturing value added, health expenditure, R&D
     expenditure, high-tech exports, internet users, GDP. Each end-use segment
     of the partition dimension (By Application) gets the driver that
     physically generates its demand (LLM picks from this fixed menu, with a
     reason; cached).
  2. Global segment value -- the assumptor writes its OWN assumption as a
     formula over named inputs (the segment's real-world driver total, the
     honest publisher artifacts, its own constants with reasons); code
     evaluates the formula (safe arithmetic only). The honest artifacts bound
     it: with 2+ independent publishers the assumption is clipped into their
     range and flagged -- the artifacts regulate, they do not decide.
  3. Allocation -- each segment's global value is split across the sheet's
     geographies by that segment's driver (country buckets built from World
     Bank countries: ASEAN, GCC, 'Rest of Europe', ...). This yields every
     country's value, each region's value and share, each country's share of
     its region, and each country's end-use mix -- all from real data.
  4. Write -- into the human-structured Data file (Market Value sheet, region
     block, the partition dimension's parent shares per country). Children
     shares within each parent and every other dimension are left as generated.

Everything (drivers, formulas, constants, artifacts, clipping) is returned
and saved, so each number can be traced to real data plus stated assumptions.
"""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any, Callable

import httpx
from openpyxl import load_workbook

from . import evaluator_agent as ev
from .scope import partition_dimension
from .structure_agent import _norm

_WB = "https://api.worldbank.org/v2"
GUARD = 3.0   # a generated share more than 3x away from the driver-implied share is pulled to the bound
DRIVERS = {
    "military_expenditure_usd": ("MS.MIL.XPND.CD", "military expenditure (current US$)"),
    "manufacturing_value_added_usd": ("NV.IND.MANF.CD", "manufacturing value added (current US$)"),
    "health_expenditure_usd": (None, "current health expenditure (US$) = per-capita health spend x population"),
    "rd_expenditure_usd": (None, "R&D expenditure (US$) = R&D % of GDP x GDP"),
    "high_tech_exports_usd": ("TX.VAL.TECH.CD", "high-technology exports (current US$)"),
    "gdp_usd": ("NY.GDP.MKTP.CD", "GDP (current US$)"),
}
_RAW = {"population": "SP.POP.TOTL", "gdp_usd": "NY.GDP.MKTP.CD", "military_expenditure_usd": "MS.MIL.XPND.CD",
        "manufacturing_value_added_usd": "NV.IND.MANF.CD", "health_pc_usd": "SH.XPD.CHEX.PC.CD",
        "rd_pct_gdp": "GB.XPD.RSDV.GD.ZS", "high_tech_exports_usd": "TX.VAL.TECH.CD", "internet_pct": "IT.NET.USER.ZS"}

# ---- geography buckets ------------------------------------------------------
_NAMED = {"U.S.": "USA", "Canada": "CAN", "U.K.": "GBR", "Germany": "DEU", "Italy": "ITA", "France": "FRA",
          "Spain": "ESP", "Russia": "RUS", "China": "CHN", "India": "IND", "Japan": "JPN", "South Korea": "KOR",
          "Australia": "AUS", "Brazil": "BRA", "Argentina": "ARG", "Mexico": "MEX", "Israel": "ISR",
          "South Africa": "ZAF"}
_GROUPS = {"ASEAN": {"BRN", "KHM", "IDN", "LAO", "MYS", "MMR", "PHL", "SGP", "THA", "VNM"},
           "GCC": {"SAU", "ARE", "QAT", "KWT", "OMN", "BHR"},
           "North Africa": {"DZA", "EGY", "LBY", "MAR", "TUN", "SDN"}}
# Afghanistan and Pakistan sit in the World Bank's renamed MENA region but belong to Asia Pacific here
_CENTRAL_ASIA = {"KAZ", "KGZ", "TJK", "TKM", "UZB", "AFG", "PAK"}
_REST = {"Rest of Europe": ("Europe & Central Asia",), "Rest of Asia Pacific": ("East Asia & Pacific", "South Asia"),
         "Rest of Latin America": ("Latin America & Caribbean",), "Rest of Middle East": ("Middle East & North Africa",
                                                       "Middle East, North Africa, Afghanistan & Pakistan"),
         "Central Africa": ("Sub-Saharan Africa",)}


def _cache(path: Path, build: Callable[[], Any]) -> Any:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    data = build()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def _get(url: str, params: dict[str, Any]) -> Any:
    import time
    last = None
    for attempt in range(5):  # the World Bank API times out under load
        try:
            return httpx.get(url, params=params, timeout=180).json()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"World Bank API unreachable: {last}")


def fetch_drivers() -> dict[str, Any]:
    """{ISO3: {name, region, population, gdp_usd, ..., derived drivers}} -- latest value 2015-2024."""
    countries = _get(f"{_WB}/country", {"format": "json", "per_page": 400})[1]
    out = {c["id"]: {"name": c["name"], "region": str((c.get("region") or {}).get("value") or "").strip()}
           for c in countries if (c.get("region") or {}).get("value") not in (None, "Aggregates")}
    for key, code in _RAW.items():
        rows = _get(f"{_WB}/country/all/indicator/{code}",
                    {"format": "json", "per_page": 20000, "date": "2018:2024"})[1]
        latest: dict[str, tuple[int, float]] = {}
        for r in rows or []:
            iso, val = r.get("countryiso3code"), r.get("value")
            if iso in out and val is not None and (iso not in latest or int(r["date"]) > latest[iso][0]):
                latest[iso] = (int(r["date"]), float(val))
        for iso, (_, val) in latest.items():
            out[iso][key] = val
    for c in out.values():
        pop, gdp = c.get("population"), c.get("gdp_usd")
        if c.get("health_pc_usd") and pop:
            c["health_expenditure_usd"] = c["health_pc_usd"] * pop
        if c.get("rd_pct_gdp") and gdp:
            c["rd_expenditure_usd"] = c["rd_pct_gdp"] / 100 * gdp
        if c.get("internet_pct") and pop:
            c["internet_users"] = c["internet_pct"] / 100 * pop
    return out


def bucket_of(iso: str, info: dict[str, Any], geographies: list[str]) -> str | None:
    for geo, code in _NAMED.items():
        if code == iso and geo in geographies:
            return geo
    for geo, members in _GROUPS.items():
        if iso in members and geo in geographies:
            return geo
    if iso in _CENTRAL_ASIA:
        return "Rest of Asia Pacific" if "Rest of Asia Pacific" in geographies else None
    for geo, regions in _REST.items():
        if info.get("region") in regions and geo in geographies:
            return geo
    return None


def driver_by_geo(drivers: dict[str, Any], geographies: list[str], key: str) -> dict[str, float]:
    """Driver summed over each geography bucket; a country missing the driver
    contributes GDP x (its region's driver/GDP ratio) so gaps don't zero a bucket."""
    ratio: dict[str, list[float]] = {}
    for c in drivers.values():
        if c.get(key) and c.get("gdp_usd"):
            ratio.setdefault(c["region"], []).append(c[key] / c["gdp_usd"])
    med = {r: statistics.median(v) for r, v in ratio.items()}
    out = {g: 0.0 for g in geographies}
    for iso, c in drivers.items():
        g = bucket_of(iso, c, geographies)
        if not g:
            continue
        v = c.get(key)
        if v is None and c.get("gdp_usd") and c.get("region") in med:
            v = c["gdp_usd"] * med[c["region"]]
        out[g] += float(v or 0.0)
    return out


# ---- the assumptor ----------------------------------------------------------
def pick_drivers(llm: Any, market: str, segments: list[str], definitions: dict[str, str]) -> dict[str, dict[str, str]]:
    menu = {k: v[1] for k, v in DRIVERS.items()}
    data = llm.complete_json(
        "For each end-use segment of a market, pick the ONE real-world economic quantity (from the menu) whose size "
        "across countries best tracks that segment's demand. Return json.",
        f"Market: {market}\nSegments: {json.dumps({s: definitions.get(s, '') for s in segments}, ensure_ascii=False)}\n"
        f"Menu: {json.dumps(menu)}\n"
        'Return JSON: {"<segment>": {"driver": "<menu key>", "reason": "<one sentence>"}}',
    )
    out = {}
    for s in segments:
        d = (data.get(s) or {}) if isinstance(data.get(s), dict) else {}
        key = d.get("driver") if d.get("driver") in DRIVERS else "gdp_usd"
        out[s] = {"driver": key, "reason": str(d.get("reason") or "fallback: GDP")[:200]}
    return out


def assume_segment(llm: Any, market: str, segment: str, definition: str, driver_key: str, driver_total_usd: float,
                   component: dict[str, Any] | None) -> dict[str, Any]:
    """The assumptor's own global value for a segment: a formula over named inputs,
    evaluated by code, regulated by the honest artifacts."""
    from me_engine.demandCountry_approach2A.estimator import evaluate

    comp = component or {}
    artifacts = [{"publisher": f["publisher"], "usd_mn": round(f["adjusted_mn"], 1), "independent": bool(f.get("independent"))}
                 for f in comp.get("figures") or []]
    independent = sorted(a["usd_mn"] for a in artifacts if a["independent"])
    names = {"driver_total_usd_mn": driver_total_usd / 1e6}
    if independent:
        names["artifact_median_usd_mn"] = statistics.median(independent)
    system = (
        "You are the assumptor. Make your OWN realistic assumption of a market segment's 2025 value from real-world "
        "data, written as a formula over named inputs. Published market-research figures are ARTIFACTS: read "
        "them to understand the order of magnitude and how others define the segment, but derive your number "
        "from the real-world driver (e.g. driver_total_usd_mn x the share of it spent on this product), stating "
        "every constant you introduce and why. SCOPE: the taxonomy definition is the scope -- every product type "
        "and sub-use it lists is in. The market's name is only a label: do NOT multiply in extra fractions such as "
        "'high-energy share' or 'addressable fraction' that narrow the taxonomy's scope, and do not stack several "
        "small fractions when one share-of-driver is meant. Return json.")
    data = llm.complete_json(
        system,
        f"Segment: {segment} use of the {market}\nTaxonomy definition: {definition}\n"
        f"Real-world driver: {DRIVERS[driver_key][1]}, global total = {driver_total_usd / 1e9:,.1f} US$ bn "
        f"(input name driver_total_usd_mn = {driver_total_usd / 1e6:,.0f})\n"
        f"Published artifacts (US$ Mn, scope-adjusted; independent = a named firm measuring this segment itself): "
        f"{json.dumps(artifacts, ensure_ascii=False)}\n"
        + (f"artifact_median_usd_mn = {names['artifact_median_usd_mn']:,.0f}\n" if independent else "")
        + 'Return JSON: {"formula": "<arithmetic over input names and your constants>", '
          '"constants": {"<name>": {"value": <number>, "reason": "<why>"}}, "assumption_usd_mn": <number>, '
          '"reasoning": "<2-3 sentences>"}',
    )
    consts = {}
    for k, v in (data.get("constants") or {}).items():
        try:
            consts[str(k)] = float(v.get("value") if isinstance(v, dict) else v)
        except (TypeError, ValueError):
            continue
    try:
        value = evaluate(str(data.get("formula") or ""), {**names, **consts})
        how = "formula"
    except Exception:  # noqa: BLE001 -- unparseable formula: the stated number, flagged
        try:
            value, how = float(data.get("assumption_usd_mn")), "stated (formula failed)"
        except (TypeError, ValueError):
            value, how = (names.get("artifact_median_usd_mn") or 0.0), "artifact median (no assumption)"
    flags = []
    all_art = sorted(a["usd_mn"] for a in artifacts if a["usd_mn"] > 0)
    lo, hi = (all_art[0] / 3, all_art[-1] * 3) if all_art else (None, None)
    if lo is not None and not lo <= value <= hi:  # repair loop: implausible vs every artifact
        feedback = (f"Your formula gives ${value:,.0f}M, more than 3x outside the range of every published artifact "
                    f"(${all_art[0]:,.0f}-{all_art[-1]:,.0f}M). Check units (driver_total_usd_mn is in US$ millions) "
                    "and whether you stacked narrowing fractions; recompute the segment as the taxonomy defines it.")
        try:
            data2 = llm.complete_json(system, f"Previous formula: {data.get('formula')} with constants "
                                      f"{json.dumps(data.get('constants'), default=str)[:1500]}\n{feedback}\n"
                                      f"Inputs: driver_total_usd_mn = {driver_total_usd / 1e6:,.0f}"
                                      + (f", artifact_median_usd_mn = {names['artifact_median_usd_mn']:,.0f}"
                                         if independent else "")
                                      + '\nReturn JSON: {"formula": "...", "constants": {"<name>": {"value": <n>, '
                                        '"reason": "..."}}, "assumption_usd_mn": <n>, "reasoning": "..."}')
            consts2 = {str(k): float(v.get("value") if isinstance(v, dict) else v)
                       for k, v in (data2.get("constants") or {}).items()}
            value2 = evaluate(str(data2.get("formula") or ""), {**names, **consts2})
            data, value, how = data2, value2, "formula (repaired)"
            flags.append("first formula was implausible vs the artifacts -- repaired")
        except Exception:  # noqa: BLE001
            pass
        if not lo <= value <= hi:
            flags.append(f"assumption ${value:,.0f}M still outside 3x the artifact range -- clipped")
            value = min(max(value, lo), hi)
    if len(independent) >= 2 and not independent[0] <= value <= independent[-1]:
        flags.append(f"assumption ${value:,.0f}M outside the independent publisher range "
                     f"${independent[0]:,.0f}-{independent[-1]:,.0f}M -- clipped")
        value = min(max(value, independent[0]), independent[-1])
    return {"segment": segment, "value_mn": value, "how": how, "formula": data.get("formula"),
            "constants": data.get("constants"), "driver": driver_key, "driver_total_usd_bn": driver_total_usd / 1e9,
            "artifacts": artifacts, "n_independent": len(independent), "flags": flags,
            "reasoning": str(data.get("reasoning") or "")[:400]}


def _geo_blend(a: dict[str, float], b: dict[str, float]) -> dict[str, float]:
    """Geometric blend of two splits (same keys), renormalised to a's total."""
    keys = [k for k in a if a.get(k, 0) > 0 and b.get(k, 0) > 0]
    if not keys:
        return dict(a)
    tot_a, tot_b = sum(a[k] for k in keys), sum(b[k] for k in keys)
    raw = {k: math.sqrt((a[k] / tot_a) * (b[k] / tot_b)) for k in keys}
    s = sum(raw.values())
    out = {k: sum(a.values()) * v / s for k, v in raw.items()}
    for k in a:
        out.setdefault(k, a[k] if k not in b else 0.0)
    return out


def regulate(llm: Any, spec: dict[str, Any], nesting: dict[str, Any], scope_rec: dict[str, Any] | None,
             cache_dir: Path, say: Callable[[str], None] = print,
             generated: dict[str, Any] | None = None) -> dict[str, Any]:
    """`generated` = the generator's own split (evaluator_agent.read_data_file of the
    human-structured Data file): real-world drivers REGULATE it (geometric blend),
    they do not replace it."""
    from .scope import segment_definition

    dim = partition_dimension(nesting)
    segments = list((nesting.get(dim) or {}).get("__root__") or [])
    geos = spec["geographies"]
    drivers = _cache(cache_dir / "wb_drivers.json", fetch_drivers)
    definitions = {s: segment_definition(nesting, dim, s) for s in segments}
    import hashlib
    picks = _cache(cache_dir / f"driver_picks_{hashlib.sha1('|'.join(segments).encode()).hexdigest()[:10]}.json",
                   lambda: pick_drivers(llm, spec["market"], segments, definitions))
    comps = {c["segment"]: c for c in ((scope_rec or {}).get("cache", {}).get("bottom_up") or {}).get("components", [])}
    seg_out, alloc = {}, {}
    for s in segments:
        key = picks[s]["driver"]
        by_geo = driver_by_geo(drivers, geos, key)
        total = sum(by_geo.values())
        a = assume_segment(llm, spec["market"], s, definitions[s], key, total, comps.get(s))
        a["driver_reason"] = picks[s]["reason"]
        seg_out[s] = a
        alloc[s] = {g: a["value_mn"] * v / total for g, v in by_geo.items()} if total else {}
        say(f"[assumptor] {s}: ${a['value_mn']:,.0f}M via {key} ({a['how']}; {a['n_independent']} independent "
            f"publishers{'; ' + a['flags'][0] if a['flags'] else ''})")
    country_value = {g: sum(alloc[s].get(g, 0.0) for s in segments) for g in geos}
    mix = {g: {s: (alloc[s].get(g, 0.0) / country_value[g] if country_value[g] else 0.0) for s in segments} for g in geos}
    guard_flags: list[str] = []
    if generated:
        # Guardrail, not replacement: the evidence-based split stands unless it is more than
        # GUARD x away from what the real-world driver implies (v8: blending made it worse).
        gen_vals = {g: v for g, v in (generated.get("region_value") or {}).items() if g in country_value and v}
        if len(gen_vals) == len(geos):
            gt, dt = sum(gen_vals.values()), sum(country_value.values())
            guarded = {}
            for g in geos:
                gs, ds = gen_vals[g] / gt, country_value[g] / dt if dt else 0.0
                if ds and not ds / GUARD <= gs <= ds * GUARD:
                    gs2 = min(max(gs, ds / GUARD), ds * GUARD)
                    guard_flags.append(f"{g}: share {gs:.1%} vs real-world driver {ds:.1%} -> {gs2:.1%}")
                    gs = gs2
                guarded[g] = gs
            s = sum(guarded.values())
            country_value = {g: dt * v / s for g, v in guarded.items()}
        for g in geos:
            gen_mix = {s: (generated.get("shares") or {}).get(((dim, (s,)), g)) for s in segments}
            if not all(v is not None for v in gen_mix.values()):
                continue
            # the assumptor's GLOBAL segment values set each country's mix level; the
            # generated per-country pattern is kept, guarded by the driver-implied mix
            guarded = {}
            for s in segments:
                gs, ds = gen_mix[s] or 0.0, mix[g][s] or 0.0
                guarded[s] = min(max(gs, ds / GUARD), ds * GUARD) if ds else gs
            tot = sum(guarded.values()) or 1.0
            mix[g] = {s: v / tot for s, v in guarded.items()}
    # RAS / iterative proportional fitting: the country x segment table must add up both ways --
    # each country to its value AND each segment to the assumptor's world total -- while keeping
    # every country's relative pattern (seen: world Defense 14% but Israel 45% Defense before this).
    seg_target = {s: seg_out[s]["value_mn"] for s in segments}
    scale = sum(country_value.values()) / (sum(seg_target.values()) or 1.0)
    seg_target = {s: v * scale for s, v in seg_target.items()}
    m = {g: {s: country_value[g] * (mix[g].get(s) or 1e-9) for s in segments} for g in geos}
    for _ in range(200):
        for s in segments:
            col = sum(m[g][s] for g in geos)
            f = seg_target[s] / col if col else 1.0
            for g in geos:
                m[g][s] *= f
        for g in geos:
            row = sum(m[g].values())
            f = country_value[g] / row if row else 1.0
            for s in segments:
                m[g][s] *= f
    mix = {g: {s: (m[g][s] / country_value[g] if country_value[g] else 0.0) for s in segments} for g in geos}
    region_value = {r: sum(country_value[c] for c in cs if c in country_value) for r, cs in spec["regions"].items()}
    total = sum(region_value.values())
    return {"dimension": dim, "segments": seg_out, "country_value": country_value, "country_mix": mix,
            "region_value": region_value, "global_value": total, "guard_flags": guard_flags,
            "region_share": {r: v / total for r, v in region_value.items()},
            "country_share_in_region": {c: country_value[c] / region_value[r] for r, cs in spec["regions"].items()
                                        for c in cs if region_value[r]}}


def write(spec: dict[str, Any], data_file: Path, reg: dict[str, Any]) -> dict[str, int]:
    """Put the regulated values into the human-structured Data file."""
    wb = load_workbook(data_file)
    ws = wb["Data"]
    stats = {"region_rows": 0, "mix_cells": 0, "market_value_rows": 0}
    labels = [_norm(ws.cell(r, 2).value) for r in range(1, ws.max_row + 1)]
    start = next((i for i, lab in enumerate(labels, start=1) if lab == "Region"), None)
    if start:
        for r in range(start + 1, ws.max_row + 1):
            lab = _norm(ws.cell(r, 2).value)
            if lab == "Global":
                ws.cell(r, 5).value = reg["global_value"]
            elif lab in reg["region_share"]:
                ws.cell(r, 3).value, ws.cell(r, 5).value = reg["region_share"][lab], reg["region_value"][lab]
                stats["region_rows"] += 1
            elif lab in reg["country_share_in_region"]:
                ws.cell(r, 3).value = reg["country_share_in_region"][lab]
                ws.cell(r, 5).value = reg["country_value"][lab]
                stats["region_rows"] += 1
    paths = ev._path_walk(labels, spec)
    geo_col = {_norm(ws.cell(3, c).value): c for c in range(3, 30) if _norm(ws.cell(3, c).value)}
    for r, p in enumerate(paths, start=1):
        if p and p[0] == reg["dimension"] and len(p[1]) == 1:
            for g, c in geo_col.items():
                if g in reg["country_mix"]:
                    ws.cell(r, c).value = reg["country_mix"][g].get(p[1][0], 0.0)
                    stats["mix_cells"] += 1
    wsm = wb["Market Value"]
    for r in range(1, wsm.max_row + 1):
        name = _norm(wsm.cell(r, 2).value)
        if name == "Global":
            wsm.cell(r, 3).value = reg["global_value"]
        elif name in reg["region_value"]:
            wsm.cell(r, 3).value, wsm.cell(r, 4).value = reg["region_value"][name], reg["region_share"][name]
            stats["market_value_rows"] += 1
    wb.save(data_file)
    return stats


# ---- cross-dimension consistency ------------------------------------------
def consistency(llm: Any, spec: dict[str, Any], nesting: dict[str, Any], reg: dict[str, Any],
                generated: dict[str, Any]) -> dict[str, Any]:
    """Dimensions that slice the same market by end use must agree with the
    regulated partition mix (v8: End User put Defense at 63-67% while the
    regulated Application mix put it near 15%):
      * mapped dimension (e.g. End User): each top-level segment that has an
        Application counterpart takes that share; the rest split the remainder
        in their generated proportions
      * dependent dimensions (e.g. Mobility): the LLM re-estimates their GLOBAL
        mix given the regulated Application mix; every country's shares are
        rescaled by new/old global and renormalised (per-country pattern kept)."""
    dim = reg["dimension"]
    app = {s: a["value_mn"] for s, a in reg["segments"].items()}
    tot = sum(app.values()) or 1.0
    app_mix = {s: v / tot for s, v in app.items()}
    others = {d: list((nesting.get(d) or {}).get("__root__") or []) for d in nesting if d != dim}
    data = llm.complete_json(
        "You keep a market's segmentation internally consistent. Given the market's end-use mix (decided from "
        "real-world data), (1) map each top-level segment of other end-use-like dimensions to its counterpart in "
        "the end-use mix, where one exists, and (2) for every dimension whose mix depends materially on end use "
        "(e.g. mobile vs fixed systems depends on how much is defense), give the global top-level shares that are "
        "consistent with this end-use mix. Leave dimensions that do not depend on end use out. Return json.",
        f"Market: {spec['market']}\nEnd-use mix ({dim}): {json.dumps({k: round(v, 3) for k, v in app_mix.items()})}\n"
        f"Other dimensions: {json.dumps(others, ensure_ascii=False)}\n"
        'Return JSON: {"mapped": {"<dimension>": {"<its segment>": "<end-use segment or null>"}}, '
        '"dependent": {"<dimension>": {"shares": {"<segment>": <share>}, "reason": "<one sentence>"}}}',
    )
    geos = spec["geographies"]
    shares = generated.get("shares") or {}
    out: dict[str, dict[str, dict[str, float]]] = {}
    notes = []
    for d, mapping in (data.get("mapped") or {}).items():
        if d not in others or not isinstance(mapping, dict):
            continue
        segs = others[d]
        per_geo = {}
        for g in geos:
            gen = {s: shares.get(((d, (s,)), g)) or 0.0 for s in segs}
            mapped = {s: mapping.get(s) for s in segs if mapping.get(s) in app_mix}
            by_target: dict[str, list[str]] = {}
            for s, target in mapped.items():
                by_target.setdefault(target, []).append(s)
            new = {}
            for target, members in by_target.items():
                gsum = sum(gen[m] for m in members) or len(members)
                for m in members:
                    new[m] = reg["country_mix"][g].get(target, 0.0) * ((gen[m] or 1) / gsum)
            rest = [s for s in segs if s not in new]
            remaining = max(0.0, 1.0 - sum(new.values()))
            rsum = sum(gen[s] for s in rest) or len(rest) or 1
            for s in rest:
                new[s] = remaining * ((gen[s] or 1) / rsum)
            per_geo[g] = new
        out[d] = per_geo
        notes.append(f"{d}: {len([s for s in segs if mapping.get(s) in app_mix])} segments mapped to {dim}")
    for d, spec_d in (data.get("dependent") or {}).items():
        if d not in others or d in out or not isinstance(spec_d, dict):
            continue
        target = {s: float(v) for s, v in (spec_d.get("shares") or {}).items() if s in others[d]}
        if len(target) != len(others[d]) or sum(target.values()) <= 0:
            continue
        tsum = sum(target.values())
        target = {s: v / tsum for s, v in target.items()}
        weights = reg["country_value"]
        old = {s: sum((shares.get(((d, (s,)), g)) or 0.0) * weights.get(g, 0.0) for g in geos) for s in others[d]}
        osum = sum(old.values()) or 1.0
        old = {s: v / osum for s, v in old.items()}
        per_geo = {}
        for g in geos:
            raw = {s: (shares.get(((d, (s,)), g)) or old[s] or 1e-6) * (target[s] / old[s] if old[s] else 1.0)
                   for s in others[d]}
            rs = sum(raw.values()) or 1.0
            per_geo[g] = {s: v / rs for s, v in raw.items()}
        out[d] = per_geo
        notes.append(f"{d}: global mix re-estimated for the regulated end-use mix ({spec_d.get('reason', '')[:120]})")
    return {"shares": out, "notes": notes, "llm": data}


def write_consistency(spec: dict[str, Any], data_file: Path, cons: dict[str, Any]) -> int:
    wb = load_workbook(data_file)
    ws = wb["Data"]
    labels = [_norm(ws.cell(r, 2).value) for r in range(1, ws.max_row + 1)]
    paths = ev._path_walk(labels, spec)
    geo_col = {_norm(ws.cell(3, c).value): c for c in range(3, 30) if _norm(ws.cell(3, c).value)}
    n = 0
    for r, p in enumerate(paths, start=1):
        if p and len(p[1]) == 1 and p[0] in cons["shares"]:
            for g, c in geo_col.items():
                v = cons["shares"][p[0]].get(g, {}).get(p[1][0])
                if v is not None:
                    ws.cell(r, c).value = v
                    n += 1
    wb.save(data_file)
    return n

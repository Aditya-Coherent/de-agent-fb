"""Evaluator agent: where does a generated market stand against the human one?

Compares a generated Data file (Input Sheet) and ME workbook with the human
pair, using the structure spec (structure_agent.extract) so every segment is
keyed by its full path -- duplicate labels across dimensions never collide.

Why a number is off matters more than how much, so every value cell of the ME
is decomposed exactly in log space:

    ln(gen / ref) at (geo, segment, year)
        =  level      ln(global_2025_gen / global_2025_ref)      the market size
        +  geo mix    ln(geo share of global, gen / ref)          the regional split
        +  seg mix    ln(segment share of geo, gen / ref)         the segmentation
        +  growth     ln((cell_y / cell_2025) gen / ref)          the curve

and each component's typical size is reported (mean |log| -> "x" factor), so
the report says e.g. "the level is 6.3x low; splits and growth are within
1.2x". Also reported: Input Sheet shares / growth / regional values, ASP and
volume, coverage (cells the human filled that we left empty), structure
fidelity, and a graded verdict. `narrate()` lets the LLM turn the numbers into
a diagnosis and the next fixes -- it never changes a metric.
"""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from . import structure_agent as sa

BASE, END = 2025, 2033


def _norm(x: Any) -> str:
    return sa._norm(x)


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _x(log_err: float) -> float:
    return round(math.exp(abs(log_err)), 3)


def _stats(pairs: list[tuple[float, float]], kind: str = "rel") -> dict[str, Any]:
    """pairs = [(ref, gen)]. kind 'rel' (values: ratio errors) or 'pts' (shares: absolute points)."""
    pairs = [(r, g) for r, g in pairs if r is not None and g is not None]
    if not pairs:
        return {"n": 0}
    if kind == "pts":
        errs = [abs(g - r) for r, g in pairs]
        return {"n": len(pairs), "mae_pts": round(100 * statistics.mean(errs), 2),
                "median_pts": round(100 * statistics.median(errs), 2),
                "within_2pts": round(sum(e <= 0.02 for e in errs) / len(errs), 3),
                "within_5pts": round(sum(e <= 0.05 for e in errs) / len(errs), 3),
                "bias_pts": round(100 * statistics.mean(g - r for r, g in pairs), 2)}
    logs = [math.log(g / r) for r, g in pairs if r > 0 and g > 0]
    if not logs:
        return {"n": len(pairs), "note": "no positive pairs"}
    a = sorted(abs(x) for x in logs)
    return {"n": len(logs), "typical_x": _x(statistics.median(a)), "p90_x": _x(a[int(0.9 * (len(a) - 1))]),
            "within_10pct": round(sum(x <= math.log(1.1) for x in a) / len(a), 3),
            "within_25pct": round(sum(x <= math.log(1.25) for x in a) / len(a), 3),
            "bias_x": round(math.exp(statistics.mean(logs)), 3)}


# --------------------------------------------------------------------------- #
# readers
# --------------------------------------------------------------------------- #
def _path_walk(labels: list[str], spec: dict[str, Any]) -> list[tuple[str, tuple[str, ...]] | None]:
    """Map a sequence of row labels to (dimension, path) using the spec;
    None for rows that are not segments (titles, checks, blanks)."""
    out: list[tuple[str, tuple[str, ...]] | None] = []
    dim, parent, seen = None, None, set()
    for lab in labels:
        if lab in spec["dimensions"]:
            dim, parent, seen = lab, None, set()
            out.append(None)
            continue
        hit = None
        if dim:
            # order-independent: match by label within the dimension; a label that
            # sits under several parents prefers the parent most recently passed
            cands = [tuple(s["path"][1:]) for s in spec["dimensions"][dim] if s["label"] == lab]
            cands = [c for c in cands if c not in seen] or cands
            if cands:
                hit_path = next((c for c in cands if len(c) == 2 and c[0] == parent), cands[0])
                if len(hit_path) == 1:
                    parent = hit_path[0]
                seen.add(hit_path)
                hit = (dim, hit_path)
        out.append(hit)
    return out


def read_data_file(path: Path, spec: dict[str, Any]) -> dict[str, Any]:
    wb = load_workbook(path, data_only=True, read_only=True)
    ws = wb["Data"]
    grid = list(ws.iter_rows(values_only=True))
    geos = [_norm(v) for v in grid[2][2:] if v is not None]
    geos = geos[: geos.index(geos[0], 1)] if geos.count(geos[0]) > 1 else geos
    labels = [_norm(r[1]) if len(r) > 1 else "" for r in grid]
    paths = _path_walk(labels, spec)
    shares, growth = {}, {}
    # second block: same labels in column AD (index 29), countries from AE on
    gcols = {}
    hdr = grid[2]
    for ci in range(30, len(hdr)):
        if _norm(hdr[ci]) in geos and _norm(hdr[ci]) not in gcols:
            gcols[_norm(hdr[ci])] = ci
    for ri, p in enumerate(paths):
        if not p:
            continue
        row = grid[ri]
        for gi, g in enumerate(geos):
            v = _num(row[2 + gi]) if 2 + gi < len(row) else None
            if v is not None:
                shares[(p, g)] = v
            ci = gcols.get(g)
            if ci is not None and ci < len(row) and _num(row[ci]) is not None:
                growth[(p, g)] = _num(row[ci])
    # region / country share block
    region_share, region_cagr, region_value = {}, {}, {}
    start = next((i for i, lab in enumerate(labels) if lab == "Region"), None)
    if start is not None:  # columns: C share of parent, D CAGR, E 2025 value (US$ Mn)
        for r in grid[start + 1:]:
            name = _norm(r[1]) if len(r) > 1 else ""
            if not name:
                continue
            if len(r) > 2 and _num(r[2]) is not None:
                region_share[name] = _num(r[2])
            if len(r) > 3 and _num(r[3]) is not None:
                region_cagr[name] = _num(r[3])
            if len(r) > 4 and _num(r[4]) is not None:
                region_value[name] = _num(r[4])
    # Market Value sheet
    mv = {}
    if "Market Value" in wb.sheetnames:
        rows = list(wb["Market Value"].iter_rows(values_only=True))
        hdr_i = next((i for i, r in enumerate(rows) if any(_norm(v).lower() in ("regions", "region") for v in r)), None)
        if hdr_i is not None:
            h = [_norm(v).lower() for v in rows[hdr_i]]
            ci_name = next(i for i, v in enumerate(h) if v in ("regions", "region"))
            ci_val = next((i for i, v in enumerate(h) if v == "usd mn"), None)
            ci_cagr = next((i for i, v in enumerate(h) if v == "cagr"), None)
            for r in rows[hdr_i + 1:]:
                name = _norm(r[ci_name]) if ci_name < len(r) else ""
                if name:
                    mv[name] = {"value": _num(r[ci_val]) if ci_val is not None else None,
                                "cagr": _num(r[ci_cagr]) if ci_cagr is not None else None}
    # ASP sheet: first block, priced dimension rows x countries
    asp = {}
    if "ASP" in wb.sheetnames:
        rows = list(wb["ASP"].iter_rows(values_only=True))
        hdr_i = next((i for i, r in enumerate(rows) if len(r) > 2 and _norm(r[2]) == geos[0]), None)
        if hdr_i is not None:
            for r in rows[hdr_i + 1:]:
                lab = _norm(r[1]) if len(r) > 1 else ""
                if not lab:
                    break
                for gi, g in enumerate(geos):
                    v = _num(r[2 + gi]) if 2 + gi < len(r) else None
                    if v:
                        asp[(lab, g)] = v
    return {"geos": geos, "shares": shares, "growth": growth, "region_share": region_share, "region_cagr": region_cagr,
            "region_value": region_value, "market_value": mv, "asp": asp}


def read_me(path: Path, spec: dict[str, Any]) -> dict[str, Any]:
    """{sheet: {band: {(dim, path) or label: {year: value}}}} + band totals."""
    wb = load_workbook(path, data_only=True, read_only=True)
    years = spec["me_layout"]["years"]
    out: dict[str, Any] = {}
    for name in wb.sheetnames:
        grid = list(wb[name].iter_rows(values_only=True))
        bands: dict[str, dict[Any, dict[int, float]]] = {}
        band, labels_seq, rows_seq = None, [], []
        for r in grid:
            marker = _norm(r[3]) if len(r) > 3 else ""
            kind = next((k for k, p in sa._BAND_PREFIXES.items() if marker.lower().startswith(p)), None)
            if kind:
                band = kind
                bands[band] = {}
                continue
            lab = _norm(r[2]) if len(r) > 2 else ""
            if band and lab:
                vals = {y: _num(r[3 + i]) for i, y in enumerate(years) if 3 + i < len(r)}
                vals = {y: v for y, v in vals.items() if v is not None}
                labels_seq.append((band, lab, vals))
        for b in bands:
            seq = [(lab, vals) for bb, lab, vals in labels_seq if bb == b]
            paths = _path_walk([lab for lab, _ in seq], spec)
            for (lab, vals), p in zip(seq, paths):
                if lab in spec["dimensions"] and vals:
                    bands[b].setdefault(("__total__", lab), vals)
                key = p if p else lab
                if vals and key not in bands[b]:
                    bands[b][key] = vals
        out[name.strip()] = bands
    return out


# --------------------------------------------------------------------------- #
# evaluation
# --------------------------------------------------------------------------- #
def _total(me_sheet: dict[str, Any], dim: str) -> dict[int, float]:
    return (me_sheet.get("value") or {}).get(("__total__", dim)) or {}


def evaluate(spec: dict[str, Any], human_data: Path, human_me: Path | None, gen_data: Path,
             gen_me: Path | None) -> dict[str, Any]:
    """Full evaluation; with gen_me=None only the Data file (Input Sheet) is evaluated."""
    rep: dict[str, Any] = {"market": spec["market"], "structure": sa.conformance(spec, gen_data, gen_me)}
    hd, gd = read_data_file(human_data, spec), read_data_file(gen_data, spec)

    # ---- Input Sheet ----
    hg, gg = hd["market_value"].get("Global", {}), gd["market_value"].get("Global", {})
    regions = [r for r in hd["market_value"] if r != "Global"]
    inp: dict[str, Any] = {
        "global_value_2025": {"human": hg.get("value"), "generated": gg.get("value"),
                              "ratio": (gg["value"] / hg["value"]) if hg.get("value") and gg.get("value") else None},
        "global_cagr": {"human": hg.get("cagr"), "generated": gg.get("cagr")},
        "regional_value": _stats([(hd["market_value"][r]["value"], (gd["market_value"].get(r) or {}).get("value"))
                                  for r in regions]),
        "regional_share": _stats([(hd["region_share"].get(r) or hd["region_share"].get(r + " "),
                                   gd["region_share"].get(r)) for r in regions], "pts"),
        "regional_cagr": _stats([(hd["market_value"][r]["cagr"], (gd["market_value"].get(r) or {}).get("cagr"))
                                 for r in regions], "pts"),
        "country_share_in_region": _stats([(hd["region_share"][c], gd["region_share"].get(c))
                                          for c in spec["geographies"] if c in hd["region_share"]], "pts"),
        "country_value": _stats([(hd["region_value"][c], gd["region_value"].get(c))
                                 for c in spec["geographies"] if c in hd["region_value"]]),
        "country_cagr": _stats([(hd["region_cagr"][c], gd["region_cagr"].get(c))
                                for c in spec["geographies"] if c in hd["region_cagr"]], "pts"),
        "segment_shares": _stats([(v, gd["shares"].get(k)) for k, v in hd["shares"].items()], "pts"),
        "segment_growth": _stats([(v, gd["growth"].get(k)) for k, v in hd["growth"].items()], "pts"),
        "asp": _stats([(v, gd["asp"].get(k)) for k, v in hd["asp"].items()]),
        "by_dimension": {},
    }
    for dim in spec["dimensions"]:
        inp["by_dimension"][dim] = {
            "shares": _stats([(v, gd["shares"].get(k)) for k, v in hd["shares"].items() if k[0][0] == dim], "pts")}
    worst = sorted(((abs(gd["shares"][k] - v), k, v, gd["shares"][k]) for k, v in hd["shares"].items() if k in gd["shares"]),
                   key=lambda t: -t[0])[:10]
    inp["worst_shares"] = [{"dimension": k[0][0], "segment": " / ".join(k[0][1]), "geo": k[1], "human": round(h, 4),
                            "generated": round(g, 4)} for _, k, h, g in worst]
    rep["input_sheet"] = inp
    if gen_me is None or human_me is None:
        rep["verdict"] = verdict_input(rep)
        return rep

    # ---- ME ----
    hm, gm = read_me(human_me, spec), read_me(gen_me, spec)
    dim0 = next(iter(spec["dimensions"]))
    h_global, g_global = _total(hm.get("Global", {}), dim0), _total(gm.get("Global", {}), dim0)
    level = math.log(g_global[BASE] / h_global[BASE]) if h_global.get(BASE) and g_global.get(BASE) else None
    comps = {"level": [], "geo_mix": [], "seg_mix": [], "growth": []}
    value_pairs, end_pairs, cagr_pairs, coverage = [], [], [], {"human_cells": 0, "generated_missing": 0}
    geo_pairs, seg_pairs = [], []
    per_geo: dict[str, list[float]] = {}
    for sheet, hb in hm.items():
        gb = gm.get(sheet, {})
        hv, gv = hb.get("value", {}), gb.get("value", {})
        ht, gt = _total(hb, dim0), _total(gb, dim0)
        if not (ht.get(BASE) and gt.get(BASE) and h_global.get(BASE) and g_global.get(BASE)):
            continue
        geo_log = math.log((gt[BASE] / g_global[BASE]) / (ht[BASE] / h_global[BASE]))
        if sheet != "Global":
            geo_pairs.append((ht[BASE] / h_global[BASE], gt[BASE] / g_global[BASE]))
        for key, hvals in hv.items():
            if not isinstance(key, tuple) or key[0] == "__total__":
                continue
            gvals = gv.get(key) or {}
            for y, h in hvals.items():
                coverage["human_cells"] += 1
                g = gvals.get(y)
                if g is None:
                    coverage["generated_missing"] += 1
                    continue
                value_pairs.append((h, g))
                if h > 0 and g > 0 and hvals.get(BASE) and gvals.get(BASE):
                    total_log = math.log(g / h)
                    seg_log = math.log((gvals[BASE] / gt[BASE]) / (hvals[BASE] / ht[BASE]))
                    growth_log = total_log - level - geo_log - seg_log
                    comps["level"].append(abs(level))
                    comps["geo_mix"].append(abs(geo_log))
                    comps["seg_mix"].append(abs(seg_log))
                    comps["growth"].append(abs(growth_log))
                    per_geo.setdefault(sheet, []).append(total_log)
            if hvals.get(BASE) and gvals.get(BASE):
                seg_pairs.append((hvals[BASE] / ht[BASE], gvals[BASE] / gt[BASE]))
            if hvals.get(END) and gvals.get(END):
                end_pairs.append((hvals[END], gvals[END]))
            if all(v.get(y) for v in (hvals, gvals) for y in (BASE, END)):
                cagr_pairs.append(((hvals[END] / hvals[BASE]) ** (1 / (END - BASE)) - 1,
                                   (gvals[END] / gvals[BASE]) ** (1 / (END - BASE)) - 1))
    asp_pairs, vol_pairs = [], []
    for sheet, hb in hm.items():
        gb = gm.get(sheet, {})
        for band, sink in (("asp", asp_pairs), ("volume", vol_pairs)):
            for key, hvals in (hb.get(band) or {}).items():
                gvals = (gb.get(band) or {}).get(key) or {}
                if hvals.get(BASE) and gvals.get(BASE):
                    sink.append((hvals[BASE], gvals[BASE]))
    decomposition = {k: {"typical_x": _x(statistics.median(v)) if v else None,
                         "mean_abs_log": round(statistics.mean(v), 4) if v else None} for k, v in comps.items()}
    total_abs = sum(d["mean_abs_log"] or 0 for d in decomposition.values()) or 1.0
    for d in decomposition.values():
        d["share_of_error"] = round((d["mean_abs_log"] or 0) / total_abs, 3)
    rep["me"] = {
        "global_2025": {"human": h_global.get(BASE), "generated": g_global.get(BASE),
                        "ratio": (g_global[BASE] / h_global[BASE]) if level is not None else None},
        "global_2033": {"human": h_global.get(END), "generated": g_global.get(END)},
        "global_cagr_2025_2033": {"human": (h_global[END] / h_global[BASE]) ** (1 / 8) - 1 if h_global.get(END) else None,
                                  "generated": (g_global[END] / g_global[BASE]) ** (1 / 8) - 1 if g_global.get(END) else None},
        "value_cells": _stats(value_pairs),
        "value_cells_2033": _stats(end_pairs),
        "geo_share_of_global": _stats(geo_pairs, "pts"),
        "segment_share_of_geo": _stats(seg_pairs, "pts"),
        "segment_cagr_2025_2033": _stats(cagr_pairs, "pts"),
        "asp_2025": _stats(asp_pairs),
        "volume_2025": _stats(vol_pairs),
        "decomposition": decomposition,
        "coverage": {**coverage, "filled": round(1 - coverage["generated_missing"] / max(coverage["human_cells"], 1), 4)},
        "worst_geographies": sorted(({"geo": g, "typical_x": _x(statistics.median([abs(x) for x in v])),
                                      "bias_x": round(math.exp(statistics.mean(v)), 3)} for g, v in per_geo.items()),
                                    key=lambda d: -d["typical_x"])[:8],
    }
    rep["verdict"] = verdict(rep)
    return rep


def _grade(x: float | None, good: float, ok: float, lower_is_better: bool = True) -> str:
    if x is None:
        return "n/a"
    return "good" if x <= good else "fair" if x <= ok else "poor"


def verdict(rep: dict[str, Any]) -> dict[str, Any]:
    me, inp = rep["me"], rep["input_sheet"]
    dec = me["decomposition"]
    lvl = me["global_2025"]["ratio"]
    items = {
        "structure": "good" if (rep["structure"].get("score") or 0) >= 0.99 else "fair" if (rep["structure"].get("score") or 0) >= 0.9 else "poor",
        "market_level": _grade(_x(math.log(lvl)) if lvl else None, 1.15, 1.5),
        "geography_split": _grade(me["geo_share_of_global"].get("mae_pts"), 1.0, 3.0),
        "segmentation": _grade(inp["segment_shares"].get("mae_pts"), 3.0, 6.0),
        "growth": _grade(me["segment_cagr_2025_2033"].get("mae_pts"), 1.5, 4.0),
        "asp": _grade(me["asp_2025"].get("typical_x"), 1.25, 2.0),
    }
    main = max(dec, key=lambda k: dec[k]["mean_abs_log"] or 0)
    return {"grades": items, "main_error_source": main,
            "summary": (f"Structure {items['structure']}; market level {items['market_level']} "
                        f"(x{lvl:.2f} of human)" if lvl else f"Structure {items['structure']}")
                       + f"; {main.replace('_', ' ')} explains {dec[main]['share_of_error']:.0%} of the value error."}


def verdict_input(rep: dict[str, Any]) -> dict[str, Any]:
    inp = rep["input_sheet"]
    ratio = inp["global_value_2025"]["ratio"]
    grades = {
        "structure": "good" if (rep["structure"].get("score") or 0) >= 0.99 else "fair",
        "market_level": _grade(_x(math.log(ratio)) if ratio else None, 1.15, 1.5),
        "regional_split": _grade(inp["regional_share"].get("mae_pts"), 2.0, 4.0),
        "country_split": _grade(inp["country_share_in_region"].get("mae_pts"), 3.0, 6.0),
        "country_values": _grade(inp["country_value"].get("typical_x"), 1.25, 1.6),
        "segmentation": _grade(inp["segment_shares"].get("mae_pts"), 3.0, 6.0),
        "asp": _grade(inp["asp"].get("typical_x"), 1.25, 2.0),
    }
    return {"grades": grades, "summary": (f"Input Sheet: global ×{ratio:.2f} of human" if ratio else "Input Sheet")
            + f"; segment shares MAE {inp['segment_shares'].get('mae_pts')} pts; country values typical "
              f"×{inp['country_value'].get('typical_x')}"}


def write_input_markdown(rep: dict[str, Any], path: Path, regulation: dict[str, Any] | None = None,
                         narrative: str = "") -> Path:
    inp, v = rep["input_sheet"], rep["verdict"]

    def s(d: dict[str, Any]) -> str:
        if not d or not d.get("n"):
            return "—"
        if "mae_pts" in d:
            return f"MAE {d['mae_pts']} pts, within 5 pts {d['within_5pts']:.0%} (n={d['n']})"
        return f"typical ×{d['typical_x']}, within 25% {d['within_25pct']:.0%}, bias ×{d['bias_x']} (n={d['n']})"

    g = inp["global_value_2025"]
    L = [f"# Input Sheet evaluation — {rep['market']}", "", f"**Verdict:** {v['summary']}", "",
         "| Area | Grade |", "|---|---|"] + [f"| {k.replace('_', ' ')} | {val} |" for k, val in v["grades"].items()]
    L += ["", f"Global 2025: human ${g['human']:,.0f}M · generated ${g['generated']:,.0f}M · ×{g['ratio']:.2f}"
          if g.get("ratio") else "", "",
          f"- Regional value: {s(inp['regional_value'])}", f"- Regional share: {s(inp['regional_share'])}",
          f"- Regional CAGR: {s(inp['regional_cagr'])}",
          f"- Country share within region: {s(inp['country_share_in_region'])}",
          f"- Country value 2025: {s(inp['country_value'])}", f"- Country CAGR: {s(inp['country_cagr'])}",
          f"- Segment shares: {s(inp['segment_shares'])}", f"- ASP: {s(inp['asp'])}", "",
          "| Dimension | Shares |", "|---|---|"] + [f"| {d} | {s(x['shares'])} |" for d, x in inp["by_dimension"].items()]
    if regulation:
        L += ["", "## Assumptor regulation (published figures are artifacts; real-world drivers decide)", "",
              "| Segment | Value | Driver (global) | How | Independent publishers | Artifact range | Flags |",
              "|---|---|---|---|---|---|---|"]
        for seg, a in regulation["segments"].items():
            ind = sorted(x["usd_mn"] for x in a["artifacts"] if x["independent"])
            rng = f"${ind[0]:,.0f}–{ind[-1]:,.0f}M" if ind else "—"
            L.append(f"| {seg} | ${a['value_mn']:,.0f}M | {a['driver']} (${a['driver_total_usd_bn']:,.0f}bn) | "
                     f"{a['how']}: `{a['formula']}` | {a['n_independent']} | {rng} | {'; '.join(a['flags']) or '—'} |")
        L += ["", "Assumptor reasoning:", ""] + [f"- **{seg}**: {a['reasoning']} (driver: {a['driver_reason']})"
                                                 for seg, a in regulation["segments"].items()]
    if narrative:
        L += ["", "## Evaluator agent diagnosis", "", narrative]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(x for x in L if x is not None), encoding="utf-8")
    return path


def narrate(llm: Any, rep: dict[str, Any]) -> str:
    """LLM diagnosis of the metrics (never alters them)."""
    slim = {k: rep[k] for k in ("structure", "verdict")}
    slim["input_sheet"] = {k: v for k, v in rep["input_sheet"].items() if k != "by_dimension"}
    slim["me"] = rep.get("me")
    try:
        data = llm.complete_json(
            "You are a senior market-research reviewer. Explain where this generated market estimate stands versus "
            "the human analyst's, using ONLY the metrics given. Identify the root causes in order of impact and "
            "concrete fixes for the generating agents. Return json.",
            json.dumps(slim, default=str)[:24000]
            + '\n\nReturn JSON: {"standing": "<3-4 sentences>", "root_causes": ["..."], "fixes": ["..."]}',
        )
    except Exception as exc:  # noqa: BLE001
        return f"(narrative unavailable: {exc})"
    return ("**Standing.** " + str(data.get("standing") or "") + "\n\n**Root causes**\n"
            + "\n".join(f"{i + 1}. {x}" for i, x in enumerate(data.get("root_causes") or []))
            + "\n\n**Fixes**\n" + "\n".join(f"- {x}" for x in data.get("fixes") or []))


def write_markdown(rep: dict[str, Any], path: Path, narrative: str = "") -> Path:
    me, inp, v = rep["me"], rep["input_sheet"], rep["verdict"]

    def s(d: dict[str, Any]) -> str:
        if not d or not d.get("n"):
            return "—"
        if "mae_pts" in d:
            return f"MAE {d['mae_pts']} pts, within 5 pts {d['within_5pts']:.0%} (n={d['n']})"
        return f"typical ×{d['typical_x']}, within 25% {d['within_25pct']:.0%}, bias ×{d['bias_x']} (n={d['n']})"

    def usd(x: float | None) -> str:
        return f"${x:,.0f}M" if x else "—"

    g = me["global_2025"]
    L = [f"# Evaluation — {rep['market']}", "", f"**Verdict:** {v['summary']}", "",
         "| Area | Grade |", "|---|---|"] + [f"| {k.replace('_', ' ')} | {val} |" for k, val in v["grades"].items()] + [
        "", "## Headline", "", "| | Human | Generated | Ratio |", "|---|---|---|---|",
        f"| Global 2025 | {usd(g['human'])} | {usd(g['generated'])} | ×{g['ratio']:.2f} |" if g.get("ratio") else "| Global 2025 | — | — | — |",
        f"| Global 2033 | {usd(me['global_2033']['human'])} | {usd(me['global_2033']['generated'])} | |",
        f"| CAGR 2025–33 | {me['global_cagr_2025_2033']['human']:.1%} | {me['global_cagr_2025_2033']['generated']:.1%} | |"
        if me["global_cagr_2025_2033"]["generated"] is not None else "",
        "", "## Why the values differ (exact log decomposition of every ME value cell)", "",
        "| Component | Typical factor | Share of error |", "|---|---|---|"]
    for k, d in me["decomposition"].items():
        L.append(f"| {k.replace('_', ' ')} | ×{d['typical_x']} | {d['share_of_error']:.0%} |")
    L += ["", "## ME workbook", "",
          f"- Value cells, all years: {s(me['value_cells'])}",
          f"- Value cells, 2033: {s(me['value_cells_2033'])}",
          f"- Geography share of global: {s(me['geo_share_of_global'])}",
          f"- Segment share of geography (2025): {s(me['segment_share_of_geo'])}",
          f"- Segment CAGR 2025–33: {s(me['segment_cagr_2025_2033'])}",
          f"- ASP 2025: {s(me['asp_2025'])}",
          f"- Volume 2025: {s(me['volume_2025'])}",
          f"- Coverage: {me['coverage']['filled']:.1%} of the human's value cells filled",
          "", "## Data file (Input Sheet)", "",
          f"- Regional value: {s(inp['regional_value'])}",
          f"- Regional share: {s(inp['regional_share'])}",
          f"- Regional CAGR: {s(inp['regional_cagr'])}",
          f"- Country share within region: {s(inp['country_share_in_region'])}",
          f"- Country value 2025: {s(inp['country_value'])}",
          f"- Country CAGR: {s(inp['country_cagr'])}",
          f"- Segment shares: {s(inp['segment_shares'])}",
          f"- Segment growth block: {s(inp['segment_growth'])}",
          f"- ASP: {s(inp['asp'])}", "", "| Dimension | Shares |", "|---|---|"]
    L += [f"| {d} | {s(x['shares'])} |" for d, x in inp["by_dimension"].items()]
    L += ["", "Largest share gaps:", "", "| Dimension | Segment | Geo | Human | Generated |", "|---|---|---|---|---|"]
    L += [f"| {w['dimension']} | {w['segment']} | {w['geo']} | {w['human']:.1%} | {w['generated']:.1%} |"
          for w in inp["worst_shares"]]
    st = rep["structure"]
    L += ["", "## Structure fidelity", "", f"Score {st.get('score')}", "", "```", json.dumps(st, indent=1, default=str), "```"]
    if narrative:
        L += ["", "## Evaluator agent diagnosis", "", narrative]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(x for x in L if x is not None), encoding="utf-8")
    return path

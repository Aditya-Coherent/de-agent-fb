"""Score a generated market against a human ME workbook laid out as one sheet
per region (Global + regions; Value / Price / Volume blocks; countries as rows
inside each region sheet; values in US$ Thousand, volume in Thousand Units).

Units: the human ME's value is US$ Thousand; its volume rows hold whole UNITS
(despite a 'Th Units' title) and its Price block is US$ Thousand per unit
(checked: price = value / volume on every region sheet). Our ME: value US$ Mn,
volume thousand units. Everything is converted to US$ Mn, units, US$ per unit.

Used when the human Input Sheet is not available: everything an Input Sheet
holds at region level can be read back from the ME (shares = segment / parent,
growth = the year series). Per-country segment mixes are NOT in such an ME and
are not scored.

    python compare_input_vs_me.py --truth-me "<human ME>.xlsx" --base 2025 --end 2032 \
        --run uc1 "output/gearbox/Data File - <market> (uc1, human structure).xlsx" "agentGeneratedMEWorkbook/ME - <market> (uc1).xlsx" \
        --run uc2 ... --out output/gearbox/COMPARISON_VS_ME

This is a score, read once per run. It must not feed back into prompts or
constants (see the blind-evaluation rule).
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

_BLOCKS = {"value": "value", "price": "price", "volume": "volume"}


def _clean(x: Any) -> str:
    return re.sub(r"\s+", " ", str(x or "")).strip()


def _num(x: Any) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


# ---- the human ME ------------------------------------------------------------------
def read_truth(path: Path) -> dict[str, Any]:
    """{sheet: {block: {(group, label): {year: value}}}}, totals under ('__total__', block).
    Values in US$ Thousand / Thousand Units exactly as stored."""
    wb = load_workbook(path, read_only=True, data_only=True)
    out: dict[str, Any] = {}
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        yr_row = next((r for r in rows if sum(isinstance(v, int) and 2000 < v < 2100 for v in r) >= 5), None)
        if yr_row is None:
            continue
        ycols, prev = {}, 0
        for i, v in enumerate(yr_row):  # the first ascending run of years
            if isinstance(v, int) and 2000 < v < 2100:
                if v <= prev:
                    break
                ycols[v], prev = i, v
            elif ycols:
                break
        sheet: dict[str, dict] = {}
        block = None
        for r in rows:
            c, d = _clean(r[2] if len(r) > 2 else ""), _clean(r[3] if len(r) > 3 else "")
            if c.lower() in _BLOCKS and not d.startswith("By "):
                block = _BLOCKS[c.lower()]
                continue
            if not c or not d:
                continue
            series = {y: _num(r[i]) for y, i in ycols.items() if i < len(r) and _num(r[i]) is not None}
            if not series:
                continue
            if " value in " in f" {d.lower()} ":
                sheet.setdefault("value", {})[("__total__", "value")] = series
            elif " volume in " in f" {d.lower()} ":
                sheet.setdefault("volume", {})[("__total__", "volume")] = series
            elif block:
                sheet.setdefault(block, {}).setdefault((c, d), series)
        out[_clean(ws.title)] = sheet
    return out


# ---- our ME and Data file ------------------------------------------------------------
def read_our_me(path: Path) -> dict[str, Any]:
    """{sheet: {band: {label: {year: value}}}} -- bands value (US$ Mn), asp, volume (thousand units)."""
    wb = load_workbook(path, read_only=True, data_only=True)
    out: dict[str, Any] = {}
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        bands: dict[str, dict[str, dict[int, float]]] = {}
        band, years = None, []
        for i, r in enumerate(rows):
            marker = _clean(r[3] if len(r) > 3 else "").lower()
            kind = ("value" if marker.startswith("market value") else "asp" if marker.startswith("asp")
                    else "volume" if marker.startswith("market volume") else None)
            if kind:
                band = kind
                nxt = rows[i + 1] if i + 1 < len(rows) else ()
                years = []
                for v in nxt[3:]:  # the first ascending run only: a Y-o-Y block repeats the years further right
                    if not (isinstance(v, (int, float)) and 1990 < v < 2100) or (years and v <= years[-1]):
                        break
                    years.append(int(v))
                continue
            lab = _clean(r[2] if len(r) > 2 else "")
            if band and lab:
                s = {y: _num(r[3 + j]) for j, y in enumerate(years) if y and 3 + j < len(r) and _num(r[3 + j]) is not None}
                if s:
                    bands.setdefault(band, {}).setdefault(lab, s)
        out[_clean(ws.title)] = bands
    return out


def read_our_regions(data_file: Path) -> dict[str, float]:
    """Base-year value (US$ Mn) per geography from the Data file's region block."""
    ws = load_workbook(data_file, read_only=True, data_only=True)["Data"]
    rows = list(ws.iter_rows(min_col=2, max_col=5, values_only=True))
    start = next(i for i, r in enumerate(rows) if _clean(r[0]) == "Region")
    return {_clean(r[0]): _num(r[3]) for r in rows[start + 1:] if _clean(r[0]) and _num(r[3]) is not None}


# ---- metrics ---------------------------------------------------------------------------
def _ratio(ours: float | None, truth: float | None) -> float | None:
    return ours / truth if ours is not None and truth and ours > 0 else None


def _cagr(s: dict[int, float], a: int, b: int) -> float | None:
    return (s[b] / s[a]) ** (1 / (b - a)) - 1 if s.get(a) and s.get(b) and s[a] > 0 and s[b] > 0 else None


def _shares(vals: dict[str, float | None]) -> dict[str, float]:
    tot = sum(v for v in vals.values() if v)
    return {k: (v or 0.0) / tot for k, v in vals.items()} if tot else {}


def _match(label: str, options: list[str]) -> str | None:
    key = _clean(label).lower()
    for o in options:
        if _clean(o).lower() == key:
            return o
    return None


def score_run(truth: dict[str, Any], me: dict[str, Any], regions_ours: dict[str, float], base: int,
              end: int) -> dict[str, Any]:
    tg = truth["Global"]
    regions = [d for (c, d) in tg["value"] if c == "Global" and d != "Global"]
    res: dict[str, Any] = {}

    # global value and growth
    t_glob = tg["value"][("__total__", "value")]
    o_glob = me["Global"]["value"].get("By Application") or {}
    res["global"] = {"truth_usd_mn": t_glob[base] / 1e3, "ours_usd_mn": o_glob.get(base),
                     "ratio": _ratio(o_glob.get(base), t_glob[base] / 1e3),
                     "truth_cagr": _cagr(t_glob, base, end), "ours_cagr": _cagr(o_glob, base, end)}
    tvb = tg.get("volume") or {}
    # the sheet's own 'Global Volume' header row is often left empty: use the priced dimension's total row
    t_vol = next((s for (c, d), s in tvb.items() if c.startswith("By ") and c == d and s.get(base)),
                 tvb.get(("__total__", "volume")) or {})
    o_vol = me["Global"].get("volume", {}).get("By Application") or {}
    o_units = o_vol.get(base) * 1e3 if o_vol.get(base) is not None else None
    res["global"]["truth_volume_units"] = t_vol.get(base)
    res["global"]["ours_volume_units"] = o_units
    res["global"]["volume_ratio"] = _ratio(o_units, t_vol.get(base))

    # regions: value, share, growth
    t_reg = {r: tg["value"][("Global", r)] for r in regions}
    o_reg = {r: regions_ours.get(r) for r in regions}
    ts, os_ = _shares({r: s.get(base) for r, s in t_reg.items()}), _shares(o_reg)
    res["regions"] = {r: {"truth_usd_mn": t_reg[r][base] / 1e3, "ours_usd_mn": o_reg[r],
                          "ratio": _ratio(o_reg[r], t_reg[r][base] / 1e3), "truth_share": ts.get(r),
                          "ours_share": os_.get(r), "share_diff_pp": 100 * (os_.get(r, 0) - ts.get(r, 0)),
                          "truth_cagr": _cagr(t_reg[r], base, end),
                          "ours_cagr": _cagr((me.get(r, {}).get("value") or {}).get("By Application") or {}, base, end)}
                      for r in regions}

    # countries within each region
    res["countries"] = {}
    for r in regions:
        t_c = {d: s.get(base) for (c, d), s in (truth.get(r, {}).get("value") or {}).items() if c == r and d != r}
        o_c = {d: regions_ours.get(d) for d in t_c}
        tsh, osh = _shares(t_c), _shares(o_c)
        for d in t_c:
            res["countries"][d] = {"region": r, "truth_usd_mn": (t_c[d] or 0) / 1e3, "ours_usd_mn": o_c[d],
                                   "ratio": _ratio(o_c[d], (t_c[d] or 0) / 1e3), "truth_share_in_region": tsh.get(d),
                                   "ours_share_in_region": osh.get(d),
                                   "share_diff_pp": 100 * (osh.get(d, 0) - tsh.get(d, 0))}

    # segment splits (application, vertical), global and per region; price and volume per application
    res["segments"] = {}
    for sheet in ["Global"] + regions:
        tv = truth.get(sheet, {}).get("value") or {}
        ov = (me.get(sheet, {}).get("value") or {})
        for dim in sorted({c for (c, _) in tv if c.startswith("By ")}):
            labels = [d for (c, d) in tv if c == dim and d != dim]
            t_vals = {d: tv[(dim, d)].get(base) for d in labels}
            o_vals = {d: (ov.get(_match(d, list(ov)) or "") or {}).get(base) for d in labels}
            tops = _top_level(labels, truth, sheet, dim)
            tsh = _shares({d: t_vals[d] for d in tops})
            osh = _shares({d: o_vals[d] for d in tops})
            for d in labels:
                row = {"truth_usd_mn": (t_vals[d] or 0) / 1e3, "ours_usd_mn": o_vals[d],
                       "ratio": _ratio(o_vals[d], (t_vals[d] or 0) / 1e3)}
                if d in tops:
                    row.update(truth_share=tsh.get(d), ours_share=osh.get(d),
                               share_diff_pp=100 * (osh.get(d, 0) - tsh.get(d, 0)))
                if sheet == "Global":
                    row.update(truth_cagr=_cagr(tv[(dim, d)], base, end),
                               ours_cagr=_cagr(ov.get(_match(d, list(ov)) or "") or {}, base, end))
                    if dim == "By Application":
                        tvol = ((truth[sheet].get("volume") or {}).get((dim, d)) or {}).get(base)
                        ovol = ((me[sheet].get("volume") or {}).get(_match(d, list(me[sheet].get("volume") or {})) or "")
                                or {}).get(base)
                        ounits = ovol * 1e3 if ovol else None
                        row.update(truth_volume_units=tvol, ours_volume_units=ounits, volume_ratio=_ratio(ounits, tvol),
                                   truth_price_usd=(t_vals[d] * 1e3 / tvol) if t_vals[d] and tvol else None,
                                   ours_price_usd=(o_vals[d] * 1e6 / ounits) if o_vals[d] and ounits else None)
                        row["price_ratio"] = _ratio(row["ours_price_usd"], row["truth_price_usd"])
                res["segments"][f"{sheet} | {dim} | {d}"] = row
    res["summary"] = summarise(res)
    return res


def _top_level(labels: list[str], truth: dict[str, Any], sheet: str, dim: str) -> list[str]:
    """Top-level segments: children are the rows whose values add up to the row just above them."""
    tv = truth[sheet]["value"]
    vals = [tv[(dim, d)].get(next(iter(tv[(dim, d)]))) or 0 for d in labels]
    kids: set[int] = set()
    for i, v in enumerate(vals):
        run = 0.0
        for j in range(i + 1, len(vals)):
            run += vals[j]
            if j > i + 1 and v and abs(run - v) <= 1e-6 * max(1.0, abs(v)):
                kids.update(range(i + 1, j + 1))
                break
            if run > v * (1 + 1e-6):
                break
    return [d for k, d in enumerate(labels) if k not in kids]


def _logerr(r: float | None) -> float | None:
    return abs(math.log(r)) if r else None


def summarise(res: dict[str, Any]) -> dict[str, Any]:
    def med(xs: list[float | None]) -> float | None:
        xs = [x for x in xs if x is not None]
        return statistics.median(xs) if xs else None

    def within(rs: list[float | None], f: float) -> float | None:
        rs = [r for r in rs if r]
        return sum(1 / f <= r <= f for r in rs) / len(rs) if rs else None

    segs = res["segments"]
    glob_app = [v for k, v in segs.items() if k.startswith("Global | By Application")]
    return {
        "global_value_ratio": res["global"]["ratio"],
        "global_cagr_diff_pp": (100 * (res["global"]["ours_cagr"] - res["global"]["truth_cagr"])
                                if res["global"]["ours_cagr"] is not None and res["global"]["truth_cagr"] is not None
                                else None),
        "global_volume_ratio": res["global"]["volume_ratio"],
        "region_share_median_abs_pp": med([abs(v["share_diff_pp"]) for v in res["regions"].values()]),
        "region_value_within_1_5x": within([v["ratio"] for v in res["regions"].values()], 1.5),
        "country_share_in_region_median_abs_pp": med([abs(v["share_diff_pp"]) for v in res["countries"].values()]),
        "country_value_within_2x": within([v["ratio"] for v in res["countries"].values()], 2.0),
        "global_application_share_median_abs_pp": med([abs(v["share_diff_pp"]) for v in glob_app if "share_diff_pp" in v]),
        "global_vertical_share_median_abs_pp": med([abs(v["share_diff_pp"]) for k, v in segs.items()
                                                    if k.startswith("Global | By Industry") and "share_diff_pp" in v]),
        "region_segment_share_median_abs_pp": med([abs(v["share_diff_pp"]) for k, v in segs.items()
                                                   if not k.startswith("Global |") and "share_diff_pp" in v]),
        "application_price_median_log_err": med([_logerr(v.get("price_ratio")) for v in glob_app]),
        "application_volume_median_log_err": med([_logerr(v.get("volume_ratio")) for v in glob_app]),
    }


# ---- report ------------------------------------------------------------------------------
def _f(x: Any, kind: str = "") -> str:
    if x is None:
        return "–"
    if kind == "pct":
        return f"{100 * x:.1f}%"
    if kind == "pp":
        return f"{x:+.1f}"
    if kind == "x":
        return f"{x:.2f}×"
    if kind == "usd":
        return f"{x:,.1f}"
    return f"{x:,.3g}" if isinstance(x, float) else str(x)


def markdown(runs: dict[str, dict[str, Any]], base: int, end: int, truth_name: str) -> str:
    names = list(runs)
    L = [f"# Generated market vs human ME: {truth_name}", "",
         f"Base year {base}; growth = CAGR {base}–{end}. Values in US$ Mn (the ME's US$ Thousand ÷ 1,000). "
         "ratio = ours ÷ truth; pp = ours − truth in percentage points. Per-country segment mixes are not in this ME "
         "and are not scored.", "", "## Summary", "",
         "| Metric | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    keys = [("global_value_ratio", "Global value (ratio)", "x"), ("global_cagr_diff_pp", "Global CAGR (diff, pp)", "pp"),
            ("global_volume_ratio", "Global volume (ratio)", "x"),
            ("region_share_median_abs_pp", "Region share, median |diff| (pp)", ""),
            ("region_value_within_1_5x", "Regions within 1.5× of truth", "pct"),
            ("country_share_in_region_median_abs_pp", "Country share in region, median |diff| (pp)", ""),
            ("country_value_within_2x", "Countries within 2× of truth", "pct"),
            ("global_application_share_median_abs_pp", "Application share (global), median |diff| (pp)", ""),
            ("global_vertical_share_median_abs_pp", "Industry vertical share (global), median |diff| (pp)", ""),
            ("region_segment_share_median_abs_pp", "Segment shares within regions, median |diff| (pp)", ""),
            ("application_price_median_log_err", "Price per gearbox, median |log error|", ""),
            ("application_volume_median_log_err", "Volume by application, median |log error|", "")]
    for k, lab, kind in keys:
        L.append(f"| {lab} | " + " | ".join(_f(runs[n]["summary"].get(k), kind) for n in names) + " |")
    first = runs[names[0]]
    L += ["", "## Global", "", "| | Truth | " + " | ".join(names) + " |", "|---|---|" + "---|" * len(names),
          f"| Value {base} (US$ Mn) | {_f(first['global']['truth_usd_mn'], 'usd')} | "
          + " | ".join(_f(runs[n]['global']['ours_usd_mn'], 'usd') for n in names) + " |",
          f"| CAGR {base}–{end} | {_f(first['global']['truth_cagr'], 'pct')} | "
          + " | ".join(_f(runs[n]['global']['ours_cagr'], 'pct') for n in names) + " |",
          f"| Volume {base} (gearboxes) | {_f(first['global']['truth_volume_units'], 'usd')} | "
          + " | ".join(_f(runs[n]['global']['ours_volume_units'], 'usd') for n in names) + " |"]
    L += ["", "## Regions", "", "| Region | Truth US$ Mn | Truth share | " +
          " | ".join(f"{n} US$ Mn | {n} share | {n} ratio" for n in names) + " |",
          "|---|---|---|" + "---|---|---|" * len(names)]
    for r, v in first["regions"].items():
        L.append(f"| {r} | {_f(v['truth_usd_mn'], 'usd')} | {_f(v['truth_share'], 'pct')} | " + " | ".join(
            f"{_f(runs[n]['regions'][r]['ours_usd_mn'], 'usd')} | {_f(runs[n]['regions'][r]['ours_share'], 'pct')} | "
            f"{_f(runs[n]['regions'][r]['ratio'], 'x')}" for n in names) + " |")
    L += ["", "## Countries (share within their region)", "", "| Country | Region | Truth share | " +
          " | ".join(f"{n} share | {n} value ratio" for n in names) + " |", "|---|---|---|" + "---|---|" * len(names)]
    for c, v in first["countries"].items():
        L.append(f"| {c} | {v['region']} | {_f(v['truth_share_in_region'], 'pct')} | " + " | ".join(
            f"{_f(runs[n]['countries'][c]['ours_share_in_region'], 'pct')} | {_f(runs[n]['countries'][c]['ratio'], 'x')}"
            for n in names) + " |")
    L += ["", "## Segments (global)", "", "| Segment | Truth US$ Mn | Truth share | " +
          " | ".join(f"{n} US$ Mn | {n} share | {n} ratio" for n in names) + " |",
          "|---|---|---|" + "---|---|---|" * len(names)]
    for k, v in first["segments"].items():
        if not k.startswith("Global |"):
            continue
        L.append(f"| {k.split(' | ', 1)[1]} | {_f(v['truth_usd_mn'], 'usd')} | {_f(v.get('truth_share'), 'pct')} | "
                 + " | ".join(f"{_f(runs[n]['segments'][k]['ours_usd_mn'], 'usd')} | "
                              f"{_f(runs[n]['segments'][k].get('ours_share'), 'pct')} | "
                              f"{_f(runs[n]['segments'][k]['ratio'], 'x')}" for n in names) + " |")
    L += ["", "## Price and volume by application (global)", "",
          "| Application | Truth price US$/unit | Truth volume (units) | " +
          " | ".join(f"{n} price ratio | {n} volume ratio" for n in names) + " |", "|---|---|---|" + "---|---|" * len(names)]
    for k, v in first["segments"].items():
        if k.startswith("Global | By Application") and "truth_price_usd" in v:
            L.append(f"| {k.rsplit(' | ', 1)[1]} | {_f(v['truth_price_usd'], 'usd')} | {_f(v['truth_volume_units'], 'usd')} | "
                     + " | ".join(f"{_f(runs[n]['segments'][k].get('price_ratio'), 'x')} | "
                                  f"{_f(runs[n]['segments'][k].get('volume_ratio'), 'x')}" for n in names) + " |")
    return "\n".join(L) + "\n"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--truth-me", required=True)
    p.add_argument("--run", nargs=3, action="append", metavar=("NAME", "DATA_FILE", "ME"), required=True)
    p.add_argument("--base", type=int, default=2025)
    p.add_argument("--end", type=int, default=2032)
    p.add_argument("--out", required=True, help="output path without extension (.json and .md are written)")
    args = p.parse_args()
    truth = read_truth(Path(args.truth_me))
    runs = {name: score_run(truth, read_our_me(Path(me)), read_our_regions(Path(data)), args.base, args.end)
            for name, data, me in args.run}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(runs, indent=2, default=str), encoding="utf-8")
    out.with_suffix(".md").write_text(markdown(runs, args.base, args.end, Path(args.truth_me).stem), encoding="utf-8")
    print(f"wrote {out.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Structure agent: copies a human market's STRUCTURE (never its numbers) from
its Data file (Input Sheet) and ME workbook, so a generated market can be laid
out identically and compared cell for cell.

    extract(data_file, me_file) -> spec
        dimensions      every segment with its full path (dimension, parent,
                        label) -- duplicate labels across dimensions (e.g.
                        'Medical' under both Application and End User) stay
                        distinct
        geography       countries (Data columns), regions -> countries (the
                        Data region block), ME sheet order
        pricing         the priced dimension and its ASP products
        me_layout       band titles as written in the ME (e.g. 'Market Volume
                        (Units)'), the volume unit and its scale, year range
        checks          Data file vs ME consistency (segments, sheets, products)
    blank_template(me_file, out)   the human ME with every data number removed
                                   (labels, headers, years and styles kept) --
                                   an unfilled cell stays EMPTY instead of
                                   silently showing the human value
    conformance(spec, input_sheet, me_workbook)   how faithfully a generated
                                   market copied the structure (0-1 per part)

Deterministic; no LLM needed -- the structure is read, not guessed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from .skeleton import load_skeleton_from_human_sheet

_BAND_PREFIXES = {"value": "market value", "asp": "asp", "volume": "market volume"}


def _norm(label: Any) -> str:
    return re.sub(r"\s+", " ", str(label or "")).strip()


def volume_scale(title: str) -> float:
    """US$ Mn / (US$ per unit) -> the volume band's unit.
    'Units' -> 1e6; 'Th Units' / 'Th Liters' / "'000" -> 1e3; 'Mn Units' -> 1."""
    t = title.lower()
    if re.search(r"\b(mn|million|mio)\b", t):
        return 1.0
    if re.search(r"\b(th|thousand|'000|000s)\b", t):
        return 1e3
    return 1e6


def _data_dimensions(data_file: Path) -> list[dict[str, Any]]:
    """[{dimension, parent, label, row, level}] in sheet order, from the Data sheet."""
    ws = load_workbook(data_file, data_only=True, read_only=True)["Data"]
    rows = list(ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=2, max_col=2, values_only=True))
    out, dim = [], None
    for i, (b,) in enumerate(rows, start=1):
        label = _norm(b)
        if not label:
            continue
        if label == "Region":
            break
        if label.startswith("By "):
            dim = label
            continue
        if dim:
            out.append({"dimension": dim, "label": label, "row": i})
    return out


def _region_block(data_file: Path) -> list[tuple[str, float | None]]:
    ws = load_workbook(data_file, data_only=True, read_only=True)["Data"]
    rows = list(ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=2, max_col=3, values_only=True))
    start = next((i for i, (b, _) in enumerate(rows) if _norm(b) == "Region"), None)
    if start is None:
        return []
    return [(_norm(b), c if isinstance(c, (int, float)) else None) for b, c in rows[start + 1:] if _norm(b)]


def _me_layout(me_file: Path) -> dict[str, Any]:
    wb = load_workbook(me_file, data_only=True, read_only=True)
    sheets = wb.sheetnames
    ws = wb[sheets[0]]
    grid = list(ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=1, max_col=20, values_only=True))
    bands, years, labels = {}, [], {}
    current = None
    for i, row in enumerate(grid, start=1):
        marker = _norm(row[3]) if len(row) > 3 else ""
        kind = next((k for k, p in _BAND_PREFIXES.items() if marker.lower().startswith(p)), None)
        if kind and kind not in bands:
            bands[kind] = {"title": marker, "row": i}
            current = kind
            nxt = grid[i] if i < len(grid) else ()
            if not years:  # first ascending run only (the Y-o-Y block repeats years further right)
                for v in nxt[3:]:
                    if not (isinstance(v, (int, float)) and 1990 < v < 2100) or (years and v <= years[-1]):
                        break
                    years.append(int(v))
            continue
        label = _norm(row[2]) if len(row) > 2 else ""
        if current and label:
            labels.setdefault(current, []).append(label)
    vol = bands.get("volume", {}).get("title", "")
    return {"sheets": sheets, "bands": bands, "years": years, "labels": labels,
            "volume_unit": (re.search(r"\((.*?)\)", vol).group(1) if "(" in vol else ""),
            "volume_scale": volume_scale(vol) if vol else None}


def extract(data_file: Path | str, me_file: Path | str | None, market_name: str) -> dict[str, Any]:
    data_file = Path(data_file)
    sk = load_skeleton_from_human_sheet(data_file, market_name)
    flat = _data_dimensions(data_file)
    # parent: a label that heads a nested group in the skeleton
    spec_dims: dict[str, list[dict[str, Any]]] = {}
    for dim, tree in sk.nesting.items():
        seq = []
        for top in tree.get("__root__", []):
            seq.append({"path": [dim, top], "label": top, "level": 1, "has_children": top in tree})
            for child in tree.get(top, []):
                seq.append({"path": [dim, top, child], "label": child, "level": 2, "has_children": False})
        spec_dims[dim] = seq
    region_block = _region_block(data_file)
    regions = {r: list(cs) for r, cs in sk.regions.items()}
    labels_by_dim: dict[str, list[str]] = {}
    for f in flat:
        labels_by_dim.setdefault(f["label"], []).append(f["dimension"])
    duplicates = {lab: dims for lab, dims in labels_by_dim.items() if len(dims) > 1}
    ws_data = load_workbook(data_file, read_only=True)["Data"]
    whitespace = sorted({v for (v,) in ws_data.iter_rows(min_col=2, max_col=2, values_only=True)
                         if isinstance(v, str) and v != v.strip()})
    spec: dict[str, Any] = {
        "market": market_name, "source_data_file": str(data_file),
        "geographies": list(sk.geographies), "regions": regions,
        "region_order": [r for r, _ in region_block if r in regions or r == "Global" or r.strip() in regions],
        "dimensions": spec_dims, "priced_dimension": sk.priced_dimension,
        "asp_products": [s["label"] for s in spec_dims.get(sk.priced_dimension, []) if not s["has_children"]],
        "duplicate_labels": duplicates, "labels_with_stray_whitespace": whitespace,
    }
    checks: list[dict[str, Any]] = []
    if me_file:
        me = _me_layout(Path(me_file))
        spec["me_layout"] = {k: v for k, v in me.items() if k != "labels"}
        value_labels = set(me["labels"].get("value", []))
        missing = [s["label"] for d in spec_dims.values() for s in d if s["label"] not in value_labels]
        checks.append({"check": "data_segments_in_me_value_band", "ok": not missing, "missing": missing})
        expected_sheets = {"Global", *regions, *sk.geographies}
        me_sheets = {_norm(s) for s in me["sheets"]}
        checks.append({"check": "me_sheets_cover_geography", "ok": expected_sheets <= me_sheets,
                       "missing": sorted(expected_sheets - me_sheets)})
        vol_labels = [lab for lab in me["labels"].get("volume", []) if not lab.startswith("By ")]
        checks.append({"check": "me_volume_rows_are_asp_products",
                       "ok": set(vol_labels) <= set(spec["asp_products"]) | {s["label"] for s in spec_dims.get(sk.priced_dimension, [])},
                       "volume_rows": len(vol_labels)})
        checks.append({"check": "volume_unit_detected", "ok": bool(me["volume_unit"]),
                       "unit": me["volume_unit"], "scale": me["volume_scale"]})
    spec["checks"] = checks
    return spec


def blank_template(me_file: Path | str, out: Path | str) -> dict[str, int]:
    """Copy the ME and clear every number in labelled data rows (column D
    onward). Year header rows (no label in column C) are kept."""
    wb = load_workbook(me_file)
    cleared = 0
    for ws in wb.worksheets:
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row):
            if len(row) < 4 or not _norm(row[2].value):
                continue
            for c in row[3:]:
                if isinstance(c.value, (int, float)) and not isinstance(c.value, bool):
                    c.value = None
                    cleared += 1
                elif isinstance(c.value, bool) or (isinstance(c.value, str) and c.value.startswith(("=", "#"))):
                    c.value = None
                    cleared += 1
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return {"sheets": len(wb.worksheets), "cells_cleared": cleared}


def conformance(spec: dict[str, Any], input_sheet: Path | str | None = None,
                me_workbook: Path | str | None = None) -> dict[str, Any]:
    """Structure fidelity of a generated market vs the spec (1.0 = identical)."""
    out: dict[str, Any] = {}
    want = [s["label"] for d in spec["dimensions"].values() for s in d]
    if input_sheet:
        got = [f["label"] for f in _data_dimensions(Path(input_sheet))]
        ws = load_workbook(input_sheet, data_only=True, read_only=True)["Data"]
        geos = [_norm(v) for v in next(ws.iter_rows(min_row=3, max_row=3, min_col=3, values_only=True)) if v]
        out["input_sheet"] = {
            "segments_present": sum(1 for x in want if x in got) / len(want),
            "segment_order_identical": got == want,
            "extra_segments": [x for x in got if x not in want],
            "geographies_present": sum(1 for g in spec["geographies"] if g in geos) / len(spec["geographies"]),
            "geography_order_identical": geos[:len(spec["geographies"])] == spec["geographies"],
        }
    if me_workbook and spec.get("me_layout"):
        me = _me_layout(Path(me_workbook))
        ref = spec["me_layout"]
        value_labels = me["labels"].get("value", [])
        out["me"] = {
            "sheets_present": sum(1 for s in ref["sheets"] if s in me["sheets"]) / len(ref["sheets"]),
            "sheet_order_identical": me["sheets"] == ref["sheets"],
            "band_titles_identical": {k: me["bands"].get(k, {}).get("title") == v["title"] for k, v in ref["bands"].items()},
            "segments_in_value_band": sum(1 for x in want if x in value_labels) / len(want),
            "years_identical": me["years"] == ref["years"],
        }
    parts = [v for part in out.values() for k, v in part.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    out["score"] = round(sum(parts) / len(parts), 4) if parts else None
    return out


def save(spec: dict[str, Any], path: Path | str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
    return p


# ---- a Data-file template from an ME's labels ------------------------------------
_ME_BLOCKS = ("value", "price", "volume")
_SUM_REF = re.compile(r"^=\s*(?:SUM\()?\s*([A-Z]+)(\d+)(?:\s*[+:,]\s*[A-Z]+\d+)*\)?\s*$")
# a residual row: "= <row p> - SUM(<siblings>)"; when p is a segment, this row and its siblings are p's children
_RESIDUAL = re.compile(r"^=\s*\$?[A-Z]+\$?(\d+)\s*-\s*SUM\(([^)]*)\)\s*$", re.I)


def _clean(label: Any) -> str:
    return re.sub(r"\s+", " ", str(label or "")).strip()


def me_structure(me_file: Path | str) -> dict[str, Any]:
    """Labels only from an ME laid out as one sheet per region with Value / Price /
    Volume blocks (column C = dimension or region, column D = label): the
    dimensions with their parent -> children nesting (a parent row's formula sums
    the rows right below it, or a child is written as "parent - SUM(its siblings)"),
    the countries of each region, and the priced
    dimension (the one with a Price block). No number is read."""
    wb = load_workbook(me_file, read_only=True)
    order: list[str] = []
    seg: dict[str, list[str]] = {}
    children: dict[str, dict[str, list[str]]] = {}
    regions: dict[str, list[str]] = {}
    region_order: list[str] = []
    priced = None
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        block, dim_rows = None, []
        for i, r in enumerate(rows, start=1):
            c, d = (_clean(r[2]) if len(r) > 2 else ""), (_clean(r[3]) if len(r) > 3 else "")
            if c.lower() in _ME_BLOCKS and not d.startswith("By "):
                block = c.lower()
                continue
            if not c or not d:
                continue
            if c.startswith("By "):
                if block == "price" and priced is None:
                    priced = c
                if block != "value" or d == c:
                    continue
                if c not in seg:
                    order.append(c)
                    seg[c] = []
                if d not in seg[c]:
                    seg[c].append(d)
                dim_rows.append((i, c, d, r[4] if len(r) > 4 else None))
            elif block == "value" and c == _clean(ws.title) and d != c and " in " not in d:
                if ws.title == "Global":
                    if d not in region_order:
                        region_order.append(d)
                    continue
                regions.setdefault(c, [])
                if d not in regions[c]:
                    regions[c].append(d)
        by_row = {i: (dim, lab) for i, dim, lab, _ in dim_rows}
        for i, dim, lab, f in dim_rows:
            res = _RESIDUAL.match(f) if isinstance(f, str) else None
            if res:  # child = parent - SUM(siblings): parent is a segment row above, not the dimension total
                p = int(res.group(1))
                refs = [int(x) for x in re.findall(r"[A-Z]+\$?(\d+)", res.group(2))]
                rng = range(min(refs), max(refs) + 1) if refs and ":" in res.group(2) else refs
                if p in by_row and by_row[p][0] == dim and p < i:
                    kids = [lab] + [by_row[x][1] for x in rng if x in by_row and by_row[x][0] == dim]
                    have = children.setdefault(dim, {}).setdefault(by_row[p][1], [])
                    have.extend(k for k in kids if k not in have)
                continue
            m = _SUM_REF.match(f) if isinstance(f, str) else None
            if not m:  # parent: its own-sheet formula adds up the rows right below it
                continue
            refs = [int(x) for x in re.findall(r"[A-Z]+(\d+)", f)]
            rng = range(min(refs), max(refs) + 1) if ":" in f else refs
            kids = [by_row[x][1] for x in rng if x in by_row and by_row[x][0] == dim]
            if kids and min(refs) == i + 1:
                children.setdefault(dim, {})[lab] = kids
    rank = {r: i for i, r in enumerate(region_order)}
    regions = dict(sorted(regions.items(), key=lambda kv: rank.get(kv[0], len(rank))))
    nesting = {}
    for dim in order:
        kids = children.get(dim, {})
        kids = {p: sorted(ks, key=seg[dim].index) for p, ks in kids.items()}  # sheet order
        all_kids = {k for ks in kids.values() for k in ks}
        nesting[dim] = {"__root__": [s for s in seg[dim] if s not in all_kids], **kids}
    return {"dimensions": nesting, "order": order, "regions": regions,
            "geographies": [g for cs in regions.values() for g in cs], "priced_dimension": priced or order[0]}


def data_template_from_me(me_file: Path | str, market: str, out: Path | str) -> dict[str, Any]:
    """A blank Data file (Data / ASP / Market Value sheets, the layout the pipeline
    reads) built from an ME's labels. Its only numbers are layout markers --
    1/n shares so the schema reader can see which rows are children, and 1s in
    the region block -- which the pipeline clears before writing its own values."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment

    st = me_structure(me_file)
    geos, nest = st["geographies"], st["dimensions"]
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.cell(1, 2, "Market")
    ws.cell(1, 3, market)
    gcol = {g: 3 + i for i, g in enumerate(geos)}
    for g, c in gcol.items():
        ws.cell(3, c, g)
        ws.cell(3, c + 28, g)  # the growth (CAGR) block starts at AE
    r = 4
    for dim in st["order"]:
        ws.cell(r, 2, dim)
        ws.cell(r, 30, dim)
        r += 1
        tops = nest[dim]["__root__"]
        for top in tops:
            for lab, share in [(top, 1 / len(tops))] + [(k, 1 / len(nest[dim][top])) for k in nest[dim].get(top, [])]:
                ws.cell(r, 2, lab)
                if lab != top:  # children are indented, the analysts' own marking the schema reader trusts
                    ws.cell(r, 2).alignment = Alignment(indent=1)
                ws.cell(r, 30, lab)
                for c in gcol.values():
                    ws.cell(r, c, share)
                r += 1
        r += 1
    r += 2
    ws.cell(r, 2, "Region")
    ws.cell(r, 3, "Market Share")
    ws.cell(r, 4, "CAGR")
    ws.cell(r, 5, "Value")
    ws.cell(r + 1, 2, "Global")
    ws.cell(r + 1, 3, 1)
    r += 2
    for reg, cs in st["regions"].items():
        for lab in [reg] + cs:
            ws.cell(r, 2, lab)
            ws.cell(r, 3, 1)
            ws.cell(r, 4, 1)
            r += 1
    asp = wb.create_sheet("ASP")
    pdim = st["priced_dimension"]
    leaves = [s for s in nest[pdim]["__root__"] if s not in nest[pdim]] + \
             [k for p, ks in nest[pdim].items() if p != "__root__" for k in ks]
    seq = [x for top in nest[pdim]["__root__"] for x in [top] + nest[pdim].get(top, [])]
    hr = 3
    for block in range(4):  # ASP, shares, value, volume -- each under a header row naming the countries
        if block == 0:
            asp.cell(hr, 2, "Segment")
        for g, i in zip(geos, range(len(geos))):
            asp.cell(hr, 3 + i, g)
        asp.cell(hr + 1, 2, pdim)
        for j, lab in enumerate(seq, start=hr + 2):
            asp.cell(j, 2, lab)
            if block == 0 and lab in leaves:
                for i in range(len(geos)):
                    asp.cell(j, 3 + i, 1)
        hr = hr + 2 + len(seq) + 3
    mv = wb.create_sheet("Market Value")
    mv.cell(2, 3, "Value")
    mv.cell(2, 5, "Volume")
    for c, h in enumerate(("Regions", "USD Mn", "Share", "Th Unit", "Share", "CAGR"), start=2):
        mv.cell(3, c, h)
    for i, reg in enumerate(list(st["regions"]) + ["Global"], start=4):
        mv.cell(i, 2, reg)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return st

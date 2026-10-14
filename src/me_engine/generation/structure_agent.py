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

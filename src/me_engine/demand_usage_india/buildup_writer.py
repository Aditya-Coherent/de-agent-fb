"""Write the India-only build-up artifact -- thin, additive wrapper over
`me_engine.demandUseCaseCountry_approach2B.buildup_writer`.

`build_report_dict` (the shared function) is called UNCHANGED to get the
existing flat `build_up_rows` (now one row per Actual Use Case, per country
-- always just India, since `countries=["India"]` collapses the country
dimension to a single value). This module only ADDS:
    - a `major_use_case` column on each row (looked up from the bucket's own
      tag, never re-derived),
    - a new top-level `major_use_case_totals` + `major_use_case_actual_use_case_counts`
      + `india_market_total` section (from `aggregation.rollup_major_use_cases`).

JSON: the existing shape, plus the additive keys above -- never removes or
renames an existing key.
XLSX: reuses the existing workbook UNCHANGED (via the shared
`write_buildup_workbook`), then appends two new sheets ("Major Use Case
Rollup", "India Market Total") -- additive sheets only, the existing
"Build-Up" sheet's own column schema is never touched.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill

from me_engine.demandUseCaseCountry_approach2B.buildup_writer import build_report_dict as _build_report_dict_shared
from me_engine.demandUseCaseCountry_approach2B.buildup_writer import write_buildup_workbook as _write_buildup_workbook_shared


def build_report_dict(state: Mapping[str, Any]) -> dict[str, Any]:
    report = _build_report_dict_shared(state)
    buckets = state.get("usage_buckets", []) or []
    major_by_name = {b["name"]: b.get("major_use_case", "") for b in buckets}

    for row in report.get("build_up_rows", []):
        row["major_use_case"] = major_by_name.get(row.get("usage_bucket"), "")

    report["major_use_case_totals"] = state.get("major_use_case_totals", {})
    report["major_use_case_actual_use_case_counts"] = state.get("major_use_case_actual_use_case_counts", {})
    report["india_market_total_usd_mn"] = state.get("india_market_total", state.get("total_market_value"))
    report["rollup_flags"] = state.get("rollup_flags", [])
    report["methodology"] = "demand_side_usage_india"
    return report


def write_buildup_json(state: Mapping[str, Any], out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(build_report_dict(state), indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path


def write_buildup_workbook(state: Mapping[str, Any], out_path: Path) -> Path:
    # The shared writer builds the ENTIRE existing workbook (Build-Up,
    # Summary, Validation, etc.) unchanged -- reused as-is, then reopened
    # here to append the two India-only sheets. Its own PermissionError
    # fallback (a sibling "_new" filename when the target is open in Excel)
    # is inherited automatically, since we always append to whatever path
    # it actually wrote to.
    written_path = _write_buildup_workbook_shared(state, out_path)
    report = build_report_dict(state)
    bold = Font(bold=True)

    wb = load_workbook(written_path)

    rollup_ws = wb.create_sheet("Major Use Case Rollup")
    rollup_ws.cell(1, 1, "Major Use Case").font = bold
    rollup_ws.cell(1, 2, "Actual Use Case Count").font = bold
    rollup_ws.cell(1, 3, "Total (US$ Mn)").font = bold
    counts = report.get("major_use_case_actual_use_case_counts", {}) or {}
    r = 2
    for major, total in (report.get("major_use_case_totals") or {}).items():
        rollup_ws.cell(r, 1, major)
        rollup_ws.cell(r, 2, counts.get(major, 0))
        rollup_ws.cell(r, 3, total)
        r += 1
    if r == 2:
        rollup_ws.cell(2, 1, "(no Major Use Case totals recorded)")
    if report.get("rollup_flags"):
        r += 1
        flag_cell = rollup_ws.cell(r, 1, "; ".join(f.get("message", "") for f in report["rollup_flags"]))
        flag_cell.fill = PatternFill("solid", fgColor="FFC7CE")

    total_ws = wb.create_sheet("India Market Total")
    total_ws.cell(1, 1, "India Market Total (US$ Mn)").font = bold
    total_ws.cell(1, 2, report.get("india_market_total_usd_mn"))

    wb.save(written_path)
    return written_path

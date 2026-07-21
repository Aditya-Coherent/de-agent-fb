"""Baseline comparison: synthetic Input Sheet vs human Input Sheet.

Reports the metrics that are robust when values are small or skewed (per rajeev.txt):
MAE, RMSE, sMAPE, R², and bias (mean signed error). Comparison is done on the
overlapping (dimension, segment, geography) share cells, plus regional value/CAGR.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

def _read_data_cells(path: Path) -> tuple[dict[tuple[str, str], float], dict[str, str]]:
    """Return {(segment, geo): share} across all dimensions, plus a segment->dimension
    map. Keying on (segment, geo) makes the comparison robust to dimension-title
    differences between the synthetic and human sheets."""
    wb = load_workbook(path, data_only=True, read_only=True)
    ws = wb["Data"]
    geos: list[str] = []
    for col in range(3, ws.max_column + 1):
        v = ws.cell(3, col).value
        if v is None:
            break
        geos.append(str(v).strip())
    cells: dict[tuple[str, str], float] = {}
    seg_dim: dict[str, str] = {}
    current_dim = "?"
    for r in range(4, ws.max_row + 1):
        b = ws.cell(r, 2).value
        if b is None:
            continue
        label = str(b).strip()
        if label.startswith("By ") or label.startswith("Cross") or label == "Region":
            current_dim = label
            continue
        for ci, geo in enumerate(geos, start=3):
            v = ws.cell(r, ci).value
            if isinstance(v, (int, float)) and 0.0 <= v <= 1.0:
                cells.setdefault((label, geo), float(v))
                seg_dim.setdefault(label, current_dim)
    wb.close()
    return cells, seg_dim


def _read_regional(path: Path) -> dict[str, dict[str, float]]:
    wb = load_workbook(path, data_only=True, read_only=True)
    if "Market Value" not in wb.sheetnames:
        wb.close()
        return {}
    ws = wb["Market Value"]
    out: dict[str, dict[str, float]] = {}
    for r in range(4, ws.max_row + 1):
        name = ws.cell(r, 2).value
        if not name:
            continue
        share = ws.cell(r, 4).value
        cagr = ws.cell(r, 5).value
        out[str(name).strip()] = {
            "share": float(share) if isinstance(share, (int, float)) else None,
            "cagr": float(cagr) if isinstance(cagr, (int, float)) else None,
        }
    wb.close()
    return out


def _metrics(pairs: list[tuple[float, float]]) -> dict[str, Any]:
    """pairs = [(human_actual, synthetic_pred), ...]"""
    n = len(pairs)
    if n == 0:
        return {"n": 0}
    err = [p - a for a, p in pairs]
    abs_err = [abs(e) for e in err]
    mae = sum(abs_err) / n
    rmse = math.sqrt(sum(e * e for e in err) / n)
    smape = (
        100.0 / n * sum(
            2 * abs(p - a) / (abs(a) + abs(p)) if (abs(a) + abs(p)) > 0 else 0.0
            for a, p in pairs
        )
    )
    bias = sum(err) / n
    mean_a = sum(a for a, _ in pairs) / n
    ss_tot = sum((a - mean_a) ** 2 for a, _ in pairs)
    ss_res = sum(e * e for e in err)
    r2 = 1 - ss_res / ss_tot if ss_tot > 1e-12 else float("nan")
    within_2pts = sum(1 for e in abs_err if e <= 0.02) / n * 100
    within_5pts = sum(1 for e in abs_err if e <= 0.05) / n * 100
    return {
        "n": n, "MAE": mae, "RMSE": rmse, "sMAPE_pct": smape, "bias": bias,
        "R2": r2, "within_2pts_pct": within_2pts, "within_5pts_pct": within_5pts,
    }


def compare(human: Path, synthetic: Path) -> dict[str, Any]:
    h_cells, h_dim = _read_data_cells(human)
    s_cells, _ = _read_data_cells(synthetic)
    common = sorted(set(h_cells) & set(s_cells))

    overall = [(h_cells[k], s_cells[k]) for k in common]
    per_dim: dict[str, list[tuple[float, float]]] = {}
    for k in common:  # k = (segment, geo); group by the human sheet's dimension
        dim = h_dim.get(k[0], "?")
        per_dim.setdefault(dim, []).append((h_cells[k], s_cells[k]))

    # regional value share + cagr
    h_reg, s_reg = _read_regional(human), _read_regional(synthetic)
    reg_share_pairs, reg_cagr_pairs = [], []
    for name, hv in h_reg.items():
        sv = s_reg.get(name)
        if not sv:
            continue
        if hv["share"] is not None and sv["share"] is not None:
            reg_share_pairs.append((hv["share"], sv["share"]))
        if hv["cagr"] is not None and sv["cagr"] is not None:
            reg_cagr_pairs.append((hv["cagr"], sv["cagr"]))

    return {
        "human": str(human),
        "synthetic": str(synthetic),
        "cells_compared": len(common),
        "human_cells_total": len(h_cells),
        "synthetic_cells_total": len(s_cells),
        "overall_shares": _metrics(overall),
        "per_dimension_shares": {d: _metrics(v) for d, v in per_dim.items()},
        "regional_value_share": _metrics(reg_share_pairs),
        "regional_cagr": _metrics(reg_cagr_pairs),
    }


def format_report(rep: dict[str, Any]) -> str:
    def fmt(m: dict[str, Any]) -> str:
        if not m or m.get("n", 0) == 0:
            return "  (no overlapping cells)"
        return (
            f"    n={m['n']:>4}  MAE={m['MAE']:.4f}  RMSE={m['RMSE']:.4f}  "
            f"sMAPE={m['sMAPE_pct']:.1f}%  bias={m['bias']:+.4f}  R2={m['R2']:.3f}  "
            f"within2pts={m['within_2pts_pct']:.0f}%  within5pts={m['within_5pts_pct']:.0f}%"
        )

    lines = [
        "=" * 78,
        "SYNTHETIC vs HUMAN — baseline comparison",
        "=" * 78,
        f"human     : {rep['human']}",
        f"synthetic : {rep['synthetic']}",
        f"overlapping share cells: {rep['cells_compared']} "
        f"(human {rep['human_cells_total']}, synthetic {rep['synthetic_cells_total']})",
        "",
        "OVERALL segmentation shares:",
        fmt(rep["overall_shares"]),
        "",
        "PER-DIMENSION segmentation shares:",
    ]
    for dim, m in rep["per_dimension_shares"].items():
        lines.append(f"  {dim}:")
        lines.append(fmt(m))
    lines += [
        "",
        "REGIONAL value share:",
        fmt(rep["regional_value_share"]),
        "REGIONAL CAGR:",
        fmt(rep["regional_cagr"]),
        "=" * 78,
    ]
    return "\n".join(lines)

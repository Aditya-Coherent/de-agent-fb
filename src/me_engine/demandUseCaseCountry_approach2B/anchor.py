"""Keep 2B's use cases inside each country's total annual demand.

2B builds every country bottom-up, use case by use case. Those use cases
split ONE country total, so together they cannot be much larger than it.
2A sizes exactly that total per country (one line per country, official data
first), so when a 2A run exists for the same market it is the anchor:

  - use cases sum to more than 1.5x the 2A country quantity -> every use
    case in that country is scaled down by the same factor so they add up to
    the 2A quantity (their split between use cases is kept), and flagged;
  - use cases sum to less than 1/1.5 of it -> scaled UP to the 2A quantity
    when that quantity is official statistics (FAOSTAT etc.), the way the
    Olive Oil 2B page reconciles household/foodservice/industry to FAO;
    otherwise flagged only (likely a missing use case).

Only anchors that are NOT assumptions are used. Deterministic, no LLM call.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import evidence, units

OVER_RATIO = 1.5
UNDER_RATIO = 1 / 1.5
# Beyond this the two figures can't describe the same thing (e.g. a 2A
# figure read as "14" instead of 14 million): flag, never scale.
IMPLAUSIBLE_RATIO = 20.0


def load_2a_country_anchor(market_name: str, root: Path | None = None) -> dict[str, dict[str, Any]] | None:
    """{country: {quantity, unit, provenance, source}} from the market's 2A
    sizing output, or None when there is no 2A run."""
    from .checkpoints import slugify

    base = Path("output") / "demand_country_2a"
    path = (Path(root) / base if root else base) / slugify(market_name) / "02_sizing.json"
    if not path.exists():
        return None
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    anchor: dict[str, dict[str, Any]] = {}
    for row in report.get("build_up_rows") or []:
        country, qty = row.get("country"), row.get("quantity")
        if not country or not isinstance(qty, (int, float)) or qty <= 0:
            continue
        anchor[country] = {
            "quantity": float(qty),
            "unit": row.get("final_demand_unit") or row.get("driver_unit") or "",
            "provenance": row.get("provenance") or "assumption",
            "source": row.get("driver_source") or "",
            "official": "faostat" in str(row.get("driver_source") or "").lower()
                        or row.get("library_status") == "official_data",
            "file": str(path),
        }
    return anchor or None


def _to_unit(value: float, from_unit: str, to_unit: str) -> float | None:
    if not from_unit or not to_unit or from_unit.strip().lower() == to_unit.strip().lower():
        return value
    scale = units.unit_scale(from_unit, to_unit)
    if scale is not None:
        return value * scale
    # Count units with different wording ("smartphones" vs "smartphones (units shipped)").
    if units._unit_head(from_unit) & units._unit_head(to_unit):
        return value
    return None


def apply_country_anchor(
    quantities: dict[str, dict[str, dict[str, Any]]],
    buckets: list[dict[str, Any]],
    anchor: dict[str, dict[str, Any]] | None,
    log: list[str],
) -> list[dict[str, str]]:
    """Scale/flag in place; returns quality flags."""
    if not anchor:
        return []
    flags: list[dict[str, str]] = []
    core = [b["name"] for b in buckets if b.get("bucket_category", "physical_consumption") == "physical_consumption"]
    countries = {c for name in core for c in (quantities.get(name) or {})}
    for c in sorted(countries):
        ref = anchor.get(c)
        if not ref or ref["provenance"] == "assumption":
            continue
        converted: dict[str, float] = {}
        for name in core:
            cell = (quantities.get(name) or {}).get(c)
            if not cell:
                continue
            v = _to_unit(float(cell.get("value") or 0.0), str(cell.get("unit") or ""), ref["unit"])
            if v is None:
                converted = {}
                break
            converted[name] = v
        total = sum(converted.values())
        if not converted or total <= 0:
            continue
        ratio = total / ref["quantity"]
        if ratio > IMPLAUSIBLE_RATIO or ratio < 1 / IMPLAUSIBLE_RATIO:
            msg = (f"{c}: use cases are {ratio:,.1f}x the 2A country total ({ref['quantity']:,.0f} {ref['unit']}) "
                   "-- too far apart to be the same quantity (likely a unit error in one of them); not scaled")
            log.append(f"[country_anchor] {msg}")
            flags.append({"code": "country_total_mismatch", "severity": "warning", "message": msg})
        elif ratio > OVER_RATIO or (ratio < UNDER_RATIO and ref.get("official")):
            factor = ref["quantity"] / total
            for name in converted:
                cell = quantities[name][c]
                cell["value"] = round(float(cell["value"]) * factor, 6)
                cell["provenance"] = evidence.worse_of(cell.get("provenance") or "assumption", "derived")
                cell["note"] = (
                    f"{cell.get('note', '')} [scaled x{factor:.3f}: the use cases added up to {ratio:.1f}x "
                    f"this country's total annual demand from 2A ({ref['quantity']:,.0f} {ref['unit']}"
                    f"{', ' + ref['source'] if ref.get('source') else ''})]"
                ).strip()
                cell["anchor_scale"] = factor
            direction = "down" if ratio > 1 else "up"
            msg = (f"{c}: use cases added up to {ratio:.1f}x the 2A country total "
                   f"({ref['quantity']:,.0f} {ref['unit']}); scaled {direction} to match it")
            log.append(f"[country_anchor] {msg}")
            flags.append({"code": f"use_cases_scaled_{direction}_to_country_total", "severity": "warning", "message": msg})
        elif ratio < UNDER_RATIO:
            msg = (f"{c}: use cases cover only {ratio:.0%} of the 2A country total "
                   f"({ref['quantity']:,.0f} {ref['unit']}) -- a use case may be missing")
            log.append(f"[country_anchor] {msg}")
            flags.append({"code": "use_cases_below_country_total", "severity": "info", "message": msg})
    return flags

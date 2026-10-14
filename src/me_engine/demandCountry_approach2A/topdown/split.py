"""Deterministic top-down split and country -> region -> global rollup.

No LLM here. Every number in the final segmentation tables is produced by
these functions from (a) the bottom-up country totals and (b) approved
per-country segment shares.
"""
from __future__ import annotations

from typing import Any

from .regions import country_region_map, region_for, REGION_ORDER

_TOLERANCE = 1e-3  # USD Mn


def normalize_shares(raw: dict[str, Any], segments: list[str]) -> tuple[dict[str, float], bool]:
    """Clamp to >= 0, fill missing segments with 0 and rescale to sum 1.

    Accepts fractions or percentages. Returns (shares, was_equal_split): when
    nothing usable was given, the split falls back to equal shares and the
    flag is True so callers can surface it.
    """
    vals: dict[str, float] = {}
    for seg in segments:
        try:
            v = float(raw.get(seg, 0.0) or 0.0)
        except (TypeError, ValueError):
            v = 0.0
        vals[seg] = max(v, 0.0)
    total = sum(vals.values())
    if total <= 0:
        n = len(segments) or 1
        return {seg: round(1.0 / n, 6) for seg in segments}, True
    return {seg: round(v / total, 6) for seg, v in vals.items()}, False


def split_top_down(
    country_totals: dict[str, float],
    shares_by_country: dict[str, dict[str, float]],
    segments: list[str],
) -> dict[str, dict[str, float]]:
    """country value x segment share -> {country: {segment: value}}.

    The last segment absorbs rounding so each country's segments sum exactly
    to its total.
    """
    out: dict[str, dict[str, float]] = {}
    for country, total in country_totals.items():
        total = float(total or 0.0)
        shares = shares_by_country.get(country) or {}
        row: dict[str, float] = {}
        running = 0.0
        for i, seg in enumerate(segments):
            if i == len(segments) - 1:
                row[seg] = round(total - running, 4)
            else:
                row[seg] = round(total * float(shares.get(seg, 0.0)), 4)
                running += row[seg]
        out[country] = row
    return out


def rollup_segments(
    country_values: dict[str, dict[str, float]], segments: list[str],
) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    """{country: {segment: v}} -> ({region: {segment: v}}, {segment: global v})."""
    regions: dict[str, dict[str, float]] = {}
    for country, row in country_values.items():
        reg = regions.setdefault(region_for(country), {seg: 0.0 for seg in segments})
        for seg in segments:
            reg[seg] += float(row.get(seg, 0.0) or 0.0)
    ordered = {r: {s: round(v, 4) for s, v in regions[r].items()} for r in REGION_ORDER if r in regions}
    global_values = {
        seg: round(sum(row.get(seg, 0.0) for row in ordered.values()), 4) for seg in segments
    }
    return ordered, global_values


def build_dimension_table(
    dimension: str,
    method: str,
    segments: list[str],
    country_values: dict[str, dict[str, float]],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    region_values, global_values = rollup_segments(country_values, segments)
    table = {
        "dimension": dimension,
        "method": method,
        "segments": list(segments),
        "country_values": country_values,
        "region_values": region_values,
        "global_values": global_values,
        "global_total": round(sum(global_values.values()), 4),
    }
    if extra:
        table.update(extra)
    return table


def consistency_checks(
    country_totals: dict[str, float], tables: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Every dimension must add back to the same country totals and global total."""
    checks: list[dict[str, Any]] = []
    global_total = round(sum(float(v or 0.0) for v in country_totals.values()), 4)
    for t in tables:
        worst = 0.0
        for country, total in country_totals.items():
            seg_sum = sum((t["country_values"].get(country) or {}).values())
            worst = max(worst, abs(seg_sum - float(total or 0.0)))
        gap = abs(t["global_total"] - global_total)
        checks.append({
            "dimension": t["dimension"],
            "max_country_gap_usd_mn": round(worst, 6),
            "global_gap_usd_mn": round(gap, 6),
            "passed": worst <= _TOLERANCE and gap <= _TOLERANCE * max(1, len(country_totals)),
        })
    return checks


def geography_summary(country_totals: dict[str, float]) -> dict[str, Any]:
    from .regions import rollup_regions

    countries = {c: round(float(v or 0.0), 4) for c, v in country_totals.items()}
    return {
        "countries": countries,
        "country_region": country_region_map(list(countries)),
        "regions": rollup_regions(countries),
        "global": round(sum(countries.values()), 4),
    }

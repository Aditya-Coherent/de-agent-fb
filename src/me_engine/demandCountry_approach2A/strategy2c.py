"""Strategy 2C deterministic core (`src/newinfo/system.md` §7.4): product
type x application x country, with the cell budget and the coverage rule.

The LLM steps (type lister, volume-mix extraction) feed this module; every
number here is computed in code and written to the ledger.

    cell quantity  = product of its recipe factors (e.g. ketchup production x
                     vinegar share of recipe x share made with white vinegar)
    cell budget    = cells whose expected share of the top-down anchor is below
                     `min_cell_share` are NOT researched: they take the
                     estimator's value and its label (`estimated_from_facts`)
    coverage rule  = if the built cells explain less than `min_explained_share`
                     of the anchor, a soft flag; the residual is ALWAYS shown as
                     an explicit "unattributed" line, never spread over cells
    type mix       = per (country, application) product-type shares must sum
                     to 1.0 +/- tolerance (hard check)
    roll-up        = type -> application -> country -> global, arithmetic only
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .checks import Flag, flag
from .ledger import Ledger, Number


@dataclass
class Factor:
    name: str
    value: float
    unit: str
    label: str
    note: str = ""
    source: dict[str, Any] | None = None
    year: int | None = None


@dataclass
class Cell:
    application: str
    country: str
    factors: list[Factor]
    product_type: str = ""
    expected_share: float | None = None          # prior share of the anchor, for the cell budget
    estimate: Factor | None = None               # estimator value used when the cell is not researched
    price: Factor | None = None                  # price per unit of the cell quantity
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return " / ".join(x for x in (self.application, self.product_type) if x)


def plan_budget(cells: list[Cell], min_cell_share: float) -> tuple[list[Cell], list[Cell]]:
    """(cells to research, cells to fill from the estimator)."""
    research, skip = [], []
    for c in cells:
        (skip if c.expected_share is not None and c.expected_share < min_cell_share and c.estimate else research).append(c)
    return research, skip


def mix_checks(mix: dict[tuple[str, str], dict[str, float]], tolerance: float = 0.001) -> list[Flag]:
    """mix: {(country, application): {product type: share}}."""
    out = []
    for (country, app), shares in mix.items():
        s = math.fsum(shares.values())
        if abs(s - 1.0) > tolerance:
            out.append(flag("hard", "mix_shares_sum", f"{app}, {country}: type shares sum to {s:.4f}", country))
    return out


def _chain(led: Ledger, cell: Cell, unit: str) -> Number:
    nums = [led.fact(f"{f.name}, {cell.country}", f.value, f.unit, cell.country,
                     "SHR" if f.unit in ("share", "t/t", "dimensionless") else "QTY", f.label, year=f.year,
                     source=f.source, note=f.note or ("" if f.label != "assumption" else "recipe-based assumption"),
                     module="strategy2c") for f in cell.factors]
    acc = nums[0]
    for i, n in enumerate(nums[1:], start=1):
        last = i == len(nums) - 1
        acc = led.mul(f"{cell.key} quantity, {cell.country}" if last else f"{cell.key} partial {i}, {cell.country}",
                      acc, n, unit, cell.country, kind="QTY")
    return acc


def build(led: Ledger, anchor_total: Number, cells: list[Cell], cfg: dict[str, Any], unit: str = "t",
          currency: str = "INR", mix: dict[tuple[str, str], dict[str, float]] | None = None) -> dict[str, Any]:
    c = cfg["checks"]
    research, skipped = plan_budget(cells, c["min_cell_share_2c"])
    flags: list[Flag] = mix_checks(mix or {}, c["share_tolerance"])
    qty: dict[str, Number] = {}
    values: dict[str, Number] = {}
    for cell in research:
        qty[cell.key] = _chain(led, cell, unit)
    for cell in skipped:
        e = cell.estimate
        qty[cell.key] = led.fact(f"{cell.key} quantity, {cell.country}", e.value, unit, cell.country, "QTY",
                                 "estimated_from_facts", module="estimator",
                                 note=e.note or f"below the cell budget ({cell.expected_share:.2%} < "
                                                f"{c['min_cell_share_2c']:.1%} of the anchor): not researched")
    for cell in cells:
        if cell.price is not None:
            p = cell.price
            pn = led.fact(f"{p.name}, {cell.country}", p.value, p.unit, cell.country, "PRC", p.label, year=p.year,
                          source=p.source, note=p.note or ("" if p.label != "assumption" else "price assumption"),
                          module="strategy2c")
            values[cell.key] = led.mul(f"{cell.key} value, {cell.country}", qty[cell.key], pn, currency,
                                       cell.country, kind="VAL")

    built = led.total("Explained quantity (built cells)", list(qty.values()), unit, anchor_total.geo, kind="QTY")
    explained_share = built.value / anchor_total.value if anchor_total.value else 0.0
    share_n = led._derived("Explained share of anchor", explained_share, "share", anchor_total.geo, "SHR",
                           f"{built.number_id} / {anchor_total.number_id}", [built, anchor_total], module="strategy2c")
    residual_value = anchor_total.value - built.value
    residual = led._derived("Unattributed (no published data)", residual_value, unit, anchor_total.geo, "QTY",
                            f"{anchor_total.number_id} − {built.number_id}", [anchor_total, built], module="strategy2c",
                            note="shown as its own line, never spread across known use cases")
    if residual_value < 0:
        flags.append(flag("hard", "negative_value", f"built cells ({built.value:,.0f} {unit}) exceed the anchor "
                          f"({anchor_total.value:,.0f} {unit})", anchor_total.geo, [residual.number_id]))
    if explained_share < c["min_explained_share_2c"]:
        flags.append(flag("soft", "recipe_coverage", f"recipe use cases explain {explained_share:.1%} of the anchor "
                          f"(< {c['min_explained_share_2c']:.0%}); {residual_value:,.0f} {unit} unattributed",
                          anchor_total.geo, [share_n.number_id, residual.number_id]))
    return {"quantities": qty, "values": values, "explained": built, "explained_share": share_n,
            "unattributed": residual, "flags": flags, "researched": [x.key for x in research],
            "estimated": [x.key for x in skipped]}

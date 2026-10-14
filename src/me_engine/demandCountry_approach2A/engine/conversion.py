"""Deterministic driver -> demand-quantity conversion. No LLM call anywhere
in this module -- the LLM's job (in `agents.py`) is only to decide which
formula shape applies and to supply an evidence-tagged intensity/adoption
value; the actual multiplication always happens here, in code.

    Observed/derived driver value  x  consumption intensity  [x adoption %]
    = demand quantity

e.g. vehicle fleet x liters-per-vehicle-per-year = lubricant demand (liters).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConversionResult:
    quantity: float
    formula: str


def apply_conversion(
    driver_value: float,
    intensity_value: float,
    adoption_pct: float | None = None,
) -> ConversionResult:
    """Pure arithmetic. `adoption_pct` (0-1) is optional -- only used when the
    usage bucket requires a penetration/adoption factor on top of raw driver
    x intensity (e.g. "vehicles" x "liters/vehicle" x "% using this oil
    grade")."""
    driver_value = max(0.0, float(driver_value))
    intensity_value = max(0.0, float(intensity_value))
    if adoption_pct is not None:
        adoption_pct = min(1.0, max(0.0, float(adoption_pct)))
        return ConversionResult(
            quantity=driver_value * intensity_value * adoption_pct,
            formula="driver_value * intensity_value * adoption_pct",
        )
    return ConversionResult(
        quantity=driver_value * intensity_value,
        formula="driver_value * intensity_value",
    )

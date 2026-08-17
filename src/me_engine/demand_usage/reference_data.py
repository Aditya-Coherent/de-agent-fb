"""Small static reference tables used ONLY as a deterministic, auditable
allocation basis when a country has no country-specific evidence and no
regional/global evidence exists to derive it from either.

This table is itself an approximation (rough 2024-order population figures,
millions), not a live-fetched figure -- it exists so that "derive a country
share from a bigger figure" is a fixed, inspectable formula instead of an
arbitrary LLM-invented split (see spec: "Never silently distribute a global
total using arbitrary country shares"). Any value allocated using this table
is always tagged `derived` (if a real regional/global anchor exists) or
`assumption` (if even that doesn't exist) -- never `observed`.
"""
from __future__ import annotations

# Approximate population, millions. Deliberately coarse -- good enough as an
# *allocation ratio* between countries, not presented as a precise census figure.
POPULATION_MN: dict[str, float] = {
    "United States": 335.0, "China": 1410.0, "India": 1429.0, "Indonesia": 278.0,
    "Brazil": 216.0, "Russia": 144.0, "Japan": 124.0, "Germany": 84.0,
    "United Kingdom": 68.0, "France": 66.0, "Italy": 59.0, "Spain": 48.0,
    "Mexico": 129.0, "Canada": 39.0, "Australia": 26.0, "South Korea": 52.0,
    "Turkey": 86.0, "Vietnam": 99.0, "Rest of World": 3500.0,
    "Rest of Europe": 250.0, "Rest of Asia Pacific": 900.0,
}
_DEFAULT_POP_MN = 30.0  # fallback for a country/bucket not in the table above


def population_share(country: str, among: list[str]) -> float:
    """Deterministic allocation ratio for `country` within `among` (population-share
    basis). Returns 0.0 if `among` is empty or all-zero."""
    weights = {c: POPULATION_MN.get(c, _DEFAULT_POP_MN) for c in among}
    total = sum(weights.values())
    if total <= 0:
        return 0.0
    return weights.get(country, _DEFAULT_POP_MN) / total

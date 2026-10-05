"""Shared top-down segmentation layer for the Demand-Side approaches.

Used by BOTH demand-side packages that need it, never by Desk Research or
Supply-Side:

- `demandCountry_approach2A`: country total demand (bottom-up) -> global, then
  EVERY segmentation dimension is split top-down from the country totals.
- `demandUseCaseCountry_approach2B`: application x country (bottom-up) ->
  country -> global; the application dimension stays bottom-up and every
  OTHER segmentation dimension is split top-down here.

Division of labour (same rule as the rest of the demand side): the LLM
proposes segment lists and share estimates from web snippets; code does all
of the arithmetic (`split.py`) and the country -> region -> global rollup
(`regions.py`).

Modules:
    regions.py   country -> region mapping (CMI's five standard regions)
    segments.py  propose dimensions, estimate per-country shares, approval gate
    split.py     deterministic split + rollup + consistency checks
    writer.py    final market report (JSON + XLSX)
"""

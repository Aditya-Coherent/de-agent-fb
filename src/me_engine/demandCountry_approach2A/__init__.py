"""Demand-Side Approach 2A: country-level total demand -> global, then
top-down for every segment.

    Market -> Country -> total demand (quantity x price) -> Country value
        -> Region -> Global                         (bottom-up, deterministic)
    Every segmentation dimension (Product Type, Application, Channel, ...)
        -> Country value x approved segment share   (top-down, deterministic)

What differs from 2B (`demandUseCaseCountry_approach2B`): 2A does NOT break the
country demand into applications first. Each country gets ONE demand line --
the whole market's consumption -- sized with the same evidence-first sizing
graph 2B uses (reused by import, never copied). All segments, including
Application, are then split top-down (`me_engine.demand_topdown`).

Phases (each ends at a human approval gate, like 2B):
    1 setup     -> 00_setup_draft.json    -> approve -> 00_setup_approved.json
    2 driver    -> 01_driver_draft.json   -> approve -> 01_driver_approved.json
    3 size      -> 02_sizing.json / .xlsx (country totals + region rollup)
    4 segments  -> 03_segments_draft.json -> approve -> 03_segments_approved.json
    5 report    -> 04_market.json / 04_market.xlsx
Run folder: output/demand_country_2a/<slug>/
"""

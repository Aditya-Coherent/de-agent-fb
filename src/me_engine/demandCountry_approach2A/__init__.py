"""Demand-Side Approach 2A: country-level total demand -> global, then
top-down for every segment.

    Market -> Country -> total demand (quantity x price) -> Country value
        -> Region -> Global                         (bottom-up, deterministic)
    Every segmentation dimension (Product Type, Application, Channel, ...)
        -> Country value x approved segment share   (top-down, deterministic)

What differs from 2B (`demandUseCaseCountry_approach2B`): 2A does NOT break the
country demand into applications first. Each country gets ONE demand line --
the whole market's consumption -- sized with the evidence-first sizing graph
forked from 2B into `engine/`. All segments, including Application, are then
split top-down (`topdown/`, forked from `me_engine.demand_topdown`).

Self-contained: 2A owns its copies of the 2B sizing engine and the top-down
layer, so 2A can change without touching 2B (or the India variant, which
reuses 2B). Only generic infrastructure is shared: `generation` (GenLLM,
GenConfig, web search, embeddings), `audit`, `io`, `domain`.

    engine/   fork of `demandUseCaseCountry_approach2B` (minus taxonomy,
              segmentation, run_report, which 2A doesn't use)
    topdown/  fork of `demand_topdown`

Phases (each ends at a human approval gate, like 2B):
    1 setup     -> 00_setup_draft.json    -> approve -> 00_setup_approved.json
    2 driver    -> 01_driver_draft.json   -> approve -> 01_driver_approved.json
    3 size      -> 02_sizing.json / .xlsx (country totals + region rollup)
    4 segments  -> 03_segments_draft.json -> approve -> 03_segments_approved.json
    5 report    -> 04_market.json / 04_market.xlsx
Run folder: output/demand_country_2a/<slug>/

Autonomous agent (`agent.py`, design in src/newinfo/system.md): replaces the
approval gates with a review loop and the newinfo controls --
    price_curve.py    per-country price from UN Comtrade unit values + a
                      price-vs-income curve (mass-measured markets)
    ledger.py         number ledger (IDs, labels, weakest input, formulas) and
                      the generated documentation.md; ledger_2a.py builds it
    checks.py         hard checks (nothing written on failure) + soft flags
    plausibility.py   0-10 pass score; never uses closeness to published sizes
    progress.py       progress.mermaid from src/newinfo/flowchart.mermaid
    strategy2c.py     2C core: recipe chains, cell budget, unattributed residual
    design_config.py  thresholds (DEAGENT_DESIGN_CONFIG=<json> overrides)
Each agent run also writes runs/<RUN>/ (summary.md, ledger, documentation.md,
progress.mermaid, market_tables.xlsx).
"""

"""2A's own copy of the top-down segmentation layer.

Forked from `me_engine.demand_topdown` so approach 2A can be changed without
affecting 2B. Not kept in sync: port fixes by hand if they apply to both.

Modules:
    regions.py   country -> region mapping (CMI's five standard regions)
    segments.py  propose dimensions, estimate per-country shares, approval gate
    split.py     deterministic split + rollup + consistency checks
    writer.py    final market report (JSON + XLSX)
    official_data.py  official statistics (FAOSTAT) for country demand
"""

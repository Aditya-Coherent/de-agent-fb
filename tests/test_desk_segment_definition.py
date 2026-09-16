"""Tests for the Desk Research Phase 0 gate
(`src/me_engine/generation/segment_definition.py`) -- base-year segments +
regional market-share input, validated and applied with NO LLM involved
anywhere in this module."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from me_engine.generation import segment_definition  # noqa: E402


def _valid_definition(market_name: str = "Global Process Analyzer Market") -> dict:
    return {
        "market_name": market_name,
        "base_year": 2025,
        "dimensions": [
            {
                "dimension_name": "By Installation",
                "segments": [
                    {"name": "Flooring", "region_shares": {
                        "North America": 60, "Europe": 55, "Asia Pacific": 50,
                        "Latin America": 50, "Middle East & Africa": 50,
                    }},
                    {"name": "Wall Cladding", "region_shares": {
                        "North America": 40, "Europe": 45, "Asia Pacific": 50,
                        "Latin America": 50, "Middle East & Africa": 50,
                    }},
                ],
            },
        ],
    }


# --------------------------------------------------------------------------- #
# Schema / validation
# --------------------------------------------------------------------------- #
def test_fixed_regions_are_the_five_specified_regions():
    assert segment_definition.FIXED_REGIONS == (
        "North America", "Europe", "Asia Pacific", "Latin America", "Middle East & Africa",
    )


def test_default_base_year_is_current_year_minus_one(monkeypatch):
    import datetime

    class _FixedDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.datetime(2026, 6, 1)

    monkeypatch.setattr(segment_definition, "datetime", _FixedDatetime)
    assert segment_definition.default_base_year() == 2025


def test_validate_accepts_a_well_formed_definition():
    normalized = segment_definition.validate_segment_definition(_valid_definition())
    assert normalized["market_name"] == "Global Process Analyzer Market"
    assert normalized["base_year"] == 2025
    assert normalized["regions"] == list(segment_definition.FIXED_REGIONS)
    assert len(normalized["dimensions"]) == 1
    assert normalized["dimensions"][0]["dimension_name"] == "By Installation"


def test_validate_rejects_missing_market_name():
    data = _valid_definition()
    data["market_name"] = ""
    try:
        segment_definition.validate_segment_definition(data)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "market_name" in str(exc)


def test_validate_rejects_non_integer_base_year():
    data = _valid_definition()
    data["base_year"] = "not-a-year"
    try:
        segment_definition.validate_segment_definition(data)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "base_year" in str(exc)


def test_validate_rejects_empty_dimensions_list():
    data = _valid_definition()
    data["dimensions"] = []
    try:
        segment_definition.validate_segment_definition(data)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "at least one dimension" in str(exc)


def test_validate_rejects_out_of_range_share():
    data = _valid_definition()
    data["dimensions"][0]["segments"][0]["region_shares"]["North America"] = 150
    try:
        segment_definition.validate_segment_definition(data)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "out of range" in str(exc)


def test_validate_rejects_missing_region_share():
    data = _valid_definition()
    del data["dimensions"][0]["segments"][0]["region_shares"]["Europe"]
    try:
        segment_definition.validate_segment_definition(data)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "missing region share" in str(exc)


def test_validate_rejects_shares_that_do_not_reconcile_to_100_pct_per_region():
    data = _valid_definition()
    # North America: 60 + 30 = 90, outside +/-1% tolerance.
    data["dimensions"][0]["segments"][1]["region_shares"]["North America"] = 30
    try:
        segment_definition.validate_segment_definition(data)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "North America" in str(exc)
        assert "90.00%" in str(exc)


def test_validate_accepts_reconciliation_within_tolerance():
    data = _valid_definition()
    # 60 + 39.5 = 99.5%, within +/-1% tolerance.
    data["dimensions"][0]["segments"][1]["region_shares"]["North America"] = 39.5
    normalized = segment_definition.validate_segment_definition(data)
    assert normalized["dimensions"][0]["segments"][1]["region_shares"]["North America"] == 39.5


def test_validate_rejects_unknown_region_key():
    data = _valid_definition()
    data["dimensions"][0]["segments"][0]["region_shares"]["Antarctica"] = 5
    try:
        segment_definition.validate_segment_definition(data)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "unknown region" in str(exc)


# --------------------------------------------------------------------------- #
# to_nesting / to_root_shares -- the exact shapes `Skeleton.nesting` /
# `state["shares"]` need, so the graph consumes user input with zero
# translation logic elsewhere.
# --------------------------------------------------------------------------- #
def test_to_nesting_produces_a_flat_root_level_sibling_group_per_dimension():
    definition = segment_definition.validate_segment_definition(_valid_definition())
    nesting = segment_definition.to_nesting(definition)
    assert nesting == {"By Installation": {"__root__": ["Flooring", "Wall Cladding"]}}


def test_to_root_shares_converts_percent_to_fraction_and_reconciles_to_one():
    definition = segment_definition.validate_segment_definition(_valid_definition())
    shares = segment_definition.to_root_shares(definition)
    dim = shares["By Installation"]
    for region in segment_definition.FIXED_REGIONS:
        vec = dim[region]["__root__"]
        assert abs(sum(vec.values()) - 1.0) < 1e-9
    assert abs(dim["North America"]["__root__"]["Flooring"] - 0.6) < 1e-9
    assert abs(dim["North America"]["__root__"]["Wall Cladding"] - 0.4) < 1e-9
    # __global__ mean across regions is also present, for parity with the
    # LLM-produced shape `sheet_writer.py`/`reward_node` already read.
    assert "__root__" in dim["__global__"]


# --------------------------------------------------------------------------- #
# Calculation logic: region_total_market_value x region_shares -- independent
# per dimension, never nested/multiplied against another dimension.
# --------------------------------------------------------------------------- #
def test_compute_segment_values_equals_region_total_times_share_over_100():
    definition = segment_definition.validate_segment_definition(_valid_definition())
    region_totals = {
        "North America": 1000.0, "Europe": 500.0, "Asia Pacific": 800.0,
        "Latin America": 200.0, "Middle East & Africa": 100.0,
    }
    values = segment_definition.compute_segment_values(region_totals, definition)
    assert values["By Installation"]["Flooring"]["North America"] == 1000.0 * 60 / 100
    assert values["By Installation"]["Wall Cladding"]["North America"] == 1000.0 * 40 / 100
    assert values["By Installation"]["Flooring"]["Europe"] == 500.0 * 55 / 100
    assert values["By Installation"]["Wall Cladding"]["Europe"] == 500.0 * 45 / 100


# --------------------------------------------------------------------------- #
# dimensions_from_segmentation_tree -- DB "depth|label" lines -> preview
# {dimension_name, segments} shape. Regression coverage for the bug where the
# dimension's own header label leaked in as a spurious segment row alongside
# its real children.
# --------------------------------------------------------------------------- #
def test_dimensions_from_segmentation_tree_excludes_the_parent_label_from_its_own_segments():
    lines = [
        "0|By Installation", "1|Flooring", "1|Wall Cladding",
        "0|By Material Type", "1|Polyethylene", "1|Polypropylene",
    ]
    dims = segment_definition.dimensions_from_segmentation_tree(lines)
    assert len(dims) == 2
    assert dims[0]["dimension_name"] == "By Installation"
    assert [s["name"] for s in dims[0]["segments"]] == ["Flooring", "Wall Cladding"]
    assert dims[1]["dimension_name"] == "By Material Type"
    assert [s["name"] for s in dims[1]["segments"]] == ["Polyethylene", "Polypropylene"]
    # The parent label must never appear among either dimension's own segments.
    all_segment_names = {s["name"] for d in dims for s in d["segments"]}
    assert "By Installation" not in all_segment_names
    assert "By Material Type" not in all_segment_names


def test_dimensions_from_segmentation_tree_drops_a_duplicated_header_line():
    """Guards the real-world malformed-HTML case: the header text reappears
    verbatim as if it were its own first child."""
    lines = ["0|Installation Insights (Revenue, USD Mn, 2021 - 2033)", "1|By Installation", "1|Flooring", "1|Wall Cladding"]
    dims = segment_definition.dimensions_from_segmentation_tree(lines)
    assert len(dims) == 1
    assert dims[0]["dimension_name"] == "By Installation"
    assert [s["name"] for s in dims[0]["segments"]] == ["Flooring", "Wall Cladding"]


def test_dimensions_from_segmentation_tree_skips_the_geography_tree():
    lines = [
        "0|By Installation", "1|Flooring", "1|Wall Cladding",
        "0|Regional Insights (Revenue, USD Mn, 2021 - 2033)",
        "1|North America", "2|U.S.", "2|Canada",
    ]
    dims = segment_definition.dimensions_from_segmentation_tree(lines)
    assert len(dims) == 1
    assert dims[0]["dimension_name"] == "By Installation"


def test_dimensions_from_segmentation_tree_drops_regions_from_a_fused_header_hadoop_style_record():
    """Regression: a real, malformed DB record (found live via a "Hadoop
    Market" nearest-neighbor match) fuses the "Regional Insights" header
    with its first child ("Regional Insights (...) North America"), leaving
    every SUBSEQUENT region ("Latin America", "Europe", ...) as its own bare
    depth-0 line with no "region"/"geograph" text -- indistinguishable from a
    real dimension header by depth alone. `looks_like_geography_dimension`
    must still catch every one of these via their country children, while a
    genuine segmentation dimension elsewhere in the same record survives."""
    lines = [
        "0|By Application", "1|Fraud Detection", "1|Log Analytics",
        "0|Regional Insights (Revenue, US$ Bn, 2021 - 2033) (Revenue, USD Bn, 2021 - 2033) North America",
        "1|U.S.", "1|Canada",
        "0|Latin America",
        "1|Brazil", "1|Argentina", "1|Mexico", "1|Rest of Latin America",
        "0|Europe",
        "1|Germany", "1|U.K.", "1|Spain", "1|France", "1|Italy",
    ]
    dims = segment_definition.dimensions_from_segmentation_tree(lines)
    # Only the genuine segmentation dimension survives...
    assert dims == [{"dimension_name": "By Application", "segments": [
        {"name": "Fraud Detection", "region_shares": {}}, {"name": "Log Analytics", "region_shares": {}},
    ]}]
    # ...every geography-derived pseudo-dimension is gone, not just hidden.
    dim_names = {d["dimension_name"] for d in dims}
    assert "By Latin America" not in dim_names
    assert "By Europe" not in dim_names
    for d in dims:
        assert not segment_definition.looks_like_geography_dimension(
            d["dimension_name"], [s["name"] for s in d["segments"]],
        )


def test_dimensions_from_segmentation_tree_cleans_insights_header_labels():
    lines = ["0|Installation Insights (Revenue, USD Mn, 2021 - 2033)", "1|Flooring", "1|Wall Cladding"]
    dims = segment_definition.dimensions_from_segmentation_tree(lines)
    assert dims[0]["dimension_name"] == "By Installation"


def test_clean_dimension_label_never_doubles_the_by_prefix():
    assert segment_definition._clean_dimension_label("By Installation") == "By Installation"
    assert segment_definition._clean_dimension_label("Installation Insights (Revenue, USD Mn)") == "By Installation"


# --------------------------------------------------------------------------- #
# looks_like_geography_dimension -- guards the Skeleton-derived nearest-
# neighbor DB fallback: an unrelated report's own geography tree must never
# be surfaced as if it were real product/application segmentation.
# --------------------------------------------------------------------------- #
def test_looks_like_geography_dimension_true_for_a_region_name_dimension():
    assert segment_definition.looks_like_geography_dimension(
        "By Region", ["North America", "Europe", "Asia Pacific", "Latin America", "Middle East & Africa"],
    )


def test_looks_like_geography_dimension_true_when_children_are_countries():
    assert segment_definition.looks_like_geography_dimension(
        "By Latin America", ["Brazil", "Argentina", "Mexico", "Rest of Latin America"],
    )


def test_looks_like_geography_dimension_true_for_region_header_text():
    assert segment_definition.looks_like_geography_dimension(
        "Regional Insights", ["North America", "Europe"],
    )


def test_looks_like_geography_dimension_false_for_a_real_segmentation():
    assert not segment_definition.looks_like_geography_dimension(
        "By Installation", ["Flooring", "Wall Cladding"],
    )


def test_looks_like_geography_dimension_false_for_empty_segments():
    assert not segment_definition.looks_like_geography_dimension("By Installation", [])


def test_looks_like_geography_dimension_tolerates_a_stray_non_geo_label():
    # 3 of 4 children are real countries -- still flagged as geography even
    # though one label doesn't match (majority-overlap threshold).
    assert segment_definition.looks_like_geography_dimension(
        "By Somewhere", ["Brazil", "Argentina", "Mexico", "Something Else Entirely"],
    )


# --------------------------------------------------------------------------- #
# Regression: live case found while re-testing the previous round's fix --
# "By Middle East" and "By Africa" (standalone sub-region names, not the
# combined fixed region "Middle East & Africa") with country children not
# in `skeleton.REGIONS`'s coarse template list.
# --------------------------------------------------------------------------- #
def test_looks_like_geography_dimension_catches_by_middle_east():
    assert segment_definition.looks_like_geography_dimension(
        "By Middle East", ["GCC Countries", "Israel", "Rest of Middle East"],
    )


def test_looks_like_geography_dimension_catches_by_africa():
    assert segment_definition.looks_like_geography_dimension(
        "By Africa", ["South Africa", "North Africa", "Central Africa"],
    )


def test_looks_like_geography_dimension_catches_standalone_geography_keywords_by_name_alone():
    """The dimension-name keyword check alone (empty/irrelevant segments)
    still catches these -- not just the segment-overlap check."""
    for name in [
        "By Middle East", "By Africa", "By Asia Pacific", "By Europe",
        "By North America", "By Latin America", "APAC", "EMEA", "By Asia", "Americas",
    ]:
        assert segment_definition.looks_like_geography_dimension(name, []), name


def test_looks_like_geography_dimension_keyword_check_has_no_false_positive_on_similar_words():
    # "Asian Cuisine" contains "asia" as a substring but not as a whole word
    # -- must not be flagged.
    assert not segment_definition.looks_like_geography_dimension(
        "By Asian Cuisine Type", ["Sushi", "Noodles"],
    )


def test_dimensions_from_segmentation_tree_drops_by_middle_east_and_by_africa():
    """The exact live case: both survive the depth-based parse (each has
    real children, so `flush()` doesn't drop them for being empty) but must
    be filtered by the broadened geography heuristic before returning."""
    lines = [
        "0|By Application", "1|Fraud Detection", "1|Log Analytics",
        "0|Middle East", "1|GCC Countries", "1|Israel", "1|Rest of Middle East",
        "0|Africa", "1|South Africa", "1|North Africa", "1|Central Africa",
    ]
    dims = segment_definition.dimensions_from_segmentation_tree(lines)
    assert dims == [{"dimension_name": "By Application", "segments": [
        {"name": "Fraud Detection", "region_shares": {}}, {"name": "Log Analytics", "region_shares": {}},
    ]}]
    dim_names = {d["dimension_name"] for d in dims}
    assert "By Middle East" not in dim_names
    assert "By Africa" not in dim_names


def test_compute_segment_values_keeps_dimensions_independent():
    """Two dimensions both slice the SAME region total -- neither is nested
    inside or multiplied against the other."""
    data = _valid_definition()
    data["dimensions"].append({
        "dimension_name": "By Material Type",
        "segments": [
            {"name": "Glass", "region_shares": {
                "North America": 70, "Europe": 70, "Asia Pacific": 70, "Latin America": 70, "Middle East & Africa": 70,
            }},
            {"name": "Polymer", "region_shares": {
                "North America": 30, "Europe": 30, "Asia Pacific": 30, "Latin America": 30, "Middle East & Africa": 30,
            }},
        ],
    })
    definition = segment_definition.validate_segment_definition(data)
    region_totals = {
        "North America": 1000.0, "Europe": 500.0, "Asia Pacific": 800.0,
        "Latin America": 200.0, "Middle East & Africa": 100.0,
    }
    values = segment_definition.compute_segment_values(region_totals, definition)
    assert values["By Installation"]["Flooring"]["North America"] == 600.0
    # "By Material Type" split of the SAME North America total, not derived
    # from "By Installation" at all.
    assert values["By Material Type"]["Glass"]["North America"] == 700.0
    assert values["By Material Type"]["Polymer"]["North America"] == 300.0

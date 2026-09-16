"""Desk Research Phase 0: Base-Year Segment Definition -- the gate that runs
BEFORE `GET /api/generate` (the LangGraph fleet in `agents.py`/`graph.py`) is
allowed to start.

Today the LLM decides BOTH which % share every segment gets within its
sibling group (`agents.py::_shares_global` / `agents.py::shares_node`, "You
are a market segmentation analyst...") AND each region's share of the global
total (`agents.py::market_size_node`'s `regions` JSON). This module lets the
user pin down the segment tree and its base-year regional shares explicitly;
the LLM is then never asked to invent or override them for any dimension the
user has defined -- see `agents.py::_shares_global` / `shares_node`, which
skip their own LLM call entirely for a dimension present in the approved
segment definition (this module's `to_nesting()`/`to_root_shares()` are what
they consume instead).

No LLM anywhere in THIS module: validation and the segment_value calculation
are both pure, deterministic Python.

`looks_like_geography_dimension` (used to keep a region/country breakdown
from being surfaced as if it were product/application segmentation) is a
hand-maintained keyword/name-list heuristic, NOT an exhaustive geography
detector -- an unlisted region/country name (e.g. "DACH", "Benelux", a
"Sub-Saharan Africa" phrasing not already listed, a non-English country
name) can still slip through. This list has already been extended twice
for real cases found in production DB records; if a THIRD gap turns up,
replace this hand-maintained list with a real exhaustive country/region
dataset (e.g. `pycountry`) instead of growing it again.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .skeleton import REGIONS

_REGION_HEADER_RE = re.compile(r"region|geograph", re.I)
_PAREN_RE = re.compile(r"\(.*?\)")
_INSIGHTS_RE = re.compile(r"\binsights\b", re.I)


def _clean_dimension_label(label: str) -> str:
    """'Installation Insights (Revenue, USD Mn, ...)' -> 'By Installation'.
    Idempotent on labels already shaped like 'By X' (never doubles the
    'By ' prefix, unlike a naive `f"By {t}"` would)."""
    t = _PAREN_RE.sub("", label)
    t = _INSIGHTS_RE.sub("", t).strip(" -–—,")
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return "By Segment"
    if re.match(r"^by\b", t, flags=re.I):
        return "By " + t[2:].strip()
    return f"By {t}"


def normalize_label_for_comparison(text: str) -> str:
    """Strip a 'By ' prefix, parentheticals, 'Insights', and punctuation/
    case differences so two spellings of the same label compare equal."""
    t = _PAREN_RE.sub("", text)
    t = _INSIGHTS_RE.sub("", t)
    t = re.sub(r"^\s*by\b", "", t, flags=re.I)
    return re.sub(r"[^a-z0-9]+", "", t.lower())


# Extra country/sub-regional names that show up as geography breakdowns in
# real market reports but aren't in `skeleton.REGIONS`'s coarse ~30-country
# template list. A pragmatic patch, not meant to be exhaustive -- see the
# module docstring.
_EXTRA_GEO_NAMES: frozenset[str] = frozenset({
    "Israel", "GCC Countries", "GCC", "North Africa", "Central Africa",
    "Sub-Saharan Africa", "East Africa", "West Africa",
    "Rest of Middle East", "Rest of Africa",
})

# Every region name and every country name known to `skeleton.REGIONS`,
# plus `_EXTRA_GEO_NAMES` above, normalized once -- the ground truth for
# "this looks like a geography breakdown, not a product/application
# segmentation."
_GEO_NAMES_NORMALIZED: frozenset[str] = frozenset(
    normalize_label_for_comparison(name)
    for name in ({*REGIONS.keys()} | {c for members in REGIONS.values() for c in members} | _EXTRA_GEO_NAMES)
)
# Majority-overlap threshold: a dimension whose children are mostly
# recognizable region/country names is treated as a geography tree even if
# a stray non-geography label slipped in.
_GEO_OVERLAP_THRESHOLD = 0.6

# Standalone geography keywords that flag a DIMENSION NAME as a geography
# breakdown even when it doesn't exactly equal a `REGIONS` key (e.g. "By
# Middle East" or "By Africa" alone, vs. the combined fixed region name
# "Middle East & Africa"). Word-boundary matched so e.g. "Africa" matches
# but a word merely containing "africa" as a substring would not (none of
# these happen to be substrings of unrelated English words in practice, but
# \b keeps the match honest regardless). A pragmatic patch, not meant to be
# exhaustive -- see the module docstring.
_GEO_KEYWORDS = (
    "middle east", "africa", "asia pacific", "europe", "north america",
    "latin america", "apac", "emea", "asia", "americas",
)
_GEO_KEYWORD_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in sorted(_GEO_KEYWORDS, key=len, reverse=True)) + r")\b", re.I,
)


def looks_like_geography_dimension(dimension_name: str, segment_names: list[str]) -> bool:
    """True if a candidate dimension is actually a region/geography
    breakdown (region names or `skeleton.REGIONS`/`_EXTRA_GEO_NAMES`
    countries) rather than a real product/application/material-type
    segmentation.

    Applied on BOTH: the Skeleton-derived nearest-neighbor DB fallback in
    `backend/main.py`'s `/api/segment-definition/preview` (an unrelated
    report's own geography tree, found via semantic-similarity, must never
    be surfaced as if it were real segmentation) and the DB-first path
    (`dimensions_from_segmentation_tree`) -- a malformed source record can
    fuse a "Regional Insights" header with its first child, leaving
    subsequent regions as bare depth-0 lines indistinguishable from real
    dimension headers by depth alone.

    This is a hand-maintained keyword/name-list heuristic, not exhaustive --
    see the module docstring."""
    dim_norm = normalize_label_for_comparison(dimension_name)
    if (
        _REGION_HEADER_RE.search(dimension_name)
        or dim_norm in {normalize_label_for_comparison(r) for r in REGIONS}
        or _GEO_KEYWORD_RE.search(dimension_name)
    ):
        return True
    if not segment_names:
        return False
    geo_matches = sum(1 for s in segment_names if normalize_label_for_comparison(s) in _GEO_NAMES_NORMALIZED)
    return (geo_matches / len(segment_names)) >= _GEO_OVERLAP_THRESHOLD


def dimensions_from_segmentation_tree(lines: list[str]) -> list[dict[str, Any]]:
    """Parses `DBEvidence.segmentation_tree` ("depth|label" indented lines,
    see `db_source.py`) into `[{dimension_name, segments: [{name,
    region_shares: {}}]}]` for the Segment Definition gate's DB prefill.

    Only two levels are modeled (dimension -> segment), matching the gate's
    flat data shape: a depth-0 line starts a new dimension (its label
    becomes `dimension_name`, cleaned via `_clean_dimension_label`); every
    following line up to the next depth-0 line becomes one of its segments.
    A line whose (normalized) text matches the dimension's own header label
    is dropped, never emitted as one of its own segments -- this is the
    exact bug this function exists to prevent (a malformed/duplicated
    source line otherwise leaks the parent category in as a spurious
    segment row alongside its real children).

    Also drops any dimension `looks_like_geography_dimension` flags before
    returning -- some records wrap their geography section in a single
    "Regional Insights"-style header (correctly skipped below), but a
    malformed source can fuse that header with its first child (e.g.
    "Regional Insights ... North America"), leaving each SUBSEQUENT region
    ("Latin America", "Europe", ...) as its own bare depth-0 line with no
    "region"/"geograph" text of its own -- indistinguishable from a real
    dimension header to the depth-based parsing above. This is the same
    safety net already applied to the Skeleton-derived nearest-neighbor
    fallback in `backend/main.py`; a country breakdown must never be
    surfaced as product/application segmentation from either path."""
    dims: list[dict[str, Any]] = []
    cur_name: str | None = None
    cur_key: str | None = None
    cur_segments: list[str] = []

    def flush() -> None:
        if cur_name and cur_segments:
            dims.append({
                "dimension_name": cur_name,
                "segments": [{"name": s, "region_shares": {}} for s in cur_segments],
            })

    for line in lines:
        depth_str, sep, label = line.partition("|")
        if not sep:
            continue
        try:
            depth = int(depth_str)
        except ValueError:
            continue
        label = label.strip()
        if not label:
            continue
        if depth == 0:
            flush()
            cur_segments = []
            if _REGION_HEADER_RE.search(label):
                cur_name, cur_key = None, None  # geography tree -- not a segmentation dimension
                continue
            cur_name = _clean_dimension_label(label)
            cur_key = normalize_label_for_comparison(cur_name)
            continue
        if cur_name is None:
            continue
        if normalize_label_for_comparison(label) == cur_key:
            continue  # the header's own label, duplicated as if it were a child -- drop it
        cur_segments.append(label)
    flush()
    return [
        d for d in dims
        if not looks_like_geography_dimension(d["dimension_name"], [s["name"] for s in d["segments"]])
    ]

# Fixed, global, non-editable region list -- identical across every market.
# Deliberately the SAME 5 names already used by `skeleton.REGIONS` (the
# hardcoded fallback topology), so `region_total_market_value` keys always
# line up with `state["regional"]` (built from `sk.regions.keys()`) once the
# skeleton's regions are forced onto this set for a gated run -- see
# `backend/main.py`'s `_run_generation_stream_inner`.
FIXED_REGIONS: tuple[str, ...] = tuple(REGIONS.keys())

# Tolerance for "sums to ~100%" reconciliation of one dimension's segment
# shares, for one region.
_RECONCILE_TOLERANCE_PCT = 1.0


def default_base_year() -> int:
    """(current_year - 1) -- "the latest year with actual data"."""
    return datetime.now().year - 1


def _validate_pct(value: Any, path: str, errors: list[str]) -> float:
    try:
        pct = float(value)
    except (TypeError, ValueError):
        errors.append(f"{path}: '{value!r}' is not numeric")
        return 0.0
    if pct < 0 or pct > 100:
        errors.append(f"{path}: {pct} is out of range 0-100")
    return pct


def validate_segment_definition(data: dict[str, Any]) -> dict[str, Any]:
    """Validates and normalizes a submitted base-year segment definition.
    Raises ValueError (with every problem found, not just the first) if the
    input is incomplete, malformed, or a dimension's region shares don't
    reconcile to ~100% per region (tolerance +/-1%). Returns the normalized
    dict on success -- this IS the structure threaded into the generation
    graph as a hard constraint (see `to_nesting`/`to_root_shares` below),
    never re-derived by the LLM."""
    errors: list[str] = []

    market_name = str(data.get("market_name") or "").strip()
    if not market_name:
        errors.append("market_name is required")

    try:
        base_year = int(data.get("base_year"))
    except (TypeError, ValueError):
        errors.append(f"base_year must be an integer, got {data.get('base_year')!r}")
        base_year = default_base_year()

    dims_in = data.get("dimensions") or []
    if not dims_in:
        errors.append("at least one dimension is required")

    normalized_dims: list[dict[str, Any]] = []
    for di, dim in enumerate(dims_in):
        dim_name = str((dim or {}).get("dimension_name") or "").strip()
        if not dim_name:
            errors.append(f"dimensions[{di}]: dimension_name is required")
            dim_name = dim_name or f"dimension_{di}"
        segments_in = (dim or {}).get("segments") or []
        if not segments_in:
            errors.append(f"dimension '{dim_name}': at least one segment is required")

        region_running_total: dict[str, float] = {r: 0.0 for r in FIXED_REGIONS}
        normalized_segs: list[dict[str, Any]] = []
        for si, seg in enumerate(segments_in):
            seg_name = str((seg or {}).get("name") or "").strip()
            if not seg_name:
                errors.append(f"dimension '{dim_name}', segments[{si}]: name is required")
                seg_name = seg_name or f"segment_{si}"
            raw_shares = (seg or {}).get("region_shares") or {}
            normalized_shares: dict[str, float] = {}
            for region in FIXED_REGIONS:
                path = f"dimension '{dim_name}' / segment '{seg_name}' / region '{region}'"
                if region not in raw_shares:
                    errors.append(f"{path}: missing region share")
                    continue
                pct = _validate_pct(raw_shares[region], path, errors)
                normalized_shares[region] = pct
                region_running_total[region] += pct
            extra = set(raw_shares) - set(FIXED_REGIONS)
            if extra:
                errors.append(f"dimension '{dim_name}' / segment '{seg_name}': unknown region(s) {sorted(extra)}")
            normalized_segs.append({"name": seg_name, "region_shares": normalized_shares})

        for region, total in region_running_total.items():
            if abs(total - 100.0) > _RECONCILE_TOLERANCE_PCT:
                errors.append(
                    f"dimension '{dim_name}', region '{region}': segment shares sum to {total:.2f}%, "
                    f"expected ~100% (tolerance +/-{_RECONCILE_TOLERANCE_PCT}%)"
                )
        normalized_dims.append({"dimension_name": dim_name, "segments": normalized_segs})

    if errors:
        raise ValueError("; ".join(errors))

    return {
        "market_name": market_name,
        "base_year": base_year,
        "regions": list(FIXED_REGIONS),
        "dimensions": normalized_dims,
    }


def to_nesting(definition: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    """The user's dimensions as a flat, root-level `Skeleton.nesting` tree
    (one sibling group per dimension, parent="__root__") -- REPLACES whatever
    `sk.nesting` the DB/LLM/hardcoded skeleton builder produced, for any run
    with an approved segment definition."""
    return {
        dim["dimension_name"]: {"__root__": [seg["name"] for seg in dim["segments"]]}
        for dim in definition["dimensions"]
    }


def to_root_shares(definition: dict[str, Any]) -> dict[str, dict[str, dict[str, dict[str, float]]]]:
    """The user's percentages, converted into the EXACT shape
    `agents.py::shares_node` / `_shares_global` would have produced from the
    LLM: `shares[dim]["__root__" ... ]`... concretely
    `shares[dim][region]["__root__"][segment] = fraction` (0-1, summing to 1
    within a region), plus a `"__global__"` entry (mean across regions) for
    parity with the LLM-produced shape that `sheet_writer.py`/`reward_node`
    already know how to read."""
    out: dict[str, dict[str, dict[str, dict[str, float]]]] = {}
    for dim in definition["dimensions"]:
        dim_name = dim["dimension_name"]
        per_region: dict[str, dict[str, float]] = {}
        for region in FIXED_REGIONS:
            vec = {seg["name"]: seg["region_shares"].get(region, 0.0) / 100.0 for seg in dim["segments"]}
            total = sum(vec.values()) or 1.0
            per_region[region] = {k: v / total for k, v in vec.items()}
        glob: dict[str, float] = {}
        for seg in dim["segments"]:
            glob[seg["name"]] = sum(per_region[r][seg["name"]] for r in FIXED_REGIONS) / len(FIXED_REGIONS)
        dim_out: dict[str, dict[str, dict[str, float]]] = {"__global__": {"__root__": glob}}
        for region in FIXED_REGIONS:
            dim_out[region] = {"__root__": per_region[region]}
        out[dim_name] = dim_out
    return out


def compute_segment_values(
    region_totals: dict[str, float], definition: dict[str, Any],
) -> dict[str, dict[str, dict[str, float]]]:
    """For every dimension independently:
        segment_value[dimension][segment][region]
            = region_total_market_value[region] * region_shares[dimension][segment][region] / 100
    Dimensions are NOT nested/multiplied against each other -- each one is a
    full, independent breakdown of the SAME `region_totals` (e.g. "By
    Installation" and "By Material Type" both divide up the same regional
    total, never each other)."""
    out: dict[str, dict[str, dict[str, float]]] = {}
    for dim in definition["dimensions"]:
        dim_name = dim["dimension_name"]
        out[dim_name] = {}
        for seg in dim["segments"]:
            values: dict[str, float] = {}
            for region, pct in seg["region_shares"].items():
                total = region_totals.get(region, 0.0) or 0.0
                values[region] = total * float(pct) / 100.0
            out[dim_name][seg["name"]] = values
    return out

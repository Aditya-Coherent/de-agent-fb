"""Tests for AiAgentsFleet/consistency.py -- the reconciliation +
validation pass that makes the AI-synthesized whitepaper numbers internally
consistent before the .docx is rendered.

Fixtures (`AiAgentsFleet/tests/fixtures/*_broken.json`) are REAL
snapshots of the 4 markets' `whitepaper_fields.json` taken before this
module existed -- the exact self-contradictory numbers described in the
bug report (regions summing past the global total, a scenario base case
that doesn't match the headline forecast, Porter's labels that don't match
their own score, etc.). The tests assert these fixtures genuinely fail
validation, and that `reconcile()` makes them pass.

Run with: pytest tests/test_ai_overview_consistency.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from AiAgentsFleet import consistency as cst  # noqa: E402

FIXTURES_DIR = ROOT / "AiAgentsFleet" / "tests" / "fixtures"
FIXTURE_SLUGS = [
    "super-intensive-shrimp-farming-market",
    "functional-poultry-feed-market",
    "agricultural-biostimulants-market",
    "generative-ai-in-agriculture-market",
]


def _load_fixture(slug: str) -> dict:
    data = json.loads((FIXTURES_DIR / f"{slug}_broken.json").read_text(encoding="utf-8"))
    return data["fields"]


@pytest.fixture(params=FIXTURE_SLUGS)
def broken_fields(request):
    return request.param, _load_fixture(request.param)


# --------------------------------------------------------------------------- #
# The real, broken fixtures must actually fail validation -- otherwise the
# bug report wasn't real, or the fixture got cleaned up accidentally.
# --------------------------------------------------------------------------- #
def test_broken_fixtures_fail_validation(broken_fields):
    slug, fields = broken_fields
    issues = cst.validate(fields, slug)
    assert issues, f"{slug}: expected the known-broken fixture to fail validation, but it passed"


def test_broken_fixtures_pass_validation_after_reconcile(broken_fields):
    slug, fields = broken_fields
    fixed, changes = cst.reconcile(fields, slug)
    issues = cst.validate(fixed, slug)
    assert issues == [], f"{slug}: still failing validation after reconcile(): {issues}"
    # A genuinely broken fixture should have required at least one real fix.
    assert changes, f"{slug}: reconcile() made no changes at all to a known-broken fixture"


def test_reconcile_does_not_mutate_its_input(broken_fields):
    """Regression: an earlier version of reconcile() only shallow-copied its
    input, so nested dicts were mutated in place -- silently corrupting any
    "before" snapshot taken from the same object after calling reconcile()."""
    slug, fields = broken_fields
    import copy

    original = copy.deepcopy(fields)
    cst.reconcile(fields, slug)
    assert fields == original, f"{slug}: reconcile() mutated its input argument"


# --------------------------------------------------------------------------- #
# Specific contradictions named in the bug report -- confirm each is
# actually gone after reconcile(), not just "fewer issues overall".
# --------------------------------------------------------------------------- #
def test_shrimp_scenario_base_matches_headline_forecast():
    fields = _load_fixture("super-intensive-shrimp-farming-market")
    fixed, _ = cst.reconcile(fields, "shrimp")
    base_scenario = fixed["scenarios"]["base"]["terminal_value_usd_mn"]
    headline_forecast = fixed["market_size"]["forecast_value_usd_mn"]
    assert base_scenario == pytest.approx(headline_forecast, rel=0.01)


def test_shrimp_forecast_series_is_monotonic():
    fields = _load_fixture("super-intensive-shrimp-farming-market")
    fixed, _ = cst.reconcile(fields, "shrimp")
    series = fixed["market_size"]["forecast_series_usd_mn"]
    years = sorted(int(y) for y in series)
    values = [series[str(y)] for y in years]
    assert values == sorted(values), "forecast series must only go up"


def test_poultry_regions_sum_to_global_after_reconcile():
    fields = _load_fixture("functional-poultry-feed-market")
    fixed, _ = cst.reconcile(fields, "poultry")
    base_val = fixed["market_size"]["base_year_value_usd_mn"]
    region_total = sum(r["value_usd_mn"] for r in fixed["regional"]["regions"].values())
    assert region_total == pytest.approx(base_val, rel=0.02)


def test_poultry_no_derivative_bigger_than_market():
    fields = _load_fixture("functional-poultry-feed-market")
    fixed, _ = cst.reconcile(fields, "poultry")
    base_val = fixed["market_size"]["base_year_value_usd_mn"]
    for d in fixed["derivatives_pricing"]["derivatives"]:
        assert d["value_usd_mn"] <= base_val, f"{d['name']} still exceeds the total market after reconcile()"


def test_generative_ai_porters_labels_match_scores_after_reconcile():
    fields = _load_fixture("generative-ai-in-agriculture-market")
    fixed, _ = cst.reconcile(fields, "ai")
    for key, force in fixed["porters_five_forces"]["forces"].items():
        assert force["label"] == cst.porter_label(force["score_1_5"]), f"{key}: label/score mismatch survived reconcile()"


def test_biostimulants_regional_shares_sum_to_100_after_reconcile():
    fields = _load_fixture("agricultural-biostimulants-market")
    fixed, _ = cst.reconcile(fields, "biostimulants")
    shares = [r["share_pct_of_global"] for r in fixed["regional"]["regions"].values()]
    assert sum(shares) == pytest.approx(100.0, abs=0.5)


def test_biostimulants_printed_cagr_matches_implied_cagr():
    fields = _load_fixture("agricultural-biostimulants-market")
    fixed, _ = cst.reconcile(fields, "biostimulants")
    ms = fixed["market_size"]
    years = ms["forecast_year"] - ms["base_year"]
    implied = ((ms["forecast_value_usd_mn"] / ms["base_year_value_usd_mn"]) ** (1 / years) - 1) * 100
    assert implied == pytest.approx(ms["cagr_pct"], abs=0.2)


def test_leaked_search_titles_are_dropped():
    fields = _load_fixture("agricultural-biostimulants-market")
    fixed, changes = cst.reconcile(fields, "biostimulants")
    all_names = [d["name"] for d in fixed.get("derivatives_pricing", {}).get("derivatives", [])]
    assert not any(cst.looks_like_leaked_search_title(name) for name in all_names)


# --------------------------------------------------------------------------- #
# Unit tests for the small pure helpers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name,expected", [
    ("Biostimulants Market (2031 projection)", True),
    ("Facial Serums", False),
    ("Global Vitamin C Market Size Report", True),
    ("Whiteleg Shrimp", False),
    ("Market forecast to 2030", True),
])
def test_looks_like_leaked_search_title(name, expected):
    assert cst.looks_like_leaked_search_title(name) is expected


@pytest.mark.parametrize("score,label", [(1.0, "Low"), (2.0, "Low"), (2.5, "Medium"), (3.4, "Medium"), (3.5, "High"), (5.0, "High")])
def test_porter_label_thresholds(score, label):
    assert cst.porter_label(score) == label


@pytest.mark.parametrize("share,label", [(10, "fragmented"), (24.9, "fragmented"), (25, "moderately concentrated"), (50, "moderately concentrated"), (50.1, "highly concentrated")])
def test_concentration_label_thresholds(share, label):
    assert cst.concentration_label(share) == label


# --------------------------------------------------------------------------- #
# Follow-up fixes: derivative filtering, scope lock, bps scaling, scenario
# spread, shared region ordering.
# --------------------------------------------------------------------------- #
def _load_fixture_full(slug: str) -> dict:
    return json.loads((FIXTURES_DIR / f"{slug}_broken.json").read_text(encoding="utf-8"))


def test_biostimulants_whole_market_derivatives_are_all_dropped():
    # Every biostimulants "derivative" is a whole-market estimate, so the
    # list legitimately ends up empty -- the report must handle that.
    fixed, _ = cst.reconcile(_load_fixture("agricultural-biostimulants-market"), "Agricultural Biostimulants Market")
    assert fixed["derivatives_pricing"]["derivatives"] == []


def test_poultry_parent_and_regional_derivatives_are_dropped():
    fixed, changes = cst.reconcile(_load_fixture("functional-poultry-feed-market"), "Functional Poultry Feed Market")
    names = [d["name"] for d in fixed["derivatives_pricing"]["derivatives"]]
    assert "Poultry Feed" not in names
    assert "Europe Poultry Feed" not in names
    assert "India Poultry Market" not in names
    assert "Poultry Probiotics" in names


@pytest.mark.parametrize("name,expected", [
    ("Poultry Feed", True),
    ("Europe Poultry Feed", True),
    ("Poultry Probiotics", False),
    ("Functional Poultry Feed", False),
])
def test_looks_like_parent_or_regional_market(name, expected):
    assert cst.looks_like_parent_or_regional_market(name, "Functional Poultry Feed Market") is expected


def test_shrimp_definition_drops_excluded_end_uses():
    data = _load_fixture_full("super-intensive-shrimp-farming-market")
    fixed, _ = cst.reconcile(data["fields"], data["market_name"], data["definition_inclusion_exclusion"])
    names = [a["name"] for a in fixed["end_use_industry"]["applications"]]
    assert not any("extensive" in n.lower() or "hatchery" in n.lower() for n in names)
    assert abs(sum(a["share_pct_of_market"] for a in fixed["end_use_industry"]["applications"]) - 100) < 0.5


def test_generative_ai_definition_keeps_core_segments():
    data = _load_fixture_full("generative-ai-in-agriculture-market")
    before = [g["name"] for g in data["fields"]["segmentation_grade_form"]["by_grade_or_type"]]
    fixed, _ = cst.reconcile(data["fields"], data["market_name"], data["definition_inclusion_exclusion"])
    after = [g["name"] for g in fixed["segmentation_grade_form"]["by_grade_or_type"]]
    assert after == before


def test_drivers_and_restraints_scale_together(broken_fields):
    slug, fields = broken_fields
    fixed, _ = cst.reconcile(fields, slug)
    dr = fixed["drivers_restraints"]
    d_total = sum(d["cagr_contribution_bps"] for d in dr["drivers"])
    r_total = sum(r["cagr_drag_bps"] for r in dr["restraints"])
    assert abs((d_total - r_total) - fixed["market_size"]["cagr_pct"] * 100) <= 2
    assert r_total <= d_total * cst._MAX_RESTRAINT_TO_DRIVER_RATIO + 1


def test_scenarios_have_a_meaningful_spread(broken_fields):
    slug, fields = broken_fields
    fixed, _ = cst.reconcile(fields, slug)
    sc, base_cagr = fixed["scenarios"], fixed["market_size"]["cagr_pct"]
    gap = max(cst._SCENARIO_MIN_GAP_PP, base_cagr * cst._SCENARIO_MIN_GAP_REL)
    assert sc["bull"]["cagr_pct"] >= base_cagr + gap - 0.01
    assert sc["bear"]["cagr_pct"] <= base_cagr - gap + 0.01


def test_region_order_is_shared_by_text_and_charts():
    sys.path.insert(0, str(ROOT / "AiAgentsFleet" / "report"))
    from narrative import ordered_regions
    regions = {"North America": {}, "Europe": {}, "Asia Pacific": {}, "Latin America": {}, "Middle East & Africa": {}}
    assert ordered_regions(regions)[0] == "Asia Pacific"

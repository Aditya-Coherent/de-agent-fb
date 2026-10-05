"""Desk Research fixes from the 2026-10-04 example audit.

- Disposable Medical Gloves matched "GCC Medical Gloves Market" (longer
  segmentation) instead of "Disposable Medical Gloves Market", so the whole
  global market was put in 6 Gulf countries.
- Olive Oil / Urea: the review loop drifted a grounded CAGR away from the
  cited one (6.3% -> 4.8%, 4.7% -> 3.2%).
"""
from __future__ import annotations

import pytest

from me_engine.generation import agents, db_source
from me_engine.generation.dynamic_skeleton import ensure_global_geography
from me_engine.generation.skeleton import REGIONS, Skeleton


@pytest.mark.parametrize("keyword,market,narrower", [
    ("GCC Medical Gloves Market", "Disposable Medical Gloves", True),
    ("Europe Olive Oil Market", "Olive Oil", True),
    ("India Vinegar Market", "Vinegar", True),
    ("India Vinegar Market", "India Vinegar", False),
    ("Disposable Medical Gloves Market", "Disposable Medical Gloves", False),
    ("Usage-Based Insurance Market", "Insurance", False),   # 'us' inside a word is not a country
])
def test_geography_narrower(keyword, market, narrower):
    assert db_source.geography_narrower(keyword, market) is narrower


def _row(news_id, keyword):
    return (news_id, keyword, "2031", "summary", "<ul></ul>", "desc")


def test_pick_best_report_prefers_exact_global_match_over_longer_gcc_report():
    rows = [_row(4116, "GCC Medical Gloves Market"), _row(217, "Disposable Medical Gloves Market")]
    best = db_source.pick_best_report("Disposable Medical Gloves", "disposable medical gloves", rows)
    assert best[0] == 217


def test_pick_best_report_returns_none_when_only_regional_reports_match():
    rows = [_row(4116, "GCC Medical Gloves Market")]
    assert db_source.pick_best_report("Medical Gloves", "medical gloves", rows) is None


def test_pick_best_report_keeps_longest_when_no_exact_match():
    rows = [_row(1, "Olive Oil Market Report Plus"), _row(2, "Olive Oil Packaging Market")]
    assert db_source.pick_best_report("Olive Oil", "olive oil", rows)[0] == 1


def _skeleton(regions):
    return Skeleton(market_name="X", geographies=[c for cs in regions.values() for c in cs],
                    regions=regions, nesting={"By Type": {"__root__": ["A", "B"]}}, priced_dimension="By Type")


def test_global_market_with_gcc_only_skeleton_gets_global_regions():
    sk = ensure_global_geography(_skeleton({"GCC": ["Saudi Arabia", "UAE"]}), "Disposable Medical Gloves")
    assert set(sk.regions) == set(REGIONS)
    assert "U.S." in sk.geographies


def test_regional_market_keeps_its_own_geography():
    sk = _skeleton({"GCC": ["Saudi Arabia", "UAE"]})
    assert ensure_global_geography(sk, "GCC Medical Gloves") is sk


def test_full_global_skeleton_is_untouched():
    sk = _skeleton({"North America": ["U.S."], "Europe": ["Germany"], "Asia Pacific": ["China"]})
    assert ensure_global_geography(sk, "Olive Oil") is sk


@pytest.mark.parametrize("text,expected", [
    ("Olive oil market size was valued at $25.2 billion in 2025 and is projected to grow from $26.5 billion in 2026 to $40.7 billion in 2033", 0.0632),
    ("The market is expected to grow at a CAGR of 4.7% through 2032.", 0.047),
    ("growing at 5.2% CAGR", 0.052),
    ("North America held 46.06% share in 2025", None),
])
def test_extract_cited_cagr(text, expected):
    got = agents._extract_cited_cagr(text)
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected, abs=0.0005)


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload

    def complete_json(self, system, user, *, retries=3):
        return self.payload


def test_market_size_node_resets_drifted_cagr_to_cited():
    sk = Skeleton(market_name="Olive Oil", geographies=["Spain"], regions={"Europe": ["Spain"]},
                  nesting={}, priced_dimension="")
    llm = _FakeLLM({"global_value_2025": 26500, "global_cagr": 0.048, "grounded": True,
                    "cited_figure": "projected to grow from $26.5 billion in 2026 to $40.7 billion in 2033",
                    "regions": {"Europe": {"value_share": 1.0, "cagr": 0.05}}})
    out = agents.market_size_node({"config": object(), "skeleton": sk, "llm": llm, "log": []})
    assert out["global_cagr"] == pytest.approx(0.0632, abs=0.0005)


def test_market_size_node_takes_cited_2026_figure_back_to_2025():
    sk = Skeleton(market_name="Disposable Medical Gloves", geographies=["U.S."], regions={"North America": ["U.S."]},
                  nesting={}, priced_dimension="")
    llm = _FakeLLM({"global_value_2025": 19000, "global_cagr": 0.10, "grounded": True,
                    "cited_figure": "USD 13.61 billion in 2026, CAGR 16.6% (2026-2034)",
                    "regions": {"North America": {"value_share": 1.0, "cagr": 0.1}}})
    out = agents.market_size_node({"config": object(), "skeleton": sk, "llm": llm, "log": []})
    # Base year is 2025: the cited 2026 value is taken back one year at the cited CAGR.
    assert out["global_cagr"] == pytest.approx(0.166)
    assert out["global_anchor_2025"] == pytest.approx(13610 / 1.166, rel=1e-3)


def test_cited_value_for_other_year_is_not_forced():
    assert agents._extract_cited_value_for_year("USD 25.2 billion in 2025", 2026) is None


def test_cited_2025_figure_is_used_directly():
    sk = Skeleton(market_name="Olive Oil", geographies=["Spain"], regions={"Europe": ["Spain"]},
                  nesting={}, priced_dimension="")
    llm = _FakeLLM({"global_value_2025": 30000, "global_cagr": 0.05, "grounded": True,
                    "cited_figure": "valued at $25.2 billion in 2025 and projected to grow from $26.5 billion in 2026 to $40.7 billion in 2033",
                    "regions": {"Europe": {"value_share": 1.0, "cagr": 0.05}}})
    out = agents.market_size_node({"config": object(), "skeleton": sk, "llm": llm, "log": []})
    assert out["global_anchor_2025"] == pytest.approx(25200)

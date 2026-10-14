"""Desk Research cited market-size figures (generation/cited_values.py).

Covers value extraction (units, years, forecasts), moving 2024/2026 figures to
2025, the outlier-robust median of cited values, and scope rejection. No
network or live LLM calls: texts are inline and the LLM is a fake.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from me_engine.generation import agents, cited_values
from me_engine.generation.cited_values import (
    check_scope,
    collect_cited_values,
    consolidate,
    extract_values,
)
from me_engine.generation.skeleton import Skeleton


# --------------------------------------------------------------------------- #
# extraction: units and years
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,value_mn,year", [
    ("The global television market was valued at USD 245.3 billion in 2025.", 245_300.0, 2025),
    ("Television market size reached $64.2 Bn in 2024.", 64_200.0, 2024),
    ("The olive oil market stood at US$ 13,590 million (2026).", 13_590.0, 2026),
    ("The semiconductor market hit USD 1.2 trillion in 2025.", 1_200_000.0, 2025),
    ("In 2025, the global smartwatch market was valued at $38.5B.", 38_500.0, 2025),
    ("The watch market was worth 81.3 billion USD in 2025.", 81_300.0, 2025),
])
def test_extract_values_units_and_year(text, value_mn, year):
    vals = extract_values(text, "src")
    assert len(vals) == 1
    assert vals[0].value_mn == pytest.approx(value_mn)
    assert vals[0].year == year


def test_forecast_and_cagr_period_years_are_not_the_value_year():
    text = ("Global Urea Fertilizers Market is estimated to be valued at USD 32.71 Bn in 2026 and is "
            "expected to reach USD 45.11 Bn by 2033, exhibiting a CAGR of 4.7% from 2026 to 2033.")
    first, second = extract_values(text, "db")
    assert (first.value_mn, first.year, first.reason) == (32_710.0, 2026, "")
    assert second.year == 2033 and "forecast" in second.reason


def test_incremental_growth_is_not_a_market_size():
    vals = extract_values("The global television market is set to grow by USD 73.1 billion in 2025.", "src")
    assert vals[0].reason.startswith("incremental")


def test_year_range_before_value_is_ignored():
    vals = extract_values("Television Market Report 2025-2033: market size USD 64 billion", "src")
    assert vals[0].year is None


def test_2024_and_2026_figures_move_to_2025_at_the_cited_cagr():
    anchor = collect_cited_values([
        ("https://a-research.example/report", "The global television market was valued at USD 60 billion in 2024, growing at a CAGR of 5%."),
    ], "Televisions")
    assert anchor.value_2025_mn == pytest.approx(63_000.0)
    assert anchor.accepted[0].cagr_basis == "cited"

    anchor = collect_cited_values([
        ("CMI report DB: Urea Fertilizers Market", "Global Urea Fertilizers Market is estimated at USD 32.71 Bn in 2026 and is expected to "
               "reach USD 45.11 Bn by 2033, exhibiting a CAGR of 4.7%."),
    ], "Urea Fertilizer")
    assert anchor.value_2025_mn == pytest.approx(32_710 / 1.047, rel=1e-3)


def test_cagr_implied_by_dated_values_then_default():
    anchor = collect_cited_values([
        ("https://a-research.example/report", "The global television market is USD 50 billion in 2026 and will reach USD 60.5 billion in 2028."),
    ], "Televisions")
    assert anchor.accepted[0].cagr_used == pytest.approx(0.1, abs=1e-6)

    anchor = collect_cited_values([("https://a-research.example/report", "The global television market was USD 50 billion in 2026.")], "Televisions")
    assert anchor.accepted[0].cagr_basis == "default"
    assert anchor.value_2025_mn == pytest.approx(50_000 / (1 + cited_values.DEFAULT_CAGR), rel=1e-3)


def test_values_outside_2024_2026_or_without_year_are_rejected():
    anchor = collect_cited_values([
        ("https://a-research.example/report", "The global television market was USD 40 billion in 2020."),
        ("https://b-research.example/report", "The global television market is worth USD 64 billion."),
    ], "Televisions")
    assert not anchor.grounded
    reasons = {r.source[8]: r.reason for r in anchor.rejected}
    assert "outside" in reasons["a"] and "no year" in reasons["b"]


# --------------------------------------------------------------------------- #
# median of cited values
# --------------------------------------------------------------------------- #
def test_consolidate_drops_outliers_beyond_2_5x_and_takes_median():
    anchor, kept, dropped = consolidate([63_900.0, 64_810.0, 7_800.0, 200_000.0, 66_000.0])
    assert sorted(dropped) == [2, 3]
    assert anchor == pytest.approx(64_810.0)


def test_consolidate_single_value_and_empty():
    assert consolidate([5_000.0])[0] == 5_000.0
    assert consolidate([])[0] is None


def test_median_of_cited_values_across_sources():
    anchor = collect_cited_values([
        ("CMI report DB: Television", "The global television market was valued at USD 63.9 billion in 2025."),
        ("https://a-research.example/report/tv", "Television Market | The TV market size reached USD 64.81 billion in 2025."),
        ("https://b-research.example/report/tv", "The global television market is valued at USD 300 billion in 2025."),
    ], "Televisions")
    assert anchor.method == "median of 2 cited values"
    assert anchor.value_2025_mn == pytest.approx((63_900 + 64_810) / 2)
    assert any("outlier" in r.reason for r in anchor.rejected)


def test_one_figure_per_source_and_syndicated_repeats_count_once():
    text = ("The global television market was valued at USD 62 billion in 2024 and "
            "USD 64 billion in 2025.")
    anchor = collect_cited_values([
        ("https://a-research.example/report", text),
        ("https://d-research.example/report", "The global television market was valued at USD 64 billion in 2025."),
        ("https://c-research.example/report", "The global TV market was valued at USD 70 billion in 2025."),
    ], "Televisions")
    used = {v.source: v.value_mn for v in anchor.accepted}
    assert used == {"https://a-research.example/report": 64_000.0, "https://c-research.example/report": 70_000.0}
    assert anchor.value_2025_mn == pytest.approx(67_000.0)


# --------------------------------------------------------------------------- #
# scope checks
# --------------------------------------------------------------------------- #
def test_narrower_surgical_gloves_figure_is_rejected():
    anchor = collect_cited_values([
        ("https://a-research.example/report", "The global surgical gloves market reached USD 3.1 billion in 2025."),
        ("https://b-research.example/report", "The disposable medical gloves market was valued at USD 13.8 billion in 2025."),
    ], "Disposable Medical Gloves")
    assert anchor.value_2025_mn == pytest.approx(13_800.0)
    rejected = [r for r in anchor.rejected if r.reason.startswith("scope:")]
    assert len(rejected) == 1 and "surgical" in rejected[0].reason


@pytest.mark.parametrize("subject,market,ok", [
    ("TV", "Televisions", True),
    ("smart TV", "Televisions", False),
    ("smart watch", "Smartwatches", True),
    ("extra virgin olive oil", "Olive Oil", False),
    ("urea", "Urea Fertilizer", False),           # broader: lacks the head noun
    ("medical gloves", "Disposable Medical Gloves", True),
    ("North America television", "Televisions", False),
    ("", "Televisions", False),
])
def test_keyword_scope_rules(subject, market, ok):
    assert check_scope(subject, market).ok is ok


class _ScopeLLM:
    """Fake LLM scope judge: anything mentioning 'fertilizer' is narrower than total urea."""

    def __init__(self):
        self.calls = 0

    def complete_json(self, system, user, *, retries=3):
        import json
        self.calls += 1
        items = json.loads(user.split("Candidates: ", 1)[1].split("\n\nReturn JSON", 1)[0])
        return {"verdicts": [
            {"id": it["id"], "scope": "narrower" if "fertilizer" in it["subject"].lower() else "same",
             "reason": "fertilizer only" if "fertilizer" in it["subject"].lower() else "total urea"}
            for it in items
        ]}


def test_urea_fertilizer_figure_rejected_against_total_urea_definition():
    llm = _ScopeLLM()
    anchor = collect_cited_values([
        ("CMI report DB: Urea Fertilizers", "Global Urea Fertilizers Market is estimated at USD 32.71 Bn in 2025."),
        ("https://a-research.example/report", "The global urea market was valued at USD 73.5 billion in 2025."),
    ], "Urea Fertilizer", definition="Total urea market, fertilizer plus industrial uses", llm=llm)
    assert llm.calls == 1
    assert anchor.value_2025_mn == pytest.approx(73_500.0)
    rejected = [r for r in anchor.rejected if r.reason.startswith("scope:")]
    assert rejected and rejected[0].source.startswith("CMI") and "narrower" in rejected[0].reason


def test_llm_scope_failure_falls_back_to_keyword_rules():
    class _Broken:
        def complete_json(self, *a, **k):
            raise RuntimeError("down")

    anchor = collect_cited_values([
        ("https://a-research.example/report", "The global surgical gloves market reached USD 3.1 billion in 2025."),
    ], "Disposable Medical Gloves", definition="all medical gloves", llm=_Broken())
    assert not anchor.grounded


# --------------------------------------------------------------------------- #
# wiring into the Desk Research nodes
# --------------------------------------------------------------------------- #
class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.user = ""

    def complete_json(self, system, user, *, retries=3):
        self.user = user
        return self.payload


def _tv_skeleton():
    return Skeleton(market_name="Televisions", geographies=["U.S."], regions={"North America": ["U.S."]},
                    nesting={}, priced_dimension="")


def test_structure_node_collects_cited_values_and_logs_scope_rejections(monkeypatch):
    monkeypatch.setattr(agents, "gather", lambda *a, **k: ["generic snippet about televisions and more text"])
    monkeypatch.setattr(agents, "gather_with_sources", lambda *a, **k: [
        {"text": "TV Market: The global TV market was valued at USD 63.9 billion in 2025.",
         "url": "https://a-research.example/report/tv", "domain": "a.example"},
        {"text": "Smart TV Market: The smart TV market was valued at USD 200 billion in 2025.",
         "url": "https://b-research.example/report/smart-tv", "domain": "b.example"},
        {"text": "Television Market Size, Share and Trends Report",
         "url": "https://c-research.example/report/tv", "domain": "c.example"},
    ])
    pages = []
    monkeypatch.setattr(agents, "fetch_page_text", lambda url: pages.append(url) or
                        ("The global television market reached USD 64.81 billion in 2025." if "c-research" in url else ""))
    cfg = SimpleNamespace(neon_url=None, enable_web=True, is_online=False, fetch_pages=True)
    state = {"config": cfg, "skeleton": _tv_skeleton(), "llm": None, "log": []}
    out = agents.structure_node(state)
    assert out["cited_anchor"]["value_2025_mn"] == pytest.approx(64_355.0)
    assert pages == ["https://c-research.example/report/tv"]   # only results that name the market are fetched
    assert any("Rejected cited figure USD 200 billion" in line and "smart" in line for line in out["log"])


def test_market_size_node_anchors_to_cited_consensus():
    cited = collect_cited_values([
        ("https://a-research.example/report", "The global TV market was valued at USD 63.9 billion in 2025."),
        ("https://b-research.example/report", "The global television market reached USD 64.81 billion in 2025."),
    ], "Televisions").to_dict()
    llm = _FakeLLM({"global_value_2025": 7_800, "global_cagr": 0.03, "grounded": False, "cited_figure": "",
                    "regions": {"North America": {"value_share": 1.0, "cagr": 0.03}}})
    out = agents.market_size_node({"config": object(), "skeleton": _tv_skeleton(), "llm": llm, "log": [],
                                   "cited_anchor": cited})
    assert out["global_anchor_2025"] == pytest.approx(64_355.0)
    assert out["market_size_grounded"] is True
    assert "https://a-research.example/report" in out["market_size_source"]
    assert "CONSENSUS ANCHOR" in llm.user and "64,355" in llm.user


def test_market_size_node_does_not_trust_an_unverifiable_llm_citation():
    llm = _FakeLLM({"global_value_2025": 70_000, "global_cagr": 0.03, "grounded": True,
                    "cited_figure": "USD 70 billion in 2025 (some report)",
                    "regions": {"North America": {"value_share": 1.0, "cagr": 0.03}}})
    out = agents.market_size_node({"config": object(), "skeleton": _tv_skeleton(), "llm": llm, "log": [],
                                   "web_snippets": ["Television demand is rising in Asia."]})
    assert out["market_size_grounded"] is False


def test_market_size_node_accepts_an_llm_citation_found_in_the_evidence():
    llm = _FakeLLM({"global_value_2025": 64_000, "global_cagr": 0.03, "grounded": True,
                    "cited_figure": "USD 64 billion",
                    "regions": {"North America": {"value_share": 1.0, "cagr": 0.03}}})
    out = agents.market_size_node({"config": object(), "skeleton": _tv_skeleton(), "llm": llm, "log": [],
                                   "web_snippets": ["The TV market is worth USD 64 billion."]})
    assert out["market_size_grounded"] is True

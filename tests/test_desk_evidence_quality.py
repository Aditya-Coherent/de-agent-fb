"""Desk Research evidence-quality fixes, steps 1-7 (all LLM, DB and web calls are fakes).

1 page title / URL slug as the subject       5 units x ASP size check
2 rejected figures masked + re-ask           6 source tiers
3 --scope drives search and DB lookup        7 weak-consensus anchor choice
4 DB title check + generic skeleton fallback
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from me_engine.generation import agents, db_source, dynamic_skeleton
from me_engine.generation.cited_values import (
    check_scope,
    collect_cited_values,
    estimate_size_range,
    extract_values,
    scope_search_name,
    source_tier,
)
from me_engine.generation.db_source import DBEvidence, pick_best_report, title_narrower
from me_engine.generation.quality import compute_demand_quality_flags
from me_engine.generation.skeleton import Skeleton

R1 = "https://a-research.example/report/x"
R2 = "https://b-research.example/report/x"
R3 = "https://c-research.example/report/x"


class _LLM:
    """Fake LLM: returns queued payloads in order (the last one repeats)."""

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.users: list[str] = []

    def complete_json(self, system, user, *, retries=3):
        self.users.append(user)
        return self.payloads.pop(0) if len(self.payloads) > 1 else self.payloads[0]


# --------------------------------------------------------------------------- #
# 1. page title / URL slug as the subject
# --------------------------------------------------------------------------- #
def test_value_without_market_in_sentence_takes_page_title_subject():
    vals = extract_values("Industry Outlook [2034]: The size was valued at USD 44.28 billion in 2025.",
                          "https://www.fortunebusinessinsights.com/smartwatch-market-106625",
                          title="Smartwatch Market Size, Share & Industry Analysis")
    assert vals[0].subject == "Smartwatch" and vals[0].subject_basis == "page title"
    assert check_scope(vals[0].subject, "Smartwatches").ok


def test_url_slug_is_the_fallback_subject():
    vals = extract_values("Market Size, 2025 USD 6.97 Billion",
                          "https://www.valuemarketresearch.com/report/medical-gloves-market")
    assert (vals[0].subject, vals[0].subject_basis) == ("medical gloves", "url slug")
    assert vals[0].year == 2025


def test_title_subject_still_goes_through_scope_check():
    anchor = collect_cited_values([
        (R1, "Market Size, 2025 USD 16.6 Billion", "Educational Television Market Size Report"),
    ], "Televisions")
    assert not anchor.grounded
    assert "educational" in anchor.rejected[0].reason


def test_segment_value_on_a_matching_page_is_not_the_market_total():
    vals = extract_values("The hardware segment accounted for USD 2.8 billion in 2025.", R1,
                          title="Disposable Medical Gloves Market Report")
    assert "part of the market" in vals[0].reason


def test_exact_match_pages_are_accepted_with_title_subject():
    anchor = collect_cited_values([
        ("https://www.fortunebusinessinsights.com/smartwatch-market-106625",
         "Industry Outlook [2034]: valued at USD 44.28 billion in 2025.", "Smartwatch Market Size"),
        ("https://www.syndicatemarketresearch.com/market-analysis/smartwatches-market.html",
         "Market Size, 2025 USD 34.73 Billion", ""),
    ], "Smartwatches")
    assert anchor.value_2025_mn == pytest.approx((44_280 + 34_730) / 2)


def test_title_subject_flag_off_keeps_old_behaviour():
    anchor = collect_cited_values([(R1, "Market Size, 2025 USD 34.73 Billion", "Smartwatches Market")],
                                  "Smartwatches", title_subject=False)
    assert not anchor.grounded and "no market named" in anchor.rejected[0].reason


# --------------------------------------------------------------------------- #
# 2. rejected figures never reach the market-size LLM
# --------------------------------------------------------------------------- #
def _sk(name="Urea", regions=None):
    regions = regions or {"Asia Pacific": ["China"]}
    return Skeleton(market_name=name, geographies=[c for cs in regions.values() for c in cs],
                    regions=regions, nesting={}, priced_dimension="")


def _web(monkeypatch, results, db=None):
    monkeypatch.setattr(agents, "gather", lambda *a, **k: [])
    calls = []
    monkeypatch.setattr(agents, "gather_with_sources", lambda queries, **k: calls.append(queries) or results)
    monkeypatch.setattr(agents, "fetch_page_text", lambda url: "")
    monkeypatch.setattr(agents, "fetch_market_evidence", lambda url, name: calls.append(["db", name]) or db)
    return calls


def test_rejected_figures_are_masked_in_the_evidence(monkeypatch):
    db = DBEvidence(news_id=1, keyword="Urea Fertilizers Market", forecast_year="", summary_text="",
                    segmentation_tree=[], raw_narrative="The global urea fertilizers market is valued at USD 32.71 Bn in 2025.")
    _web(monkeypatch, [{"text": "Urea Market: The global urea market was valued at USD 73.5 billion in 2025.",
                        "url": R1, "domain": "a-research.example", "title": "Urea Market"}], db=db)
    cfg = SimpleNamespace(neon_url="x", enable_web=True, is_online=False, fetch_pages=False)
    out = agents.structure_node({"config": cfg, "skeleton": _sk(), "llm": None, "log": []})
    assert out["cited_anchor"]["value_2025_mn"] == pytest.approx(73_500)
    assert "32.71 Bn" not in out["db_evidence"].raw_narrative
    assert "[figure removed: different scope]" in out["db_evidence"].raw_narrative
    assert any("73.5 billion" in s for s in out["web_snippets"])          # accepted figure stays
    brief = agents._evidence_brief({**out})
    assert "32.71" not in brief


def _size_state(llm, rejected_value=32_710.0):
    cited = {"value_2025_mn": None, "accepted": [], "rejected": [
        {"raw_text": "USD 32.71 Bn", "value_mn": rejected_value, "value_2025_mn": rejected_value / 1.047,
         "reason": "scope: narrower: fertilizer only", "source": "CMI report DB: Urea Fertilizers Market"}]}
    return {"config": object(), "skeleton": _sk(), "llm": llm, "log": [], "cited_anchor": cited}


def _answer(value):
    return {"global_value_2025": value, "global_cagr": 0.04, "grounded": False, "cited_figure": "",
            "regions": {"Asia Pacific": {"value_share": 1.0, "cagr": 0.04}}}


def test_answer_equal_to_a_rejected_figure_is_discarded_and_reasked():
    llm = _LLM(_answer(32_710), _answer(70_000))
    state = _size_state(llm)
    out = agents.market_size_node(state)
    assert out["global_anchor_2025"] == pytest.approx(70_000)
    assert len(llm.users) == 2 and "REJECTED figure" in llm.users[1]
    assert any("Discarded an answer of $32,710M" in line for line in state["log"])


def test_answer_matching_the_2025_moved_rejected_value_within_1pct_is_also_caught():
    llm = _LLM(_answer(31_400), _answer(72_000))
    out = agents.market_size_node(_size_state(llm))
    assert out["global_anchor_2025"] == pytest.approx(72_000)


def test_reask_is_bounded():
    llm = _LLM(_answer(32_710))
    agents.market_size_node(_size_state(llm))
    assert len(llm.users) == 1 + agents._MAX_REASKS


# --------------------------------------------------------------------------- #
# 3. --scope drives the web queries and the DB lookup
# --------------------------------------------------------------------------- #
def test_scope_search_name_from_definition():
    llm = _LLM({"search_name": "Urea Market"})
    assert scope_search_name("Urea Fertilizer", "Total urea, fertilizer + industrial uses", llm) == "Urea"
    assert scope_search_name("Urea Fertilizer", "", llm) == "Urea Fertilizer"
    assert scope_search_name("Urea Fertilizer", "x", _LLM({"search_name": ""})) == "Urea Fertilizer"


def test_structure_node_searches_web_and_db_for_the_scope_name(monkeypatch):
    calls = _web(monkeypatch, [])
    cfg = SimpleNamespace(neon_url="x", enable_web=True, is_online=False, fetch_pages=False)
    agents.structure_node({"config": cfg, "skeleton": _sk("Urea Fertilizer"), "llm": None, "log": [],
                           "market_search_name": "Urea", "market_definition": "total urea"})
    assert ["db", "Urea"] in calls
    queries = [q for batch in calls if batch[0] != "db" for q in batch]
    assert '"urea market" size 2025 billion' in queries
    assert not any("fertilizer" in q.lower() for q in queries)


def test_load_skeleton_from_db_looks_up_the_search_name(monkeypatch):
    seen = []
    monkeypatch.setattr(dynamic_skeleton, "fetch_market_evidence", lambda url, name: seen.append(name) or None)
    cfg = SimpleNamespace(is_online=False, neon_url="x")
    sk, ev = dynamic_skeleton.load_skeleton_from_db("Urea Fertilizer", cfg, search_name="Urea", generic_fallback=True)
    assert seen == ["Urea"] and ev is None and sk.market_name == "Urea Fertilizer"


# --------------------------------------------------------------------------- #
# 4. DB report title check + generic skeleton fallback
# --------------------------------------------------------------------------- #
def _row(news_id, keyword):
    return (news_id, keyword, "2031", "summary", "<ul></ul>", "desc")


def test_report_matched_only_through_newssubject_is_rejected():
    # 'Smartphones' is a segment of the eSIM report (newsSubject), not its subject.
    assert pick_best_report("Smartphones", "smartphones", [_row(4290, "eSIM Market")]) is None


def test_narrower_product_report_is_rejected():
    rows = [_row(1, "Curved Televisions Market"), _row(2, "Television Market")]
    assert pick_best_report("Televisions", "televisions", rows)[0] == 2
    assert pick_best_report("Televisions", "televisions", rows[:1]) is None


def test_title_narrower_for_semantic_hits():
    assert title_narrower("Curved Televisions Market", "Televisions")
    assert not title_narrower("Television Market", "Televisions")
    assert not title_narrower("Electric Vehicle Charging Infrastructure Market", "EV Charging")


def test_generic_skeleton_fallback_when_db_match_is_rejected(monkeypatch):
    monkeypatch.setattr(dynamic_skeleton, "fetch_market_evidence", lambda url, name: None)
    cfg = SimpleNamespace(is_online=False, neon_url="x")
    assert dynamic_skeleton.load_skeleton_from_db("Smartphones", cfg) == (None, None)   # old contract kept
    sk, ev = dynamic_skeleton.load_skeleton_from_db("Smartphones", cfg, generic_fallback=True)
    assert ev is None and set(sk.regions) == set(dynamic_skeleton.REGIONS)
    assert sk.nesting == {"By Type": {"__root__": ["Smartphones"]}}


def test_generic_skeleton_uses_llm_dimensions():
    llm = _LLM({"dimensions": [{"title": "By Operating System", "segments": ["Android", "iOS", "Others"]},
                               {"title": "Price Band", "segments": ["Budget", "Premium"]}]})
    sk = dynamic_skeleton.build_generic_skeleton("Smartphones", llm)
    assert list(sk.nesting) == ["By Operating System", "By Price Band"]
    assert sk.priced_dimension == "By Operating System"


# --------------------------------------------------------------------------- #
# 5. units x ASP size check
# --------------------------------------------------------------------------- #
def test_size_range_from_units_and_price():
    rng = estimate_size_range("Televisions", "", _LLM({"unit": "TV set", "units_low": 200e6, "units_high": 220e6,
                                                       "asp_low_usd": 300, "asp_high_usd": 450}))
    assert rng["low_mn"] == pytest.approx(60_000) and rng["high_mn"] == pytest.approx(99_000)


@pytest.mark.parametrize("payload", [
    {"units_low": "many", "units_high": 1, "asp_low_usd": 1, "asp_high_usd": 1},
    {"units_low": 0, "units_high": 1e6, "asp_low_usd": 10, "asp_high_usd": 20},
    {"units_low": 1, "units_high": 1e9, "asp_low_usd": 1, "asp_high_usd": 100},   # >1000x span
    {},
])
def test_size_range_nonsense_is_skipped(payload):
    assert estimate_size_range("X", "", _LLM(payload)) is None


def test_size_range_llm_failure_is_skipped():
    class _Broken:
        def complete_json(self, *a, **k):
            raise RuntimeError("down")

    assert estimate_size_range("X", "", _Broken()) is None


def test_cited_values_far_outside_the_size_range_are_rejected():
    rng = {"low_mn": 60_000, "high_mn": 100_000}
    anchor = collect_cited_values([
        (R1, "The global television market was valued at USD 64 billion in 2025."),
        (R2, "The global television market was valued at USD 430 billion in 2025."),
        (R3, "The global television market was valued at USD 10 billion in 2025."),
    ], "Televisions", size_range=rng)
    assert anchor.value_2025_mn == pytest.approx(64_000)
    assert sorted(r.value_mn for r in anchor.rejected if r.reason.startswith("magnitude")) == [10_000, 430_000]


def test_final_value_outside_size_range_is_flagged():
    state = {"global_anchor_2025": 7_800.0, "cited_anchor": {"size_range": {"low_mn": 60_000, "high_mn": 100_000}}}
    assert "final_value_outside_size_range" in [f["code"] for f in compute_demand_quality_flags(state)]
    state["global_anchor_2025"] = 70_000.0
    assert "final_value_outside_size_range" not in [f["code"] for f in compute_demand_quality_flags(state)]


# --------------------------------------------------------------------------- #
# 6. source tiers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("source,tier", [
    ("CMI report DB: Urea Fertilizers Market", 1),
    ("https://www.grandviewresearch.com/industry-analysis/television-market-report", 1),
    ("https://www.imarcgroup.com/report/en/watch-market", 1),
    ("https://www.mordorintelligence.com/industry-reports/smartphones-market", 1),
    ("https://www.linkedin.com/pulse/urea-market-booming", 2),
    ("https://www.einnews.com/pr_news/841835066/disposable-medical-gloves-market", 2),
    ("https://www.pinterest.com/pin/argan-oil-market", 2),
    ("https://www.tunisiadailysun.com/article/921246989-olive-oil-market", 2),
    ("https://www.globenewswire.com/news-release/2025/x-market-report", 2),
    ("web snippet", 2),
])
def test_source_tier(source, tier):
    assert source_tier(source) == tier


def test_tier2_only_corroborates_when_two_tier1_values_exist():
    anchor = collect_cited_values([
        (R1, "The global olive oil market was valued at USD 15.7 billion in 2025."),
        (R2, "The global olive oil market was valued at USD 18.7 billion in 2025."),
        ("https://www.linkedin.com/pulse/olive", "The global olive oil market was valued at USD 30 billion in 2025."),
    ], "Olive Oil")
    assert anchor.value_2025_mn == pytest.approx(17_200)
    assert [c.source for c in anchor.corroborating] == ["https://www.linkedin.com/pulse/olive"]


def test_tier2_joins_a_single_tier1_value():
    anchor = collect_cited_values([
        (R1, "The global olive oil market was valued at USD 15 billion in 2025."),
        ("https://www.linkedin.com/pulse/olive", "The global olive oil market was valued at USD 17 billion in 2025."),
    ], "Olive Oil")
    assert anchor.value_2025_mn == pytest.approx(16_000)


def test_tier2_is_never_the_sole_anchor():
    anchor = collect_cited_values([
        ("https://www.linkedin.com/pulse/olive", "The global olive oil market was valued at USD 17 billion in 2025."),
        ("https://www.einnews.com/pr_news/1/olive", "The global olive oil market was valued at USD 18 billion in 2025."),
    ], "Olive Oil")
    assert not anchor.grounded and len(anchor.corroborating) == 2


# --------------------------------------------------------------------------- #
# 7. weak consensus
# --------------------------------------------------------------------------- #
_DISPUTED = [
    (R1, "The global television market was valued at USD 165 billion in 2025."),
    (R2, "The global television market was valued at USD 393 billion in 2025."),
]


def test_disputed_values_anchor_nearest_size_range_middle():
    anchor = collect_cited_values(_DISPUTED, "Televisions", size_range={"low_mn": 60_000, "high_mn": 150_000},
                                  weak_consensus=True)
    assert anchor.disputed and anchor.value_2025_mn == pytest.approx(165_000)
    assert anchor.method.startswith("disputed: value closest")


def test_disputed_values_without_range_use_the_cmi_db_figure():
    anchor = collect_cited_values(
        _DISPUTED + [("CMI report DB: Television Market", "The global television market was valued at USD 380 billion in 2025.")],
        "Televisions", weak_consensus=True)
    assert anchor.disputed and anchor.value_2025_mn == pytest.approx(380_000)


def test_disputed_values_otherwise_use_the_median():
    anchor = collect_cited_values(_DISPUTED, "Televisions", weak_consensus=True)
    assert anchor.disputed and anchor.value_2025_mn == pytest.approx((165_000 + 393_000) / 2)


def test_single_value_is_disputed_and_flagged():
    anchor = collect_cited_values(_DISPUTED[:1], "Televisions")
    assert anchor.disputed
    flags = compute_demand_quality_flags({"cited_anchor": anchor.to_dict()})
    assert "cited_values_disputed" in [f["code"] for f in flags]


def test_agreeing_values_are_not_disputed():
    anchor = collect_cited_values([
        (R1, "The global television market was valued at USD 64 billion in 2025."),
        (R2, "The global television market was valued at USD 66 billion in 2025."),
    ], "Televisions")
    assert not anchor.disputed
    assert json.dumps(anchor.to_dict())  # serialisable for the state dump


def test_weak_consensus_flag_off_keeps_median():
    anchor = collect_cited_values(_DISPUTED, "Televisions", size_range={"low_mn": 60_000, "high_mn": 150_000},
                                  weak_consensus=False)
    assert anchor.value_2025_mn == pytest.approx((165_000 + 393_000) / 2)


def test_weak_consensus_is_off_by_default():
    # Held-out evaluation (2026-10-09): median error 14.2% with it on vs 6.9% off.
    from me_engine.generation.config import GenConfig
    assert GenConfig.__dataclass_fields__["weak_consensus"].default is False
    anchor = collect_cited_values(_DISPUTED, "Televisions", size_range={"low_mn": 60_000, "high_mn": 150_000})
    assert anchor.disputed and anchor.value_2025_mn == pytest.approx((165_000 + 393_000) / 2)


def test_sentence_break_without_space_is_not_part_of_the_subject():
    vals = extract_values("growing to USD 926.84 Billion by 2030.Cloud Computing market was USD 750 billion in 2025.",
                          R1)
    assert vals[-1].subject == "Cloud Computing"


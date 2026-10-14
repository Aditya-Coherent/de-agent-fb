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
    ("https://www.imarcgroup.com/olive-oil-market", 1),          # no report path, still established
    ("https://www.statista.com/outlook/cmo/food/oils-fats/olive-oil", 1),
    ("https://www.mordorintelligence.com/industry-reports/smartphones-market", 1),
    ("https://www.gii.co.jp/report/tbrc1668667-urea-global-market-report.html", 1),  # reseller
    ("https://www.fao.org/statistics/olive-oil", 1),             # official
    ("https://market.us/report/olive-oil-market/", 2),           # other report page
    ("https://dataintelo.com/report/global-mattresses-market", 2),
    ("https://www.marketresearch.biz/report/olive-oil-market/", 2),
    ("https://www.linkedin.com/pulse/urea-market-booming", 3),
    ("https://www.einnews.com/pr_news/841835066/disposable-medical-gloves-market", 3),
    ("https://www.pinterest.com/pin/argan-oil-market", 3),
    ("https://www.tunisiadailysun.com/article/921246989-olive-oil-market", 3),
    ("https://www.globenewswire.com/news-release/2025/x-market-report", 3),
    ("web snippet", 3),
])
def test_source_tier(source, tier):
    assert source_tier(source) == tier


def test_two_pages_of_one_site_count_once():
    anchor = collect_cited_values([
        ("https://dataintelo.com/report/the-mattress-market", "The mattress market was valued at USD 28.5 billion in 2025."),
        ("https://dataintelo.com/report/global-mattresses-market", "Global mattresses market valued at $30.8 billion in 2025."),
        ("https://www.grandviewresearch.com/industry-analysis/mattress-market",
         "The global mattress market size was valued at USD 49.2 billion in 2025."),
    ], "Mattresses")
    assert len(anchor.accepted) == 2
    assert any("same source" in r.reason for r in anchor.rejected if "dataintelo" in r.source)


def test_repost_naming_the_publisher_counts_as_that_publisher():
    anchor = collect_cited_values([
        ("https://www.imarcgroup.com/corrugated-box-market",
         "The global corrugated boxes market size reached USD 212.3 billion in 2025."),
        ("https://www.linkedin.com/pulse/corrugated-boxes-market",
         "According to IMARC Group, the global corrugated boxes market was USD 210.1 billion in 2025."),
        ("https://www.grandviewresearch.com/industry-analysis/corrugated-boxes-market",
         "The global corrugated boxes market was valued at USD 195.6 billion in 2025."),
    ], "Corrugated Boxes")
    assert sorted(v.source.split("/")[2] for v in anchor.accepted) == ["www.grandviewresearch.com", "www.imarcgroup.com"]


def test_report_pages_anchor_and_social_posts_only_corroborate():
    anchor = collect_cited_values([
        ("https://www.grandviewresearch.com/industry-analysis/x-market", "The global orthopedic implants market was USD 50 billion in 2025."),
        ("https://www.imarcgroup.com/x-market", "The global orthopedic implants market was USD 58 billion in 2025."),
        ("https://dataintelo.com/report/x", "The global orthopedic implants market was USD 54 billion in 2025."),
        ("https://www.linkedin.com/pulse/x", "The global orthopedic implants market was USD 90 billion in 2025."),
    ], "Orthopedic Implants")
    assert anchor.value_2025_mn == pytest.approx(54_000)
    assert [c.source for c in anchor.corroborating] == ["https://www.linkedin.com/pulse/x"]
    assert anchor.confidence == "A" and anchor.low_mn == 50_000 and anchor.high_mn == 58_000


def test_social_post_does_not_move_a_single_report_figure():
    anchor = collect_cited_values([
        ("https://www.metastatinsight.com/press-details/air-compressor",
         "Global Air Compressors market size was USD 19,188.24 million in 2025."),
        ("https://www.linkedin.com/pulse/air-compressor-market", "Air Compressor Market size was valued at USD 44.53 Billion in 2025."),
    ], "Air Compressors")
    assert anchor.value_2025_mn == pytest.approx(19_188.2, rel=1e-3)
    assert anchor.confidence == "C"


def test_confidence_grades():
    strong = collect_cited_values([
        ("https://www.grandviewresearch.com/a", "The global olive oil market was USD 15.7 billion in 2025."),
        ("https://www.imarcgroup.com/b", "The global olive oil market was USD 16.5 billion in 2025."),
        ("https://www.straitsresearch.com/c", "The global olive oil market was USD 18.0 billion in 2025."),
    ], "Olive Oil")
    assert strong.confidence == "A"
    many = collect_cited_values([
        (f"https://www.{d}.com/olive-oil", f"The global olive oil market was USD {v} billion in 2025.")
        for d, v in [("grandviewresearch", 36.8), ("imarcgroup", 38.2), ("straitsresearch", 38.4),
                     ("mordorintelligence", 35.6), ("fortunebusinessinsights", 36.0), ("futuremarketreport", 16.9)]
    ], "Olive Oil")
    assert many.confidence == "A" and not many.disputed   # one stray low figure among six agreeing
    weak = collect_cited_values([(R1, "The global olive oil market was USD 15.7 billion in 2025.")], "Olive Oil")
    assert weak.confidence == "C"
    assert "low_confidence_citation" in [f["code"] for f in compute_demand_quality_flags({"cited_anchor": weak.to_dict()})]


def test_segment_figure_in_a_sentence_naming_the_market_is_not_the_total():
    text = ("The online segment of the global smartwatch market accounted for USD 9.5 billion in 2025. "
            "The global smartwatch market was valued at USD 38.2 billion in 2024.")
    vals = extract_values(text, R1)
    assert "part of the market" in vals[0].reason and not vals[1].reason
    anchor = collect_cited_values([(R1, text)], "Smartwatches")
    assert anchor.accepted[0].value_mn == pytest.approx(38_200)


def test_total_after_a_segment_heading_still_counts():
    vals = extract_values("Laptops Market Segment Analysis : By Type , Screen Size: The market size of Laptops "
                          "Market in 2025 was US $ 134.9 Bn.", R1)
    assert vals and not vals[0].reason


def test_cagr_comes_from_the_figures_own_sentence_not_a_segment():
    text = ("The global olive oil market was USD 15.0 billion in 2024. The extra virgin type was USD 3.0 billion "
            "in 2024 and is expected to reach USD 25.0 billion by 2032.")
    anchor = collect_cited_values([(R1, text)], "Olive Oil")
    # the 30%/yr implied by the segment's own 3.0 -> 25.0 must not move the total
    assert anchor.value_2025_mn < 16_500


def test_two_word_market_rejects_a_figure_for_the_broader_market():
    assert not check_scope("turbines", "Wind Turbines").ok
    assert not check_scope("masks", "Surgical Masks").ok
    assert check_scope("wind turbine", "Wind Turbines").ok
    assert check_scope("coatings", "Paints & Coatings").ok   # '&' markets keep the looser rule


def test_cmi_report_gap_is_flagged():
    cited = {"value_2025_mn": 9_000, "accepted": [
        {"source": "CMI report DB: X Market", "value_2025_mn": 6_000.0, "raw_text": "USD 6 Bn", "reason": ""}]}
    codes = [f["code"] for f in compute_demand_quality_flags({"global_anchor_2025": 9_000, "cited_anchor": cited})]
    assert "differs_from_cmi_report" in codes
    codes = [f["code"] for f in compute_demand_quality_flags({"global_anchor_2025": 6_500, "cited_anchor": cited})]
    assert "differs_from_cmi_report" not in codes


def test_only_scope_and_magnitude_rejections_are_masked():
    cited = {"accepted": [], "corroborating": [], "rejected": [
        {"raw_text": "USD 32.71 Bn", "reason": "scope: narrower"},
        {"raw_text": "USD 15.7 billion", "reason": "no year stated next to the value"},
        {"raw_text": "USD 9 billion", "reason": "outlier: more than 2.5x from the median of cited values"},
    ]}
    assert set(agents._figure_masks(cited)) == {"USD 32.71 Bn"}
    assert agents._matches_rejected(15_700, {"rejected": [
        {"raw_text": "USD 15.7 billion", "value_mn": 15_700, "reason": "no year stated next to the value"}]}) is None


def test_tier2_only_corroborates_when_two_tier1_values_exist():
    anchor = collect_cited_values([
        (R1, "The global olive oil market was valued at USD 15.7 billion in 2025."),
        (R2, "The global olive oil market was valued at USD 18.7 billion in 2025."),
        ("https://www.linkedin.com/pulse/olive", "The global olive oil market was valued at USD 30 billion in 2025."),
    ], "Olive Oil")
    assert anchor.value_2025_mn == pytest.approx(17_200)
    assert [c.source for c in anchor.corroborating] == ["https://www.linkedin.com/pulse/olive"]


def test_social_post_only_corroborates_a_single_report_value():
    anchor = collect_cited_values([
        (R1, "The global olive oil market was valued at USD 15 billion in 2025."),
        ("https://www.linkedin.com/pulse/olive", "The global olive oil market was valued at USD 17 billion in 2025."),
    ], "Olive Oil")
    assert anchor.value_2025_mn == pytest.approx(15_000)
    assert len(anchor.corroborating) == 1


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


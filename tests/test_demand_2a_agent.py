"""Approach 2A agent: driver-library market scoping, product gate, review
checks and the pass loop -- all offline (fake LLM, faked phases)."""
from __future__ import annotations

import json

import pytest

from me_engine.demandCountry_approach2A import agent, review
from me_engine.demandCountry_approach2A.engine import driver_library


class FakeLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def complete_json(self, system, user, **_):
        self.calls.append((system, user))
        return self.responses.pop(0)


# --------------------------------------------------------------------------- #
# driver library: never reuse another market's driver
# --------------------------------------------------------------------------- #
def test_driver_library_reuses_only_same_market(tmp_path, monkeypatch):
    import me_engine.generation.semantic_search as ss

    monkeypatch.setattr(ss, "embed", lambda text: [1.0, 0.0])
    path = tmp_path / "lib.json"
    driver_library.append(
        usage_description="All final consumption of X", driver_name="National smartphone shipments",
        driver_unit="smartphones", consumption_unit="", conversion_formula="driver_value",
        required_evidence=[], market_name="Smartphones", path=path,
    )
    assert driver_library.lookup("All final consumption of X", path=path, market_name="Laptops") is None
    hit = driver_library.lookup("All final consumption of X", path=path, market_name="smartphones")
    assert hit["driver_name"] == "National smartphone shipments"


# --------------------------------------------------------------------------- #
# product gate
# --------------------------------------------------------------------------- #
def test_product_gate_rejects_other_products_without_llm():
    smartphone = {"driver_name": "National smartphone shipments", "unit": "smartphones (units shipped)"}
    watch = {"driver_name": "National watch consumption volume", "unit": "watches"}
    assert review.product_gate(smartphone, "Laptops") is not None
    assert review.product_gate(watch, "Smartwatches") is not None
    assert review.product_gate(watch, "Televisions") is not None


def test_product_gate_passes_own_product_and_official_data():
    olive = {"driver_name": "Apparent consumption (FAOSTAT: Olive Oil)", "unit": "tonnes"}
    gloves = {"driver_name": "National apparent consumption of disposable medical gloves", "unit": "gloves"}
    assert review.product_gate(olive, "Olive Oil") is None
    assert review.product_gate(gloves, "Disposable Medical Gloves") is None
    assert review.product_gate({"driver_name": "anything", "library_status": "official_data"}, "Vinegar") is None


def test_product_gate_asks_llm_only_when_words_differ():
    tv = {"driver_name": "Annual TV shipments", "unit": "TV sets"}
    llm = FakeLLM({"same_product": True, "reason": "TV = television"})
    assert review.product_gate(tv, "Televisions", llm) is None
    assert len(llm.calls) == 1

    llm = FakeLLM({"same_product": False, "reason": "smartphones are not laptops"})
    reason = review.product_gate({"driver_name": "Smartphone shipments"}, "Laptops", llm)
    assert "smartphones are not laptops" in reason


# --------------------------------------------------------------------------- #
# deterministic issues + scoring
# --------------------------------------------------------------------------- #
def test_rest_of_world_and_single_country_dominance_are_critical():
    issues = review.deterministic_issues({"total_market_value": 100.0,
                                          "country_totals": {"US": 20.0, "Rest of World": 80.0}})
    assert [i["code"] for i in issues] == ["rest_of_world_dominates"]

    issues = review.deterministic_issues({"total_market_value": 100.0,
                                          "country_totals": {"China": 70.0, "India": 30.0, "Japan": 0.0}})
    assert {i["code"] for i in issues} == {"single_country_dominates", "empty_countries"}


def test_score_and_acceptance():
    good = {"plausibility": 0.9, "verdict": "accept", "problems": [], "feedback": ""}
    critical = [{"severity": "critical", "code": "x", "message": "m"}]
    assert review.accepted(good, []) and review.score(good, []) == 0.9
    assert not review.accepted(good, critical) and review.score(good, critical) == 0.7
    assert not review.accepted(dict(good, plausibility=0.6), [])


def test_llm_review_never_trusts_malformed_output():
    out = review.llm_review(FakeLLM({"plausibility": "high", "verdict": "sure"}),
                            {"market_name": "M"}, {"total_market_value": 1.0}, [])
    assert out["plausibility"] == 0.0 and out["verdict"] == "redo"


# --------------------------------------------------------------------------- #
# agent loop
# --------------------------------------------------------------------------- #
def _fake_phases(monkeypatch, tmp_path, drivers, sized_totals, reviews):
    from me_engine.demandCountry_approach2A import checkpoints

    monkeypatch.setattr(checkpoints, "_OUTPUT_ROOT", tmp_path / "demand_country_2a")
    setup_draft = {"market_name": "Laptops", "scope": "new laptops", "geography": ["US", "Rest of World"],
                   "base_year": 2025, "market_unit": "USD Mn", "physical_unit": "laptops"}
    monkeypatch.setattr(agent.setup, "run_setup_phase", lambda *a, **k: dict(setup_draft))

    feedbacks = []

    def run_driver_phase(approved_setup, config, cache_path=None, root=None, feedback=None):
        feedbacks.append(feedback)
        bucket = {"name": "Total Laptops demand", "description": "d", "bucket_category": "physical_consumption"}
        return {"market_name": "Laptops", "usage_buckets": [bucket], "drivers": {bucket["name"]: drivers.pop(0)}}

    monkeypatch.setattr(agent.driver, "run_driver_phase", run_driver_phase)

    def size(approved_setup, approved_driver, *a):
        total = sized_totals.pop(0)
        return {"market_name": "Laptops", "total_market_value": total,
                "country_totals": {"US": total * 0.6, "Rest of World": total * 0.4},
                "region_totals": {"North America": total * 0.6}, "quality_flags": [], "validation": {}}

    monkeypatch.setattr(agent, "_size", size)
    written = []
    monkeypatch.setattr(agent, "write_sizing_outputs", lambda state, root=None: written.append(state))
    monkeypatch.setattr(agent.segmentation, "run_segments_phase", lambda *a, **k: {"market_name": "Laptops"})
    monkeypatch.setattr(agent.segmentation, "approve_segments", lambda *a, **k: {})
    monkeypatch.setattr(agent.segmentation, "run_report_phase",
                        lambda *a, **k: ({"segmentation": []}, None, None))
    (tmp_path / "r.xlsx").write_bytes(b"")
    monkeypatch.setattr(agent.segmentation, "write_report", lambda *a, **k: (tmp_path / "r.json", tmp_path / "r.xlsx"))
    monkeypatch.setattr(agent, "GenLLM", lambda *a, **k: FakeLLM(*reviews))
    return feedbacks, written


class _Cfg:
    model = "deepseek-chat"


# No ledger in the faked phases -> evidence coverage 0 and one soft flag, so
# the plausibility ceiling here is ~6.6/10; accept at 6.
_DESIGN = {"agent": {"min_score": 6.0}}


def _rubric(x):
    return {k: x for k, _ in review.RUBRIC}


def test_agent_rejects_wrong_product_then_redoes_until_accepted(tmp_path, monkeypatch):
    drivers = [
        {"driver_name": "National smartphone shipments", "unit": "smartphones"},
        {"driver_name": "Annual laptop shipments", "unit": "laptops"},
        {"driver_name": "Annual laptop shipments", "unit": "laptops"},
    ]
    reviews = [
        {"same_product": False, "reason": "smartphones are not laptops"},
        {"rubric": _rubric(5), "verdict": "redo", "problems": ["installed base"], "feedback": "use annual shipments"},
        {"rubric": _rubric(8.5), "verdict": "accept", "problems": [], "feedback": ""},
    ]
    feedbacks, written = _fake_phases(monkeypatch, tmp_path, drivers, [600_000.0, 120_000.0], reviews)

    summary = agent.run_agent("Laptops", _Cfg(), max_passes=3, say=lambda s: None, design=_DESIGN)

    assert [p["status"] for p in summary["passes"]] == ["rejected_before_sizing", "redo", "accepted"]
    assert feedbacks[0] is None
    assert "smartphones are not laptops" in feedbacks[1]
    assert feedbacks[2] == "use annual shipments"
    assert summary["kept_pass"] == 3 and summary["accepted"] and summary["global_usd_mn"] == 120_000.0
    assert written[-1]["total_market_value"] == 120_000.0
    saved = json.loads(agent.summary_path("Laptops").read_text(encoding="utf-8"))
    assert saved["kept_pass"] == 3 and saved["run_id"] == "R001" and saved["status"] == "written"
    run_dir = agent.checkpoints.run_dir("Laptops") / "runs" / "R001"
    assert (run_dir / "summary.md").exists() and (run_dir / "progress.mermaid").exists()
    assert (run_dir / "documentation.md").exists() and (run_dir / "ledger.csv").exists()


def test_agent_keeps_best_pass_when_none_accepted(tmp_path, monkeypatch):
    drivers = [{"driver_name": "Annual laptop shipments", "unit": "laptops"} for _ in range(2)]
    reviews = [
        {"plausibility": 0.7, "verdict": "redo", "problems": [], "feedback": "check price"},
        {"plausibility": 0.4, "verdict": "redo", "problems": [], "feedback": "worse"},
    ]
    _fake_phases(monkeypatch, tmp_path, drivers, [100.0, 900.0], reviews)

    summary = agent.run_agent("Laptops", _Cfg(), max_passes=2, say=lambda s: None, design=_DESIGN)

    assert summary["kept_pass"] == 1 and not summary["accepted"] and summary["global_usd_mn"] == 100.0


def test_agent_fails_when_no_pass_measures_the_product(tmp_path, monkeypatch):
    drivers = [{"driver_name": "Smartphone shipments"} for _ in range(2)]
    reviews = [{"same_product": False, "reason": "no"}, {"same_product": False, "reason": "no"}]
    _fake_phases(monkeypatch, tmp_path, drivers, [], reviews)

    with pytest.raises(RuntimeError, match="no pass"):
        agent.run_agent("Laptops", _Cfg(), max_passes=2, say=lambda s: None)


# --------------------------------------------------------------------------- #
# Google AI Overview: leads only, never evidence (system.md §3.4; backend mocked)
# --------------------------------------------------------------------------- #
class _Resp:
    def __init__(self, data=None, status_code=200, text="", headers=None):
        self._data, self.status_code, self.text = data, status_code, text
        self.headers = headers or {"content-type": "application/json"}

    def json(self):
        return self._data


_CITED_HTML = "<html><body><p>IDC: laptop shipments in India reached 14.2 million units in 2025.</p></body></html>"


@pytest.fixture()
def overview(monkeypatch, tmp_path):
    from me_engine.demandCountry_approach2A.engine import ai_overview

    monkeypatch.setenv("ME_AI_OVERVIEW", "1")
    monkeypatch.setenv("ME_AI_OVERVIEW_LEADS", str(tmp_path / "leads.jsonl"))
    monkeypatch.setattr(ai_overview, "_health", {"ok": False, "checked_at": 0.0})
    monkeypatch.setattr(ai_overview, "_answers", {})
    asked = []

    def get(url, params=None, timeout=None, **_):
        if url.endswith("/health"):
            return _Resp({"extension_status": "connected"})
        if url.endswith("/ask"):
            asked.append(params["q"])
            return _Resp({"markdown": "Laptop shipments in India were 15 million units in 2025.",
                          "citations": ["https://idc.com/x"], "error": None})
        return _Resp(text=_CITED_HTML, headers={"content-type": "text/html"})

    monkeypatch.setattr(ai_overview.httpx, "get", get)
    return ai_overview, asked, tmp_path / "leads.jsonl"


def test_ai_overview_text_never_reaches_the_extractor(overview, monkeypatch):
    from me_engine.demandCountry_approach2A.engine import web_search

    ai_overview, asked, leads = overview
    monkeypatch.setattr(web_search.web_evidence, "gather", lambda q, per_query=4, cap=12: ["a", "b", "c"])
    snippets = web_search.gather(["laptops india 2025", "other"], cap=3)
    assert snippets[:2] == ["a", "b"] and snippets[2].startswith("(https://idc.com/x)")
    assert "14.2 million" in snippets[2] and not any("15 million" in s for s in snippets)

    import ddgs

    monkeypatch.setattr(ddgs, "DDGS", lambda: type("D", (), {"text": lambda self, q, max_results=5: []})())
    results = web_search.search("laptops india 2025")
    assert [r.url for r in results] == ["https://idc.com/x"]  # the cited page, under its own URL
    assert not any(ai_overview.is_ai_overview_url(r.url) for r in results)
    assert asked == ["laptops india 2025"]  # second ask served from the cache
    lead = json.loads(leads.read_text(encoding="utf-8").splitlines()[0])
    assert lead["origin"] == "ai_overview" and lead["status"] == "cited_pages_fetched" and lead["sha256"]


def test_overview_number_absent_from_cited_page_cannot_be_verified(overview):
    from me_engine.demandCountry_approach2A.engine import evidence

    ai_overview, _, _ = overview
    (url, text), = ai_overview.cited_page_results("q", {"markdown": "15 million", "citations": ["https://idc.com/x"]})
    fact = evidence.build_fact(value=15e6, unit="laptops", year=2025, source_name="IDC", source_url=url,
                               citation="", evidence_text=text)
    assert fact["evidence_type"] == "assumption"  # 15M is only in the overview, not on the cited page
    fact = evidence.build_fact(value=14.2e6, unit="laptops", year=2025, source_name="IDC", source_url=url,
                               citation="", evidence_text=text)
    assert fact["evidence_type"] == "verified_observed"


def test_ai_overview_off_by_default_and_when_extension_missing(monkeypatch, overview):
    ai_overview, asked, _ = overview
    monkeypatch.delenv("ME_AI_OVERVIEW")
    assert ai_overview.ask("q") is None
    monkeypatch.setenv("ME_AI_OVERVIEW", "0")
    assert ai_overview.ask("q") is None

    monkeypatch.setenv("ME_AI_OVERVIEW", "auto")
    monkeypatch.setattr(ai_overview.httpx, "get", lambda *a, **k: _Resp({"extension_status": "never_seen"}))
    assert ai_overview.ask("q") is None and asked == []


def test_fact_from_ai_overview_is_never_verified():
    from me_engine.demandCountry_approach2A.engine import ai_overview, evidence

    fact = evidence.build_fact(
        value=15e6, unit="laptops", year=2025, source_name="web", source_url=ai_overview.search_url("q"),
        citation="Laptop shipments in India were 15 million units", evidence_text="Laptop shipments in India were 15 million units",
    )
    assert fact["evidence_type"] in ("source_found_unverified", "assumption")

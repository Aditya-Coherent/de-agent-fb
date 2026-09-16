"""Evidence Router + typed acquirer tests (PDF/CSV-Excel/API/web), plus the
new shared acceptance gates in evidence.py.

Everything that would touch the network (requests/ddgs/pdfplumber/pandas
download calls) is monkeypatched -- these are unit tests of the acceptance
discipline and dispatch logic, not integration tests of real downloads.

Run with: pytest tests/test_demand_usage_evidence_router.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from me_engine.demand_usage import (
    api_acquirer,
    asset_verifier,
    evidence,
    evidence_router,
    pdf_acquirer,
    tabular_acquirer,
    web_search,
)


def _ok_verification(url: str, content: bytes, content_type: str = "") -> asset_verifier.AssetVerification:
    """Shorthand for a successful `asset_verifier.verify_asset` result, used
    to monkeypatch acquirer download steps in these dispatch/acceptance
    tests without hitting the network."""
    return asset_verifier.AssetVerification(
        resolved_url=url, is_accessible=True, actual_content_type=content_type,
        matches_expected_type=True, http_status=200, content=content,
    )


def _failed_verification(url: str, rejection_reason: str = "download_failed") -> asset_verifier.AssetVerification:
    return asset_verifier.AssetVerification(
        resolved_url=url, is_accessible=False, actual_content_type="",
        matches_expected_type=False, http_status=404, rejection_reason=rejection_reason,
    )


# --------------------------------------------------------------------------- #
# evidence.build_fact / build_structured_fact -- the shared acceptance gates
# --------------------------------------------------------------------------- #
def test_build_fact_observed_when_value_traceable_and_url_present():
    fact = evidence.build_fact(
        value=8_900_000.0, unit="barrels per day", year=2025, source_name="EIA",
        source_url="https://eia.gov/x", citation="8.9 million barrels per day",
        evidence_text="US consumption is 8.9 million barrels per day.",
    )
    assert fact["evidence_type"] == "verified_observed"
    assert fact["verification_status"] == "verified_observed"
    assert fact["source"] == "EIA"
    assert fact["source_url"] == "https://eia.gov/x"


def test_build_fact_never_observed_without_url():
    fact = evidence.build_fact(
        value=8_900_000.0, unit="barrels per day", year=2025, source_name="EIA", source_url="",
        citation="c", evidence_text="US consumption is 8.9 million barrels per day.",
    )
    assert fact["evidence_type"] != "verified_observed"


def test_build_structured_fact_observed_when_unambiguous_and_url_present():
    fact = evidence.build_structured_fact(
        value=100.0, unit="tonnes", year=2025, source_name="USGS", source_url="https://usgs.gov/x",
        location_ref="sheet='S1', column='Value'", geography_matched=True, ambiguous=False,
    )
    assert fact["evidence_type"] == "verified_observed"


def test_build_structured_fact_derived_when_ambiguous():
    fact = evidence.build_structured_fact(
        value=100.0, unit="tonnes", year=2025, source_name="USGS", source_url="https://usgs.gov/x",
        location_ref="ref", geography_matched=True, ambiguous=True,
    )
    assert fact["evidence_type"] == "derived"


def test_build_structured_fact_assumption_when_no_value():
    fact = evidence.build_structured_fact(
        value=None, unit="tonnes", year=2025, source_name="USGS", source_url="https://usgs.gov/x",
        location_ref="ref", geography_matched=False,
    )
    assert fact["evidence_type"] == "assumption"


# --------------------------------------------------------------------------- #
# evidence_router.propose_evidence_plan -- typed source chain
# --------------------------------------------------------------------------- #
class _PlanLLM:
    def __init__(self, sources):
        self.sources = sources

    def complete_json(self, system, user):
        return {"sources": self.sources}


def test_propose_plan_keeps_only_well_typed_sources():
    llm = _PlanLLM([
        {"source_name": "USGS", "source_type": "pdf", "locator": "site:usgs.gov",
         "search_query_template": "site:usgs.gov {country} cement production filetype:pdf", "rationale": "r"},
        {"source_name": "Bad", "source_type": "not_a_real_type", "locator": "",
         "search_query_template": "{country} x", "rationale": "rejected -- unknown type"},
        {"source_name": "Bad2", "source_type": "web_page", "locator": "", "search_query_template": "no placeholder", "rationale": "rejected -- no {country}"},
    ])
    plan = evidence_router.propose_evidence_plan(llm, "Cement", "Concrete", "desc", "Cement production", "tonnes", "demand driver", "guidance")
    assert len(plan["sources"]) == 1
    assert plan["sources"][0]["source_type"] == "pdf"


def test_propose_plan_never_returns_empty_source_list():
    plan = evidence_router.propose_evidence_plan(_PlanLLM([]), "M", "B", "desc", "q", "u", "demand driver", "g")
    assert len(plan["sources"]) >= 1
    assert plan["sources"][0]["source_type"] == "web_page"


def test_propose_driver_intensity_pricing_wrappers_are_labelled():
    driver = {"driver_name": "d", "unit": "u", "consumption_unit": "c", "final_demand_unit": "u"}
    d_plan = evidence_router.propose_driver_evidence_plan(_PlanLLM([]), "M", "B", "desc", driver)
    i_plan = evidence_router.propose_intensity_evidence_plan(_PlanLLM([]), "M", "B", "desc", driver)
    p_plan = evidence_router.propose_pricing_evidence_plan(_PlanLLM([]), "M", "B", "desc", "tonne", "q")
    assert d_plan["kind"] == "demand driver"
    assert i_plan["kind"] == "consumption intensity / conversion factor"
    assert p_plan["kind"] == "average selling price (ASP)"


# --------------------------------------------------------------------------- #
# Phase 8 -- pricing now consumes the same Evidence Strategy framework
# (Phase 7) as the driver path, via the SAME `_strategy_guidance`/
# `_merge_strategy_sources` machinery.
# --------------------------------------------------------------------------- #
def test_propose_pricing_evidence_plan_without_strategy_is_unchanged():
    plan = evidence_router.propose_pricing_evidence_plan(_PlanLLM([]), "M", "B", "desc", "tonne", "q")
    assert plan["sources"][0]["source_type"] == "web_page"
    assert all(s.get("origin") != "evidence_strategy" for s in plan["sources"])


def test_propose_pricing_evidence_plan_injects_price_evidence_strategy_sources():
    price_strategy = {
        "preferred_sources": ["LME"], "primary_source_types": ["commodity_exchange"],
        "search_query_templates": ["{country} tonne spot price"], "metric_required": "Spot price of tonne",
        "physical_unit": "USD per tonne", "strategy_source": "deterministic_price_basis",
    }
    plan = evidence_router.propose_pricing_evidence_plan(
        _PlanLLM([{"source_name": "General", "source_type": "web_page", "locator": "",
                    "search_query_template": "{country} price", "rationale": "r"}]),
        "M", "B", "desc", "tonne", "q", strategy=price_strategy,
    )
    assert plan["sources"][0]["source_name"] == "LME"
    assert plan["sources"][0]["origin"] == "evidence_strategy"
    # The LLM-proposed source is still present, just not first.
    assert any(s["source_name"] == "General" for s in plan["sources"])


def test_propose_pricing_evidence_plan_ignores_missing_strategy():
    plan = evidence_router.propose_pricing_evidence_plan(
        _PlanLLM([]), "M", "B", "desc", "tonne", "q", strategy=None,
    )
    assert all(s.get("origin") != "evidence_strategy" for s in plan["sources"])


# --------------------------------------------------------------------------- #
# evidence_router.execute_evidence_plan -- dispatch + fall-through + full log
# --------------------------------------------------------------------------- #
def test_execute_plan_dispatches_to_the_right_acquirer_by_type(monkeypatch):
    calls = []
    monkeypatch.setattr(
        evidence_router.web_acquirer, "acquire",
        lambda llm, source, geo, *, unit, context, **_kwargs: (calls.append("web") or (None, {"source_type": "web_page", "source_name": source["source_name"], "found": False, "rejection_reason": "no_source_found"})),
    )
    monkeypatch.setattr(
        evidence_router.pdf_acquirer, "acquire",
        lambda llm, source, geo, *, unit, context, keywords, **_kwargs: (calls.append("pdf") or ({"value": 1.0, "unit": unit, "evidence_type": "verified_observed"}, {"source_type": "pdf", "source_name": source["source_name"], "found": True})),
    )
    plan = {"unit": "tonnes", "quantity_name": "q", "sources": [
        {"source_name": "A", "source_type": "web_page", "search_query_template": "{country} a"},
        {"source_name": "B", "source_type": "pdf", "search_query_template": "{country} b"},
    ]}
    fact, attempts = evidence_router.execute_evidence_plan(object(), plan, "India", context="ctx")
    assert calls == ["web", "pdf"]
    assert fact is not None
    assert len(attempts) == 2
    assert attempts[0]["found"] is False
    assert attempts[1]["found"] is True


def test_execute_plan_records_acquirer_exceptions_without_raising(monkeypatch):
    def _raise(llm, source, geo, *, unit, context, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(evidence_router.web_acquirer, "acquire", _raise)
    plan = {"unit": "u", "sources": [{"source_name": "A", "source_type": "web_page", "search_query_template": "{country} a"}]}
    fact, attempts = evidence_router.execute_evidence_plan(object(), plan, "India", context="ctx")
    assert fact is None
    assert attempts[0]["found"] is False
    assert attempts[0]["rejection_reason"] == "extraction_failed"


# --------------------------------------------------------------------------- #
# pdf_acquirer -- deterministic shortlist + page-anchored acceptance
# --------------------------------------------------------------------------- #
def test_pdf_keyword_shortlist_is_deterministic_and_ranked():
    pages = ["irrelevant filler text", "cement production tonnes cement tonnes", "cement mentioned once"]
    idx = pdf_acquirer._keyword_shortlist(pages, ["cement", "tonnes"])
    assert idx[0] == 1  # highest keyword density ranked first


def _page(text: str, tables: list | None = None) -> "pdf_acquirer._PageContent":
    tables = tables or []
    combined = text if not tables else text + "\n" + pdf_acquirer._serialize_tables(tables)
    return pdf_acquirer._PageContent(text=text, tables=tables, combined_text=combined)


def test_pdf_acquirer_rejects_page_index_not_in_shortlist(monkeypatch):
    monkeypatch.setattr(pdf_acquirer, "_search_pdf_candidates", lambda source, geo: ["https://usgs.gov/report.pdf"])
    monkeypatch.setattr(pdf_acquirer, "_download_pdf_bytes", lambda url: _ok_verification(url, b"fake"))
    monkeypatch.setattr(
        pdf_acquirer, "_extract_pages",
        lambda b: ([_page("page0 cement 100 tonnes"), _page("page1 nothing relevant")], None, ""),
    )

    class _LLM:
        def complete_json(self, system, user):
            return {"found": True, "value": 100.0, "page_index": 99, "year": 2025, "citation": "c", "is_direct_figure": True}

    fact, attempt = pdf_acquirer.acquire(
        _LLM(), {"source_name": "USGS", "search_query_template": "{country} cement"}, "US",
        unit="tonnes", context="ctx", keywords=["cement", "tonnes"],
    )
    assert fact is None
    assert attempt["rejection_reason"] == "extraction_failed"


def test_pdf_acquirer_accepts_a_value_anchored_to_a_shortlisted_page(monkeypatch):
    monkeypatch.setattr(pdf_acquirer, "_search_pdf_candidates", lambda source, geo: ["https://usgs.gov/report.pdf"])
    monkeypatch.setattr(pdf_acquirer, "_download_pdf_bytes", lambda url: _ok_verification(url, b"fake"))
    monkeypatch.setattr(
        pdf_acquirer, "_extract_pages",
        lambda b: ([_page("irrelevant"), _page("cement production was 100 tonnes in 2025")], None, ""),
    )

    class _LLM:
        def complete_json(self, system, user):
            return {"found": True, "value": 100.0, "page_index": 1, "table_index": None, "year": 2025, "citation": "100 tonnes", "is_direct_figure": True}

    fact, attempt = pdf_acquirer.acquire(
        _LLM(), {"source_name": "USGS", "search_query_template": "{country} cement"}, "US",
        unit="tonnes", context="ctx", keywords=["cement"],
    )
    assert fact is not None
    assert fact["evidence_type"] == "verified_observed"
    assert attempt["page_number"] == 2


def test_pdf_acquirer_reports_download_failure(monkeypatch):
    monkeypatch.setattr(pdf_acquirer, "_search_pdf_candidates", lambda source, geo: ["https://usgs.gov/report.pdf"])
    monkeypatch.setattr(pdf_acquirer, "_download_pdf_bytes", lambda url: _failed_verification(url))
    fact, attempt = pdf_acquirer.acquire(
        object(), {"source_name": "USGS", "search_query_template": "{country} cement"}, "US",
        unit="tonnes", context="ctx", keywords=["cement"],
    )
    assert fact is None
    assert attempt["rejection_reason"] == "download_failed"


# --------------------------------------------------------------------------- #
# tabular_acquirer -- deterministic pandas lookup, LLM proposes mapping only
# --------------------------------------------------------------------------- #
def _ok_resolve(kind: str = "csv"):
    """Shorthand: monkeypatch `_resolve_dataset` to succeed immediately on
    the first (only) candidate URL, for tests of the extraction logic below
    discovery -- mirrors `_ok_verification`'s role for `_download` pre-Phase-4."""
    def _resolve(url, **_kw):
        return kind, _ok_verification(url, b"fake"), url, None
    return _resolve


def test_tabular_acquirer_deterministic_lookup(monkeypatch):
    import pandas as pd

    df = pd.DataFrame({"Country": ["India", "United States"], "Value": [100.0, 200.0]})
    monkeypatch.setattr(tabular_acquirer, "_search_dataset_candidates", lambda source, geo: ["https://data.gov/x.csv"])
    monkeypatch.setattr(tabular_acquirer, "_resolve_dataset", _ok_resolve())
    monkeypatch.setattr(tabular_acquirer, "_load_sheets", lambda content, url, kind=None: {"Sheet1": df})

    class _LLM:
        def complete_json(self, system, user):
            return {"found": True, "sheet": "Sheet1", "value_column": "Value", "filter": {"Country": "India"}, "aggregation": "none"}

    fact, attempt = tabular_acquirer.acquire(
        _LLM(), {"source_name": "Data.gov", "search_query_template": "{country} cement"}, "India",
        unit="tonnes", context="ctx", year=2025,
    )
    assert fact is not None
    assert fact["value"] == 100.0  # the REAL India row, not the US row
    assert fact["evidence_type"] == "verified_observed"


def test_tabular_acquirer_marks_ambiguous_when_multiple_rows_match(monkeypatch):
    import pandas as pd

    df = pd.DataFrame({"Country": ["India", "India"], "Value": [100.0, 50.0]})
    monkeypatch.setattr(tabular_acquirer, "_search_dataset_candidates", lambda source, geo: ["https://data.gov/x.csv"])
    monkeypatch.setattr(tabular_acquirer, "_resolve_dataset", _ok_resolve())
    monkeypatch.setattr(tabular_acquirer, "_load_sheets", lambda content, url, kind=None: {"Sheet1": df})

    class _LLM:
        def complete_json(self, system, user):
            return {"found": True, "sheet": "Sheet1", "value_column": "Value", "filter": {"Country": "India"}, "aggregation": "sum"}

    fact, attempt = tabular_acquirer.acquire(
        _LLM(), {"source_name": "Data.gov", "search_query_template": "{country} cement"}, "India",
        unit="tonnes", context="ctx", year=2025,
    )
    assert fact is not None
    assert fact["value"] == 150.0  # sum aggregation, deterministic
    assert fact["evidence_type"] == "derived"  # ambiguous -- never silently "verified_observed"


def test_tabular_acquirer_geography_mismatch_when_filter_matches_nothing(monkeypatch):
    import pandas as pd

    df = pd.DataFrame({"Country": ["United States"], "Value": [200.0]})
    monkeypatch.setattr(tabular_acquirer, "_search_dataset_candidates", lambda source, geo: ["https://data.gov/x.csv"])
    monkeypatch.setattr(tabular_acquirer, "_resolve_dataset", _ok_resolve())
    monkeypatch.setattr(tabular_acquirer, "_load_sheets", lambda content, url, kind=None: {"Sheet1": df})

    class _LLM:
        def complete_json(self, system, user):
            return {"found": True, "sheet": "Sheet1", "value_column": "Value", "filter": {"Country": "India"}, "aggregation": "none"}

    fact, attempt = tabular_acquirer.acquire(
        _LLM(), {"source_name": "Data.gov", "search_query_template": "{country} cement"}, "India",
        unit="tonnes", context="ctx", year=2025,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "geography_mismatch"


# --------------------------------------------------------------------------- #
# api_acquirer -- deterministic JSON-path traversal, LLM proposes path only
# --------------------------------------------------------------------------- #
def test_api_acquirer_deterministic_path_traversal(monkeypatch):
    import json as _json

    payload = {"data": {"country": "India", "series": [{"year": 2025, "value": 42.0}]}}
    monkeypatch.setattr(
        api_acquirer, "_fetch",
        lambda url: _ok_verification(url, _json.dumps(payload).encode("utf-8"), "application/json"),
    )

    class _LLM:
        def complete_json(self, system, user):
            return {"found": True, "path": ["data", "series", "0", "value"]}

    fact, attempt = api_acquirer.acquire(
        _LLM(), {"source_name": "OpenData API", "locator": "https://api.example.com/{country}"}, "India",
        unit="tonnes", context="ctx", year=2025,
    )
    assert fact is not None
    assert fact["value"] == 42.0
    assert fact["evidence_type"] == "verified_observed"


def test_api_acquirer_reports_download_failure(monkeypatch):
    # Phase 5 (`DEMAND_SIDE_EVIDENCE_PLAN.md`): a 404 is now classified as the
    # finer-grained `endpoint_not_found`, not the generic `download_failed` --
    # see `tests/test_demand_usage_api_acquirer.py` for the full rejection-
    # reason taxonomy this phase introduces.
    monkeypatch.setattr(api_acquirer, "_fetch", lambda url: _failed_verification(url))
    fact, attempt = api_acquirer.acquire(
        object(), {"source_name": "X", "locator": "https://api.example.com/{country}"}, "India",
        unit="u", context="ctx", year=None,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "endpoint_not_found"


def test_api_acquirer_rejects_unresolvable_path(monkeypatch):
    import json as _json

    monkeypatch.setattr(
        api_acquirer, "_fetch",
        lambda url: _ok_verification(url, _json.dumps({"a": 1}).encode("utf-8"), "application/json"),
    )

    class _LLM:
        def complete_json(self, system, user):
            return {"found": True, "path": ["b", "c"]}  # doesn't exist in the payload

    fact, attempt = api_acquirer.acquire(
        _LLM(), {"source_name": "X", "locator": "https://api.example.com/{country}"}, "India",
        unit="u", context="ctx", year=None,
    )
    # Phase 5: a path that doesn't resolve against the actual JSON structure
    # is a schema mismatch, not the old generic `extraction_failed`.
    assert fact is None
    assert attempt["rejection_reason"] == "schema_mismatch"


# --------------------------------------------------------------------------- #
# web_acquirer -- full-page-body fetch enrichment (falls back to snippet)
# --------------------------------------------------------------------------- #
def test_web_acquirer_falls_back_to_snippet_when_fetch_fails(monkeypatch):
    import me_engine.demand_usage.web_acquirer as wa

    monkeypatch.setattr(wa, "_fetch_page_text", lambda url: None)
    monkeypatch.setattr(
        wa.web_search, "search",
        lambda query, max_results=5: [web_search.SearchResult(title="t", url="https://eia.gov/x", snippet="8.9 million barrels per day")],
    )

    class _LLM:
        def complete_json(self, system, user):
            return {"found": True, "value": 8_900_000.0, "result_index": 0, "year": 2025, "citation": "c", "is_direct_figure": True}

    fact, attempt = wa.acquire(_LLM(), {"source_name": "EIA", "search_query_template": "{country} fuel"}, "US", unit="barrels per day", context="ctx")
    assert fact is not None
    assert fact["evidence_type"] == "verified_observed"


def test_web_acquirer_no_results_is_no_source_found(monkeypatch):
    import me_engine.demand_usage.web_acquirer as wa

    monkeypatch.setattr(wa.web_search, "search", lambda query, max_results=5: [])
    fact, attempt = wa.acquire(object(), {"source_name": "EIA", "search_query_template": "{country} fuel"}, "US", unit="u", context="ctx")
    assert fact is None
    assert attempt["rejection_reason"] == "no_source_found"

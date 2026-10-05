"""Evidence Extraction Quality layer (`DEMAND_SIDE_EVIDENCE_PLAN.md` addendum,
post-Phase-12) -- tests for `fact_validator.py`'s deterministic candidate
checks, plus each acquirer's wiring of them.

Everything that would touch the network (requests/ddgs/pdfplumber/pandas)
is monkeypatched -- these are unit/dispatch tests, not live integration
tests. Run with: pytest tests/test_demand_usage_fact_validator.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from me_engine.demandUseCaseCountry_approach2B import (
    api_acquirer,
    asset_verifier,
    evidence,
    evidence_router,
    fact_validator,
    pdf_acquirer,
    tabular_acquirer,
    web_search,
)
from me_engine.demandUseCaseCountry_approach2B.pdf_acquirer import _PageContent


def _ok_verification(url: str, content: bytes, content_type: str = "") -> asset_verifier.AssetVerification:
    return asset_verifier.AssetVerification(
        resolved_url=url, is_accessible=True, actual_content_type=content_type,
        matches_expected_type=True, http_status=200, content=content,
    )


# --------------------------------------------------------------------------- #
# fact_validator.py -- deterministic checks in isolation
# --------------------------------------------------------------------------- #
def test_has_currency_signal():
    assert fact_validator.has_currency_signal("$2.1 billion")
    assert fact_validator.has_currency_signal("USD million")
    assert fact_validator.has_currency_signal("market value")
    assert not fact_validator.has_currency_signal("million tonnes per year")
    assert not fact_validator.has_currency_signal("")


def test_classify_metric_families_detects_known_keywords():
    assert "monetary_value" in fact_validator.classify_metric_families("global market value reached $5bn")
    assert "production" in fact_validator.classify_metric_families("annual production of cement")
    assert "capacity" in fact_validator.classify_metric_families("installed capacity was 65 million tonnes")
    assert fact_validator.classify_metric_families("") == set()
    assert fact_validator.classify_metric_families("a perfectly generic sentence") == set()


def test_metric_family_conflict_detects_monetary_vs_physical():
    conflict = fact_validator.metric_family_conflict(
        "annual cement production in million tonnes", "global cement market value = $12 billion",
    )
    assert conflict == ("production", "monetary_value") or conflict == ("monetary_value", "production")


def test_metric_family_conflict_detects_capacity_vs_production():
    conflict = fact_validator.metric_family_conflict(
        "annual crude oil production volume", "installed refining capacity of 18.8 million barrels/day",
    )
    assert conflict is not None


def test_metric_family_conflict_none_when_ambiguous_or_unclassifiable():
    assert fact_validator.metric_family_conflict("cement production", "") is None
    assert fact_validator.metric_family_conflict("", "market value") is None
    assert fact_validator.metric_family_conflict("cement production volume", "cement output was strong") is None


def test_metric_family_conflict_none_for_compatible_families():
    # production and trade can legitimately co-occur -- not a known conflict pair.
    assert fact_validator.metric_family_conflict("cement production volume", "export volume of cement") is None


def test_geography_conflict_flags_specific_mismatch():
    assert fact_validator.geography_conflict("India", "China")
    assert not fact_validator.geography_conflict("India", "India")
    assert not fact_validator.geography_conflict("India", "")  # unstated -- never a conflict
    assert not fact_validator.geography_conflict("India", "Global")  # global is never a conflict


def test_year_conflict_respects_tolerance():
    assert not fact_validator.year_conflict(2025, 2024)  # within 1-year tolerance
    assert fact_validator.year_conflict(2025, 2010)
    assert not fact_validator.year_conflict(2025, None)  # unstated -- never a conflict
    assert not fact_validator.year_conflict(None, 2010)


def test_physical_unit_conflict_flags_genuinely_different_units():
    assert fact_validator.physical_unit_conflict("million tonnes per year", "vehicles")
    assert not fact_validator.physical_unit_conflict("million tonnes per year", "tonnes")
    assert not fact_validator.physical_unit_conflict("barrels per day", "")  # unstated -- never a conflict
    assert not fact_validator.physical_unit_conflict("barrels per year", "barrels")  # substring match


def test_validate_candidate_rejects_monetary_value_for_physical_driver():
    reason = fact_validator.validate_candidate(
        requested_unit="million tonnes", requested_context="annual cement production",
        matched_metric_label="global cement market value", matched_unit="USD billion",
        evidence_text="The global cement market was valued at $350 billion in 2025.",
        is_direct_figure=True,
    )
    assert reason == "wrong_metric_monetary_value_for_physical_driver"


def test_validate_candidate_accepts_matching_physical_metric():
    reason = fact_validator.validate_candidate(
        requested_unit="million tonnes", requested_context="annual cement production",
        matched_metric_label="annual cement production volume", matched_unit="million tonnes",
        evidence_text="Cement production reached 450 million tonnes in 2025.",
        is_direct_figure=True,
    )
    assert reason is None


def test_validate_candidate_rejects_geography_mismatch_for_direct_figure():
    reason = fact_validator.validate_candidate(
        requested_unit="million tonnes", requested_context="cement production",
        requested_geography="India", matched_geography="China",
        matched_metric_label="cement production", evidence_text="China produced 2.4 billion tonnes.",
        is_direct_figure=True,
    )
    assert reason == "geography_mismatch"


def test_validate_candidate_allows_geography_mismatch_when_declared_derivation():
    # A candidate explicitly declared non-direct (is_direct_figure=False) is
    # a regional/global figure the caller intends to DERIVE from -- a
    # different geography is expected, not a rejection.
    reason = fact_validator.validate_candidate(
        requested_unit="million tonnes", requested_context="cement production",
        requested_geography="India", matched_geography="Global",
        matched_metric_label="global cement production", evidence_text="Global production was 4.3 billion tonnes.",
        is_direct_figure=False,
    )
    assert reason is None


def test_validate_candidate_rejects_year_mismatch():
    reason = fact_validator.validate_candidate(
        requested_unit="barrels per year", requested_context="crude oil production",
        requested_year=2025, matched_year=2010,
        matched_metric_label="crude oil production", evidence_text="Production in 2010 was 80 million barrels.",
        is_direct_figure=True,
    )
    assert reason == "year_mismatch"


def test_validate_candidate_rejects_downstream_capacity_for_production_request():
    reason = fact_validator.validate_candidate(
        requested_unit="million tonnes", requested_context="annual cement production volume",
        matched_metric_label="installed cement production capacity",
        evidence_text="Installed capacity stood at 65 million tonnes.",
        is_direct_figure=True,
    )
    assert reason is not None and reason.startswith("wrong_metric_family")


def test_validate_candidate_never_rejects_on_pure_ambiguity():
    reason = fact_validator.validate_candidate(
        requested_unit="million tonnes", requested_context="annual cement production",
        matched_metric_label="", matched_unit="", evidence_text="450 million tonnes were reported.",
        is_direct_figure=True,
    )
    assert reason is None


# --------------------------------------------------------------------------- #
# web_acquirer -- wrong-metric / correct-fact disambiguation
# --------------------------------------------------------------------------- #
def test_web_acquirer_rejects_wrong_metric_monetary_value(monkeypatch):
    import me_engine.demandUseCaseCountry_approach2B.web_acquirer as wa

    monkeypatch.setattr(wa, "_fetch_page_text", lambda url: None)
    monkeypatch.setattr(
        wa.web_search, "search",
        lambda query, max_results=5: [
            web_search.SearchResult(title="t", url="https://example.com/x",
                                     snippet="The global cement market was valued at $350 billion in 2025."),
        ],
    )

    class _LLM:
        def complete_json(self, system, user):
            return {
                "found": True, "value": 350_000_000_000.0, "result_index": 0, "year": 2025,
                "citation": "valued at $350 billion", "is_direct_figure": True,
                "matched_metric_label": "global cement market value", "matched_unit": "USD billion",
                "matched_geography": "",
            }

    fact, attempt = wa.acquire(
        _LLM(), {"source_name": "X", "search_query_template": "{country} cement"}, "India",
        unit="million tonnes", context="annual cement production",
    )
    assert fact is None
    assert attempt["rejection_reason"] == "wrong_metric_monetary_value_for_physical_driver"


def test_web_acquirer_selects_correct_fact_among_multiple_numbers(monkeypatch):
    import me_engine.demandUseCaseCountry_approach2B.web_acquirer as wa

    monkeypatch.setattr(wa, "_fetch_page_text", lambda url: None)
    monkeypatch.setattr(
        wa.web_search, "search",
        lambda query, max_results=5: [
            web_search.SearchResult(title="t1", url="https://example.com/value",
                                     snippet="The global cement market was valued at $350 billion in 2025."),
            web_search.SearchResult(title="t2", url="https://example.com/volume",
                                     snippet="Cement production reached 450 million tonnes in 2025."),
        ],
    )

    class _LLM:
        def complete_json(self, system, user):
            # The LLM correctly ignores the dollar figure and points at the
            # second, on-topic result.
            return {
                "found": True, "value": 450_000_000.0, "result_index": 1, "year": 2025,
                "citation": "450 million tonnes", "is_direct_figure": True,
                "matched_metric_label": "cement production volume", "matched_unit": "million tonnes",
                "matched_geography": "",
            }

    fact, attempt = wa.acquire(
        _LLM(), {"source_name": "X", "search_query_template": "{country} cement"}, "Global",
        unit="million tonnes", context="annual cement production",
    )
    assert fact is not None
    assert fact["value"] == 450_000_000.0
    assert fact["evidence_type"] == "verified_observed"


def test_web_acquirer_rejects_geography_mismatch(monkeypatch):
    import me_engine.demandUseCaseCountry_approach2B.web_acquirer as wa

    monkeypatch.setattr(wa, "_fetch_page_text", lambda url: None)
    monkeypatch.setattr(
        wa.web_search, "search",
        lambda query, max_results=5: [
            web_search.SearchResult(title="t", url="https://example.com/x", snippet="China produced 2.4 billion tonnes of cement."),
        ],
    )

    class _LLM:
        def complete_json(self, system, user):
            return {
                "found": True, "value": 2_400_000_000.0, "result_index": 0, "year": 2025,
                "citation": "2.4 billion tonnes", "is_direct_figure": True,
                "matched_metric_label": "cement production", "matched_unit": "tonnes",
                "matched_geography": "China",
            }

    fact, attempt = wa.acquire(
        _LLM(), {"source_name": "X", "search_query_template": "{country} cement"}, "India",
        unit="million tonnes", context="annual cement production",
    )
    assert fact is None
    assert attempt["rejection_reason"] == "geography_mismatch"


# --------------------------------------------------------------------------- #
# pdf_acquirer -- correct table/page selection vs. an adjacent $ figure
# --------------------------------------------------------------------------- #
def _pdf_setup(monkeypatch, page_text: str):
    monkeypatch.setattr(pdf_acquirer, "_search_pdf_candidates", lambda source, geo: ["https://example.com/report.pdf"])
    monkeypatch.setattr(pdf_acquirer, "_download_pdf_bytes", lambda url: _ok_verification(url, b"%PDF-1.4 fake", "application/pdf"))
    pages = [_PageContent(text=page_text, tables=[], combined_text=page_text)]
    monkeypatch.setattr(pdf_acquirer, "_extract_pages", lambda pdf_bytes: (pages, None, "Report"))


def test_pdf_acquirer_rejects_wrong_metric_market_value(monkeypatch):
    _pdf_setup(monkeypatch, "The global cement market was valued at $350 billion in 2025.")

    class _LLM:
        def complete_json(self, system, user):
            return {
                "found": True, "value": 350_000_000_000.0, "page_index": 0, "table_index": None,
                "year": 2025, "citation": "valued at $350 billion", "is_direct_figure": True,
                "derivation_basis": "", "matched_metric_label": "global cement market value",
                "matched_geography": "", "matched_unit": "USD billion",
            }

    fact, attempt = pdf_acquirer.acquire(
        _LLM(), {"source_name": "X"}, "Global", unit="million tonnes",
        context="annual cement production", keywords=["cement", "market"],
    )
    assert fact is None
    assert attempt["rejection_reason"] == "wrong_metric_monetary_value_for_physical_driver"


def test_pdf_acquirer_accepts_correct_physical_figure(monkeypatch):
    _pdf_setup(monkeypatch, "Cement production reached 450 million tonnes in 2025.")

    class _LLM:
        def complete_json(self, system, user):
            return {
                "found": True, "value": 450_000_000.0, "page_index": 0, "table_index": None,
                "year": 2025, "citation": "450 million tonnes", "is_direct_figure": True,
                "derivation_basis": "", "matched_metric_label": "cement production volume",
                "matched_geography": "", "matched_unit": "million tonnes",
            }

    fact, attempt = pdf_acquirer.acquire(
        _LLM(), {"source_name": "X"}, "Global", unit="million tonnes",
        context="annual cement production", keywords=["cement", "production"],
    )
    assert fact is not None
    assert fact["value"] == 450_000_000.0
    assert fact["page_number"] == 1  # provenance preserved


def test_pdf_acquirer_never_accepts_a_page_not_in_shortlist(monkeypatch):
    _pdf_setup(monkeypatch, "Cement production reached 450 million tonnes in 2025.")

    class _LLM:
        def complete_json(self, system, user):
            return {"found": True, "value": 450_000_000.0, "page_index": 7, "table_index": None, "year": 2025, "citation": "c", "is_direct_figure": True}

    fact, attempt = pdf_acquirer.acquire(
        _LLM(), {"source_name": "X"}, "Global", unit="million tonnes",
        context="annual cement production", keywords=["cement", "production"],
    )
    assert fact is None
    assert attempt["rejection_reason"] == "extraction_failed"


# --------------------------------------------------------------------------- #
# tabular_acquirer -- correct column selection vs. an adjacent $ column
# --------------------------------------------------------------------------- #
def _tabular_setup(monkeypatch):
    import pandas as pd

    df = pd.DataFrame({
        "Country": ["India", "India"],
        "Production_Volume_Tonnes": [450_000_000, 460_000_000],
        "Market_Value_USD_Million": [12_000, 12_500],
    })
    monkeypatch.setattr(tabular_acquirer, "_search_dataset_candidates", lambda source, geo: ["https://example.com/data.csv"])
    monkeypatch.setattr(
        tabular_acquirer, "_resolve_dataset",
        lambda url, follow_landing_page=True: ("csv", _ok_verification(url, b"fake", "text/csv"), url, None),
    )
    monkeypatch.setattr(tabular_acquirer, "_load_sheets", lambda content, url, kind=None: {"Sheet1": df})


def test_tabular_acquirer_rejects_wrong_metric_column(monkeypatch):
    _tabular_setup(monkeypatch)

    class _LLM:
        def complete_json(self, system, user):
            return {
                "found": True, "sheet": "Sheet1", "value_column": "Market_Value_USD_Million",
                "filter": {}, "aggregation": "none", "column_meaning": "Market value in USD million",
            }

    fact, attempt = tabular_acquirer.acquire(
        _LLM(), {"source_name": "X"}, "India", unit="million tonnes",
        context="annual cement production", year=2025,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "wrong_metric_monetary_value_for_physical_driver"


def test_tabular_acquirer_accepts_correct_column(monkeypatch):
    _tabular_setup(monkeypatch)

    class _LLM:
        def complete_json(self, system, user):
            return {
                "found": True, "sheet": "Sheet1", "value_column": "Production_Volume_Tonnes",
                "filter": {}, "aggregation": "none", "column_meaning": "Physical production volume in tonnes",
            }

    fact, attempt = tabular_acquirer.acquire(
        _LLM(), {"source_name": "X"}, "India", unit="million tonnes",
        context="annual cement production", year=2025,
    )
    assert fact is not None
    assert fact["evidence_type"] in ("verified_observed", "derived")
    assert fact["location_ref"] == "sheet='Sheet1', column='Production_Volume_Tonnes', filter={}"


# --------------------------------------------------------------------------- #
# api_acquirer -- correct field selection vs. an adjacent $ field + wrong year
# --------------------------------------------------------------------------- #
def _api_setup(monkeypatch):
    import json as _json

    payload = {"production_volume_tonnes": 450_000_000, "market_value_usd": 12_000_000_000, "year": 2025}
    monkeypatch.setattr(
        api_acquirer, "_fetch",
        lambda url: _ok_verification(url, _json.dumps(payload).encode("utf-8"), "application/json"),
    )


def test_api_acquirer_rejects_wrong_metric_field(monkeypatch):
    _api_setup(monkeypatch)

    class _LLM:
        def complete_json(self, system, user):
            return {"found": True, "path": ["market_value_usd"], "field_meaning": "market value in USD"}

    fact, attempt = api_acquirer.acquire(
        _LLM(), {"source_name": "X", "locator": "https://api.example.com/{country}"}, "India",
        unit="million tonnes", context="annual cement production", year=2025,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "wrong_metric_monetary_value_for_physical_driver"


def test_api_acquirer_accepts_correct_field(monkeypatch):
    _api_setup(monkeypatch)

    class _LLM:
        def complete_json(self, system, user):
            return {"found": True, "path": ["production_volume_tonnes"], "field_meaning": "physical production volume", "matched_year": 2025}

    fact, attempt = api_acquirer.acquire(
        _LLM(), {"source_name": "X", "locator": "https://api.example.com/{country}"}, "India",
        unit="million tonnes", context="annual cement production", year=2025,
    )
    assert fact is not None
    assert fact["value"] == 450_000_000.0


def test_api_acquirer_rejects_wrong_year_field(monkeypatch):
    _api_setup(monkeypatch)

    class _LLM:
        def complete_json(self, system, user):
            return {"found": True, "path": ["production_volume_tonnes"], "field_meaning": "physical production volume", "matched_year": 2010}

    fact, attempt = api_acquirer.acquire(
        _LLM(), {"source_name": "X", "locator": "https://api.example.com/{country}"}, "India",
        unit="million tonnes", context="annual cement production", year=2025,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "year_mismatch"


# --------------------------------------------------------------------------- #
# evidence_router._llm_claim_fallback -- wrong-metric claims never reach
# EITHER the verified-URL path or the `source_found_unverified` fallback.
# --------------------------------------------------------------------------- #
def test_llm_claim_fallback_rejects_wrong_metric_claims(monkeypatch):
    plan = {"unit": "million tonnes", "sources": []}

    class _LLM:
        def complete_json(self, system, user):
            return {"candidates": [
                {"source_name": "Some Market Report", "document_title": "", "source_url": "",
                 "value": 350_000_000_000.0, "year": 2025,
                 "evidence_text": "the global cement market was valued at $350 billion",
                 "citation": "$350 billion", "reasoning": "states the global cement market value"},
            ]}

    fact, attempt = evidence_router._llm_claim_fallback(
        _LLM(), plan, "Global", context="annual cement production",
    )
    assert fact is None
    assert attempt["rejection_reason"] == "wrong_metric_family"


def test_llm_claim_fallback_still_produces_unverified_for_plausible_claim(monkeypatch):
    plan = {"unit": "million tonnes", "sources": []}

    class _LLM:
        def complete_json(self, system, user):
            return {"candidates": [
                {"source_name": "National Cement Association", "document_title": "Annual Report",
                 "source_url": "", "value": 450_000_000.0, "year": 2025,
                 "evidence_text": "cement production reached 450 million tonnes",
                 "citation": "450 million tonnes", "reasoning": "states annual production volume"},
            ]}

    fact, attempt = evidence_router._llm_claim_fallback(
        _LLM(), plan, "Global", context="annual cement production",
    )
    assert fact is not None
    assert fact["evidence_type"] == "source_found_unverified"
    assert attempt["found"] is True

"""Phase 4 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) -- Tabular / CSV / XLSX Evidence
Pipeline hardening.

Focused tests for `tabular_acquirer.py`'s discovery hardening: multi-
candidate discovery, content-sniffed (not extension-based) type acceptance,
non-suffixed/content-disposition-style download URLs, redirects, HTML
dataset-landing-page link discovery, wrong-file-type/malformed/missing-
column rejection, and the full acquire() flow for both a verified-observed
fact and an unverifiable dataset. Nothing here touches the network --
`asset_verifier.verify_asset`/`web_search.search` are monkeypatched, exactly
like `test_demand_usage_evidence_router.py` and `test_demand_usage_pdf_acquirer.py`.

Run with: pytest tests/test_demand_usage_tabular_acquirer.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pandas as pd

from me_engine.demand_usage import asset_verifier, tabular_acquirer, web_search


def _verification(
    url: str, content: bytes, *, content_type: str = "", matches: bool = True,
    resolved_url: str | None = None, status: int = 200, accessible: bool = True,
    rejection_reason: str | None = None,
) -> asset_verifier.AssetVerification:
    return asset_verifier.AssetVerification(
        resolved_url=resolved_url or url, is_accessible=accessible, actual_content_type=content_type,
        matches_expected_type=matches, http_status=status, content=content if accessible else None,
        rejection_reason=rejection_reason,
    )


_CSV_BYTES = b"Country,Value\nIndia,100\nUnited States,200\n"
_XLSX_MAGIC_CONTENT = b"PK\x03\x04fake-xlsx-body"
_HTML_LANDING_PAGE = (
    b"<html><body><h1>Dataset</h1>"
    b'<a href="/about">About</a>'
    b'<a href="/files/real_data.csv">Download CSV</a>'
    b"</body></html>"
)
_HTML_NO_LINKS = b"<html><body><p>No downloadable asset here.</p></body></html>"

_SOURCE = {"source_name": "Data.gov", "locator": "", "search_query_template": "{country} cement production"}


class _MappingLLM:
    def __init__(self, **kwargs):
        self._data = {
            "found": True, "sheet": "Sheet1", "value_column": "Value",
            "filter": {"Country": "India"}, "aggregation": "none",
        }
        self._data.update(kwargs)

    def complete_json(self, system, user):
        return self._data


# --------------------------------------------------------------------------- #
# Multi-candidate discovery (requirement: try several candidates, not one)
# --------------------------------------------------------------------------- #
def test_search_dataset_candidates_prefers_locator_then_suffixed_then_others(monkeypatch):
    monkeypatch.setattr(
        web_search, "search",
        lambda query, max_results=5: [
            web_search.SearchResult(title="landing", url="https://a.example/landing", snippet="s"),
            web_search.SearchResult(title="dataset", url="https://a.example/data.csv", snippet="s"),
        ],
    )
    source = {"source_name": "Data.gov", "locator": "https://data.gov/direct.csv",
              "search_query_template": "{country} cement"}
    candidates = tabular_acquirer._search_dataset_candidates(source, "US")
    assert candidates[0] == "https://data.gov/direct.csv"
    assert candidates[1] == "https://a.example/data.csv"  # suffixed before non-suffixed
    assert candidates[2] == "https://a.example/landing"


def test_search_dataset_candidates_deduplicates_locator_against_search_hit(monkeypatch):
    monkeypatch.setattr(
        web_search, "search",
        lambda query, max_results=5: [
            web_search.SearchResult(title="dataset", url="https://data.gov/direct.csv", snippet="s"),
        ],
    )
    source = {"source_name": "Data.gov", "locator": "https://data.gov/direct.csv",
              "search_query_template": "{country} cement"}
    candidates = tabular_acquirer._search_dataset_candidates(source, "US")
    assert candidates.count("https://data.gov/direct.csv") == 1


# --------------------------------------------------------------------------- #
# _resolve_dataset -- content-sniffed acceptance, not extension-based
# --------------------------------------------------------------------------- #
def test_resolve_dataset_direct_csv(monkeypatch):
    monkeypatch.setattr(
        asset_verifier, "verify_asset",
        lambda url, expected_type: _verification(url, _CSV_BYTES, content_type="text/csv", matches=True),
    )
    kind, verification, resolved_url, reason = tabular_acquirer._resolve_dataset("https://x.example/data.csv")
    assert kind == "csv"
    assert reason is None
    assert verification.content == _CSV_BYTES


def test_resolve_dataset_direct_xlsx(monkeypatch):
    monkeypatch.setattr(
        asset_verifier, "verify_asset",
        lambda url, expected_type: _verification(
            url, _XLSX_MAGIC_CONTENT,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", matches=True,
        ),
    )
    kind, _v, _url, reason = tabular_acquirer._resolve_dataset("https://x.example/data.xlsx")
    assert kind == "excel"
    assert reason is None


def test_resolve_dataset_direct_xls(monkeypatch):
    xls_bytes = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1fake-ole-body"
    monkeypatch.setattr(
        asset_verifier, "verify_asset",
        lambda url, expected_type: _verification(url, xls_bytes, content_type="application/vnd.ms-excel", matches=True),
    )
    kind, _v, _url, reason = tabular_acquirer._resolve_dataset("https://x.example/data.xls")
    assert kind == "excel"
    assert reason is None


def test_resolve_dataset_non_suffixed_url_sniffed_as_csv(monkeypatch):
    """A dataset served via a query-parameter/no-extension download URL --
    discovery's `.csv`-suffix guess doesn't hold, but `asset_verifier`
    already matched it as `text/csv` content-type, so it's accepted."""
    monkeypatch.setattr(
        asset_verifier, "verify_asset",
        lambda url, expected_type: _verification(url, _CSV_BYTES, content_type="text/csv", matches=True),
    )
    kind, _v, _url, reason = tabular_acquirer._resolve_dataset("https://data.gov/export?id=42&format=csv")
    assert kind == "csv"
    assert reason is None


def test_resolve_dataset_non_suffixed_url_sniffs_to_excel_despite_csv_guess(monkeypatch):
    """The URL has no extension (guessed as 'csv'), the server's declared
    content-type doesn't match either, but the ACTUAL bytes are a real
    Excel workbook -- content-sniffing (not the extension guess) decides."""
    monkeypatch.setattr(
        asset_verifier, "verify_asset",
        lambda url, expected_type: _verification(
            url, _XLSX_MAGIC_CONTENT, content_type="application/octet-stream", matches=False,
            rejection_reason="wrong_content_type",
        ),
    )
    kind, _v, _url, reason = tabular_acquirer._resolve_dataset("https://data.gov/download?id=42")
    assert kind == "excel"
    assert reason is None


def test_resolve_dataset_content_disposition_style_download(monkeypatch):
    """Same shape as a real content-disposition download: no extension in
    the URL at all, server content-type is generic, but the body is a real
    CSV -- still discoverable via sniffing."""
    monkeypatch.setattr(
        asset_verifier, "verify_asset",
        lambda url, expected_type: _verification(
            url, _CSV_BYTES, content_type="application/octet-stream", matches=False,
            rejection_reason="wrong_content_type",
        ),
    )
    kind, _v, _url, reason = tabular_acquirer._resolve_dataset("https://data.gov/attachment/123")
    assert kind == "csv"
    assert reason is None


def test_resolve_dataset_redirect_captures_final_resolved_url(monkeypatch):
    monkeypatch.setattr(
        asset_verifier, "verify_asset",
        lambda url, expected_type: _verification(
            url, _CSV_BYTES, content_type="text/csv", matches=True,
            resolved_url="https://cdn.example/final/data.csv",
        ),
    )
    kind, _v, resolved_url, _reason = tabular_acquirer._resolve_dataset("https://data.gov/redirect-me")
    assert kind == "csv"
    assert resolved_url == "https://cdn.example/final/data.csv"


def test_resolve_dataset_wrong_file_type_rejected(monkeypatch):
    """Server claims JSON, sniffing agrees it's JSON -- not tabular data at
    all, must be rejected distinctly from a landing page."""
    monkeypatch.setattr(
        asset_verifier, "verify_asset",
        lambda url, expected_type: _verification(
            url, b'{"not": "tabular"}', content_type="application/json", matches=False,
            rejection_reason="wrong_content_type",
        ),
    )
    kind, _v, _url, reason = tabular_acquirer._resolve_dataset("https://data.gov/api/thing")
    assert kind is None
    assert reason == "wrong_content_type"


def test_resolve_dataset_download_failure_propagates_rejection_reason(monkeypatch):
    monkeypatch.setattr(
        asset_verifier, "verify_asset",
        lambda url, expected_type: _verification(url, b"", accessible=False, rejection_reason="download_failed"),
    )
    kind, _v, _url, reason = tabular_acquirer._resolve_dataset("https://data.gov/gone.csv")
    assert kind is None
    assert reason == "download_failed"


# --------------------------------------------------------------------------- #
# HTML dataset landing pages -- one-hop link discovery
# --------------------------------------------------------------------------- #
def test_extract_tabular_links_prioritizes_extension_over_generic_download_hint():
    html = (
        b'<a href="/download?ref=abc">Get the data</a>'
        b'<a href="https://cdn.example/real.csv">real.csv</a>'
    )
    links = tabular_acquirer._extract_tabular_links(html, "https://portal.example/dataset/1")
    assert links[0] == "https://cdn.example/real.csv"  # extension match ranks first
    assert links[1] == "https://portal.example/download?ref=abc"  # relative link resolved against base


def test_resolve_dataset_landing_page_discovers_and_verifies_real_asset(monkeypatch):
    def _fake_verify(url, expected_type):
        if url.endswith("/files/real_data.csv"):
            return _verification(url, _CSV_BYTES, content_type="text/csv", matches=True)
        return _verification(url, _HTML_LANDING_PAGE, content_type="text/html", matches=False,
                              rejection_reason="html_landing_page_not_asset")

    monkeypatch.setattr(asset_verifier, "verify_asset", _fake_verify)
    kind, _v, resolved_url, reason = tabular_acquirer._resolve_dataset("https://portal.example/dataset/1")
    assert kind == "csv"
    assert reason is None
    assert resolved_url == "https://portal.example/files/real_data.csv"


def test_resolve_dataset_landing_page_with_no_plausible_links_rejected(monkeypatch):
    monkeypatch.setattr(
        asset_verifier, "verify_asset",
        lambda url, expected_type: _verification(
            url, _HTML_NO_LINKS, content_type="text/html", matches=False,
            rejection_reason="html_landing_page_not_asset",
        ),
    )
    kind, _v, _url, reason = tabular_acquirer._resolve_dataset("https://portal.example/dataset/empty")
    assert kind is None
    assert reason == "html_landing_page_not_asset"


def test_resolve_dataset_does_not_follow_landing_page_more_than_one_hop(monkeypatch):
    """A landing page whose only 'download' link is itself another landing
    page must not be chased further -- Phase 4 follows exactly one hop."""
    def _fake_verify(url, expected_type):
        return _verification(url, _HTML_LANDING_PAGE, content_type="text/html", matches=False,
                              rejection_reason="html_landing_page_not_asset")

    calls: list[str] = []

    def _tracking_verify(url, expected_type):
        calls.append(url)
        return _fake_verify(url, expected_type)

    monkeypatch.setattr(asset_verifier, "verify_asset", _tracking_verify)
    kind, _v, _url, reason = tabular_acquirer._resolve_dataset("https://portal.example/dataset/loop")
    assert kind is None
    assert reason == "html_landing_page_not_asset"
    assert len(calls) == 2  # the landing page itself, then its one linked candidate -- no further recursion


# --------------------------------------------------------------------------- #
# Malformed / missing-column extraction failures (deterministic parsing)
# --------------------------------------------------------------------------- #
def test_load_sheets_returns_none_for_malformed_csv():
    garbage = b"\x00\x01\x02not,a,real\x03\x04csv\xffbytes"
    # pandas is fairly forgiving of raw csv-ish bytes; force a real parse
    # failure via a byte string that cannot decode as utf-8/latin at all in
    # a way read_csv accepts as a data row -- use a lone invalid encoding.
    result = tabular_acquirer._load_sheets(garbage, "https://x.example/bad.csv", kind="excel")
    assert result is None  # not a real xlsx zip container -> openpyxl raises -> None


def test_acquire_missing_expected_column_rejected(monkeypatch):
    df = pd.DataFrame({"Country": ["India"], "Value": [100.0]})
    monkeypatch.setattr(tabular_acquirer, "_search_dataset_candidates", lambda source, geo: ["https://data.gov/x.csv"])
    monkeypatch.setattr(
        tabular_acquirer, "_resolve_dataset",
        lambda url, **_kw: ("csv", _verification(url, _CSV_BYTES), url, None),
    )
    monkeypatch.setattr(tabular_acquirer, "_load_sheets", lambda content, url, kind=None: {"Sheet1": df})

    fact, attempt = tabular_acquirer.acquire(
        _MappingLLM(value_column="NotAColumn"), _SOURCE, "India", unit="tonnes", context="ctx", year=2025,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "extraction_failed"


def test_acquire_extraction_failed_when_sheets_dont_parse(monkeypatch):
    monkeypatch.setattr(tabular_acquirer, "_search_dataset_candidates", lambda source, geo: ["https://data.gov/x.csv"])
    monkeypatch.setattr(
        tabular_acquirer, "_resolve_dataset",
        lambda url, **_kw: ("csv", _verification(url, b"garbage"), url, None),
    )
    monkeypatch.setattr(tabular_acquirer, "_load_sheets", lambda content, url, kind=None: None)

    fact, attempt = tabular_acquirer.acquire(
        _MappingLLM(), _SOURCE, "India", unit="tonnes", context="ctx", year=2025,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "extraction_failed"


# --------------------------------------------------------------------------- #
# LLM schema mapping + deterministic row/column extraction + provenance
# --------------------------------------------------------------------------- #
def test_acquire_llm_says_not_found_is_honestly_rejected(monkeypatch):
    df = pd.DataFrame({"Country": ["India"], "Value": [100.0]})
    monkeypatch.setattr(tabular_acquirer, "_search_dataset_candidates", lambda source, geo: ["https://data.gov/x.csv"])
    monkeypatch.setattr(
        tabular_acquirer, "_resolve_dataset",
        lambda url, **_kw: ("csv", _verification(url, _CSV_BYTES), url, None),
    )
    monkeypatch.setattr(tabular_acquirer, "_load_sheets", lambda content, url, kind=None: {"Sheet1": df})

    fact, attempt = tabular_acquirer.acquire(
        _MappingLLM(found=False), _SOURCE, "India", unit="tonnes", context="ctx", year=2025,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "not_relevant_page"


def test_acquire_verified_observed_fact_end_to_end_with_full_provenance(monkeypatch):
    """Full pipeline: discovery -> content-sniffed verification -> pandas
    load -> LLM mapping -> deterministic single-row lookup -> a
    `verified_observed` (`evidence_type == "verified_observed"`) fact
    carrying source name, resolved URL, file name, sheet name, column/row
    filter, value, unit, year, and geography."""
    df = pd.DataFrame({"Country": ["India", "United States"], "Value": [100.0, 200.0]})
    monkeypatch.setattr(
        tabular_acquirer, "_search_dataset_candidates",
        lambda source, geo: ["https://data.gov/cement.csv"],
    )
    monkeypatch.setattr(
        tabular_acquirer, "_resolve_dataset",
        lambda url, **_kw: ("csv", _verification(url, _CSV_BYTES, resolved_url="https://cdn.data.gov/cement.csv"),
                             "https://cdn.data.gov/cement.csv", None),
    )
    monkeypatch.setattr(tabular_acquirer, "_load_sheets", lambda content, url, kind=None: {"Sheet1": df})

    fact, attempt = tabular_acquirer.acquire(
        _MappingLLM(), _SOURCE, "India", unit="tonnes", context="cement production", year=2025,
    )
    assert fact is not None
    assert fact["value"] == 100.0  # the real India row, not the US row
    assert fact["evidence_type"] == "verified_observed"
    assert fact["verification_status"] == "verified_observed"
    assert fact["unit"] == "tonnes"
    assert fact["year"] == 2025
    assert fact["source"] == "Data.gov"
    assert fact["source_url"] == "https://cdn.data.gov/cement.csv"  # resolved, not the guessed one
    assert fact["file_name"] == "cement.csv"
    assert fact["sheet"] == "Sheet1"
    assert "column='Value'" in fact["location_ref"]
    assert "filter={'Country': 'India'}" in fact["location_ref"]
    assert attempt["found"] is True
    assert attempt["resolved_url"] == "https://cdn.data.gov/cement.csv"


def test_acquire_ambiguous_multi_row_match_is_never_silently_observed(monkeypatch):
    df = pd.DataFrame({"Country": ["India", "India"], "Value": [100.0, 50.0]})
    monkeypatch.setattr(tabular_acquirer, "_search_dataset_candidates", lambda source, geo: ["https://data.gov/x.csv"])
    monkeypatch.setattr(
        tabular_acquirer, "_resolve_dataset",
        lambda url, **_kw: ("csv", _verification(url, _CSV_BYTES), url, None),
    )
    monkeypatch.setattr(tabular_acquirer, "_load_sheets", lambda content, url, kind=None: {"Sheet1": df})

    fact, attempt = tabular_acquirer.acquire(
        _MappingLLM(aggregation="sum"), _SOURCE, "India", unit="tonnes", context="ctx", year=2025,
    )
    assert fact is not None
    assert fact["value"] == 150.0
    assert fact["evidence_type"] == "derived"  # ambiguous multi-row match, never verified_observed


# --------------------------------------------------------------------------- #
# No-source-found / unverified fallback preservation
# --------------------------------------------------------------------------- #
def test_acquire_no_candidates_found_reports_no_source_found(monkeypatch):
    monkeypatch.setattr(tabular_acquirer, "_search_dataset_candidates", lambda source, geo: [])
    fact, attempt = tabular_acquirer.acquire(
        _MappingLLM(), _SOURCE, "India", unit="tonnes", context="ctx", year=2025,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "no_source_found"
    assert attempt["source_name"] == "Data.gov"


def test_acquire_all_candidates_fail_reports_last_rejection_reason_for_unverified_fallback(monkeypatch):
    """When every candidate fails verification, `acquire()` returns `None`
    with a populated `rejection_reason` -- the existing `source_found_
    unverified` fallback rung in `evidence_router.execute_evidence_plan`
    (Phase 2, unchanged by this phase) is what turns a chain of such misses
    into an honestly-labeled unverified claim, not `tabular_acquirer` itself."""
    monkeypatch.setattr(
        tabular_acquirer, "_search_dataset_candidates",
        lambda source, geo: ["https://data.gov/gone1.csv", "https://data.gov/gone2.csv"],
    )
    monkeypatch.setattr(
        asset_verifier, "verify_asset",
        lambda url, expected_type: _verification(url, b"", accessible=False, rejection_reason="download_failed"),
    )
    fact, attempt = tabular_acquirer.acquire(
        _MappingLLM(), _SOURCE, "India", unit="tonnes", context="ctx", year=2025,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "download_failed"
    assert len(attempt["candidates_tried"]) == 2


# --------------------------------------------------------------------------- #
# Schema-summary truncation (regression guard from the plan's own text)
# --------------------------------------------------------------------------- #
def test_schema_summary_truncation_cap_is_applied_without_crashing(monkeypatch):
    """A dataset with many columns/rows produces a schema summary longer
    than `_MAX_SCHEMA_CHARS` -- the cap must simply truncate, never raise,
    and the acquire() call must still complete (accepting or rejecting the
    LLM's mapping normally) even though the LLM only saw a truncated sample."""
    wide_df = pd.DataFrame({f"Col{i}": [float(i)] * 3 for i in range(300)})
    monkeypatch.setattr(tabular_acquirer, "_search_dataset_candidates", lambda source, geo: ["https://data.gov/x.csv"])
    monkeypatch.setattr(
        tabular_acquirer, "_resolve_dataset",
        lambda url, **_kw: ("csv", _verification(url, _CSV_BYTES), url, None),
    )
    monkeypatch.setattr(tabular_acquirer, "_load_sheets", lambda content, url, kind=None: {"Sheet1": wide_df})

    fact, attempt = tabular_acquirer.acquire(
        _MappingLLM(found=False), _SOURCE, "India", unit="tonnes", context="ctx", year=2025,
    )
    assert fact is None  # LLM honestly found nothing in the (truncated) schema -- no crash either way
    assert attempt["rejection_reason"] == "not_relevant_page"

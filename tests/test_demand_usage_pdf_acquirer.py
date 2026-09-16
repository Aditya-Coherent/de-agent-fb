"""Phase 3 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) -- PDF Evidence Pipeline hardening.

Focused tests for `pdf_acquirer.py`'s discovery/extraction hardening:
multi-candidate discovery, failure-mode-specific rejection reasons, page and
table provenance, shortlist determinism, and the full acquire() flow for
both a verified-observed fact and an unverifiable/unparseable PDF. Nothing
here touches the network -- `asset_verifier`/pdfplumber/`web_search` are all
monkeypatched, exactly like `test_demand_usage_evidence_router.py`.

Run with: pytest tests/test_demand_usage_pdf_acquirer.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from me_engine.demand_usage import asset_verifier, pdf_acquirer, web_search


def _ok_verification(url: str, content: bytes = b"%PDF-fake") -> asset_verifier.AssetVerification:
    return asset_verifier.AssetVerification(
        resolved_url=url, is_accessible=True, actual_content_type="application/pdf",
        matches_expected_type=True, http_status=200, content=content,
    )


def _failed_verification(url: str, rejection_reason: str = "download_failed") -> asset_verifier.AssetVerification:
    return asset_verifier.AssetVerification(
        resolved_url=url, is_accessible=False, actual_content_type="",
        matches_expected_type=False, http_status=404, rejection_reason=rejection_reason,
    )


def _page(text: str, tables: list | None = None) -> pdf_acquirer._PageContent:
    tables = tables or []
    combined = text if not tables else text + "\n" + pdf_acquirer._serialize_tables(tables)
    return pdf_acquirer._PageContent(text=text, tables=tables, combined_text=combined)


_SOURCE = {"source_name": "USGS", "locator": "", "search_query_template": "{country} cement production"}


class _FoundLLM:
    def __init__(self, **kwargs):
        self._data = {"found": True, "value": 100.0, "page_index": 0, "table_index": None,
                      "year": 2025, "citation": "100 tonnes", "is_direct_figure": True}
        self._data.update(kwargs)

    def complete_json(self, system, user):
        return self._data


# --------------------------------------------------------------------------- #
# Multi-candidate discovery (Phase 3 requirement #1)
# --------------------------------------------------------------------------- #
def test_search_pdf_candidates_prefers_locator_then_pdf_like_then_others(monkeypatch):
    monkeypatch.setattr(
        web_search, "search",
        lambda query, max_results=5: [
            web_search.SearchResult(title="landing", url="https://a.example/landing", snippet="s"),
            web_search.SearchResult(title="report", url="https://a.example/report.pdf", snippet="s"),
        ],
    )
    source = {"source_name": "USGS", "locator": "https://usgs.gov/direct.pdf",
              "search_query_template": "{country} cement"}
    candidates = pdf_acquirer._search_pdf_candidates(source, "US")
    assert candidates[0] == "https://usgs.gov/direct.pdf"
    assert candidates[1] == "https://a.example/report.pdf"  # pdf-looking before non-pdf-looking
    assert "https://a.example/landing" in candidates


def test_multi_candidate_discovery_uses_second_when_first_fails_verification(monkeypatch):
    monkeypatch.setattr(
        pdf_acquirer, "_search_pdf_candidates",
        lambda source, geo: ["https://broken.example/fake.pdf", "https://real.example/report.pdf"],
    )

    calls = []

    def _download(url):
        calls.append(url)
        if url == "https://broken.example/fake.pdf":
            return _failed_verification(url, "html_landing_page_not_asset")
        return _ok_verification(url)

    monkeypatch.setattr(pdf_acquirer, "_download_pdf_bytes", _download)
    monkeypatch.setattr(
        pdf_acquirer, "_extract_pages",
        lambda b: ([_page("cement production was 100 tonnes in 2025")], None, ""),
    )

    fact, attempt = pdf_acquirer.acquire(
        _FoundLLM(), _SOURCE, "US", unit="tonnes", context="ctx", keywords=["cement"],
    )
    assert calls == ["https://broken.example/fake.pdf", "https://real.example/report.pdf"]
    assert fact is not None
    assert attempt["source_url"] == "https://real.example/report.pdf"
    assert len(attempt["candidates_tried"]) == 2
    assert attempt["candidates_tried"][0]["rejection_reason"] == "html_landing_page_not_asset"


def test_all_candidates_fail_reports_last_rejection_reason(monkeypatch):
    monkeypatch.setattr(
        pdf_acquirer, "_search_pdf_candidates",
        lambda source, geo: ["https://a.example/x.pdf", "https://b.example/y.pdf"],
    )
    monkeypatch.setattr(pdf_acquirer, "_download_pdf_bytes", lambda url: _failed_verification(url, "wrong_content_type"))
    fact, attempt = pdf_acquirer.acquire(
        _FoundLLM(), _SOURCE, "US", unit="tonnes", context="ctx", keywords=["cement"],
    )
    assert fact is None
    assert attempt["rejection_reason"] == "wrong_content_type"
    assert len(attempt["candidates_tried"]) == 2


def test_no_candidates_found_at_all(monkeypatch):
    monkeypatch.setattr(pdf_acquirer, "_search_pdf_candidates", lambda source, geo: [])
    fact, attempt = pdf_acquirer.acquire(
        _FoundLLM(), _SOURCE, "US", unit="tonnes", context="ctx", keywords=["cement"],
    )
    assert fact is None
    assert attempt["rejection_reason"] == "no_source_found"


# --------------------------------------------------------------------------- #
# HTML landing page pretending to be a PDF (real asset_verifier, no mocking
# of the verification function itself -- only the network call it makes)
# --------------------------------------------------------------------------- #
def test_html_landing_page_masquerading_as_pdf_is_rejected(monkeypatch):
    class _Resp:
        url = "https://example.com/landing"
        status_code = 200
        headers = {"content-type": "text/html"}
        content = b"<!doctype html><html><body>not a pdf</body></html>"

    class _Session:
        max_redirects = 5

        def get(self, *a, **kw):
            return _Resp()

    import requests

    monkeypatch.setattr(requests, "Session", lambda: _Session())
    monkeypatch.setattr(pdf_acquirer, "_search_pdf_candidates", lambda source, geo: ["https://example.com/report.pdf"])
    fact, attempt = pdf_acquirer.acquire(
        _FoundLLM(), _SOURCE, "US", unit="tonnes", context="ctx", keywords=["cement"],
    )
    assert fact is None
    assert attempt["rejection_reason"] == "html_landing_page_not_asset"


# --------------------------------------------------------------------------- #
# Extraction failure-mode classification (Phase 3 requirement #2)
# --------------------------------------------------------------------------- #
def _acquire_with_extraction(monkeypatch, extract_return):
    monkeypatch.setattr(pdf_acquirer, "_search_pdf_candidates", lambda source, geo: ["https://usgs.gov/report.pdf"])
    monkeypatch.setattr(pdf_acquirer, "_download_pdf_bytes", lambda url: _ok_verification(url))
    monkeypatch.setattr(pdf_acquirer, "_extract_pages", lambda b: extract_return)
    return pdf_acquirer.acquire(_FoundLLM(), _SOURCE, "US", unit="tonnes", context="ctx", keywords=["cement"])


def test_encrypted_pdf_rejection_reason(monkeypatch):
    fact, attempt = _acquire_with_extraction(monkeypatch, ([], "encrypted_pdf", ""))
    assert fact is None
    assert attempt["rejection_reason"] == "encrypted_pdf"


def test_scanned_no_text_layer_rejection_reason(monkeypatch):
    fact, attempt = _acquire_with_extraction(monkeypatch, ([], "scanned_no_text_layer", ""))
    assert fact is None
    assert attempt["rejection_reason"] == "scanned_no_text_layer"


def test_corrupted_pdf_rejection_reason(monkeypatch):
    fact, attempt = _acquire_with_extraction(monkeypatch, ([], "corrupted_or_unreadable", ""))
    assert fact is None
    assert attempt["rejection_reason"] == "corrupted_or_unreadable"


def test_extract_pages_classifies_real_encrypted_pdf():
    # Minimal, deterministic check against the real pdfplumber code path (not
    # mocked) -- a completely malformed byte string can never open, and must
    # be classified as corrupted, never crash `acquire()`.
    pages, reason, title = pdf_acquirer._extract_pages(b"not a real pdf at all")
    assert pages == []
    assert reason == "corrupted_or_unreadable"


def test_extract_pages_scanned_detection_when_all_pages_blank():
    class _FakePage:
        def extract_text(self):
            return ""

        def extract_tables(self):
            return []

    class _FakePdf:
        is_encrypted = False
        metadata = {}
        pages = [_FakePage(), _FakePage()]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    import pdfplumber

    import io as _io

    orig_open = pdfplumber.open
    try:
        pdfplumber.open = lambda *_a, **_kw: _FakePdf()
        pages, reason, title = pdf_acquirer._extract_pages(b"%PDF-fake")
    finally:
        pdfplumber.open = orig_open
    assert pages == []
    assert reason == "scanned_no_text_layer"


# --------------------------------------------------------------------------- #
# Shortlist determinism (Phase 3 requirement #3)
# --------------------------------------------------------------------------- #
def test_shortlist_tie_break_is_original_page_order_not_reverse():
    # Two pages tie on keyword score -- the earlier page (index 0) must win
    # the tie, deterministically, every time.
    pages = ["cement tonnes", "cement tonnes", "irrelevant"]
    idx = pdf_acquirer._keyword_shortlist(pages, ["cement", "tonnes"])
    assert idx[:2] == [0, 1]


def test_shortlist_is_deterministic_across_repeated_calls():
    pages = ["cement production tonnes cement tonnes", "cement mentioned once", "irrelevant filler text"]
    first = pdf_acquirer._keyword_shortlist(pages, ["cement", "tonnes"])
    second = pdf_acquirer._keyword_shortlist(pages, ["cement", "tonnes"])
    assert first == second == [0, 1]


# --------------------------------------------------------------------------- #
# Table + page provenance (Phase 3 requirement -- "identify relevant
# page/table", "preserve provenance")
# --------------------------------------------------------------------------- #
def test_relevant_fact_found_in_a_table_carries_table_provenance(monkeypatch):
    table = [["Year", "Tonnes"], ["2025", "100"]]
    monkeypatch.setattr(pdf_acquirer, "_search_pdf_candidates", lambda source, geo: ["https://usgs.gov/report.pdf"])
    monkeypatch.setattr(pdf_acquirer, "_download_pdf_bytes", lambda url: _ok_verification(url))
    monkeypatch.setattr(
        pdf_acquirer, "_extract_pages",
        lambda b: ([_page("cement production summary", tables=[table])], None, "Cement Statistics 2025"),
    )
    llm = _FoundLLM(page_index=0, table_index=0, value=100.0, citation="100")
    fact, attempt = pdf_acquirer.acquire(
        llm, _SOURCE, "US", unit="tonnes", context="ctx", keywords=["cement"],
    )
    assert fact is not None
    assert fact["table_index"] == 0
    assert fact["location_ref"] == "page 1, table 1"
    assert fact["document_title"] == "Cement Statistics 2025"
    assert attempt["table_index"] == 0


def test_relevant_fact_found_in_plain_text_has_no_table_index(monkeypatch):
    monkeypatch.setattr(pdf_acquirer, "_search_pdf_candidates", lambda source, geo: ["https://usgs.gov/report.pdf"])
    monkeypatch.setattr(pdf_acquirer, "_download_pdf_bytes", lambda url: _ok_verification(url))
    monkeypatch.setattr(
        pdf_acquirer, "_extract_pages",
        lambda b: ([_page("cement production was 100 tonnes in 2025")], None, ""),
    )
    fact, attempt = pdf_acquirer.acquire(
        _FoundLLM(), _SOURCE, "US", unit="tonnes", context="ctx", keywords=["cement"],
    )
    assert fact is not None
    assert fact["table_index"] is None
    assert fact["location_ref"] == "page 1"
    assert fact["page_number"] == 1


def test_invalid_table_index_from_llm_drops_table_provenance_but_keeps_value(monkeypatch):
    monkeypatch.setattr(pdf_acquirer, "_search_pdf_candidates", lambda source, geo: ["https://usgs.gov/report.pdf"])
    monkeypatch.setattr(pdf_acquirer, "_download_pdf_bytes", lambda url: _ok_verification(url))
    monkeypatch.setattr(
        pdf_acquirer, "_extract_pages",
        lambda b: ([_page("cement production was 100 tonnes in 2025")], None, ""),
    )
    # LLM claims table_index=5 on a page with zero tables -- must not be trusted.
    llm = _FoundLLM(page_index=0, table_index=5)
    fact, attempt = pdf_acquirer.acquire(
        llm, _SOURCE, "US", unit="tonnes", context="ctx", keywords=["cement"],
    )
    assert fact is not None  # the page-anchored value is still accepted
    assert fact["table_index"] is None  # but the bogus table pointer is dropped


def test_llm_cannot_invent_a_page_not_in_the_shortlist(monkeypatch):
    monkeypatch.setattr(pdf_acquirer, "_search_pdf_candidates", lambda source, geo: ["https://usgs.gov/report.pdf"])
    monkeypatch.setattr(pdf_acquirer, "_download_pdf_bytes", lambda url: _ok_verification(url))
    monkeypatch.setattr(
        pdf_acquirer, "_extract_pages",
        lambda b: ([_page("cement production was 100 tonnes in 2025")], None, ""),
    )
    llm = _FoundLLM(page_index=7)  # not a real page, not in the shortlist
    fact, attempt = pdf_acquirer.acquire(
        llm, _SOURCE, "US", unit="tonnes", context="ctx", keywords=["cement"],
    )
    assert fact is None
    assert attempt["rejection_reason"] == "extraction_failed"


# --------------------------------------------------------------------------- #
# Table spanning multiple pages
# --------------------------------------------------------------------------- #
def test_table_spanning_two_pages_is_merged_for_shortlisting_and_provenance(monkeypatch):
    table_p0 = [["Country", "Tonnes"], ["US", "100"]]
    table_p1 = [["China", "500"]]  # continuation -- same column count, no header
    monkeypatch.setattr(pdf_acquirer, "_search_pdf_candidates", lambda source, geo: ["https://usgs.gov/report.pdf"])
    monkeypatch.setattr(pdf_acquirer, "_download_pdf_bytes", lambda url: _ok_verification(url))

    def _real_extract(pdf_bytes):
        pages = [
            pdf_acquirer._PageContent(text="cement table (cont'd)", tables=[table_p0]),
            pdf_acquirer._PageContent(text="", tables=[table_p1]),
        ]
        for p in pages:
            p.combined_text = pdf_acquirer._rebuild_combined_text(p)
        pdf_acquirer._merge_spanning_tables(pages)
        return pages, None, ""

    monkeypatch.setattr(pdf_acquirer, "_extract_pages", _real_extract)
    llm = _FoundLLM(page_index=0, table_index=0, value=500.0, citation="500")
    fact, attempt = pdf_acquirer.acquire(
        llm, _SOURCE, "US", unit="tonnes", context="ctx", keywords=["cement"],
    )
    assert fact is not None
    # The continuation row (China/500) is now reachable from page 0's merged table.
    assert "China" in fact["evidence_text"]
    assert fact["page_number"] == 1
    assert fact["table_index"] == 0


def test_merge_spanning_tables_skips_mismatched_column_counts():
    table_p0 = [["A", "B"], ["1", "2"]]
    table_p1 = [["X", "Y", "Z"]]  # different column count -- not a continuation
    pages = [
        pdf_acquirer._PageContent(text="", tables=[table_p0]),
        pdf_acquirer._PageContent(text="", tables=[table_p1]),
    ]
    pdf_acquirer._merge_spanning_tables(pages)
    assert pages[0].tables == [table_p0]
    assert pages[1].tables == [table_p1]


# --------------------------------------------------------------------------- #
# End-to-end: verified_observed vs unverifiable/unparseable outcomes
# --------------------------------------------------------------------------- #
def test_verified_observed_fact_from_accessible_and_parseable_pdf(monkeypatch):
    monkeypatch.setattr(pdf_acquirer, "_search_pdf_candidates", lambda source, geo: ["https://usgs.gov/report.pdf"])
    monkeypatch.setattr(pdf_acquirer, "_download_pdf_bytes", lambda url: _ok_verification(url))
    monkeypatch.setattr(
        pdf_acquirer, "_extract_pages",
        lambda b: ([_page("cement production was 100 tonnes in 2025")], None, "USGS Cement Report"),
    )
    fact, attempt = pdf_acquirer.acquire(
        _FoundLLM(), _SOURCE, "US", unit="tonnes", context="ctx", keywords=["cement"],
    )
    assert fact is not None
    assert fact["evidence_type"] == "verified_observed"
    assert fact["source_url"] == "https://usgs.gov/report.pdf"
    assert fact["document_title"] == "USGS Cement Report"
    assert attempt["found"] is True


def test_unverified_when_pdf_content_never_actually_states_the_value(monkeypatch):
    # The candidate value IS extractable text-wise, but does not appear in
    # the page the LLM points at -- `evidence.build_fact` must downgrade
    # away from `observed`, never accept the LLM's word alone.
    monkeypatch.setattr(pdf_acquirer, "_search_pdf_candidates", lambda source, geo: ["https://usgs.gov/report.pdf"])
    monkeypatch.setattr(pdf_acquirer, "_download_pdf_bytes", lambda url: _ok_verification(url))
    monkeypatch.setattr(
        pdf_acquirer, "_extract_pages",
        lambda b: ([_page("cement production was 100 tonnes in 2025")], None, ""),
    )
    # LLM claims a completely different, untraceable value.
    llm = _FoundLLM(value=999999.0, citation="not really in the text")
    fact, attempt = pdf_acquirer.acquire(
        llm, _SOURCE, "US", unit="tonnes", context="ctx", keywords=["cement"],
    )
    assert fact is not None
    assert fact["evidence_type"] != "verified_observed"


def test_source_found_unverified_when_pdf_cannot_be_acquired(monkeypatch):
    # Download fails outright -- the pipeline must never claim `observed`;
    # `execute_evidence_plan`'s Phase 2 LLM-claim fallback (unchanged, not
    # touched by Phase 3) is what may later tag this `source_found_unverified`
    # -- `pdf_acquirer.acquire` itself just reports the honest miss.
    monkeypatch.setattr(pdf_acquirer, "_search_pdf_candidates", lambda source, geo: ["https://usgs.gov/report.pdf"])
    monkeypatch.setattr(pdf_acquirer, "_download_pdf_bytes", lambda url: _failed_verification(url))
    fact, attempt = pdf_acquirer.acquire(
        _FoundLLM(), _SOURCE, "US", unit="tonnes", context="ctx", keywords=["cement"],
    )
    assert fact is None
    assert attempt["found"] is False
    assert attempt["rejection_reason"] == "download_failed"


def test_llm_page_interpretation_failure_is_reported_as_extraction_failed(monkeypatch):
    monkeypatch.setattr(pdf_acquirer, "_search_pdf_candidates", lambda source, geo: ["https://usgs.gov/report.pdf"])
    monkeypatch.setattr(pdf_acquirer, "_download_pdf_bytes", lambda url: _ok_verification(url))
    monkeypatch.setattr(
        pdf_acquirer, "_extract_pages",
        lambda b: ([_page("cement production was 100 tonnes in 2025")], None, ""),
    )

    class _RaisingLLM:
        def complete_json(self, system, user):
            raise RuntimeError("LLM call failed")

    fact, attempt = pdf_acquirer.acquire(
        _RaisingLLM(), _SOURCE, "US", unit="tonnes", context="ctx", keywords=["cement"],
    )
    assert fact is None
    assert attempt["rejection_reason"] == "extraction_failed"


def test_llm_says_not_found_is_reported_as_extraction_failed(monkeypatch):
    monkeypatch.setattr(pdf_acquirer, "_search_pdf_candidates", lambda source, geo: ["https://usgs.gov/report.pdf"])
    monkeypatch.setattr(pdf_acquirer, "_download_pdf_bytes", lambda url: _ok_verification(url))
    monkeypatch.setattr(
        pdf_acquirer, "_extract_pages",
        lambda b: ([_page("cement production was 100 tonnes in 2025")], None, ""),
    )

    class _NotFoundLLM:
        def complete_json(self, system, user):
            return {"found": False}

    fact, attempt = pdf_acquirer.acquire(
        _NotFoundLLM(), _SOURCE, "US", unit="tonnes", context="ctx", keywords=["cement"],
    )
    assert fact is None
    assert attempt["rejection_reason"] == "extraction_failed"


# --------------------------------------------------------------------------- #
# Valid single-page / multi-page PDFs via the real pdfplumber code path
# --------------------------------------------------------------------------- #
def _make_real_pdf_bytes(pages_text: list[str]) -> bytes:
    """Build a tiny, genuinely valid multi-page PDF using reportlab if
    available; otherwise skip the test that needs it."""
    import io

    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for text in pages_text:
        c.drawString(72, 720, text)
        c.showPage()
    c.save()
    return buf.getvalue()


def test_extract_pages_handles_a_real_single_page_pdf():
    try:
        pdf_bytes = _make_real_pdf_bytes(["Cement production was 100 tonnes in 2025."])
    except ImportError:
        import pytest

        pytest.skip("reportlab not installed -- skipping real-PDF construction test")
    pages, reason, title = pdf_acquirer._extract_pages(pdf_bytes)
    assert reason is None
    assert len(pages) == 1
    assert "100" in pages[0].text and "tonnes" in pages[0].text


def test_extract_pages_handles_a_real_multi_page_pdf():
    try:
        pdf_bytes = _make_real_pdf_bytes(["Page one text.", "Cement production was 100 tonnes in 2025.", "Page three text."])
    except ImportError:
        import pytest

        pytest.skip("reportlab not installed -- skipping real-PDF construction test")
    pages, reason, title = pdf_acquirer._extract_pages(pdf_bytes)
    assert reason is None
    assert len(pages) == 3
    assert "100" in pages[1].text

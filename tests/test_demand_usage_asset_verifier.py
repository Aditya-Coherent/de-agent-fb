"""Phase 1 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) -- `asset_verifier.verify_asset`
tests.

Everything that would touch the network (`requests.Session.get`) is
monkeypatched with a fake response object -- these are unit tests of the
deterministic verification discipline (redirects, HTTP status, magic
bytes/content-type sniffing, HTML-landing-page detection), not integration
tests of real downloads.

Run with: pytest tests/test_demand_usage_asset_verifier.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest
import requests

from me_engine.demandUseCaseCountry_approach2B import asset_verifier


class _FakeResponse:
    def __init__(self, *, url: str, status_code: int, content: bytes, content_type: str = ""):
        self.url = url
        self.status_code = status_code
        self.content = content
        self.headers = {"content-type": content_type} if content_type else {}


def _patch_get(monkeypatch, fn):
    """Patch `requests.Session.get` (what `verify_asset` actually calls)."""
    monkeypatch.setattr(requests.Session, "get", lambda self, url, **kw: fn(url, **kw))


# --------------------------------------------------------------------------- #
# Valid, matching assets
# --------------------------------------------------------------------------- #
def test_valid_web_page_is_accessible_and_matches_html(monkeypatch):
    _patch_get(monkeypatch, lambda url, **kw: _FakeResponse(
        url=url, status_code=200, content=b"<html><body>Some real content 8.9 million</body></html>",
        content_type="text/html",
    ))
    result = asset_verifier.verify_asset("https://eia.gov/page", "html")
    assert result.is_accessible is True
    assert result.matches_expected_type is True
    assert result.rejection_reason is None
    assert result.resolved_url == "https://eia.gov/page"


def test_valid_pdf_url_matches_via_magic_bytes(monkeypatch):
    _patch_get(monkeypatch, lambda url, **kw: _FakeResponse(
        url=url, status_code=200, content=b"%PDF-1.4\n%real pdf body", content_type="application/pdf",
    ))
    result = asset_verifier.verify_asset("https://usgs.gov/report.pdf", "pdf")
    assert result.is_accessible is True
    assert result.matches_expected_type is True
    assert result.content == b"%PDF-1.4\n%real pdf body"


def test_valid_csv_is_accepted(monkeypatch):
    _patch_get(monkeypatch, lambda url, **kw: _FakeResponse(
        url=url, status_code=200, content=b"Country,Value\nIndia,100\n", content_type="text/csv",
    ))
    result = asset_verifier.verify_asset("https://data.gov/x.csv", "csv")
    assert result.matches_expected_type is True


def test_valid_xlsx_matches_via_zip_magic_bytes(monkeypatch):
    _patch_get(monkeypatch, lambda url, **kw: _FakeResponse(
        url=url, status_code=200, content=b"PK\x03\x04" + b"\x00" * 20,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ))
    result = asset_verifier.verify_asset("https://data.gov/x.xlsx", "excel")
    assert result.matches_expected_type is True


def test_valid_xls_matches_via_ole2_magic_bytes(monkeypatch):
    _patch_get(monkeypatch, lambda url, **kw: _FakeResponse(
        url=url, status_code=200, content=b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 20,
        content_type="application/vnd.ms-excel",
    ))
    result = asset_verifier.verify_asset("https://data.gov/x.xls", "excel")
    assert result.matches_expected_type is True


def test_valid_api_json_endpoint_is_accepted(monkeypatch):
    body = json.dumps({"data": {"value": 42}}).encode("utf-8")
    _patch_get(monkeypatch, lambda url, **kw: _FakeResponse(
        url=url, status_code=200, content=body, content_type="application/json",
    ))
    result = asset_verifier.verify_asset("https://api.example.com/v1/series", "json")
    assert result.matches_expected_type is True
    assert json.loads(result.content) == {"data": {"value": 42}}


# --------------------------------------------------------------------------- #
# HTML landing pages masquerading as a real asset
# --------------------------------------------------------------------------- #
def test_pdf_looking_url_that_actually_returns_html_is_rejected(monkeypatch):
    _patch_get(monkeypatch, lambda url, **kw: _FakeResponse(
        url=url, status_code=200, content=b"<html><body>Please enable JavaScript</body></html>",
        content_type="text/html",
    ))
    result = asset_verifier.verify_asset("https://usgs.gov/report.pdf", "pdf")
    assert result.is_accessible is True
    assert result.matches_expected_type is False
    assert result.rejection_reason == "html_landing_page_not_asset"


def test_invalid_api_endpoint_returning_html_error_page_is_rejected(monkeypatch):
    _patch_get(monkeypatch, lambda url, **kw: _FakeResponse(
        url=url, status_code=200, content=b"<html><body>404 - Not Found</body></html>",
        content_type="text/html",
    ))
    result = asset_verifier.verify_asset("https://api.example.com/bad", "json")
    assert result.matches_expected_type is False
    assert result.rejection_reason == "html_landing_page_not_asset"


def test_csv_url_returning_html_content_type_mismatch_is_rejected(monkeypatch):
    _patch_get(monkeypatch, lambda url, **kw: _FakeResponse(
        url=url, status_code=200, content=b"<html><body>error page</body></html>", content_type="text/html",
    ))
    result = asset_verifier.verify_asset("https://data.gov/x.csv", "csv")
    assert result.matches_expected_type is False
    assert result.rejection_reason == "html_landing_page_not_asset"


# --------------------------------------------------------------------------- #
# Invalid / unreachable / erroring URLs
# --------------------------------------------------------------------------- #
def test_invalid_file_url_download_failed(monkeypatch):
    _patch_get(monkeypatch, lambda url, **kw: (_ for _ in ()).throw(requests.exceptions.ConnectionError("no dns")))
    result = asset_verifier.verify_asset("https://does-not-exist.example/report.pdf", "pdf")
    assert result.is_accessible is False
    assert result.rejection_reason == "download_failed"


def test_http_error_status_is_download_failed(monkeypatch):
    _patch_get(monkeypatch, lambda url, **kw: _FakeResponse(url=url, status_code=404, content=b"", content_type=""))
    result = asset_verifier.verify_asset("https://data.gov/missing.csv", "csv")
    assert result.is_accessible is False
    assert result.http_status == 404
    assert result.rejection_reason == "download_failed"


def test_server_error_status_is_download_failed(monkeypatch):
    _patch_get(monkeypatch, lambda url, **kw: _FakeResponse(url=url, status_code=500, content=b"", content_type=""))
    result = asset_verifier.verify_asset("https://api.example.com/series", "json")
    assert result.is_accessible is False
    assert result.rejection_reason == "download_failed"


def test_malformed_url_is_rejected_without_a_network_call(monkeypatch):
    def _should_not_be_called(url, **kw):
        raise AssertionError("verify_asset must not attempt a network call for a malformed URL")

    _patch_get(monkeypatch, _should_not_be_called)
    result = asset_verifier.verify_asset("not-a-real-url", "pdf")
    assert result.is_accessible is False
    assert result.rejection_reason == "malformed_url"


def test_empty_url_is_rejected():
    result = asset_verifier.verify_asset("", "pdf")
    assert result.is_accessible is False
    assert result.rejection_reason == "malformed_url"


# --------------------------------------------------------------------------- #
# Redirects
# --------------------------------------------------------------------------- #
def test_redirect_resolution_captures_the_final_url(monkeypatch):
    _patch_get(monkeypatch, lambda url, **kw: _FakeResponse(
        url="https://cdn.usgs.gov/reports/final-2025.pdf", status_code=200,
        content=b"%PDF-1.4 real body", content_type="application/pdf",
    ))
    result = asset_verifier.verify_asset("https://usgs.gov/report.pdf", "pdf")
    assert result.resolved_url == "https://cdn.usgs.gov/reports/final-2025.pdf"
    assert result.matches_expected_type is True


def test_redirect_loop_is_a_distinct_rejection_reason(monkeypatch):
    _patch_get(monkeypatch, lambda url, **kw: (_ for _ in ()).throw(
        requests.exceptions.TooManyRedirects("exceeded max redirects")
    ))
    result = asset_verifier.verify_asset("https://example.com/loop", "pdf")
    assert result.is_accessible is False
    assert result.rejection_reason == "redirect_loop"


# --------------------------------------------------------------------------- #
# sniff_type -- the underlying content-sniffing helper
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "content,content_type,expected",
    [
        (b"%PDF-1.4 ...", "", "pdf"),
        (b"PK\x03\x04abc", "", "excel"),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1abc", "", "excel"),
        (b"<!DOCTYPE html><html></html>", "", "html"),
        (b'{"a": 1}', "", "json"),
        (b"a,b,c\n1,2,3\n", "text/csv", "csv"),
        (b"", "", None),
    ],
)
def test_sniff_type_prioritizes_actual_content_over_header(content, content_type, expected):
    assert asset_verifier.sniff_type(content, content_type) == expected

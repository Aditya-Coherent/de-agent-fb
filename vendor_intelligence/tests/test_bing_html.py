"""Bing HTML cite URL parsing (no network)."""
from __future__ import annotations

from vendor_intel.clients.bing_html import _cite_to_url


def test_cite_to_url_https():
    assert _cite_to_url("https://cipla.com › about") == "https://cipla.com"


def test_cite_to_url_bare_host():
    assert _cite_to_url("www.sunpharma.com › home").startswith("https://www.sunpharma.com")


def test_cite_to_url_empty():
    assert _cite_to_url("") == ""

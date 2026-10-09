"""Ensure page fetch API routes through Selenium, not httpx."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from vendor_intel.scraping import fetch as scrape_fetch
from vendor_intel.scraping.selenium_browser import SeleniumPageResult


def test_fetch_page_uses_selenium_thread():
    fake = SeleniumPageResult(
        url="https://example.com",
        final_url="https://example.com/",
        alive=True,
        html="<html><body><h1>Acme</h1><p>We build phones.</p></body></html>",
        title="Acme",
    )
    with patch(
        "vendor_intel.scraping.fetch._selenium_fetch_page_html",
        return_value=fake,
    ) as mock_sync:
        scrape_fetch.SCRAPING_ENABLED = True
        result = asyncio.run(scrape_fetch.fetch_page("example.com"))
        mock_sync.assert_called_once()
        assert result.alive
        assert "Acme" in result.html


def test_fetch_page_text_extracts_from_selenium_html():
    fake = SeleniumPageResult(
        url="https://example.com",
        final_url="https://example.com/",
        alive=True,
        html=(
            "<html><body><article><p>Pharmaceutical manufacturer in India."
            "</p></article></body></html>"
        ),
    )
    with patch(
        "vendor_intel.scraping.fetch._selenium_fetch_page_html",
        return_value=fake,
    ):
        scrape_fetch.SCRAPING_ENABLED = True
        text = asyncio.run(
            scrape_fetch.fetch_page_text("https://example.com", max_chars=500)
        )
        assert "pharmaceutical" in text.lower() or "manufacturer" in text.lower()

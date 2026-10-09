"""
Website scraping package — all company/page loads use Selenium + Google Chrome.

Import from here or from ``vendor_intel.scraping.fetch`` for page fetches.
"""

from vendor_intel.scraping.fetch import (
    SCRAPING_ENABLED,
    check_url_alive,
    fetch_page,
    fetch_page_html,
    fetch_page_text,
)
from vendor_intel.scraping.selenium_browser import apply_selenium_env, shutdown_chrome_driver

__all__ = [
    "SCRAPING_ENABLED",
    "WebsiteScrapeResult",
    "apply_selenium_env",
    "check_url_alive",
    "fetch_page",
    "fetch_page_html",
    "fetch_page_text",
    "scrape_company_website",
    "shutdown_chrome_driver",
]


def __getattr__(name: str):
    if name == "WebsiteScrapeResult":
        from vendor_intel.scraping.website import WebsiteScrapeResult

        return WebsiteScrapeResult
    if name == "scrape_company_website":
        from vendor_intel.scraping.website import scrape_company_website

        return scrape_company_website
    raise AttributeError(name)

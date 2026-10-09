"""Google Alerts backend collector (RSS + optional Selenium, no API)."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs

from vendor_intel.alerts.models import AlertArticle
from vendor_intel.alerts.rss_feeds import fetch_alerts_from_rss, parse_rss_url_list
from vendor_intel.alerts.store import AlertStore

_NAV_TITLE_RE = re.compile(
    r"^(help center|terms of service|privacy policy|sign in|create alert|show options)$",
    re.I,
)


def _extract_google_redirect(href: str) -> str:
    if "google.com/url" in href and "q=" in href:
        parsed = parse_qs(urlparse(href).query)
        q = parsed.get("q", [""])[0]
        return unquote(q) if q else href
    return href


def _is_noise_link(href: str, title: str) -> bool:
    if not href or not title:
        return True
    if _NAV_TITLE_RE.match(title.strip()):
        return True
    low_href = href.lower()
    if any(
        x in low_href
        for x in (
            "google.com/support",
            "google.com/accounts",
            "google.com/intl/",
            "google.com/alerts#",
            "google.com/alerts/manage",
            "google.com/alerts?source",
        )
    ):
        return True
    if "google.com/alerts" in low_href and "/feeds/" not in low_href:
        return True
    return False


def _is_article_candidate(href: str, title: str) -> bool:
    if _is_noise_link(href, title):
        return False
    url = _extract_google_redirect(href)
    if not url.startswith("http"):
        return False
    host = urlparse(url).netloc.lower()
    if host.endswith("google.com") and "google.com/url" not in href.lower():
        return False
    if len(title.strip()) < 12:
        return False
    return True


class GoogleAlertsScraper:
    """Opens Google Alerts UI with a saved Chrome profile; discovers RSS feeds and article links."""

    def __init__(
        self,
        profile_path: Path,
        *,
        headless: bool = True,
        alerts_url: str = "https://www.google.com/alerts",
    ):
        self.profile_path = profile_path
        self.headless = headless
        self.alerts_url = alerts_url

    def discover_rss_feed_urls(self) -> list[str]:
        try:
            from selenium import webdriver
            from selenium.webdriver.common.by import By
        except ImportError as e:
            raise RuntimeError(
                "selenium is required for Google Alerts scraping: pip install selenium"
            ) from e

        from vendor_intel.scraping.selenium_browser import build_chrome_options

        self.profile_path.mkdir(parents=True, exist_ok=True)
        options = build_chrome_options(
            headless=self.headless,
            user_data_dir=str(self.profile_path.resolve()),
        )

        feeds: list[str] = []
        driver = webdriver.Chrome(options=options)
        try:
            driver.get(self.alerts_url)
            driver.implicitly_wait(5)
            for anchor in driver.find_elements(By.CSS_SELECTOR, "a[href*='/alerts/feeds/']"):
                href = anchor.get_attribute("href") or ""
                if href and href not in feeds:
                    feeds.append(href)
        finally:
            driver.quit()
        return feeds

    def collect_ui_articles(self, max_articles: int = 50) -> list[AlertArticle]:
        try:
            from selenium import webdriver
            from selenium.webdriver.common.by import By
        except ImportError as e:
            raise RuntimeError(
                "selenium is required for Google Alerts scraping: pip install selenium"
            ) from e

        from vendor_intel.scraping.selenium_browser import build_chrome_options

        self.profile_path.mkdir(parents=True, exist_ok=True)
        options = build_chrome_options(
            headless=self.headless,
            user_data_dir=str(self.profile_path.resolve()),
        )

        articles: list[AlertArticle] = []
        seen: set[str] = set()
        now = datetime.now(timezone.utc).isoformat()

        driver = webdriver.Chrome(options=options)
        try:
            driver.get(self.alerts_url)
            driver.implicitly_wait(5)
            for anchor in driver.find_elements(By.CSS_SELECTOR, "a[href]"):
                if len(articles) >= max_articles:
                    break
                href = anchor.get_attribute("href") or ""
                title = (anchor.text or "").strip()
                if not _is_article_candidate(href, title):
                    continue
                url = _extract_google_redirect(href)
                if url in seen:
                    continue
                seen.add(url)
                articles.append(
                    AlertArticle(
                        title=title[:300],
                        url=url,
                        snippet=title[:500],
                        collected_at=now,
                    )
                )
        finally:
            driver.quit()

        return articles


def collect_alerts(
    *,
    rss_urls: str = "",
    profile_path: Path | None = None,
    headless: bool = True,
    use_selenium: bool = True,
    max_per_feed: int = 30,
) -> list[AlertArticle]:
    """Collect from RSS URLs (env) and optionally Selenium-discovered RSS + UI links."""
    from vendor_intel.config import _project_root

    profile = profile_path or (_project_root() / "data" / "chrome-profile")
    feed_urls = list(parse_rss_url_list(rss_urls))
    articles: list[AlertArticle] = []
    seen: set[str] = set()

    def _merge(batch: list[AlertArticle]) -> None:
        for a in batch:
            if a.url not in seen:
                seen.add(a.url)
                articles.append(a)

    if feed_urls:
        _merge(fetch_alerts_from_rss(feed_urls, max_per_feed=max_per_feed))

    if use_selenium:
        scraper = GoogleAlertsScraper(profile, headless=headless)
        discovered = scraper.discover_rss_feed_urls()
        new_feeds = [u for u in discovered if u not in feed_urls]
        if new_feeds:
            _merge(fetch_alerts_from_rss(new_feeds, max_per_feed=max_per_feed))
        _merge(scraper.collect_ui_articles())

    return articles


def scrape_alerts_sync(
    profile_path: Path | None = None,
    *,
    headless: bool = True,
    store_path: Path | None = None,
    rss_urls: str = "",
    use_selenium: bool = True,
) -> list[AlertArticle]:
    articles = collect_alerts(
        rss_urls=rss_urls,
        profile_path=profile_path,
        headless=headless,
        use_selenium=use_selenium,
    )
    store = AlertStore(store_path)
    return store.upsert(articles)

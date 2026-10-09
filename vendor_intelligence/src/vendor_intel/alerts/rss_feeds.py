"""Fetch Google Alerts articles from RSS feed URLs (no Selenium)."""
from __future__ import annotations

from datetime import datetime, timezone

from vendor_intel.alerts.models import AlertArticle
from vendor_intel.news.rss import fetch_rss_articles


def parse_rss_url_list(raw: str) -> list[str]:
    return [u.strip() for u in (raw or "").split(",") if u.strip()]


def fetch_alerts_from_rss(
    feed_urls: list[str],
    *,
    max_per_feed: int = 30,
    default_query: str = "",
) -> list[AlertArticle]:
    """Pull articles from Google Alerts RSS URLs (or any RSS URL)."""
    now = datetime.now(timezone.utc).isoformat()
    articles: list[AlertArticle] = []
    seen: set[str] = set()

    for feed_url in feed_urls:
        query = default_query or (
            "google_alerts_rss" if "/alerts/feeds/" in feed_url else ""
        )

        for item in fetch_rss_articles(feed_url, max_items=max_per_feed):
            if item.url in seen:
                continue
            seen.add(item.url)
            articles.append(
                AlertArticle(
                    title=item.title,
                    url=item.url,
                    snippet=item.snippet,
                    published_at=item.published_at,
                    alert_query=query,
                    collected_at=now,
                )
            )
    return articles

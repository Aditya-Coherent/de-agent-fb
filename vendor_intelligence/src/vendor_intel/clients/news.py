from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from vendor_intel.config import Settings
from vendor_intel.news.ddg_news import ddg_news_search
from vendor_intel.news.rss import fetch_rss_articles


@dataclass
class NewsHit:
    title: str
    url: str
    source: str
    snippet: str
    backend: str
    published_at: str = ""


async def fetch_company_news(
    company: str,
    *,
    geo: str = "",
    days: int = 7,
    max_articles: int = 8,
    settings: Settings | None = None,
    alert_store_path: Path | None = None,
) -> list[NewsHit]:
    """DDG news + Google Alerts store + optional RSS (free only)."""
    del days
    hits: list[NewsHit] = []

    try:
        ddg_rows = await ddg_news_search(
            f"{company} {geo} news".strip(),
            max_results=max_articles,
            geo=geo,
            settings=settings,
        )
    except Exception:
        ddg_rows = []

    for art in ddg_rows:
        hits.append(
            NewsHit(
                title=art.title,
                url=art.url,
                source=art.source or "web_search_news",
                snippet=art.snippet,
                backend="duckduckgo_news",
                published_at=art.published_at,
            )
        )

    if settings and settings.google_alerts_enabled:
        from vendor_intel.alerts.store import AlertStore

        store = AlertStore(alert_store_path)
        for art in store.articles_for_company(company, limit=max_articles):
            hits.append(
                NewsHit(
                    title=art.title,
                    url=art.url,
                    source="google_alerts",
                    snippet=art.snippet,
                    backend="google_alerts",
                    published_at=art.published_at or art.collected_at,
                )
            )

    if settings and settings.rss_feed_urls:
        for feed in settings.rss_feed_urls.split(","):
            feed = feed.strip()
            if not feed:
                continue
            for art in fetch_rss_articles(feed, max_items=5):
                if company.lower() in art.title.lower() or company.lower() in art.snippet.lower():
                    hits.append(
                        NewsHit(
                            title=art.title,
                            url=art.url,
                            source="rss",
                            snippet=art.snippet,
                            backend="rss",
                            published_at=art.published_at,
                        )
                    )

    seen: set[str] = set()
    unique: list[NewsHit] = []
    for h in hits:
        if h.url not in seen:
            seen.add(h.url)
            unique.append(h)
    return unique[:max_articles]

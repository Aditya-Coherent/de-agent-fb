"""Optional RSS feeds for activity signals."""
from __future__ import annotations

from dataclasses import dataclass

try:
    import feedparser
except ImportError:
    feedparser = None  # type: ignore[assignment]


@dataclass
class RssArticle:
    title: str
    url: str
    snippet: str
    published_at: str


def fetch_rss_articles(feed_url: str, max_items: int = 10) -> list[RssArticle]:
    if feedparser is None or not feed_url:
        return []
    parsed = feedparser.parse(feed_url)
    rows: list[RssArticle] = []
    for entry in (parsed.entries or [])[:max_items]:
        link = entry.get("link") or ""
        if not link:
            continue
        rows.append(
            RssArticle(
                title=(entry.get("title") or "")[:200],
                url=link,
                snippet=(entry.get("summary") or "")[:500],
                published_at=(entry.get("published") or "")[:40],
            )
        )
    return rows

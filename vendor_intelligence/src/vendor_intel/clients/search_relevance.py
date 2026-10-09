"""Filter search hits using anchors derived from search_topic (generic, adaptive)."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vendor_intel.clients.search_router import SearchResult

_STOP = frozenset(
    {
        "a", "an", "the", "in", "on", "for", "of", "and", "or", "to",
        "best", "top", "leading", "major", "list", "give", "me",
        "companies", "company", "brands", "brand", "manufacturers",
        "vendors", "suppliers", "2025", "2026", "ranked", "directory",
        "report", "industry", "overview", "fast", "growing", "startups",
    }
)

_DISCOVERY_BAD_DOMAINS = frozenset(
    {
        "myntra.com",
        "nykaafashion.com",
        "nykaa.com",
        "westside.com",
        "zara.com",
        "andindia.com",
        "maxfashion.in",
        "ajio.com",
        "amazon.in",
        "flipkart.com",
        "ndtv.com",
        "timesofindia.indiatimes.com",
        "indianexpress.com",
        "moneycontrol.com",
        "livemint.com",
    }
)

_BAD_URL_FRAGMENTS = (
    "grokipedia.com",
    "merriam-webster.com",
    "dictionary.cambridge.org",
    "emerging.com/",
    "facebook.com/",
    "instagram.com/reel",
    "youtube.com/watch",
    "quora.com/",
    "pinterest.com/",
    "scribd.com/document",
    "wikipedia.org/wiki/category",
    "linkedin.com/pulse",
    "britannica.com/technology/",
    "money.usnews.com",
    "investopedia.com",
)

_LISTICLE_TITLE_FRAGMENTS = (
    "top 10",
    "top 20",
    "best 7",
    "best 10",
    "list of",
    "full list",
    "category:pharmaceutical",
)


def _tokens(text: str, *, min_len: int = 3) -> set[str]:
    return {
        w
        for w in re.findall(r"[a-z0-9]{3,}", text.lower())
        if w not in _STOP and len(w) >= min_len
    }


def _geo_match(blob: str, geo: str) -> bool:
    if not geo or geo.lower() == "global":
        return True
    parts = [p.strip().lower() for p in geo.split(",") if p.strip()]
    for part in parts:
        if len(part) >= 4 and part in blob:
            return True
    return any(t in blob for t in _tokens(geo, min_len=3))


def _topic_match(
    blob: str,
    anchors: dict[str, set[str]],
    *,
    min_token_hits: int,
    require_phrase: bool,
) -> bool:
    tokens = anchors.get("tokens") or set()
    phrases = anchors.get("phrases") or set()
    ambiguous = anchors.get("ambiguous") or set()

    if phrases and require_phrase:
        if not any(p in blob for p in phrases):
            return False

    token_hits = sum(1 for t in tokens if t in blob)
    if token_hits < min_token_hits:
        return False

    # Reject if only ambiguous modifier from topic matched (e.g. "modular" alone)
    if ambiguous and token_hits <= 1:
        if any(a in blob for a in ambiguous) and not any(
            t in blob for t in tokens if t not in ambiguous
        ):
            return False

    return True


def _filter_pass(
    rows: list["SearchResult"],
    search_query: str,
    market: str,
    geo: str,
    search_topic: str,
    *,
    strict: bool,
) -> list["SearchResult"]:
    from vendor_intel.funnel.prompt_builder import core_anchor_tokens, refine_search_topic

    topic = search_topic or refine_search_topic(market, geo)
    anchors = core_anchor_tokens(market, topic)
    tokens = anchors.get("tokens") or set()
    phrases = anchors.get("phrases") or set()

    min_token_hits = 2 if len(tokens) >= 3 else 1
    require_phrase = bool(phrases) and len(tokens) >= 2 and strict
    require_geo = strict and geo and geo.lower() != "global"

    kept: list[SearchResult] = []
    for r in rows:
        link_low = (r.link or "").lower()
        if any(bad in link_low for bad in _BAD_URL_FRAGMENTS):
            continue
        try:
            from urllib.parse import urlparse

            host = urlparse(link_low).netloc.lower().replace("www.", "")
            if host in _DISCOVERY_BAD_DOMAINS:
                continue
        except Exception:
            pass
        title_low = (r.title or "").lower()
        blob = f"{r.title} {r.snippet} {r.link}".lower()
        if strict and any(t in title_low for t in _LISTICLE_TITLE_FRAGMENTS):
            if require_geo and not _geo_match(blob, geo):
                continue
        if not _topic_match(
            blob,
            anchors,
            min_token_hits=min_token_hits,
            require_phrase=require_phrase,
        ):
            continue
        if require_geo and not _geo_match(blob, geo):
            continue
        kept.append(r)
    return kept


def filter_search_results(
    search_query: str,
    market: str,
    geo: str,
    rows: list["SearchResult"],
    *,
    search_topic: str = "",
    min_keep: int = 5,
    require_geo_match: bool = False,
    extra_anchor_terms: list[str] | None = None,
) -> list["SearchResult"]:
    """
    Strict pass first; if too few results, relax (fewer token rules, softer geo).
    """
    if not rows:
        return rows

    try:
        from vendor_intel.discovery.company_registry import get_registry_scope

        sc = get_registry_scope() or {}
        merged_extra = list(extra_anchor_terms or [])
        for term in (sc.get("relevance_keywords") or []) + (sc.get("industry_terms") or []):
            merged_extra.append(str(term))
        extra_anchor_terms = merged_extra
    except Exception:
        pass

    if require_geo_match and geo and geo.lower() != "global":
        geo_rows = [r for r in rows if _geo_match(f"{r.title} {r.snippet} {r.link}".lower(), geo)]
        if len(geo_rows) >= min_keep:
            return geo_rows[: len(rows)]

    strict = _filter_pass(
        rows, search_query, market, geo, search_topic, strict=True
    )
    if len(strict) >= min_keep:
        return strict

    relaxed = _filter_pass(
        rows, search_query, market, geo, search_topic, strict=False
    )
    if len(relaxed) >= min_keep:
        return relaxed

    # Last resort: token match only, no geo (still drop bad URLs)
    from vendor_intel.funnel.prompt_builder import core_anchor_tokens, refine_search_topic

    topic = search_topic or refine_search_topic(market, geo)
    anchors = core_anchor_tokens(market, topic)
    tokens = set(anchors.get("tokens") or set())
    for term in extra_anchor_terms or []:
        for w in re.findall(r"[a-z0-9]{3,}", str(term).lower()):
            if w not in _STOP:
                tokens.add(w)
    fallback: list[SearchResult] = []
    for r in rows:
        link_low = (r.link or "").lower()
        if any(bad in link_low for bad in _BAD_URL_FRAGMENTS):
            continue
        blob = f"{r.title} {r.snippet} {r.link}".lower()
        if tokens and any(t in blob for t in tokens):
            fallback.append(r)
    return fallback if fallback else rows[: min(len(rows), min_keep)]

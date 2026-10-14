"""Google AI Overview as an extra evidence source, via the local
`google-ai-scraper` backend (`../chrome-scraper-extensions/google-ai-scraper`):
a FastAPI server on :15551 relays each query to a Chrome extension, which
runs the search in a background tab of the user's own browser and returns
the AI Overview as Markdown plus its citation URLs.

Optional and never blocking: used only when the backend is up AND the
extension is connected (checked via `/health`, re-checked every
`_HEALTH_TTL` seconds); any failure returns None and the run carries on with
the ordinary `ddgs` results.

LEADS ONLY, NEVER EVIDENCE (`src/newinfo/system.md` §3.4). An AI Overview is
generated text: it can merge numbers from different years, geographies or
scopes, and state numbers none of its citations contain. So:

  1. the overview text and its cited URLs are snapshotted as a LEAD
     (`leads.jsonl`), never handed to the extractor;
  2. every cited page is fetched with the ordinary web fetcher and returned
     as a normal web result under ITS OWN URL (`cited_page_results`);
  3. the Verifier then checks any number against the cited page's text, so
     a fact's label comes from the cited page -- the overview is never cited.

Off by default. The design's recommended access path is a SERP API that
returns AI Overview blocks; the local browser-extension scraper is kept as an
opt-in backend only (scraping google.com directly breaches Google's terms).

Environment:
    ME_AI_OVERVIEW        "0" (default, off) | "1" / "auto" (use when the backend is up)
    ME_AI_OVERVIEW_URL    backend URL, default http://127.0.0.1:15551
    ME_AI_OVERVIEW_LEADS  leads file, default output/ai_overview_leads.jsonl
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import httpx

TITLE = "Google AI Overview"
SEARCH_URL_PREFIX = "https://www.google.com/search?q="
_HEALTH_TTL = 60.0
_ASK_TIMEOUT = 120.0  # the backend itself gives up after 110 s
_MAX_CHARS = 2500

_lock = threading.Lock()
_health: dict[str, Any] = {"ok": False, "checked_at": 0.0}
_answers: dict[str, dict[str, Any] | None] = {}


def _base_url() -> str:
    return os.environ.get("ME_AI_OVERVIEW_URL", "http://127.0.0.1:15551").rstrip("/")


def enabled() -> bool:
    return os.environ.get("ME_AI_OVERVIEW", "0").strip().lower() in ("1", "on", "true", "yes", "auto")


def available() -> bool:
    """Backend up and the browser extension connected (cached for a minute)."""
    if not enabled():
        return False
    with _lock:
        if time.monotonic() - _health["checked_at"] < _HEALTH_TTL:
            return _health["ok"]
    try:
        data = httpx.get(f"{_base_url()}/health", timeout=3.0).json()
        # "stale" = the extension has connected before but its Manifest V3
        # worker is idle; a queued query usually wakes it. A failed ask
        # below marks the scraper unavailable for the next _HEALTH_TTL.
        ok = data.get("extension_status") in ("connected", "stale")
    except Exception:  # noqa: BLE001
        ok = False
    with _lock:
        _health.update(ok=ok, checked_at=time.monotonic())
    return ok


def is_ai_overview_url(url: str) -> bool:
    return (url or "").startswith(SEARCH_URL_PREFIX)


def ask(query: str) -> dict[str, Any] | None:
    """{markdown, citations} for one query, or None. Cached per process."""
    query = query.strip()
    if not query or not available():
        return None
    with _lock:
        if query in _answers:
            return _answers[query]
    answer: dict[str, Any] | None = None
    failed = False
    try:
        resp = httpx.get(f"{_base_url()}/ask", params={"q": query, "close_thread": "true"}, timeout=_ASK_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            markdown = str(data.get("markdown") or "").strip()
            if markdown and not data.get("error"):
                answer = {"markdown": markdown, "citations": [str(c) for c in data.get("citations") or []]}
        else:
            failed = True  # e.g. 504: the extension didn't answer
    except Exception:  # noqa: BLE001 -- never let the scraper break a run
        failed = True
    with _lock:
        _answers[query] = answer
        if failed:  # a query with no AI Overview is normal; a timeout/error means stop asking for a while
            _health.update(ok=False, checked_at=time.monotonic())
    return answer


def snippet_text(query: str, answer: dict[str, Any]) -> str:
    cites = ", ".join(answer["citations"][:5]) or "none listed"
    return (f"{TITLE} for '{query}' (AI-generated summary; cites: {cites}): "
            f"{answer['markdown'][:_MAX_CHARS]}")


def search_url(query: str) -> str:
    return SEARCH_URL_PREFIX + quote_plus(query.strip())


# --------------------------------------------------------------------------- #
# leads -> cited pages (the only thing that reaches the Verifier)
# --------------------------------------------------------------------------- #
_MAX_CITED = 3
_PAGE_TIMEOUT = 20.0
_FIGURE = re.compile(r"\d[\d,.]*\s*(?:%|million|mn|billion|bn|thousand|lakh|crore|tonnes?|tons?|units?|kg|t\b)",
                     re.IGNORECASE)
_TAGS = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.IGNORECASE | re.DOTALL)


def _leads_path() -> Path:
    return Path(os.environ.get("ME_AI_OVERVIEW_LEADS", "output/ai_overview_leads.jsonl"))


def record_lead(query: str, answer: dict[str, Any], status: str, fetched_urls: list[str]) -> None:
    """Snapshot the overview (it changes over time and can't be re-fetched)."""
    row = {"retrieved_at": datetime.now(timezone.utc).isoformat(), "query": query, "origin": "ai_overview",
           "sha256": hashlib.sha256(answer["markdown"].encode("utf-8")).hexdigest(),
           "overview_text": answer["markdown"][:_MAX_CHARS], "cited_urls": answer["citations"],
           "fetched_cited_urls": fetched_urls, "status": status}
    try:
        path = _leads_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock, path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def page_passages(url: str, width: int = 300, max_passages: int = 5) -> str:
    """Text around figures on the cited page ("" when it can't be fetched)."""
    try:
        resp = httpx.get(url, timeout=_PAGE_TIMEOUT, follow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0 (DeAgent evidence fetcher)"})
        if resp.status_code != 200 or "html" not in resp.headers.get("content-type", "html"):
            return ""
        text = re.sub(r"\s+", " ", _TAGS.sub(" ", resp.text))
    except Exception:  # noqa: BLE001 -- an unreachable citation is just not evidence
        return ""
    out, last_end = [], -1
    for m in _FIGURE.finditer(text):
        if m.start() < last_end:
            continue
        a, b = max(0, m.start() - width), min(len(text), m.end() + width)
        out.append(text[a:b].strip())
        last_end = b
        if len(out) >= max_passages:
            break
    return " ... ".join(out)[:_MAX_CHARS]


def cited_page_results(query: str, answer: dict[str, Any]) -> list[tuple[str, str]]:
    """[(cited url, passage text)] for the overview's citations; the overview
    itself is only logged as a lead."""
    results = []
    for url in [u for u in answer["citations"] if u.startswith("http")][:_MAX_CITED]:
        text = page_passages(url)
        if text:
            results.append((url, text))
    record_lead(query, answer, "cited_pages_fetched" if results else "not_found_in_cited_source",
                [u for u, _ in results])
    return results

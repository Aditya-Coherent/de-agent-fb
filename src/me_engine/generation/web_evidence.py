"""Live web evidence: the Perplexity Search API when PERPLEXITY_API_KEY is set
(see `backend()`), otherwise `ddgs` (multi-backend: Bing, Mojeek, Google,
DuckDuckGo, with fallback). Degrades gracefully to [] on any failure.

Replaces the previous single-backend raw DuckDuckGo-HTML scrape, which was
failing (ConnectTimeout) in this environment — meaning every Desk Research
generation was silently running on zero real web evidence, with the LLM
estimating market size/regions/segments/pricing from its own training
knowledge alone. `ddgs` has been confirmed to actually return results here.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

# --------------------------------------------------------------------------- #
# backend selection: Perplexity Search API when a key is available, else ddgs
# --------------------------------------------------------------------------- #
# WEB_SEARCH_BACKEND = "auto" (default: Perplexity if PERPLEXITY_API_KEY is set, else ddgs),
# "perplexity" or "ddgs". Perplexity's Search API returns real search-engine results with
# extracted page text; the free ddgs backends were returning off-topic pages (airline sites
# for a regulator query, software firms for Kenya's fertiliser regulator), so facts that
# exist on the web could not be confirmed.
PERPLEXITY_URL = "https://api.perplexity.ai/search"
_PPLX_TOKENS_PER_PAGE = 300          # ~1,200 characters of page text per result: enough to quote, not a page dump
_dotenv_loaded = False

# Cost controls (Perplexity bills per request: standard $5 / 1,000, fast $1 / 1,000).
# WEB_SEARCH_MODE = "auto" (default): fast search for bulk desk research, standard search only for
#   `precise=True` lookups (fact checks, company verification) -- a live test showed fast mode
#   returning drug-registration pages for a fertiliser-regulator query that standard mode answered
#   with the GCC fertiliser law. "standard" / "fast" force one mode everywhere.
# Batching several queries into one request was tested and rejected: the merged results are
#   dominated by the first query (9 of 12), so later queries lose their evidence.
# WEB_SEARCH_CACHE_DAYS (default 30; 0 disables): identical searches are answered from a local cache,
#   so re-running a market or rebuilding a report does not pay for the same searches again.
_CACHE_PATH = Path(__file__).resolve().parents[3] / "output" / ".search_cache.db"
_PRICE_PER_1000 = {"fast": 1.0, "standard": 5.0}
# paid Perplexity requests and cache hits in this process, for the cost line printed after each run
STATS = {"fast": 0, "standard": 0, "cached": 0}


def cost_summary() -> str:
    """'Perplexity: 41 fast + 23 standard searches paid (~$0.16), 30 from cache'."""
    usd = sum(STATS[m] * _PRICE_PER_1000[m] / 1000 for m in ("fast", "standard"))
    return (f"Perplexity: {STATS['fast']} fast + {STATS['standard']} standard searches paid "
            f"(~${usd:.2f}), {STATS['cached']} from cache")


def _ensure_env() -> None:
    """Load the project .env once (same rule as GenConfig: never overrides a set variable)."""
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    _dotenv_loaded = True
    env = Path(__file__).resolve().parents[3] / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def backend() -> str:
    """The search backend in use: "perplexity" or "ddgs"."""
    _ensure_env()
    choice = os.environ.get("WEB_SEARCH_BACKEND", "auto").strip().lower()
    if choice == "ddgs":
        return "ddgs"
    if choice == "perplexity" or os.environ.get("PERPLEXITY_API_KEY"):
        return "perplexity" if os.environ.get("PERPLEXITY_API_KEY") else "ddgs"
    return "ddgs"


def _domain(url: str) -> str:
    return url.split("//")[-1].split("/")[0].removeprefix("www.") if url else ""


def search_mode(precise: bool = False) -> str:
    """"fast" or "standard" for a Perplexity request (see WEB_SEARCH_MODE above)."""
    _ensure_env()
    choice = os.environ.get("WEB_SEARCH_MODE", "auto").strip().lower()
    if choice in ("fast", "standard"):
        return choice
    return "standard" if precise else "fast"


def _cache_days() -> float:
    _ensure_env()
    try:
        return float(os.environ.get("WEB_SEARCH_CACHE_DAYS", "30"))
    except ValueError:
        return 30.0


def _cache_key(*parts) -> str:
    import hashlib
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()


def _cache_get(key: str) -> list[dict] | None:
    days = _cache_days()
    if days <= 0 or not _CACHE_PATH.exists():
        return None
    try:
        import json
        import sqlite3
        with sqlite3.connect(_CACHE_PATH, timeout=10) as db:
            row = db.execute("SELECT value, created FROM search WHERE key = ?", (key,)).fetchone()
        if row and time.time() - row[1] <= days * 86400:
            return json.loads(row[0])
    except Exception:  # noqa: BLE001 - a broken cache is just a miss
        return None
    return None


def _cache_put(key: str, results: list[dict]) -> None:
    if _cache_days() <= 0:
        return
    try:
        import json
        import sqlite3
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(_CACHE_PATH, timeout=10) as db:
            db.execute("CREATE TABLE IF NOT EXISTS search (key TEXT PRIMARY KEY, value TEXT, created REAL)")
            db.execute("INSERT OR REPLACE INTO search VALUES (?, ?, ?)",
                       (key, json.dumps(results, ensure_ascii=False), time.time()))
    except Exception:  # noqa: BLE001 - caching must never break a search
        pass


def _perplexity_results(query: str, max_results: int, retries: int = 2, mode: str = "fast") -> list[dict] | None:
    """Raw Perplexity Search API results [{title, url, snippet, date}], or None on failure
    (the caller then falls back to ddgs). Retries 429/5xx with a short backoff."""
    import httpx

    key = os.environ.get("PERPLEXITY_API_KEY", "")
    body = {"query": query, "max_results": max(1, min(20, int(max_results))),
            "max_tokens_per_page": _PPLX_TOKENS_PER_PAGE}
    if mode == "fast":
        body["search_type"] = "fast"
    for attempt in range(retries + 1):
        try:
            r = httpx.post(PERPLEXITY_URL, json=body, timeout=30.0,
                           headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        except Exception:  # noqa: BLE001 - network error: retry, then give up
            r = None
        if r is not None and r.status_code == 200:
            try:
                return list(r.json().get("results") or [])
            except Exception:  # noqa: BLE001
                return None
        if r is not None and r.status_code not in (429, 500, 502, 503, 504):
            return None                      # 4xx other than 429: bad key / bad request -- don't retry
        time.sleep(2 * (attempt + 1))
    return None


def _ddgs_results(query: str, max_results: int) -> list[dict]:
    try:
        from ddgs import DDGS

        return [{"title": r.get("title"), "url": r.get("href"), "snippet": r.get("body")}
                for r in DDGS().text(query, max_results=max_results)]
    except Exception:  # noqa: BLE001 - never let a search failure break generation
        return []


def _raw_results(query: str, max_results: int, precise: bool = False) -> list[dict]:
    """[{title, url, snippet}] from the configured backend (cached); Perplexity falls back to ddgs on failure.
    Only successful Perplexity answers are cached -- a ddgs fallback is retried next time."""
    if backend() == "perplexity":
        mode = search_mode(precise)
        key = _cache_key("perplexity", mode, _PPLX_TOKENS_PER_PAGE, int(max_results), query.strip().lower())
        hit = _cache_get(key)
        if hit is not None:
            STATS["cached"] += 1
            return hit
        res = _perplexity_results(query, max_results, mode=mode)
        if res is not None:
            STATS[mode] += 1
            _cache_put(key, res)
            return res
    return _ddgs_results(query, max_results)


def search_snippets(query: str, max_results: int = 6) -> list[str]:
    """Return short text snippets for a query. Never raises — returns [] on failure."""
    snippets: list[str] = []
    for r in _raw_results(query, max_results):
        title = (r.get("title") or "").strip()
        body = " ".join((r.get("snippet") or "").split())
        if not body:
            continue
        text = f"{title}: {body}" if title else body
        if len(text) > 30:
            snippets.append(text)
    return snippets


def gather(queries: list[str], per_query: int = 4, cap: int = 12) -> list[str]:
    """Run several queries and return a de-duplicated, capped snippet list."""
    out: list[str] = []
    seen: set[str] = set()
    for q in queries:
        for s in search_snippets(q, max_results=per_query):
            key = s[:80].lower()
            if key not in seen:
                seen.add(key)
                out.append(s)
            if len(out) >= cap:
                return out
    return out


def search_snippets_with_sources(query: str, max_results: int = 6, precise: bool = False) -> list[dict]:
    """Same as `search_snippets`, but also returns each result's URL/domain --
    additive, new function (never raises; degrades to []), so existing
    `search_snippets`/`gather` callers (Desk Research's `agents.py`) are
    completely untouched. Used by `AiAgentsFleet` to compute a
    per-section confidence label from source-domain diversity."""
    out: list[dict] = []
    for r in _raw_results(query, max_results, precise):
        title = (r.get("title") or "").strip()
        body = " ".join((r.get("snippet") or "").split())
        url = (r.get("url") or "").strip()
        if not body:
            continue
        text = f"{title}: {body}" if title else body
        if len(text) > 30:
            out.append({"text": text, "url": url, "domain": _domain(url), "title": title})
    return out


def gather_with_sources(queries: list[str], per_query: int = 4, cap: int = 12, precise: bool = False) -> list[dict]:
    """Source-tracked version of `gather`. Returns
    `[{"text": ..., "url": ..., "domain": ...}, ...]`, de-duplicated by text
    and capped the same way."""
    out: list[dict] = []
    seen: set[str] = set()
    for q in queries:
        for item in search_snippets_with_sources(q, max_results=per_query, precise=precise):
            key = item["text"][:80].lower()
            if key not in seen:
                seen.add(key)
                out.append(item)
            if len(out) >= cap:
                return out
    return out


def fetch_page_text(url: str, timeout: float = 8.0, max_chars: int = 20_000) -> str:
    """Visible text of a web page (tags stripped, first `max_chars`), for
    cited-figure extraction. Never raises -- returns "" on any failure
    (blocked, timeout, non-HTML)."""
    try:
        import html as _html
        import re as _re

        import httpx

        resp = httpx.get(url, timeout=timeout, follow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0 (compatible; DeAgent/1.0)"})
        if resp.status_code != 200 or "html" not in resp.headers.get("content-type", "html"):
            return ""
        raw = _re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", resp.text)
        raw = _re.sub(r"(?i)<(br|p|li|h\d|div|tr)[^>]*>", "\n", raw)
        text = _html.unescape(_re.sub(r"<[^>]+>", " ", raw))
        text = _re.sub(r"[ \t\r\f\v]+", " ", text)
        return _re.sub(r"\n\s*", "\n", text).strip()[:max_chars]
    except Exception:  # noqa: BLE001 - never let a page fetch break generation
        return ""

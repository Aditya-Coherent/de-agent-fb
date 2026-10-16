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


def _perplexity_results(query: str, max_results: int, retries: int = 2) -> list[dict] | None:
    """Raw Perplexity Search API results [{title, url, snippet, date}], or None on failure
    (the caller then falls back to ddgs). Retries 429/5xx with a short backoff."""
    import httpx

    key = os.environ.get("PERPLEXITY_API_KEY", "")
    body = {"query": query, "max_results": max(1, min(20, int(max_results))),
            "max_tokens_per_page": _PPLX_TOKENS_PER_PAGE}
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


def _raw_results(query: str, max_results: int) -> list[dict]:
    """[{title, url, snippet}] from the configured backend; Perplexity falls back to ddgs on failure."""
    if backend() == "perplexity":
        res = _perplexity_results(query, max_results)
        if res is not None:
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


def search_snippets_with_sources(query: str, max_results: int = 6) -> list[dict]:
    """Same as `search_snippets`, but also returns each result's URL/domain --
    additive, new function (never raises; degrades to []), so existing
    `search_snippets`/`gather` callers (Desk Research's `agents.py`) are
    completely untouched. Used by `AiAgentsFleet` to compute a
    per-section confidence label from source-domain diversity."""
    out: list[dict] = []
    for r in _raw_results(query, max_results):
        title = (r.get("title") or "").strip()
        body = " ".join((r.get("snippet") or "").split())
        url = (r.get("url") or "").strip()
        if not body:
            continue
        text = f"{title}: {body}" if title else body
        if len(text) > 30:
            out.append({"text": text, "url": url, "domain": _domain(url), "title": title})
    return out


def gather_with_sources(queries: list[str], per_query: int = 4, cap: int = 12) -> list[dict]:
    """Source-tracked version of `gather`. Returns
    `[{"text": ..., "url": ..., "domain": ...}, ...]`, de-duplicated by text
    and capped the same way."""
    out: list[dict] = []
    seen: set[str] = set()
    for q in queries:
        for item in search_snippets_with_sources(q, max_results=per_query):
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

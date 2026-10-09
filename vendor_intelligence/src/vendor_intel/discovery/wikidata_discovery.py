"""Wikidata structured discovery — web/structured company enumeration (NOT LLM recall).

Resolves the market to Wikidata concept Q-ids via the entity-search API, then SPARQL-queries
for organizations whose INDUSTRY (P452) or TYPE (P31) is that concept and returns their
OFFICIAL-WEBSITE (P856) domains. This is real structured data read from Wikidata — not model
memory — so it surfaces the regional / obscure tail that doesn't rank in search or sit on a
listicle. Best-effort; never raises.
"""
from __future__ import annotations

import re
from typing import Any

import time

_WD_SEARCH = "https://www.wikidata.org/w/api.php"
_WD_SPARQL = "https://query.wikidata.org/sparql"
# QLever: a fast, high-throughput SPARQL mirror of Wikidata that rarely rate-limits. Used as the
# PRIMARY endpoint so we don't hit WDQS's ~1 req/s throttle; WDQS is the fallback.
_QLEVER_SPARQL = "https://qlever.cs.uni-freiburg.de/api/wikidata"
# Wikimedia enforces a robot policy: a NON-descriptive User-Agent is 403'd. Must identify
# the tool and a contact. See https://meta.wikimedia.org/wiki/User-Agent_policy
_HEADERS = {
    "User-Agent": "VendorIntelBot/1.0 (vendor-intelligence market research; bot-traffic-contact@example.org) python-httpx",
    "Accept": "application/sparql-results+json",
}
# Portable prefixes so the same query runs on BOTH QLever and WDQS (and uses rdfs:label directly
# instead of WDQS's Blazegraph-only wikibase:label SERVICE, which QLever doesn't support).
_SPARQL_PREFIXES = (
    "PREFIX wd: <http://www.wikidata.org/entity/> "
    "PREFIX wdt: <http://www.wikidata.org/prop/direct/> "
    "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> "
)

# Wikidata classes that are NOT companies we want (people, gov agencies handled elsewhere).
_BLOCK_DOMAINS = ("wikipedia.org", "wikidata.org", "facebook.com", "linkedin.com")

_last_request = [0.0]  # module-wide: serialize + space out Wikimedia requests


def _pace(min_gap: float = 1.1) -> None:
    """Keep at least min_gap seconds between successive requests (single-threaded good-citizen)."""
    wait = min_gap - (time.monotonic() - _last_request[0])
    if wait > 0:
        time.sleep(wait)
    _last_request[0] = time.monotonic()


def _get(client: Any, url: str, params: dict, *, timeout: float = 30.0, attempts: int = 3):
    """GET with pacing + 429/503 back-off that HONORS Retry-After. Returns the response or None."""
    for i in range(attempts):
        _pace()
        try:
            r = client.get(url, params=params, timeout=timeout)
        except Exception:
            time.sleep(2.0 * (i + 1))
            continue
        if r.status_code in (429, 503) and i < attempts - 1:
            ra = r.headers.get("Retry-After") or r.headers.get("retry-after")
            try:
                wait = float(ra) if ra else 0.0
            except ValueError:
                wait = 0.0
            time.sleep(min(max(wait, 2.0 * (2 ** i)), 30.0))  # honor Retry-After, else backoff
            continue
        return r
    return None


def _parse_bindings(r) -> list | None:
    """SPARQL JSON -> bindings list, or None on failure."""
    if r is None or r.status_code >= 400:
        return None
    try:
        return (r.json().get("results") or {}).get("bindings") or []
    except Exception:
        return None


def _run_sparql(query: str, *, max_rows: int) -> list:
    """Run a SPARQL query — QLever first (fast, no throttle), WDQS as fallback."""
    import httpx

    full = _SPARQL_PREFIXES + query
    with httpx.Client(headers=_HEADERS, follow_redirects=True) as c:
        r = _get(c, _QLEVER_SPARQL, {"query": full, "action": "json_export"}, timeout=45.0)
        rows = _parse_bindings(r)
        if rows:
            return rows
        r = _get(c, _WD_SPARQL, {"query": full, "format": "json"}, timeout=60.0)
        return _parse_bindings(r) or []


def _domain_of(url: str) -> str:
    try:
        from vendor_intel.utils.domains import domain_from_url

        d = (domain_from_url(url) or "").strip().lower().removeprefix("www.")
    except Exception:
        d = ""
    if not d:
        d = re.sub(r"^https?://(www\.)?", "", str(url or "").strip().lower()).split("/")[0]
    return d


def _search_concepts(terms: list[str], limit: int = 3) -> list[str]:
    """Resolve free-text market/industry terms to Wikidata Q-ids (best concepts only).
    Fewer terms + pacing + retry so the entity-search API doesn't rate-limit us."""
    import httpx

    qids: list[str] = []
    seen: set[str] = set()
    with httpx.Client(headers=_HEADERS, follow_redirects=True) as c:
        for t in [x for x in terms if x and x.strip()][:4]:  # 4 best terms = fewer API calls
            r = _get(
                c,
                _WD_SEARCH,
                {
                    "action": "wbsearchentities", "search": t.strip(), "language": "en",
                    "format": "json", "type": "item", "limit": limit,
                },
                timeout=15.0,
            )
            if r is None or r.status_code >= 400:
                continue
            try:
                hits = r.json().get("search") or []
            except Exception:
                continue
            for hit in hits[:limit]:
                q = hit.get("id")
                if q and q not in seen:
                    seen.add(q)
                    qids.append(q)
    return qids


def _sparql_companies(qids: list[str], max_rows: int = 250) -> list[dict]:
    """Real ORGANIZATIONS in the market, with their official websites.

    Two patterns UNIONed: (1) the operator/manufacturer/owner (P137/P176/P127) of items that
    are instances of the market concept — e.g. the company that operates a communications
    satellite; (2) organizations whose industry (P452) is the concept. Requiring an org link
    (not the satellite/product itself) gives company names, not product names."""
    if not qids:
        return []
    # NOTE: no P279* subclass walk — it times out the endpoints. Direct P31/P452 only.
    # rdfs:label (not wikibase:label SERVICE) so the query runs on QLever too.
    values = " ".join(f"wd:{q}" for q in qids[:8])
    query = (
        "SELECT DISTINCT ?orgLabel ?website WHERE {"
        f"  VALUES ?concept {{ {values} }}"
        "  { ?item wdt:P31 ?concept. ?item (wdt:P137|wdt:P176|wdt:P127) ?org. ?org wdt:P856 ?website. }"
        "  UNION"
        "  { ?org wdt:P452 ?concept. ?org wdt:P856 ?website. }"
        '  ?org rdfs:label ?orgLabel. FILTER(LANG(?orgLabel) = "en")'
        f"}} LIMIT {max_rows}"
    )
    try:
        rows = _run_sparql(query, max_rows=max_rows)
    except Exception:
        rows = []

    out: list[dict] = []
    seen_dom: set[str] = set()
    for b in rows:
        name = ((b.get("orgLabel") or {}).get("value") or "").strip()
        website = ((b.get("website") or {}).get("value") or "").strip()
        if not name or not website:
            continue
        # skip Wikidata Q-id labels that never resolved to a human name
        if re.fullmatch(r"Q\d+", name):
            continue
        dom = _domain_of(website)
        if not dom or "." not in dom or dom in seen_dom:
            continue
        if any(b_ in dom for b_ in _BLOCK_DOMAINS):
            continue
        seen_dom.add(dom)
        out.append({"name": name, "domain": dom})
    return out


def discover_via_wikidata(
    market: str,
    sections: list[str],
    industry_terms: list[str] | None = None,
    *,
    max_rows: int = 250,
) -> list[dict]:
    """Return [{"name","domain"}] for companies Wikidata lists in this market's industry.

    `domain` is the company's OFFICIAL website from Wikidata (verified structured data, no
    guessing). Best-effort; never raises. Empty list on any failure."""
    terms: list[str] = []
    for t in [market, *(industry_terms or []), *(sections or [])]:
        t = str(t or "").strip()
        if t and t.lower() not in (x.lower() for x in terms):
            terms.append(t)
    if not terms:
        return []
    qids: list[str] = []
    try:
        qids = _search_concepts(terms)
        rows = _sparql_companies(qids, max_rows=max_rows)
    except Exception:
        rows = []
    if rows:
        _cache_save(market, rows)
        print(
            f"  [wikidata] {len(rows)} compan(ies) from Wikidata structured data "
            f"(official websites, no guess) across {len(qids)} concept(s)",
            flush=True,
        )
        return rows
    # Live fetch empty (rate-limit / timeout) — fall back to the last good cached result so the
    # operator section doesn't randomly vanish run-to-run.
    cached = _cache_load(market)
    if cached:
        print(
            f"  [wikidata] live fetch empty — using {len(cached)} cached compan(ies) from a prior run",
            flush=True,
        )
    return cached


def _wd_cache_path(market: str):
    from pathlib import Path

    from vendor_intel.config import _project_root

    d = _project_root() / "output" / ".wikidata_cache"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        return None
    slug = re.sub(r"[^a-z0-9]+", "_", str(market or "").lower()).strip("_")[:80] or "market"
    return d / f"{slug}.json"


def _cache_save(market: str, rows: list[dict]) -> None:
    import json

    p = _wd_cache_path(market)
    if not p:
        return
    try:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"market": market, "companies": rows}, indent=2), encoding="utf-8")
        tmp.replace(p)
    except Exception:
        pass


def _cache_load(market: str) -> list[dict]:
    import json

    p = _wd_cache_path(market)
    if not p or not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        rows = data.get("companies") if isinstance(data, dict) else data
        return [r for r in (rows or []) if isinstance(r, dict) and r.get("domain") and r.get("name")]
    except Exception:
        return []

"""Deterministic signals from company website text using LLM scope keywords."""
from __future__ import annotations

import re

from vendor_intel.funnel.scope_schema import normalize_run_scope

_JUNK_SITE_TERMS = re.compile(
    # "javascript" alone is too broad — Xiaomi shows "JavaScript is not available"
    # which is a browser JS-block message, not a tutorial page
    r"\b(?:python\s+lists?|"
    r"javascript\s+(?:tutorial|guide|code|example|function|syntax)|"
    r"w3schools|"
    r"buy\s+shirts|women'?s\s+tops?|news\s+headlines|"
    r"subscribe\s+to\s+newsletter)\b",
    re.I,
)

# Dead / inactive website signals
_DEAD_SITE_PATTERNS = re.compile(
    r"\b(?:404\s+not\s+found|page\s+not\s+found|this\s+page\s+(?:does\s+not\s+exist|cannot\s+be\s+found)|"
    r"error\s+404|http\s+404|the\s+requested\s+url\s+was\s+not\s+found|"
    r"domain\s+(?:for\s+sale|is\s+for\s+sale|has\s+expired|expired|not\s+configured)|"
    r"this\s+domain\s+is\s+(?:available|parked)|parked\s+(?:domain|page)|"
    r"account\s+suspended|website\s+coming\s+soon|under\s+construction|"
    r"server\s+not\s+found|dns\s+address\s+could\s+not\s+be\s+found|"
    r"refused\s+to\s+connect|err_connection_refused|"
    r"buy\s+this\s+domain|register\s+this\s+domain|"
    r"godaddy|namecheap|domain\.com|sedo\.com)\b",
    re.I,
)


def is_dead_site(text: str) -> bool:
    """True when scraped text indicates a 404, parked domain, or inactive website."""
    if not text or len(text.strip()) < 10:
        return True
    return bool(_DEAD_SITE_PATTERNS.search(text))

_COMPANY_ABOUT = re.compile(
    r"\b(?:about\s+us|who\s+we\s+are|our\s+company|official\s+website|"
    r"official\s+(?:store|site|page)|"
    r"manufacturer|we\s+are\s+a|leading\s+(?:global\s+)?|"
    r"buy\s+(?:now|online)|launch\s+offer|recommended\s+for\s+you|"
    r"explore\s+(?:the\s+)?(?:stories|products|range|lineup)|"
    r"shop\s+by\s+category|new\s+arrivals?)\b",
    re.I,
)

# Website structure signals — pages/sections a real company site has
_COMPANY_STRUCTURE = re.compile(
    r"\b(?:our\s+products?|our\s+solutions?|our\s+services?|"
    r"products?\s+(?:portfolio|catalog|range|overview)|"
    r"solutions?\s+(?:overview|for\s+\w+)|"
    r"contact\s+us|get\s+in\s+touch|request\s+a\s+(?:demo|quote|trial)|"
    r"customer\s+(?:support|success|stories|case\s+studies)|"
    r"(?:free\s+)?trial|schedule\s+(?:a\s+)?demo|"
    r"partner\s+(?:portal|program|network)|"
    r"careers|(?:join|work\s+with)\s+us)\b",
    re.I,
)


def has_company_structure(text: str) -> bool:
    """True when scraped text contains website structural elements of a real company.

    Checks for product/solution pages, contact forms, demo requests, career
    pages — signals that distinguish real companies from news/blog/directory sites.
    """
    return bool(_COMPANY_STRUCTURE.search(text or ""))

def _keyword_hit(blob: str, keywords: list[str]) -> bool:
    low = blob.lower()
    for kw in keywords:
        k = str(kw).strip().lower()
        if len(k) >= 3 and k in low:
            return True
    return False


def _build_keyword_pattern(keywords: list[str]) -> re.Pattern[str] | None:
    terms = [re.escape(str(k).strip()) for k in keywords if len(str(k).strip()) >= 3]
    if not terms:
        return None
    return re.compile(r"\b(?:" + "|".join(terms[:24]) + r")\b", re.I)


def analyze_site_text(
    text: str,
    *,
    market: str = "",
    scope: dict | None = None,
    url: str = "",
) -> dict[str, bool | float]:
    sc = normalize_run_scope(scope or {}, "")
    if not sc.get("relevance_keywords") and market:
        sc = {**sc, "market": market}

    relevance = list(sc.get("relevance_keywords") or [])
    negative = list(sc.get("negative_keywords") or [])
    rel_re = _build_keyword_pattern(relevance)

    blob = (text or "").strip()

    # Dead site check — 404, parked domain, expired, server error
    if is_dead_site(blob):
        return {
            "has_substance": False,
            "market_relevant": False,
            "pharma_relevant": False,
            "geo_match": False,
            "geo_india": False,
            "looks_like_company": False,
            "junk_site": True,
            "dead_site": True,
            "confidence": 0.0,
        }

    if len(blob) < 20:
        return {
            "has_substance": False,
            "market_relevant": False,
            "pharma_relevant": False,
            "geo_match": False,
            "geo_india": False,
            "looks_like_company": False,
            "junk_site": False,
            "dead_site": False,
            "confidence": 0.0,
        }

    # IMPORTANT: scope.negative_keywords are search-result filters (e.g. "pharmaceutical"
    # excludes pharma companies from a smartphone query). They must NOT be applied to
    # scraped company-website text — the target companies WILL mention their own
    # industry category (e.g. "smartphone") on their own sites.
    # Only apply truly-junk keywords (non-industry terms like "pharma", "drug", "tutorial").
    _GENERAL_JUNK = {"pharmaceutical", "medicine", "drug", "drugs",
                     "tutorial", "how-to guide", "job portal", "job board",
                     "real estate", "property listing", "matrimony", "dating site"}
    neg_junk = [k for k in negative if str(k).strip().lower() in _GENERAL_JUNK]
    junk = bool(_JUNK_SITE_TERMS.search(blob)) or _keyword_hit(blob, neg_junk)
    # Thin content (< 400 chars) = very low confidence even if not dead
    if len(blob) < 400:
        return {
            "has_substance": False,
            "market_relevant": False,
            "pharma_relevant": False,
            "geo_match": False,
            "geo_india": False,
            "looks_like_company": False,
            "junk_site": junk,
            "dead_site": False,
            "confidence": 0.05 if junk else 0.08,
        }

    market_ok = bool(rel_re.search(blob)) if rel_re else _keyword_hit(blob, relevance)
    # CHANGED: dynamic geo gate — scope-driven signals, not hardcoded India cities
    from vendor_intel.validation.geo_signals import check_geo_match, get_geo_signals

    geographies = sc.get("geographies") or []
    geo_signals = get_geo_signals(geographies)
    # Extract domain from url so TLD checks (.in, .co.uk) work properly
    _domain_from_url = ""
    if url:
        try:
            from urllib.parse import urlparse as _up
            _domain_from_url = _up(url).netloc.lstrip("www.").lower()
        except Exception:
            pass
    geo_matched, _ = (
        check_geo_match(blob, _domain_from_url, geo_signals, url=url)
        if geo_signals
        else (False, "")
    )
    geo = geo_matched
    about = bool(_COMPANY_ABOUT.search(blob))
    structure = has_company_structure(blob)
    # Country-specific URL (e.g. apple.com/in, motorola.in) is strong evidence this
    # is the brand's official local page — treat as "looks_like_company" if it has
    # enough content (even without explicit product category keywords).
    _url_lower = url.lower() if url else ""
    _country_url = geo and (
        any(s in _url_lower for s in ("/in/", "/in", "/en-in", "/in-en"))
        or (_domain_from_url and any(_domain_from_url.endswith(t) for t in (".in", ".in")))
    )
    looks_co = about or structure or (_country_url and len(blob) >= 100 and not junk) or (
        market_ok and len(blob) > 200 and not junk
    )

    conf = 0.0
    if junk:
        conf = 0.05
    elif looks_co and market_ok and geo:
        conf = 0.92
    elif looks_co and market_ok:
        conf = 0.78
    elif looks_co:
        conf = 0.55
    elif market_ok:
        conf = 0.4

    pharma = _keyword_hit(blob, ["pharma", "pharmaceutical", "medicine", "drug"])

    return {
        "has_substance": len(blob) >= 400,
        "market_relevant": market_ok and not junk,
        "pharma_relevant": pharma and not junk,
        "geo_match": geo,
        "geo_india": geo,  # legacy key — now scope-driven when geographies includes India
        "looks_like_company": looks_co,
        "junk_site": junk,
        "dead_site": False,
        "confidence": conf,
    }

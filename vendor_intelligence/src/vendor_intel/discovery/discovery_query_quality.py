"""Discovery search query quality — block listicle-style queries before DDG."""
from __future__ import annotations

import re

_LISTICLE_QUERY_RE = re.compile(
    r"\b(?:top\s+\d+|best\s+\d+|\d+\s+(?:best|top)|list\s+of|companies\s+list|"
    r"full\s+list|largest\s+\d+|leading\s+\d+|ranking\s+of|wiki\s+)\b|"
    r"\b(?:market\s+leaders?|industry\s+players?|company\s+names)\b|"
    r"\b(?:top|best)\s+(?:\w+\s+){0,5}(?:companies|company|firms?|manufacturers?|vendors?)\b|"
    r"\b(?:companies|company|firms?)\s+list\b|"
    r"\blist\s+of\s+(?:companies|firms?|manufacturers?)\b",
    re.I,
)

_LISTICLE_QUERY_SUBS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bcompanies\s+list\b", re.I), "manufacturers official site"),
    (re.compile(r"\blist\s+of\b", re.I), ""),
    (re.compile(r"\b(?:top|best)\s+", re.I), ""),
    (re.compile(r"\btop\s+\d+\b", re.I), ""),
    (re.compile(r"\bbest\s+\d+\b", re.I), ""),
    (re.compile(r"\bleading\s+\d+\b", re.I), ""),
    (re.compile(r"\blargest\s+\d+\b", re.I), ""),
)


# Generic acronyms/tokens that, on their own, return junk (orbit types, geometry terms, etc.).
# "GEO official website" matched geocaching/geotimber; "LEO ..." matched 'Leo' apartments, etc.
_GENERIC_QUERY_WORDS = frozenset(
    {
        "official", "website", "web", "site", "sites", "homepage", "manufacturer", "manufacturers",
        "manufacturing", "corporate", "company", "companies", "headquarters", "hq", "supplier",
        "suppliers", "vendor", "vendors", "producer", "producers", "plant", "facility", "oem",
        "distributor", "distributors", "firm", "firms", "of", "the", "and", "inc", "ltd", "llc",
    }
)
_JUNK_SOLO_TERMS = frozenset(
    {"geo", "leo", "meo", "gso", "heo", "ngso", "hts", "mss", "fss", "bss", "gto", "dth"}
)


def _is_generic_acronym_query(t: str) -> bool:
    """True when the query's only distinctive token is a generic acronym (GEO/LEO/MEO/HTS/...)."""
    toks = [w for w in re.split(r"[^a-z0-9]+", t.lower()) if w and w not in _GENERIC_QUERY_WORDS]
    return bool(toks) and all(w in _JUNK_SOLO_TERMS for w in toks)


def is_listicle_discovery_query(text: str) -> bool:
    """Queries that mostly return directories and listicles, not company sites."""
    t = (text or "").strip()
    if len(t) < 6:
        return True
    if _is_generic_acronym_query(t):
        return True
    return bool(_LISTICLE_QUERY_RE.search(t))


def sanitize_discovery_query(text: str) -> str:
    """Best-effort rewrite; returns empty if still listicle-heavy."""
    t = re.sub(r"\s+", " ", (text or "").strip())
    for pat, repl in _LISTICLE_QUERY_SUBS:
        t = pat.sub(repl, t)
    t = re.sub(r"\s+", " ", t).strip()
    if is_listicle_discovery_query(t):
        return ""
    return t

"""Shared heuristics — market-research / directory domains vs operating companies."""
from __future__ import annotations

import re

# Domains that sell reports, rankings, or listings — not value-chain participants
_MARKETPLACE_DOMAIN = re.compile(
    r"(?:^|\.)(?:"
    r"alibaba|aliexpress|made-in-china|indiamart|tradeindia|exporthub|"
    r"amazon\.|ebay\.|etsy\.|walmart\.|flipkart"
    r")(?:\.|$)",
    re.I,
)

_MARKET_RESEARCH_DOMAIN = re.compile(
    r"(?:^|\.)(?:"
    r"marketresearch|coherentmarket|emergenresearch|expertmarket|theinsightpartners|"
    r"sphericalinsights|intentmarket|datainsights|futuremarketinsights|grandviewresearch|"
    r"factmr|zionmarketresearch|straitsresearch|credenceresearch|cognitivemarketresearch|"
    r"mnemonicsresearch|markwideresearch|valuespectrum|mordorintelligence|precedenceresearch|"
    r"alliedmarketresearch|transparencymarketresearch|researchandmarkets|"
    r"bizapedia|dnb\.com|crunchbase|zauba|volza|seair|exporthub|indiamart|tradeindia|"
    r"globalmarketstatistics|dataintelo|strategymrc|startus-insights|marknteladvisors|"
    r"globalgrowthinsights|reanin|eternityinsights|towardschemandmaterials|"
    r"archiexpo|aecinfo|accio|agents24|aajjo|clocate|chemanalyst|gminsights|"
    r"marketdataforecast|marketsandmarkets|amecoresearch|giiresearch|"
    r"datalibraryresearch|globenewswire|prnewswire|digitalsignagetoday|"
    r"avnetwork\.com|arxiv\.org|nature\.com|"
    r"arizton|azom|approvedbusiness|"
    r"justdial|foodtechbiz|fotor|resumekraft|opgram|naver\.com|cuteinternet|"
    r"instabioidea|chemicalonline\.com|ethanolproducer\.com|packaging-gateway|"
    r"ilbioeconomista|teletype\.in|status\.net|wikipedia|researchgate"
    r")(?:\.|$)",
    re.I,
)

_MARKET_RESEARCH_NAME = re.compile(
    r"\b(?:market\s+research|market\s+intelligence|research\s+reports?|"
    r"industry\s+reports?|company\s+profile\s+on\s+dnb)\b",
    re.I,
)

_GENERIC_MARKET_TERMS = frozenset(
    {
        "market",
        "company",
        "companies",
        "industry",
        "based",
        "global",
        "official",
        "website",
        "corporate",
        "producer",
        "manufacturers",
    }
)


def is_marketplace_domain(domain: str) -> bool:
    d = (domain or "").strip().lower().removeprefix("www.")
    return bool(d and _MARKETPLACE_DOMAIN.search(d))


def is_market_research_domain(domain: str) -> bool:
    d = (domain or "").strip().lower().removeprefix("www.")
    return bool(d and _MARKET_RESEARCH_DOMAIN.search(d))


def is_market_research_entity(name: str, domain: str) -> bool:
    if is_market_research_domain(domain):
        return True
    return bool(_MARKET_RESEARCH_NAME.search(name or ""))


def filter_industry_match_terms(terms: list[str]) -> list[str]:
    """Drop generic tokens that cause false 'industry_match' on report sites."""
    out: list[str] = []
    for t in terms:
        s = str(t).strip().lower()
        if len(s) < 5 or s in _GENERIC_MARKET_TERMS:
            continue
        if s.endswith(" market") and "ethylene" not in s and "bio" not in s:
            continue
        out.append(s)
    return out

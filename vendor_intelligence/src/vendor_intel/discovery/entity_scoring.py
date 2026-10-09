"""
Entity confidence scoring and domain-first name resolution.

Precision-first: extract → validate → score → add (never add on regex alone).
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vendor_intel.models import Entity

from vendor_intel.utils.domains import normalize_name

# Minimum integer score to accept a candidate at extraction time
ENTITY_ADD_THRESHOLD = 2
ENTITY_ADD_THRESHOLD_LISTICLE = 4
ENTITY_KEEP_THRESHOLD = 1
ENTITY_KEEP_THRESHOLD_SINGLE_HIT = 1

_LEADING_MARKETING_RE = re.compile(
    r"^(?:trusted|leading|innovative|global|premier|reliable|renowned|"
    r"top|best|world(?:class|wide)?|award[\s-]?winning)\s+",
    re.I,
)

_CERTIFICATION_PHRASE_RE = re.compile(
    r"\b(?:who[\s-]?gmp|iso\s*\d+|gmp\s+certified|certified\s+pharma|"
    r"who\s+certified|fda\s+approved)\b",
    re.I,
)

_CATEGORY_IDENTITY_WORDS = frozenset(
    {
        "top",
        "best",
        "leading",
        "list",
        "companies",
        "company",
        "manufacturers",
        "manufacturer",
        "suppliers",
        "supplier",
        "exporters",
        "exporter",
        "distributors",
        "distributor",
        "services",
        "solutions",
        "gmp",
        "who",
        "certified",
        "industry",
        "market",
        "firms",
        "firm",
        "providers",
        "provider",
        "wholesalers",
        "wholesaler",
        "dealers",
        "dealer",
    }
)

_GEO_CATEGORY_TAIL_RE = re.compile(
    r"^(?:india|indian|us|usa|uk|global)\s+[\w\s]{0,30}"
    r"(?:companies|company|firms|manufacturers|suppliers|exporters|distributors)\s*$",
    re.I,
)

_INDUSTRY_WORDS = _CATEGORY_IDENTITY_WORDS | frozenset(
    {
        "pharma",
        "pharmaceutical",
        "pharmaceuticals",
        "medicine",
        "medicines",
        "healthcare",
        "biotech",
        "cybersecurity",
        "security",
        "technology",
        "technologies",
        "software",
        "network",
        "networks",
        "medical",
        "formulation",
        "formulations",
        "manufacturing",
        "research",
        "clinical",
        "contract",
        "wholesale",
        "retail",
        "trading",
        "international",
        "indian",
        "india",
        "global",
    }
)


def _contains_industry_word(name: str) -> bool:
    words = set(re.findall(r"[a-z]{3,}", (name or "").lower()))
    return bool(words & _INDUSTRY_WORDS)


def is_bad_phrase(name: str) -> bool:
    """
    Reject category/listicle/marketing phrases before candidate creation.
    Returns True when the name must NOT become a candidate.
    """
    from vendor_intel.discovery.entity_extract import (
        is_generic_category_name,
        is_generic_phrase_name,
    )

    t = (name or "").strip()
    if len(t) < 3:
        return True
    if is_generic_phrase_name(t) or is_generic_category_name(t):
        return True
    if is_category_dominant_name(t):
        return True
    if _LEADING_MARKETING_RE.match(t):
        return True
    if _CERTIFICATION_PHRASE_RE.search(t):
        from vendor_intel.discovery.entity_extract import _CORPORATE_SUFFIX_IN_NAME

        if not _CORPORATE_SUFFIX_IN_NAME.search(t):
            return True
    # Long listicle-style titles: "Identify Leading Indian Pharmaceutical Companies"
    if len(t.split()) > 5 and _contains_industry_word(t):
        return True
    return False


def finalize_entity_name(title_name: str, domain: str = "") -> str | None:
    """
    Domain-first canonical name. Returns None when rejected.

    Rule: if a brand can be derived from a valid company domain, use it.
    """
    from vendor_intel.discovery.entity_extract import (
        domain_to_brand_name,
        has_valid_candidate_domain,
        is_plausible_company_name,
    )

    dom = (domain or "").strip().lower()
    domain_name = ""
    if dom and has_valid_candidate_domain(dom):
        domain_name = domain_to_brand_name(dom)
        if domain_name and not is_bad_phrase(domain_name):
            return normalize_entity_name(domain_name)

    title = normalize_entity_name(title_name)
    if not title:
        return None
    if is_bad_phrase(title):
        return None
    if domain_name:
        return normalize_entity_name(domain_name)
    if dom and is_plausible_company_name(title, dom):
        return title
    if not dom and is_plausible_company_name(title):
        return title
    return None


def normalize_entity_name(name: str) -> str:
    return normalize_name((name or "").strip())


def is_category_dominant_name(name: str) -> bool:
    """True when name is mostly industry/geo/category tokens — not a brand."""
    t = (name or "").strip()
    if not t:
        return True
    if _GEO_CATEGORY_TAIL_RE.match(t):
        return True
    if _LEADING_MARKETING_RE.match(t):
        return True
    if _CERTIFICATION_PHRASE_RE.search(t):
        from vendor_intel.discovery.entity_extract import _CORPORATE_SUFFIX_IN_NAME

        if not _CORPORATE_SUFFIX_IN_NAME.search(t):
            return True
    words = re.findall(r"[a-z]{3,}", t.lower())
    if not words:
        return True
    brandish = [
        w
        for w in words
        if w not in _CATEGORY_IDENTITY_WORDS
        and w not in {"ltd", "limited", "pvt", "inc", "corp", "labs", "pharma", "pharmaceutical"}
    ]
    if not brandish:
        return True
    if len(brandish) == 1 and brandish[0] in {"india", "indian", "international"}:
        return True
    return False


def domain_name_matches_brand(name: str, domain: str) -> bool:
    """True when domain base aligns with the company name."""
    from vendor_intel.validation.site_kind import name_domain_mismatch

    if not name or not domain:
        return False
    return not name_domain_mismatch(name, domain)


def resolve_preferred_name(title_name: str, domain: str) -> str:
    """Backward-compatible wrapper — prefer finalize_entity_name for new code."""
    final = finalize_entity_name(title_name, domain)
    return final or normalize_entity_name(title_name)


def score_entity_candidate(
    name: str,
    domain: str = "",
    *,
    origin: str = "unknown",
    occurrence_count: int = 1,
    scope: dict[str, Any] | None = None,
) -> int:
    """
    Integer confidence score for pre-insert gating.

    +2 domain matches name on valid company site
    +2 appears in multiple sources (occurrence_count >= 2)
    +1 proper noun / corporate structure
    +2 origin is company_site
    -3 generic phrase / marketing adjective lead
    -5 category or certification-only identity
    """
    from vendor_intel.discovery.entity_extract import (
        has_valid_candidate_domain,
        is_generic_category_name,
        is_generic_phrase_name,
        looks_like_company_structure,
    )
    from vendor_intel.discovery.company_registry import is_registry_company

    n = normalize_entity_name(name)
    if not n:
        return -10
    if is_bad_phrase(n):
        return -10
    if is_registry_company(n, scope):
        return 10

    score = 0

    if is_generic_phrase_name(n) or is_generic_category_name(n):
        score -= 5
    if is_category_dominant_name(n):
        score -= 5
    if _LEADING_MARKETING_RE.match(n):
        score -= 3

    if looks_like_company_structure(n):
        score += 1

    dom = (domain or "").strip().lower()
    if dom and has_valid_candidate_domain(dom):
        from vendor_intel.discovery.entity_extract import domain_to_brand_name

        brand = domain_to_brand_name(dom).lower().replace(" ", "")
        n_compact = n.lower().replace(" ", "")
        if domain_name_matches_brand(n, dom) or (
            brand and (brand == n_compact or brand in n_compact or n_compact in brand)
        ):
            score += 2
        elif resolve_preferred_name(n, dom).lower() != n.lower():
            score -= 2

    if occurrence_count >= 2:
        score += 2
    elif occurrence_count >= 3:
        score += 3

    if origin == "company_site":
        score += 2
    elif origin in {"listicle_snippet", "snippet", "title"}:
        score -= 2

    return score


def passes_entity_score(score: int, *, origin: str = "unknown") -> bool:
    if origin in {"listicle_snippet", "snippet"}:
        return score >= ENTITY_ADD_THRESHOLD_LISTICLE
    return score >= ENTITY_ADD_THRESHOLD


def rank_entity_for_verification(
    entity: "Entity | Any",
    *,
    scope: dict[str, Any] | None = None,
) -> float:
    """Pre-verify ranking — verify top-scored candidates first."""
    from vendor_intel.discovery.company_registry import is_registry_company

    name = getattr(entity, "canonical_name", "") or ""
    dom = getattr(entity, "primary_domain", "") or ""
    hits = int(getattr(entity, "discovery_count", 0) or 0)

    if is_registry_company(name, scope):
        return 1.0
    score = min(0.95, 0.25 + hits * 0.08)
    if dom and len(dom) > 4:
        score += 0.25
    if hits >= 4:
        score += 0.15
    escore = score_entity_candidate(name, dom, occurrence_count=hits, scope=scope)
    score += min(0.2, escore * 0.03)
    return min(1.0, score)


def passes_entity_keep_score(
    score: int,
    *,
    discovery_count: int = 1,
) -> bool:
    if discovery_count >= 2:
        return score >= ENTITY_KEEP_THRESHOLD
    return score >= ENTITY_KEEP_THRESHOLD_SINGLE_HIT

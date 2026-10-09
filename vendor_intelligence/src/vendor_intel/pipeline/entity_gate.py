"""
Pre-classification entity gate — reject non-participants (reports, media, marketplaces).

Keeps recall reasonable while blocking the ~40% junk the manager flagged.
Stricter lists live in discovery_fast._PIPELINE_JUNK_* (commented / retired there).
"""
from __future__ import annotations

import re
from typing import Any

from vendor_intel.pipeline.participant_domains import is_market_research_domain
from vendor_intel.validation.site_kind import classify_domain, is_non_product_site

# Hard domain blocks (platforms, gov, research portals)
_HARD_DOMAIN_EXACT = frozenset(
    {
        "researchgate.net",
        "researchgate.com",
        "pubmed.ncbi.nlm.nih.gov",
        "ncbi.nlm.nih.gov",
        "nasdaq.com",
        "polymerupdate.com",
        "exporthub.com",
        "export-hub.com",
        "iea.org",
        "fas.usda.gov",
        "usda.gov",
        "wikipedia.org",
        "facebook.com",
        "linkedin.com",
        "twitter.com",
        "x.com",
        "youtube.com",
        "instagram.com",
        "pinterest.com",
        "medium.com",
        "reddit.com",
        "github.com",
        "figma.com",
        "arduino-ide.org",
        "nature.com",
        "sourceforge.net",
        # User-requested blacklist (marketplaces, classifieds, data/news/portal sites)
        "tiktok.com",
        "globalsources.com",
        "craft.co",
        "signalhire.com",
        "slideserve.com",
        "brighthubengineering.com",
        "downtoearth.org.in",
        "carbike360.com",
        "environmental-expert.com",
        "marinelink.com",
        "profitableventure.com",
    }
)

_HARD_DOMAIN_PARTS = re.compile(
    r"(?:^|\.)("
    r"researchgate|pubmed|ncbi\.nlm|nasdaq|polymerupdate|exporthub|"
    r"indiamart|tradeindia|justdial|crunchbase|zauba|volza|seair|"
    r"alibaba|made-in-china|exportersindia|europages|dial4trade|everychina|ecer|globalsources|"
    r"marketresearch|coherentmarket|emergenresearch|expertmarket|"
    r"theinsightpartners|sphericalinsights|"
    r"chemdive|chemengonline|packaginginsights|farmprogress|"
    r"plasticstoday|worldatlas|handwiki|bloomberg|"
    r"wholesalesugar|braziliansugar|sugarexport|sugarsupplier"
    r")(?:\.|$)",
    re.I,
)

_REPORT_NAME = re.compile(
    r"\b(?:annual\s+report|biofuels?\s+annual|market\s+report|"
    r"industry\s+report|white\s+paper|press\s+release\s+archive)\b",
    re.I,
)

_GOV_EDU_TLD = re.compile(r"\.(?:gov|edu)(?:\.|$)", re.I)

_PHARMA_DOMAIN = re.compile(
    r"(?:^|\.)("
    r"firstwordpharma|emergobyul|mai-cdmo|mrchub|markspark|pharmtech|"
    r"pharmapproach|pharmchoices"
    r")(?:\.|$)",
    re.I,
)

_BAD_NAME = re.compile(
    r"^(?:home\s*[-–]|welcome|untitled|404|error|index|"
    r"top\s+\d+|best\s+\d+|list\s+of\b)",
    re.I,
)

# --- Non-commercial entity filter (government agencies, space agencies, militaries,
# universities, research labs). These "operate" satellites but are NOT market vendors, so
# they waste crawl budget. Dropped PRE-crawl. A commercial-satcom override protects real
# government-owned OPERATORS (e.g. NIGCOMSAT on .gov.ng).
_AGENCY_NAME = re.compile(
    r"\b(?:space agency|aerospace cent(?:er|re)|national aeronautics|administration|"
    r"universit(?:y|ies)|ministry|department of|air force|armed forces|army|navy|military|"
    r"armament|bundeswehr|gendarmerie|reconnaissance|state research|"
    r"design bureau|design office|research cent(?:er|re)|research institute|"
    r"research laborator|laborator(?:y|ies))\b",
    re.I,
)
_NONCOMMERCIAL_TLD = re.compile(
    r"(?:^|\.)(?:gov|mil|edu|int)(?:\.|$)|\.(?:ac|go|gob|gouv)\.[a-z]{2,3}$|\.gc\.ca$",
    re.I,
)
# Satcom-commercial markers that override the TLD rule (so a real operator on a .gov/.mil
# ccTLD — NIGCOMSAT, a national comsat operator — is NOT dropped).
_SATCOM_COMMERCIAL = re.compile(
    r"\b(?:sat|satellite|telecom|communications|broadcast|broadband)\b|comsat", re.I
)


def is_noncommercial_entity(name: str, domain: str) -> bool:
    """True if this is a government agency / military / university / research body — not a
    market vendor. Conservative: a clear agency-style NAME drops it; a non-commercial TLD
    drops it ONLY when neither name nor domain carries a satcom-commercial marker."""
    n = str(name or "")
    d = str(domain or "").lower().removeprefix("www.")
    if _AGENCY_NAME.search(n):
        return True
    if _NONCOMMERCIAL_TLD.search(d) and not (
        _SATCOM_COMMERCIAL.search(n) or _SATCOM_COMMERCIAL.search(d)
    ):
        return True
    return False


def reject_domain_only(domain: str, *, text: str = "", name: str = "") -> str | None:
    """Domain-only gate — safe when company name is not yet known (Phase 2 domain filter)."""
    domain = (domain or "").strip().lower().removeprefix("www.")
    if not domain:
        return "missing_domain"

    if domain in _HARD_DOMAIN_EXACT:
        return "blocked_platform"

    if is_market_research_domain(domain):
        return "market_research_site"

    if _HARD_DOMAIN_PARTS.search(domain):
        return "blocked_platform_pattern"

    if _GOV_EDU_TLD.search(domain):
        return "gov_or_edu"

    blob = f"{name} {domain} {text}".lower()
    if any(
        x in blob
        for x in (
            "pubmed central",
            "researchgate",
            "usda",
            "iea.org",
            "farmprogress",
            "the counter",
            "chemical engineering online",
            "plasticstoday",
        )
    ):
        return "non_company_entity"

    site_cls = classify_domain(domain)
    if site_cls in ("media", "education", "aggregator", "finance", "directory", "review"):
        return f"site_kind_{site_cls}"

    if is_non_product_site(domain, text=text, name=name or None):
        return "non_product_site"

    if _PHARMA_DOMAIN.search(domain):
        return "off_topic_pharma_cdmo"

    return None


def reject_reason(name: str, domain: str, *, text: str = "") -> str | None:
    """
    Return rejection reason string, or None if the entity may proceed.
    """
    name = (name or "").strip()
    domain = (domain or "").strip().lower().removeprefix("www.")
    if not domain:
        return "missing_domain"
    dom_reason = reject_domain_only(domain, text=text, name=name)
    if dom_reason:
        return dom_reason
    if not name:
        return "missing_name"

    # Government agency / military / university / research body — not a market vendor.
    # HARD reject (not in _SOFT_REJECTS) so it applies even to authoritative Wikidata domains.
    if is_noncommercial_entity(name, domain):
        return "non_commercial_entity"

    if _REPORT_NAME.search(name):
        return "report_not_company"

    if len(name) < 3 or len(name) > 120:
        return "bad_name_length"

    if name.lower() in ("home", "blog", "news", "contact", "about"):
        return "generic_name"

    if _BAD_NAME.search(name):
        return "article_or_page_title"

    try:
        from vendor_intel.discovery.entity_extract import is_plausible_company_name

        if not is_plausible_company_name(name, domain):
            return "not_plausible_company_name"
    except Exception:
        pass

    return None


# Soft/fuzzy rejects: heuristic classifications that misfire on real companies
# (e.g. SES mis-tagged non_product_site, government-owned operators on .gov.<cc>).
# A trusted-provenance company (seed / known major / authoritative source) overrides
# these — but never the HARD blocks below (research portals, marketplaces, missing
# domain), so junk can't sneak back in on a provenance flag.
_SOFT_REJECTS = frozenset(
    {
        "gov_or_edu",
        "non_product_site",
        "not_plausible_company_name",
        "article_or_page_title",
        "generic_name",
        "site_kind_media",
        "site_kind_education",
        "site_kind_aggregator",
        "site_kind_finance",
        "site_kind_directory",
        "site_kind_review",
    }
)


def _norm_dom(d: str) -> str:
    return str(d or "").strip().lower().removeprefix("http://").removeprefix("https://").removeprefix("www.").split("/")[0]


def filter_companies(
    companies: list[dict[str, str]],
    *,
    keep_registry: bool = True,
    trusted_domains: set[str] | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """Return (kept, rejected_with_reason).

    Provenance-aware: a company whose domain is in ``trusted_domains`` — the analyst's
    CURATED seed list only, NOT LLM-enumerated guesses — is exempt from the SOFT
    heuristic rejects in ``_SOFT_REJECTS``. Those misfire on real players (SES →
    non_product_site, NIGCOMSAT → gov_or_edu). Hard blocks (blocked platforms,
    market-research portals, missing domain) still apply to everyone, trusted or not.
    """
    trusted = {_norm_dom(d) for d in (trusted_domains or set()) if d}
    kept: list[dict[str, str]] = []
    rejected: list[dict[str, Any]] = []
    for c in companies:
        name = str(c.get("name") or "")
        domain = str(c.get("domain") or "")
        reason = reject_reason(name, domain)
        if reason and reason in _SOFT_REJECTS and _norm_dom(domain) in trusted:
            # curated company misflagged by a fuzzy rule — keep it
            kept.append({**c, "_gate_override": reason})
            continue
        if reason:
            rejected.append({**c, "reject_reason": reason})
            continue
        kept.append(c)
    return kept, rejected

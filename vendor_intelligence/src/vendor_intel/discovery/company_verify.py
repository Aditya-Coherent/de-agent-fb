"""Verify candidate company names via DuckDuckGo (real company vs junk)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from vendor_intel.discovery.entity_extract import (
    domain_to_brand_name,
    is_junk_url,
    is_listicle_domain,
    is_listicle_or_article_title,
    is_plausible_company_name,
    looks_like_company_site,
)
from vendor_intel.utils.domains import domain_from_url, normalize_name

_NON_COMPANY_TITLE = re.compile(
    r"\b(?:journal|open access|fda|food and drug|pharmacopeia|definition\s*&\s*meaning|"
    r"dictionary|delivery or pickup|wikipedia|category:)\b",
    re.I,
)

_NON_COMPANY_DOMAINS = (
    "fda.gov",
    "usp.org",
    "mdpi.com",
    "merriam-webster",
    "dictionary.com",
    "cambridge.org",
    "britannica.com",
    "wikipedia.org/wiki/",
    "grokipedia.com",
    "investopedia.com",
    "markets.com",
    "topshat.com",
    "topsmarkets.com",
)


@dataclass
class VerificationResult:
    name: str
    verdict: str  # real_company | likely_real | not_company | unclear
    confidence: float
    reason: str
    sample_urls: list[str]


def _domain_blocked(domain: str) -> bool:
    low = (domain or "").lower()
    return any(b in low for b in _NON_COMPANY_DOMAINS)


def score_verification_hit(
    name: str,
    title: str,
    link: str,
    snippet: str,
    geo: str,
) -> tuple[float, str]:
    """Return score 0-1 and short reason."""
    domain = domain_from_url(link)
    blob = f"{title} {snippet} {link}".lower()
    name_low = name.lower()
    geo_low = (geo or "").lower()

    if _domain_blocked(domain) or is_junk_url(link) or is_listicle_domain(domain):
        return 0.0, "blocked_domain"

    if _NON_COMPANY_TITLE.search(title or ""):
        return 0.05, "non_company_title"

    if is_listicle_or_article_title(title) and name_low not in blob:
        return 0.1, "listicle_not_about_name"

    # Name appears in result
    if name_low not in blob and name_low.replace(" ", "") not in domain.replace(".", ""):
        return 0.15, "name_not_in_hit"

    if looks_like_company_site(link, domain, title):
        if name_low.replace(" ", "") in domain.replace("-", "").replace(".", ""):
            # CHANGED: phase3 quality fix — domain match ≠ product company (blogs/news/dirs)
            from vendor_intel.validation.site_kind import verification_cap_for_site

            return verification_cap_for_site(
                name,
                domain,
                title,
                snippet,
                proposed_score=0.95,
                proposed_reason="official_domain_match",
            )
        return 0.75, "company_website"

    if "wikipedia.org/wiki/" in link.lower() and name_low.split()[0] in blob:
        return 0.85, "wikipedia_company_page"

    if geo_low and geo_low != "global":
        if geo_low in blob or (geo_low == "india" and ("india" in blob or "indian" in blob)):
            if re.search(r"\b(?:ltd|limited|pharma|laboratories|inc\.|corporation)\b", blob):
                return 0.7, "geo_and_corporate_signals"
        else:
            return 0.25, "geo_mismatch"

    if re.search(r"\b(?:company|corporation|manufacturer|pharma)\b", blob):
        return 0.55, "corporate_mention"

    return 0.35, "weak_match"


async def verify_company_name(
    name: str,
    *,
    geo: str = "global",
    market: str = "",
    router: object | None = None,
    discovery_count: int = 0,
) -> VerificationResult:
    clean = normalize_name(name)
    if len(clean) < 3:
        return VerificationResult(clean, "not_company", 0.0, "name_too_short", [])

    if not is_plausible_company_name(clean):
        return VerificationResult(clean, "not_company", 0.05, "implausible_name", [])

    from vendor_intel.validation.site_kind import is_article_title_name

    if (
        _NON_COMPANY_TITLE.search(clean)
        or is_listicle_or_article_title(clean)
        or is_article_title_name(clean)
    ):
        return VerificationResult(clean, "not_company", 0.05, "title_pattern_junk", [])

    from vendor_intel.discovery.company_registry import is_registry_company

    if is_registry_company(clean):
        return VerificationResult(
            clean, "real_company", 0.9, "registry_skip_search", []
        )

    if discovery_count >= 6:
        return VerificationResult(
            clean,
            "likely_real",
            0.72,
            "high_discovery_count_skip_search",
            [],
        )

    if discovery_count >= 4:
        return VerificationResult(
            clean,
            "likely_real",
            0.65,
            "multi_hit_skip_search",
            [],
        )

    geo_q = geo if geo and geo.lower() != "global" else ""
    market_bit = (market or "pharma")[:40]
    q = f"{clean} {market_bit} {geo_q}".strip()

    best = 0.0
    best_reason = "no_results"
    urls: list[str] = []

    try:
        if router is not None:
            rows = await router.search(  # type: ignore[attr-defined]
                q,
                market=market_bit,
                geo=geo,
                search_topic=clean,
                validation_mode=True,
            )
            for r in rows:
                sc, reason = score_verification_hit(
                    clean, r.title, r.link, r.snippet, geo
                )
                if sc > best:
                    best = sc
                    best_reason = reason
                if r.link and r.link not in urls:
                    urls.append(r.link)
        else:
            from vendor_intel.clients.duckduckgo import duckduckgo_search

            for r in await duckduckgo_search(q, max_results=6, geo=geo):
                sc, reason = score_verification_hit(
                    clean, r.title, r.link, r.snippet, geo
                )
                if sc > best:
                    best = sc
                    best_reason = reason
                if r.link and r.link not in urls:
                    urls.append(r.link)
    except Exception as exc:
        return VerificationResult(clean, "unclear", 0.0, f"search_error:{exc}"[:80], [])

    if is_listicle_or_article_title(clean) and best < 0.85:
        verdict = "not_company"
        best = min(best, 0.2)
        best_reason = "generic_listicle_name"
    elif best >= 0.72:
        verdict = "real_company"
    elif best >= 0.52:
        verdict = "likely_real"
    elif best >= 0.38:
        verdict = "unclear"
    else:
        verdict = "not_company"

    return VerificationResult(
        name=clean,
        verdict=verdict,
        confidence=round(best, 2),
        reason=best_reason,
        sample_urls=urls[:5],
    )

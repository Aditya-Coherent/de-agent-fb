"""Dynamic market-aware relevance — keywords from Phase 1 LLM plan, not hardcoded per industry."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Universal junk signals (any market)
_DEFAULT_EXCLUDE = (
    "market report",
    "market research",
    "directory",
    "yellow pages",
    "job board",
    "careers page",
    "wikipedia",
    "news article",
    "blog post",
    "trade magazine",
    "industry association",
    "listing site",
    "business directory",
)

_UNIVERSAL_NON_VENDOR = re.compile(
    r"\b(?:design\s+agency|marketing\s+agency|creative\s+agency|freelance\s+design|"
    r"job\s+board|careers\s+page|industry\s+association|trade\s+association|"
    r"market\s+research\s+report|business\s+directory|yellow\s+pages)\b",
    re.I,
)

# Website chrome — safe to hardcode (not industry-specific)
_UNIVERSAL_JUNK_TOKENS = frozenset(
    {
        "login",
        "register",
        "signup",
        "signin",
        "cookie",
        "cookies",
        "privacy",
        "newsletter",
        "subscribe",
        "cart",
        "checkout",
        "menu",
        "navigation",
        "skip",
        "copyright",
        "rights",
        "reserved",
        "contact",
        "home",
    }
)

_GENERIC_VENDOR_PHRASE = re.compile(
    r"\b(?:provides?\s+solutions|leading\s+provider|participates\s+in|vendor\s+in|"
    r"we\s+are\s+a\s+leading)\b",
    re.I,
)


@dataclass(frozen=True)
class MarketKeywordProfile:
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    strict_product_gate: bool


def _clean_kw_list(raw: Any, *, max_items: int = 20) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        s = str(item or "").strip().lower()
        if len(s) < 3 or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= max_items:
            break
    return out


def keyword_profile(
    scope: dict[str, Any] | None,
    query_context: dict[str, Any] | None = None,
) -> MarketKeywordProfile:
    """Resolve include/exclude keywords from LLM market plan (scope)."""
    scope = scope or {}
    ctx = query_context or {}
    include = _clean_kw_list(scope.get("include_keywords"), max_items=16)
    if not include:
        include = _clean_kw_list(scope.get("relevance_keywords"), max_items=16)
    if not include:
        include = _clean_kw_list(scope.get("industry_terms"), max_items=12)
    if not include:
        industry = str(ctx.get("industry") or scope.get("market") or "").strip()
        if industry:
            include = [w for w in re.findall(r"[a-z0-9][a-z0-9\-]{2,}", industry.lower()) if len(w) >= 4]

    exclude = _clean_kw_list(scope.get("exclude_keywords"), max_items=16)
    if not exclude:
        exclude = _clean_kw_list(scope.get("negative_keywords"), max_items=16)
    for x in _DEFAULT_EXCLUDE:
        if x not in exclude:
            exclude.append(x)

    strict = len(include) >= 3
    return MarketKeywordProfile(
        include=tuple(include),
        exclude=tuple(exclude),
        strict_product_gate=strict,
    )


def _blob(*parts: str) -> str:
    return " ".join(p for p in parts if p).lower()


# Single words — only block when they appear in domain/company name, not page body
_SOFT_IDENTITY_EXCLUDE = frozenset(
    {
        "blog",
        "news",
        "magazine",
        "consultant",
        "consulting",
        "pharma",
        "pharmaceutical",
    }
)


def include_keyword_hits(text: str, profile: MarketKeywordProfile) -> int:
    blob = _blob(text)
    hits = 0
    for kw in profile.include:
        if len(kw) < 3:
            continue
        if kw in blob or kw.replace("-", " ") in blob:
            hits += 1
    return hits


def has_product_fit(text: str, profile: MarketKeywordProfile) -> bool:
    if not profile.strict_product_gate:
        return True
    return include_keyword_hits(text, profile) >= 1


def universal_junk_penalty(text: str) -> int:
    """Negative score from nav/cookie/login chrome in crawled text."""
    from vendor_intel.pipeline.csv_fields import is_nav_keyword_junk

    if is_nav_keyword_junk(text):
        return 4
    low = _blob(text)
    penalty = 0
    tokens = re.findall(r"[a-z]{4,}", low)
    if not tokens:
        return 0
    junk_hits = sum(1 for t in tokens if t in _UNIVERSAL_JUNK_TOKENS)
    if junk_hits >= 5 and junk_hits / max(len(tokens), 1) >= 0.25:
        penalty += 3
    if _GENERIC_VENDOR_PHRASE.search(low):
        penalty += 2
    return penalty


def market_relevance_score(
    text: str,
    profile: MarketKeywordProfile,
    *,
    domain: str = "",
    name: str = "",
) -> int:
    """
    Dynamic market-aware score from Phase 1 include/exclude keywords.
    Positive = include hits; negative = exclude + universal junk.
    """
    blob = _blob(name, domain, text)
    score = include_keyword_hits(blob, profile)
    for kw in profile.exclude:
        if not kw or kw in _SOFT_IDENTITY_EXCLUDE:
            continue
        if kw in blob:
            score -= 2
    score -= universal_junk_penalty(blob)
    return score


def passes_market_relevance(
    text: str,
    profile: MarketKeywordProfile,
    *,
    domain: str = "",
    name: str = "",
    min_score: int = 2,
) -> bool:
    return market_relevance_score(text, profile, domain=domain, name=name) >= min_score


def market_context_summary(
    scope: dict[str, Any] | None,
    query_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compact market plan for LLM classify prompts."""
    scope = scope or {}
    ctx = query_context or {}
    prof = keyword_profile(scope, ctx)
    return {
        "market": str(ctx.get("industry") or scope.get("market") or ""),
        "market_definition": str(scope.get("market_definition") or "")[:500],
        "include_keywords": list(prof.include)[:12],
        "exclude_keywords": list(prof.exclude)[:10],
        "in_scope": (scope.get("market_boundary") or {}).get("in_scope")
        if isinstance(scope.get("market_boundary"), dict)
        else [],
    }


def exclude_keyword_hit(
    text: str,
    domain: str,
    profile: MarketKeywordProfile,
    *,
    name: str = "",
    trust_classify: bool = False,
) -> str | None:
    """
    Exclude junk entities. Soft words (blog, news) match domain/name only —
    not page body (footers/newsrooms mention 'blog' on real vendor sites).
    When trust_classify=True, phrase excludes also skip body text (LLM already vetted).
    """
    identity = _blob(name, domain)
    body = _blob(text)
    for kw in profile.exclude:
        if not kw:
            continue
        if kw in identity:
            return f"exclude_keyword:{kw[:24]}"
        if kw in _SOFT_IDENTITY_EXCLUDE:
            continue
        is_phrase = " " in kw or len(kw) >= 10
        if is_phrase and not trust_classify and kw in body:
            return f"exclude_keyword:{kw[:24]}"
        if not is_phrase and not trust_classify and len(kw) >= 8 and kw in body:
            return f"exclude_keyword:{kw[:24]}"
    check = identity if trust_classify else _blob(identity, body)
    if _UNIVERSAL_NON_VENDOR.search(check):
        return "non_vendor_entity"
    return None


def non_vendor_reject_reason(
    name: str,
    domain: str,
    text: str,
    *,
    scope: dict[str, Any] | None = None,
    query_context: dict[str, Any] | None = None,
    trust_classify: bool = False,
) -> str | None:
    """Hard non-vendor signals only — product fit is decided by LLM classify."""
    profile = keyword_profile(scope, query_context)
    return exclude_keyword_hit(
        text,
        domain,
        profile,
        name=name,
        trust_classify=trust_classify,
    )


def strict_market_product_match(
    text: str,
    query_context: dict[str, Any],
    scope: dict[str, Any] | None = None,
) -> bool:
    profile = keyword_profile(scope, query_context)
    return has_product_fit(text, profile)


def derive_include_keywords(query: str, industry_terms: list[str] | None = None) -> list[str]:
    """Offline fallback when LLM market map is thin — generic, not per-industry."""
    low = f"{query} {' '.join(industry_terms or [])}".lower()
    terms: list[str] = []
    seen: set[str] = set()
    for src in (industry_terms or []) + [query]:
        for phrase in re.findall(r"[a-z][a-z0-9\-]{2,}(?:\s+[a-z][a-z0-9\-]{2,}){0,3}", str(src).lower()):
            p = phrase.strip()
            if len(p) < 4 or p in seen:
                continue
            if p in ("market", "global", "companies", "brands", "official", "website"):
                continue
            seen.add(p)
            terms.append(p)
    for word in re.findall(r"[a-z]{5,}", low):
        if word not in seen and word not in ("market", "global", "companies", "brands"):
            seen.add(word)
            terms.append(word)
    for role in industry_terms or []:
        for phrase in re.findall(r"[a-z][a-z0-9\-]{2,}(?:\s+[a-z][a-z0-9\-]{2,}){0,2}", str(role).lower()):
            p = phrase.strip()
            if len(p) >= 6 and p not in seen:
                seen.add(p)
                terms.append(p)
    return terms[:14]

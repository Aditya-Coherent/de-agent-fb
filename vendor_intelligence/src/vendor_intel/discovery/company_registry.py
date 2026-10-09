"""Company seeds and domain hints from LLM scope (no hardcoded industry lists)."""
from __future__ import annotations

import re
from typing import Iterable

from vendor_intel.funnel.scope_schema import normalize_run_scope, normalize_seed_companies
from vendor_intel.models import DiscoveryHit
from vendor_intel.utils.domains import domain_from_url, normalize_name

_active_scope: dict | None = None


def set_registry_scope(scope: dict | None) -> None:
    global _active_scope
    _active_scope = normalize_run_scope(scope or {}, "") if scope else None


def get_registry_scope() -> dict | None:
    return _active_scope


def registry_companies_for_scope(scope: dict | None) -> list[tuple[str, str, str]]:
    """Known companies from Phase 1 LLM scope (seed_companies)."""
    sc = normalize_run_scope(scope or _active_scope or {}, "")
    return normalize_seed_companies(sc.get("seed_companies"))


def seed_function_for_name(name: str, scope: dict | None = None) -> str:
    """company_function from LLM seed list, if present."""
    key = normalize_name(name).lower()
    for display, _, fn in registry_companies_for_scope(scope):
        if normalize_name(display).lower() == key and fn:
            return fn
    return ""


def _seed_domain_map(scope: dict | None) -> dict[str, str]:
    dom_map: dict[str, str] = {}
    aliases: dict[str, str] = {}
    for display, domain, _fn in registry_companies_for_scope(scope):
        key = normalize_name(display).lower()
        if domain:
            dom_map[key] = domain
        aliases[key] = display
        compact = re.sub(r"[^a-z0-9]", "", key)
        if compact and domain:
            dom_map[compact] = domain
    return dom_map


def registry_domain_for_name(name: str, scope: dict | None = None) -> str:
    scope = normalize_run_scope(scope or _active_scope or {}, "")
    dom_map = _seed_domain_map(scope)
    key = normalize_name(name).lower()
    if key in dom_map:
        dom = dom_map[key]
        return dom if dom and not is_blocklisted_domain(dom) else ""
    tokens = key.split()
    if len(tokens) >= 2:
        short = " ".join(tokens[:2])
        if short in dom_map:
            return dom_map[short]
    for reg_name, dom in dom_map.items():
        if len(reg_name) < 6:
            continue
        if reg_name in key or key in reg_name:
            return dom
    return ""


def is_registry_company(name: str, scope: dict | None = None) -> bool:
    return bool(registry_domain_for_name(name, scope))


def canonical_display_name(name: str, scope: dict | None = None) -> str:
    scope = normalize_run_scope(scope or _active_scope or {}, "")
    key = normalize_name(name).lower()
    for display, _, _fn in registry_companies_for_scope(scope):
        if normalize_name(display).lower() == key:
            return display
    return normalize_name(name)


def resolve_official_domain(
    name: str,
    candidate_domains: Iterable[str] | None = None,
    *,
    scope: dict | None = None,
) -> str:
    reg = registry_domain_for_name(name, scope)
    if reg:
        return reg

    name_low = normalize_name(name).lower()
    name_compact = re.sub(r"[^a-z0-9]", "", name_low)
    best = ""
    best_score = -999

    for raw in candidate_domains or []:
        dom = (raw or "").lower().strip()
        if dom.startswith("http"):
            dom = domain_from_url(dom) or dom
        if not dom or is_blocklisted_domain(dom):
            continue
        dom_compact = dom.replace("-", "").replace(".", "")
        score = 0
        if name_compact and name_compact in dom_compact:
            score += 20
        elif name_low.split()[0] in dom and len(name_low.split()[0]) >= 4:
            score += 8
        if dom.count(".") <= 2:
            score += 2
        if score > best_score:
            best_score = score
            best = dom
    return best


def seed_discovery_hits(
    scope: dict,
    *,
    backend: str = "registry",
) -> list[DiscoveryHit]:
    """Inject LLM seed_companies from scope (0 if compiler did not provide seeds)."""
    sc = normalize_run_scope(scope, "")
    companies = registry_companies_for_scope(sc)
    if not companies:
        return []

    market = str(sc.get("market") or "market")
    geo = (sc.get("geographies") or ["global"])[0]
    company_type = str(sc.get("company_type") or "company")
    hits: list[DiscoveryHit] = []
    for display, domain, _fn in companies:
        if not domain or is_blocklisted_domain(domain):
            continue
        url = f"https://{domain}/"
        for i in range(3):
            hits.append(
                DiscoveryHit(
                    name_raw=display,
                    source_url=url,
                    source_domain=domain,
                    prompt_id="SEED" if i == 0 else f"SEED{i + 1}",
                    backend=backend,
                    snippet=(
                        f"LLM seed: {display} — {company_type} in {market} "
                        f"({geo}; official domain)."
                    ),
                    funnel_level="L0",
                    search_theme="llm_seed",
                )
            )
    return hits


def enrich_entity_domain(name: str, primary_domain: str, hit_domains: Iterable[str]) -> str:
    resolved = resolve_official_domain(name, [primary_domain, *list(hit_domains)])
    return resolved or primary_domain


def boost_registry_discovery_count(name: str, count: int, scope: dict | None = None) -> int:
    if is_registry_company(name, scope):
        return max(count, 3)
    return count


def _entity_blob(ent) -> str:
    parts = [
        getattr(ent, "canonical_name", "") or "",
        getattr(ent, "scraped_text", "") or "",
        getattr(ent, "company_description", "") or "",
        getattr(ent, "primary_domain", "") or "",
    ]
    return " ".join(parts).lower()


def _matches_negative_keywords(text: str, scope: dict) -> bool:
    import re

    for kw in scope.get("negative_keywords") or []:
        k = str(kw).strip().lower()
        if len(k) < 4:
            continue
        # Word-boundary match — avoids "ethane" matching inside "ethylene"
        if re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", text):
            return True
    return False


def filter_scope_mismatches(entities: list, scope: dict) -> list:
    """Remove candidates that clearly belong to a different industry (LLM negative_keywords)."""
    sc = normalize_run_scope(scope, "")
    negatives = sc.get("negative_keywords") or []
    if not negatives:
        return entities

    kept = []
    dropped_names: list[str] = []
    for ent in entities:
        blob = _entity_blob(ent)
        if _matches_negative_keywords(blob, sc):
            dropped_names.append(getattr(ent, "canonical_name", "") or "?")
            continue
        kept.append(ent)
    if dropped_names:
        sample = ", ".join(dropped_names[:4])
        extra = f" (+{len(dropped_names) - 4} more)" if len(dropped_names) > 4 else ""
        print(
            f"  [phase2] Dropped {len(dropped_names)} company name(s) - scope negative_keywords "
            f"(not duplicate searches): {sample}{extra}",
            flush=True,
        )
    return kept


# Backward-compatible alias
filter_wrong_vertical_entities = filter_scope_mismatches

# Legacy export (empty — seeds come from scope only)
REGISTRY_COMPANIES: list[tuple[str, str]] = []
INDIA_PHARMA_DOMAINS: dict[str, str] = {}


def registry_vertical_key(scope: dict | None) -> str | None:
    """Deprecated: vertical is defined by LLM scope market, not rule tables."""
    sc = normalize_run_scope(scope or {}, "")
    market = str(sc.get("market") or "").strip().lower()
    return market[:48] if market else None


def registry_vertical_label(vertical: str | None) -> str:
    return vertical or "market"


def registry_vertical_for_name(name: str) -> str | None:
    if is_registry_company(name):
        return str((_active_scope or {}).get("market") or "seed")
    return None


# Domains that must never be a company's primary_domain
_BLOCKLIST_DOMAINS = frozenset(
    {
        "w3schools.com",
        "geeksforgeeks.org",
        "tutorialspoint.com",
        "stackoverflow.com",
        "python.org",
        "mind2markets.com",
        "medkart.in",
        "amazon.in",
        "amazon.com",
        "flipkart.com",
        "wikipedia.org",
        "youtube.com",
        "facebook.com",
        "linkedin.com",
        "myntra.com",
        "nykaa.com",
        # CHANGED: phase2 quality fix — news/review/media (not product companies)
        "analyticsinsight.net",
        "livemint.com",
        "economictimes.com",
        "economictimes.indiatimes.com",
        "ndtv.com",
        "timesofindia.com",
        "hindustantimes.com",
        "moneycontrol.com",
        "91mobiles.com",
        "gsmarena.com",
        "gadgets360.com",
        "techradar.com",
        "theverge.com",
        "wired.com",
        "cnet.com",
        "techcrunch.com",
        "bgr.in",
        "digit.in",
        "indianexpress.com",
        "bing.com",
        "google.com",
        "yahoo.com",
        "duckduckgo.com",
        "microsoft.com",
        "snapdeal.com",
        "swiggy.com",
        "zomato.com",
        "paytm.com",
        "phonepe.com",
    }
)

_BLOCKLIST_PARTS = (
    "listicle",
    "directory",
    "marketplace",
    "wikipedia",
    "w3school",
    "tutorial",
)


def is_blocklisted_domain(domain: str) -> bool:
    if not domain:
        return True
    low = domain.lower().strip()
    if low in _BLOCKLIST_DOMAINS:
        return True
    if any(low == b or low.endswith("." + b) for b in _BLOCKLIST_DOMAINS):
        return True
    return any(p in low for p in _BLOCKLIST_PARTS)

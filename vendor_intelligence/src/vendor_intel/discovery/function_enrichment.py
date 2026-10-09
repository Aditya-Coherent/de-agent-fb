"""Post-validation company_function enrichment — domain-agnostic.

Uses scraped text + optional ``site:domain.com`` search (one query per company)
to classify role: vendor, distributor, manufacturer, service_provider, etc.

Works across pharma, ICT, food, cybersecurity — patterns are industry-neutral.
"""
from __future__ import annotations

import re
from typing import Any

from vendor_intel.discovery.candidate_quality import infer_function_from_content
from vendor_intel.utils.domains import domain_from_url

# Functions that are often wrong defaults — worth enriching
_WEAK_FUNCTIONS = frozenset({
    "unknown",
    "unclear",
    "vendor",
    "brand",
    "landscape",
    "market_leader",
    "software_vendor",
})

_MIN_TEXT_FOR_CLASSIFY = 150


def _normalize_domain(domain: str) -> str:
    d = (domain or "").strip().lower().removeprefix("www.").split("/")[0]
    return d


def needs_function_enrichment(
    company_function: str,
    *,
    content_confidence: float = 0.0,
) -> bool:
    """True when we should run site: search + re-classify."""
    fn = (company_function or "unknown").strip().lower()
    if fn in _WEAK_FUNCTIONS:
        return True
    return content_confidence < 0.78


def build_site_function_query(domain: str) -> str:
    """Single short DDG query — site: operator targets pages on that domain only."""
    dom = _normalize_domain(domain)
    if not dom or "." not in dom:
        return ""
    # Keep ≤7 words for DDG reliability (prompt_builder cap)
    return f"site:{dom} products services distributor"


def _text_blob_from_entity(entity: Any) -> str:
    parts: list[str] = []
    for attr in ("scraped_text", "company_description"):
        t = str(getattr(entity, attr, "") or "").strip()
        if t:
            parts.append(t)
    for items in (getattr(entity, "gates", None) or {}).values():
        for it in items:
            sn = str(getattr(it, "snippet", "") or "").strip()
            if sn:
                parts.append(sn[:500])
    return "\n".join(parts)


def apply_function_to_entity(
    entity: Any,
    *,
    primary_function: str,
    all_functions: list[str],
    confidence: float,
) -> None:
    """Write classification back onto Entity for Phase 3 export."""
    fn = primary_function or "unknown"
    all_fns = [f for f in (all_functions or []) if f and f != "unknown"] or [fn]
    entity.company_function = fn
    entity.discovered_functions = all_fns
    # Human-readable legacy field
    entity.company_type = fn.replace("_", " ").title()
    bd = dict(getattr(entity, "score_breakdown", None) or {})
    bd["content_function_confidence"] = round(confidence, 3)
    bd["discovered_functions"] = all_fns
    entity.score_breakdown = bd


def classify_from_text(
    text: str,
    current_function: str = "unknown",
    *,
    min_confidence: float = 0.78,
    company_name: str = "",
    domain: str = "",
) -> tuple[str, list[str], float]:
    return infer_function_from_content(
        text,
        current_function,
        min_confidence=min_confidence,
        company_name=company_name,
        domain=domain,
    )


async def enrich_entity_function(
    entity: Any,
    router: Any,
    *,
    market: str = "",
    geo: str = "global",
    search_topic: str = "",
    max_hits: int = 4,
    force_site_search: bool = False,
) -> bool:
    """
    Re-classify entity using scraped text + optional site:domain search.

    Returns True if function was updated.
    """
    domain = _normalize_domain(
        getattr(entity, "primary_domain", "") or domain_from_url(
            (getattr(entity, "scraped_urls", None) or [""])[0]
        )
    )
    current = str(
        getattr(entity, "company_function", "")
        or getattr(entity, "company_type", "")
        or "unknown"
    ).strip().lower().replace(" ", "_")

    bd = dict(getattr(entity, "score_breakdown", None) or {})
    prev_conf = float(bd.get("content_function_confidence") or 0.0)

    if not force_site_search and not needs_function_enrichment(current, content_confidence=prev_conf):
        return False

    blob = _text_blob_from_entity(entity)

    # Layer 4: site:domain search — company-specific pages only
    if domain and router is not None:
        q = build_site_function_query(domain)
        if q:
            try:
                rows = await router.search(
                    q,
                    market=market,
                    geo=geo,
                    search_topic=search_topic or market,
                    discovery_mode=False,
                    validation_mode=True,
                )
                for r in (rows or [])[:max_hits]:
                    link_dom = domain_from_url(getattr(r, "link", "") or "")
                    # Prefer snippets from the company's own domain
                    if link_dom and domain in link_dom:
                        blob += f"\n{getattr(r, 'title', '')}\n{getattr(r, 'snippet', '')}"
                    else:
                        blob += f"\n{getattr(r, 'snippet', '')}"
            except Exception:
                pass

    if len(blob.strip()) < _MIN_TEXT_FOR_CLASSIFY:
        return False

    fn, all_fns, conf = classify_from_text(
        blob,
        current,
        min_confidence=0.78,
        company_name=str(getattr(entity, "canonical_name", "") or ""),
        domain=domain,
    )
    if fn == current and conf < 0.78:
        return False

    apply_function_to_entity(
        entity, primary_function=fn, all_functions=all_fns, confidence=conf
    )
    return True

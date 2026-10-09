"""Hardcoded demo data — isolated from live pipeline."""
from __future__ import annotations

import re
from typing import Any

from vendor_intel.models import DiscoveryHit, RunConfig

MOCK_COMPANIES_LAPTOP = [
    "Dell", "HP", "Lenovo", "Asus", "Acer", "Apple", "MSI", "Samsung", "Microsoft",
    "Realme", "Xiaomi", "Redmi", "Infinix", "Honor", "LG", "Vaio", "Framework",
    "HCL", "iBall", "Micromax", "Chuwi", "Intel", "Croma",
]
MOCK_COMPANIES_PHONE = [
    "Samsung", "Apple", "Xiaomi", "Redmi", "POCO", "Oppo", "Realme", "Vivo",
    "OnePlus", "iQOO", "Motorola", "Lenovo", "Nokia", "Infinix", "Tecno", "Honor",
]

MOCK_CONFIG_BASE: dict[str, Any] = {
    "scope": {
        "intent": "market_map",
        "market": "General",
        "geographies": [],
        "segment_conditions": [],
        "anchor_company": None,
        "anchor_exclude_from_company_list": False,
        "in_scope": ["vendors in query scope"],
        "out_of_scope": ["retailers-only", "component-only"],
        "interpretation_summary": "Mock compiled scope",
    },
    "gates": {
        "operational_status": "hard_fail",
        "geography": "hard_fail",
        "product": "hard_fail",
        "activity_12_months": "soft_flag",
    },
    "listing_rules": {
        "use_cluster_priors": False,
        "sibling_independence": {
            "split_if": ["material_share"],
            "collapse_if": ["single_player_discourse"],
            "parent_only_if": ["subsidiary_dead_in_geo"],
        },
    },
    "ranking": {},
    "evidence_policy": {"min_distinct_domains_final": 3, "min_domains_per_gate": 2},
    "freshness_policy": {"corporate_events_lookback_days": 7, "ma_cache_ttl_hours": 0},
    "output": {"list_purpose": "research", "min_final_count": 25, "max_final_count": 50, "never_pad": True},
}


def is_mock_run(settings) -> bool:
    return bool(settings.mock_mode or settings.use_mock_data)


def build_mock_compiler_config(query: str) -> RunConfig:
    from vendor_intel.funnel.levels import merge_funnel_into_config
    from vendor_intel.funnel.query_intent import enrich_scope_from_query

    data = {**MOCK_CONFIG_BASE}
    scope = enrich_scope_from_query({**data["scope"]}, query)
    scope["interpretation_summary"] = f"Mock mode for: {query[:80]}"
    scope["scope_source"] = "mock"
    if "xiaomi" in query.lower():
        scope["anchor_company"] = "Xiaomi"
        scope["intent"] = "competitor_set"
        scope["anchor_exclude_from_company_list"] = True
    if "budget" in query.lower():
        scope["segment_conditions"] = [{"type": "price_band", "label": "budget"}]
        scope["explicit_exclusions"] = ["Apple"]

    cfg = RunConfig(
        scope=scope,
        gates=data["gates"],
        listing_rules=data["listing_rules"],
        ranking=data["ranking"],
        evidence_policy=data["evidence_policy"],
        freshness_policy=data["freshness_policy"],
    )
    return merge_funnel_into_config(cfg, query)


def generate_mock_discovery_hits(
    query: str,
    prompts: list[dict],
    backend: str = "mock",
) -> list[DiscoveryHit]:
    q = query.lower()
    pool = MOCK_COMPANIES_PHONE if "phone" in q or "smartphone" in q or "xiaomi" in q else MOCK_COMPANIES_LAPTOP
    hits: list[DiscoveryHit] = []
    for p in prompts:
        level = p.get("level", p.get("id", ""))
        for name in pool:
            slug = re.sub(r"[^a-z0-9]", "", name.lower())
            hits.append(
                DiscoveryHit(
                    name_raw=name,
                    source_url=f"https://example-mock.com/list/{p['id']}/{slug}",
                    source_domain="example-mock.com",
                    prompt_id=p.get("id", "P0"),
                    backend=backend,
                    snippet=f"{name} listed in {p.get('text', '')}",
                    funnel_level=level if level in {"L0", "L1", "L2", "L3"} else "",
                    search_theme=p.get("text", ""),
                )
            )
    return hits


def _mock_add_evidence(entity, gate: str, url: str, etype: str) -> None:
    from vendor_intel.models import EvidenceItem
    from vendor_intel.utils.domains import domain_from_url

    dom = domain_from_url(url) or "unknown"
    entity.gates.setdefault(gate, []).append(
        EvidenceItem(url=url, domain=dom, type=etype, snippet="", gate=gate)
    )


def _mock_distinct_domains(entity) -> set[str]:
    domains: set[str] = set()
    for items in entity.gates.values():
        for it in items:
            if it.domain and it.domain != "unknown":
                domains.add(it.domain)
    return domains


def apply_mock_validation(entity) -> None:
    """Synthetic gate evidence for demo runs only."""
    low = entity.canonical_name.lower()
    domain = entity.primary_domain or f"{low.replace(' ', '')}.com"
    entity.primary_domain = domain
    _mock_add_evidence(entity, "operational", f"https://{domain}/", "official")
    _mock_add_evidence(entity, "operational", f"https://news.example.com/{low.replace(' ', '-')}", "news")
    _mock_add_evidence(entity, "operational", f"https://trade.example.org/brands/{low.replace(' ', '-')}", "analyst")
    _mock_add_evidence(entity, "geography", f"https://{domain}/in", "official")
    _mock_add_evidence(entity, "geography", f"https://market.example.in/brands/{low.replace(' ', '-')}", "marketplace")
    _mock_add_evidence(entity, "ma", "https://www.wikidata.org", "registry")
    _mock_add_evidence(entity, "product", f"https://{domain}/products", "official")
    _mock_add_evidence(entity, "activity", f"https://news.example.com/{low}-launch", "news")
    entity.scraped_urls = [f"https://{domain}/"]
    entity.distinct_domains = len(_mock_distinct_domains(entity))
    entity.gate_pass = {"operational": True, "geography": True, "product": True, "activity": True}
    entity.tier = "A"
    entity.composite_score = 0.8 + min(entity.discovery_count * 0.01, 0.15)

"""LLM scope-driven seeds and filters (no hardcoded vertical tables)."""
from vendor_intel.discovery.company_registry import (
    filter_scope_mismatches,
    registry_companies_for_scope,
    seed_discovery_hits,
)
from vendor_intel.funnel.scope_schema import normalize_run_scope
from vendor_intel.models import Entity


def _scope_smartphone_llm() -> dict:
    return normalize_run_scope(
        {
            "market": "smartphone",
            "geographies": ["India"],
            "company_type": "manufacturer and brand",
            "relevance_keywords": ["smartphone", "mobile phone", "handset"],
            "negative_keywords": ["pharmaceutical", "medicine", "drug", "cipla"],
            "seed_companies": [
                {"canonical_name": "Samsung", "primary_domain": "samsung.com"},
                {"canonical_name": "Xiaomi", "primary_domain": "mi.com"},
            ],
        },
        "top smartphone companies in India",
    )


def test_llm_seeds_only_for_scope():
    scope = _scope_smartphone_llm()
    companies = registry_companies_for_scope(scope)
    assert any(n == "Samsung" for n, _ in companies)
    hits = seed_discovery_hits(scope)
    names = {h.name_raw for h in hits}
    assert "Samsung" in names
    assert "Cipla" not in names


def test_negative_keywords_filter_pharma():
    scope = _scope_smartphone_llm()
    entities = [
        Entity(canonical_name="Samsung", primary_domain="samsung.com"),
        Entity(canonical_name="Cipla", primary_domain="cipla.com"),
    ]
    kept = filter_scope_mismatches(entities, scope)
    names = {e.canonical_name for e in kept}
    assert "Samsung" in names
    assert "Cipla" not in names

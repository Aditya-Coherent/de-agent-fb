"""Tests for hybrid validation (scrape signals + agent helpers)."""
from vendor_intel.models import Entity
from vendor_intel.validation.scrape_signals import analyze_site_text
from vendor_intel.validation.validation_agent import (
    apply_agent_verdicts,
    borderline_for_agent_review,
    final_quality_sweep,
    try_deterministic_promotion,
)


def test_analyze_site_pharma_india():
    text = (
        "Cipla is a leading pharmaceutical company in India. "
        "We manufacture medicines and APIs for global healthcare."
    )
    sig = analyze_site_text(text)
    assert sig["pharma_relevant"]
    assert sig["geo_india"]
    assert sig["looks_like_company"]
    assert sig["confidence"] > 0.7


def test_analyze_site_junk():
    sig = analyze_site_text("Python Lists - W3Schools tutorial for beginners")
    assert sig["junk_site"]
    assert not sig["pharma_relevant"]


def test_deterministic_promotion_registry():
    from vendor_intel.discovery.company_registry import set_registry_scope

    set_registry_scope(
        {
            "market": "pharmaceutical",
            "geographies": ["India"],
            "company_type": "manufacturer",
            "relevance_keywords": ["pharmaceutical", "medicine", "drug", "api"],
            "negative_keywords": ["smartphone", "tutorial"],
            "seed_companies": [
                {"canonical_name": "Cipla", "primary_domain": "cipla.com"},
            ],
        }
    )
    ent = Entity(
        canonical_name="Cipla",
        primary_domain="cipla.com",
        tier="C",
        suppression_reason="gates_insufficient",
        scraped_text=(
            "Cipla is a leading pharmaceutical company in India manufacturing "
            "medicines and healthcare products for patients worldwide."
        ),
        gate_pass={"operational": True, "product": True, "geography": True},
    )
    assert try_deterministic_promotion(ent, market="pharmaceutical", geo="India")
    assert ent.tier == "B"


def test_borderline_tier_c_with_gates():
    ent = Entity(
        canonical_name="Dr. Reddy's Laboratories",
        primary_domain="drreddys.com",
        tier="C",
        suppression_reason="gates_insufficient",
        scraped_text="Pharmaceutical manufacturer in India with formulations and APIs.",
        gate_pass={"operational": True, "product": True, "geography": False},
        discovery_count=3,
    )
    assert borderline_for_agent_review(ent, geo="India", market="pharmaceutical")


def test_agent_verdict_demotes_junk():
    ent = Entity(
        canonical_name="Python Lists",
        primary_domain="w3schools.com",
        tier="B",
        scraped_text="Python Lists tutorial",
    )
    apply_agent_verdicts(
        [ent],
        [
            {
                "canonical_name": "Python Lists",
                "is_real_company": False,
                "is_pharma_relevant": False,
                "suggested_tier": "C",
                "confidence": 0.95,
            }
        ],
    )
    assert ent.tier == "C"
    assert ent.suppression_reason == "agent_rejected"


def test_final_quality_sweep_demotes_blocklist():
    ent = Entity(
        canonical_name="Python Lists",
        primary_domain="w3schools.com",
        tier="B",
    )
    out = final_quality_sweep([ent])
    assert out["demoted"] == 1
    assert ent.tier == "C"

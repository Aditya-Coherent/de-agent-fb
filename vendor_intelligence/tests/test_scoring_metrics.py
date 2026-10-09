"""Grounded scoring — no arbitrary LLM composite."""
from vendor_intel.models import Entity
from vendor_intel.scoring.metrics import (
    WEIGHT_DISCOVERY,
    compute_score_breakdown,
)


def test_composite_is_weighted_sum():
    ent = Entity(
        canonical_name="Acme Mobile",
        primary_domain="acme.com",
        discovery_count=6,
        distinct_domains=3,
        gate_pass={
            "operational": True,
            "geography": True,
            "product": True,
            "activity": False,
        },
        scraped_text="We are a leading smartphone manufacturer in India with 5G handsets.",
    )
    scope = {
        "market": "smartphone",
        "relevance_keywords": ["smartphone", "5G", "handset"],
        "negative_keywords": ["pharma"],
    }
    bd = compute_score_breakdown(ent, market="smartphone", verification_confidence=0.8, scope=scope)
    assert 0.0 <= bd.composite <= 1.0
    assert bd.discovery_score > 0.5
    assert bd.evidence_score > 0.4
    assert bd.formula.startswith(f"{WEIGHT_DISCOVERY}*disc")


def test_junk_site_low_tier():
    ent = Entity(
        canonical_name="Tutorial",
        scraped_text="Python Lists - W3Schools",
        gate_pass={"operational": True, "geography": False, "product": False},
    )
    bd = compute_score_breakdown(ent, scope={"relevance_keywords": ["smartphone"]})
    assert bd.tier_recommendation == "C"

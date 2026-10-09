from vendor_intel.discovery.candidate_pool import (
    VerificationRecord,
    ensure_minimum_candidates,
    verification_passes,
)
from vendor_intel.models import Entity


def test_verification_passes_relaxed():
    assert verification_passes(VerificationRecord("real_company", 0.8))
    assert verification_passes(VerificationRecord("likely_real", 0.55))
    assert verification_passes(VerificationRecord("unclear", 0.4), discovery_count=3)
    assert not verification_passes(VerificationRecord("not_company", 0.1))


def test_ensure_minimum_fills_to_target():
    names = [
        "Sun Pharmaceutical Industries",
        "Cipla",
        "Lupin",
        "Aurobindo Pharma",
        "Torrent Pharmaceuticals",
        "Divis Laboratories",
        "Dr Reddys Laboratories",
        "Cadila Healthcare",
        "Glenmark Pharmaceuticals",
        "Biocon",
        "Alkem Laboratories",
        "Mankind Pharma",
        "Zydus Lifesciences",
        "Abbott India",
        "Pfizer",
        "Novartis India",
        "Sanofi India",
        "GSK Pharmaceuticals",
        "Merck India",
        "Bayer Zydus Pharma",
    ]
    entities = [
        Entity(
            canonical_name=names[i],
            primary_domain=f"example{i}.com",
            discovery_count=10 - (i % 10),
        )
        for i in range(20)
    ]
    report = [
        {"name": names[i], "verdict": "not_company", "confidence": 0.1}
        for i in range(12)
    ] + [
        {"name": names[i], "verdict": "likely_real", "confidence": 0.6}
        for i in range(12, 20)
    ]
    kept, _ = ensure_minimum_candidates(entities, report, target_solid=15, max_pool=25)
    assert len(kept) >= 8

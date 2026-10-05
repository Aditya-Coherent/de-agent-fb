"""Phase 2 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) -- LLM-Assisted Evidence Discovery.

Tests the last-resort `evidence_router._llm_claim_fallback` rung wired into
`execute_evidence_plan`, and its ONLY gate function,
`evidence.build_unverified_claim_fact`. Everything that would touch the
network is monkeypatched -- these are unit tests of the acceptance/promotion
discipline, not integration tests of real fetches.

Run with: pytest tests/test_demand_usage_phase2_discovery.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from me_engine.demandUseCaseCountry_approach2B import asset_verifier, evidence, evidence_router


def _no_sources_plan(unit: str = "tonnes") -> dict:
    """A plan whose typed-source chain is already exhausted (empty list) --
    `execute_evidence_plan`'s `for` loop is a no-op and control falls
    straight through to the Phase 2 last-resort rung, exactly as it would
    after every real acquirer in the chain has already missed."""
    return {"unit": unit, "quantity_name": "Cement production", "sources": []}


def _ok_verification(url: str, content: bytes) -> asset_verifier.AssetVerification:
    return asset_verifier.AssetVerification(
        resolved_url=url, is_accessible=True, actual_content_type="text/html",
        matches_expected_type=True, http_status=200, content=content,
    )


def _failed_verification(url: str, rejection_reason: str = "download_failed") -> asset_verifier.AssetVerification:
    return asset_verifier.AssetVerification(
        resolved_url=url, is_accessible=False, actual_content_type="",
        matches_expected_type=False, http_status=404, rejection_reason=rejection_reason,
    )


class _ClaimLLM:
    """Stub LLM returning a fixed `complete_json` response for the claim rung."""

    def __init__(self, response: dict):
        self.response = response

    def complete_json(self, system, user):
        return self.response


class _RaisingLLM:
    def complete_json(self, system, user):
        raise RuntimeError("network down")


# --------------------------------------------------------------------------- #
# evidence.build_unverified_claim_fact -- the hard-coded ceiling
# --------------------------------------------------------------------------- #
def test_build_unverified_claim_fact_is_always_source_found_unverified():
    fact = evidence.build_unverified_claim_fact(
        value=100.0, unit="tonnes", year=2025, source_name="USGS",
        source_url="https://usgs.gov/x", citation="c", evidence_text="e",
    )
    assert fact["evidence_type"] == "source_found_unverified"
    assert fact["source"] == "USGS"


def test_build_unverified_claim_fact_requires_a_named_source():
    try:
        evidence.build_unverified_claim_fact(
            value=100.0, unit="tonnes", year=2025, source_name="", source_url="",
        )
        assert False, "expected ValueError for an unnamed claim"
    except ValueError:
        pass


def test_unverified_claim_cannot_be_promoted_by_confidence_wording():
    # Even a maximally "confident"-sounding claim never becomes anything but
    # `source_found_unverified` -- the tag is hard-coded, not read from the
    # LLM's own claimed confidence/certainty.
    fact = evidence.build_unverified_claim_fact(
        value=100.0, unit="tonnes", year=2025, source_name="Very Authoritative Institute",
        source_url="https://example.com/report.pdf", citation="definitely 100 tonnes, no doubt",
        evidence_text="I am 100% certain this is correct.", reasoning="extremely confident",
    )
    assert fact["evidence_type"] == "source_found_unverified"


# --------------------------------------------------------------------------- #
# evidence_router._llm_claim_fallback / execute_evidence_plan wiring
# --------------------------------------------------------------------------- #
def test_llm_claim_with_named_source_and_number_becomes_unverified_not_observed(monkeypatch):
    monkeypatch.setattr(
        evidence_router.asset_verifier, "verify_asset",
        lambda url, expected_type: _failed_verification(url),
    )
    llm = _ClaimLLM({"candidates": [
        {"source_name": "USGS Minerals Yearbook", "document_title": "Cement 2025",
         "source_url": "https://usgs.gov/cement-2025.pdf", "value": 4200000.0, "year": 2025,
         "evidence_text": "US cement production was 4.2 million tonnes in 2025.",
         "citation": "4.2 million tonnes", "reasoning": "USGS is the authoritative US source."},
    ]})
    fact, attempts = evidence_router.execute_evidence_plan(llm, _no_sources_plan(), "US", context="ctx")
    assert fact is not None
    assert fact["evidence_type"] == "source_found_unverified"
    assert fact["source"] == "USGS Minerals Yearbook"
    assert fact["source_url"] == "https://usgs.gov/cement-2025.pdf"
    assert fact["value"] == 4200000.0
    assert attempts[-1]["source_type"] == "llm_claim"
    assert attempts[-1]["found"] is True


def test_llm_claim_multiple_candidates_first_verified_wins(monkeypatch):
    calls = []

    def _verify(url, expected_type):
        calls.append(url)
        if url == "https://real.gov/data.html":
            return _ok_verification(url, b"<html><body>Cement production was 5 tonnes in 2025.</body></html>")
        return _failed_verification(url)

    monkeypatch.setattr(evidence_router.asset_verifier, "verify_asset", _verify)
    llm = _ClaimLLM({"candidates": [
        {"source_name": "Fake Institute", "source_url": "https://fake.example/x", "value": 1.0,
         "year": 2025, "evidence_text": "", "citation": "", "reasoning": "r1"},
        {"source_name": "Real Gov", "source_url": "https://real.gov/data.html", "value": 5.0,
         "year": 2025, "evidence_text": "", "citation": "5 tonnes", "reasoning": "r2"},
    ]})
    fact, attempts = evidence_router.execute_evidence_plan(llm, _no_sources_plan(), "US", context="ctx")
    assert fact is not None
    assert fact["source"] == "Real Gov"
    # both candidate URLs were actually offered to the verifier -- confirms
    # "multiple candidate sources" are genuinely considered, not just the first.
    assert set(calls) == {"https://fake.example/x", "https://real.gov/data.html"}


def test_llm_claim_accessible_verified_source_is_observed_not_unverified(monkeypatch):
    monkeypatch.setattr(
        evidence_router.asset_verifier, "verify_asset",
        lambda url, expected_type: _ok_verification(
            url, b"<html><body>Cement production was 4200000 tonnes in 2025.</body></html>",
        ),
    )
    llm = _ClaimLLM({"candidates": [
        {"source_name": "USGS", "source_url": "https://usgs.gov/cement", "value": 4200000.0,
         "year": 2025, "evidence_text": "", "citation": "4200000 tonnes", "reasoning": "authoritative"},
    ]})
    fact, attempts = evidence_router.execute_evidence_plan(llm, _no_sources_plan(), "US", context="ctx")
    assert fact is not None
    # a candidate URL that code independently verified AND whose fetched body
    # actually states the value earns the ordinary `build_fact` gate's
    # strongest tag -- never capped at `source_found_unverified`.
    assert fact["evidence_type"] == "verified_observed"


def test_llm_claim_inaccessible_source_stays_unverified_never_promoted(monkeypatch):
    monkeypatch.setattr(
        evidence_router.asset_verifier, "verify_asset",
        lambda url, expected_type: _failed_verification(url, "download_failed"),
    )
    llm = _ClaimLLM({"candidates": [
        {"source_name": "USGS", "source_url": "https://usgs.gov/cement-2025.pdf", "value": 4200000.0,
         "year": 2025, "evidence_text": "e", "citation": "c", "reasoning": "r"},
    ]})
    fact, attempts = evidence_router.execute_evidence_plan(llm, _no_sources_plan(), "US", context="ctx")
    assert fact is not None
    assert fact["evidence_type"] == "source_found_unverified"


def test_llm_claim_candidate_url_rejected_by_phase1_verifier_falls_back_to_unverified(monkeypatch):
    # The URL exists and the LLM is confident, but Phase 1's verifier catches
    # it as an HTML landing page / wrong content type, not the real asset --
    # the claim must still survive as `source_found_unverified`, not be
    # discarded outright.
    monkeypatch.setattr(
        evidence_router.asset_verifier, "verify_asset",
        lambda url, expected_type: asset_verifier.AssetVerification(
            resolved_url=url, is_accessible=True, actual_content_type="text/html",
            matches_expected_type=False, http_status=200, content=b"<html>landing page</html>",
            rejection_reason="html_landing_page_not_asset",
        ),
    )
    llm = _ClaimLLM({"candidates": [
        {"source_name": "USGS", "source_url": "https://usgs.gov/cement.pdf", "value": 100.0,
         "year": 2025, "evidence_text": "", "citation": "", "reasoning": "r"},
    ]})
    fact, attempts = evidence_router.execute_evidence_plan(llm, _no_sources_plan(), "US", context="ctx")
    assert fact is not None
    assert fact["evidence_type"] == "source_found_unverified"


def test_llm_claim_with_no_named_source_falls_through_to_none(monkeypatch):
    llm = _ClaimLLM({"candidates": []})
    fact, attempts = evidence_router.execute_evidence_plan(llm, _no_sources_plan(), "US", context="ctx")
    assert fact is None
    assert attempts[-1]["source_type"] == "llm_claim"
    assert attempts[-1]["found"] is False
    assert attempts[-1]["rejection_reason"] == "no_specific_claim"


def test_llm_claim_malformed_candidate_is_ignored_not_crashed(monkeypatch):
    llm = _ClaimLLM({"candidates": [
        {"source_name": "", "source_url": "https://x.example", "value": 100.0},  # no source_name
        {"source_name": "No Value Institute", "value": "not-a-number"},  # unparseable value
        "not-even-a-dict",
    ]})
    fact, attempts = evidence_router.execute_evidence_plan(llm, _no_sources_plan(), "US", context="ctx")
    assert fact is None
    assert attempts[-1]["rejection_reason"] == "no_specific_claim"
    assert attempts[-1]["num_candidates"] == 0


def test_llm_claim_call_failure_falls_through_without_raising():
    fact, attempts = evidence_router.execute_evidence_plan(_RaisingLLM(), _no_sources_plan(), "US", context="ctx")
    assert fact is None
    assert attempts[-1]["source_type"] == "llm_claim"
    assert attempts[-1]["rejection_reason"] == "call_failed"


def test_llm_claim_rung_never_runs_once_a_typed_source_already_won(monkeypatch):
    # Regression: if an earlier typed acquirer in the chain already produced
    # a verified fact, the router must stop there -- the Phase 2 rung must
    # never even be reached, let alone override a real, verified fact.
    monkeypatch.setattr(
        evidence_router.web_acquirer, "acquire",
        lambda llm, source, geo, *, unit, context, **_kwargs: (
            {"value": 1.0, "unit": unit, "evidence_type": "verified_observed"},
            {"source_type": "web_page", "source_name": source["source_name"], "found": True},
        ),
    )
    plan = {"unit": "tonnes", "quantity_name": "q", "sources": [
        {"source_name": "A", "source_type": "web_page", "search_query_template": "{country} a"},
    ]}
    fact, attempts = evidence_router.execute_evidence_plan(object(), plan, "US", context="ctx")
    assert fact is not None
    assert fact["evidence_type"] == "verified_observed"
    assert len(attempts) == 1  # no llm_claim rung attempt appended
    assert attempts[0]["source_type"] == "web_page"

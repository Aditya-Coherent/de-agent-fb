"""Phase 5 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) -- `api_acquirer.py` hardening
tests.

The dominant failure mode Phase 5 fixes is that "the endpoint doesn't
exist," "the endpoint is real but this query returned nothing," and every
other kind of miss all used to collapse into one generic
`download_failed`/`extraction_failed` bucket. These tests lock in the
finer-grained taxonomy this phase introduces:
    endpoint_not_found, no_data_for_query, http_error, invalid_json,
    schema_mismatch, rate_limited, network_error

Two styles are used, matching the existing Phase 1/3/4 acquirer test files:
  - Unit tests that monkeypatch `api_acquirer._fetch` directly with a fake
    `AssetVerification` (fast, isolates the acceptance/classification logic
    from the network layer).
  - A handful of tests that monkeypatch `requests.Session.get` (what the
    REAL, unmodified `asset_verifier.verify_asset` calls) to prove the
    classification is correct end-to-end from an actual HTTP response
    shape, not just from a hand-built `AssetVerification`.

Run with: pytest tests/test_demand_usage_api_acquirer.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import requests

from me_engine.demand_usage import api_acquirer, asset_verifier, evidence, evidence_router


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _ok_verification(url: str, content: bytes, content_type: str = "application/json") -> asset_verifier.AssetVerification:
    return asset_verifier.AssetVerification(
        resolved_url=url, is_accessible=True, actual_content_type=content_type,
        matches_expected_type=True, http_status=200, content=content,
    )


def _failed_verification(
    url: str, *, rejection_reason: str = "download_failed", http_status: int | None = 404,
    resolved_url: str | None = None,
) -> asset_verifier.AssetVerification:
    return asset_verifier.AssetVerification(
        resolved_url=resolved_url if resolved_url is not None else url, is_accessible=False,
        actual_content_type="", matches_expected_type=False, http_status=http_status,
        rejection_reason=rejection_reason,
    )


class _StubLLM:
    def __init__(self, response):
        self._response = response

    def complete_json(self, system, user):
        return self._response


class _FakeResponse:
    def __init__(self, *, url: str, status_code: int, content: bytes, content_type: str = ""):
        self.url = url
        self.status_code = status_code
        self.content = content
        self.headers = {"content-type": content_type} if content_type else {}


def _patch_get(monkeypatch, fn):
    monkeypatch.setattr(requests.Session, "get", lambda self, url, **kw: fn(url, **kw))


_SOURCE = {"source_name": "OpenData API", "locator": "https://api.example.com/{country}"}


# --------------------------------------------------------------------------- #
# Valid endpoint + valid JSON + matching field -- deterministic extraction,
# LLM proposes the path only.
# --------------------------------------------------------------------------- #
def test_valid_endpoint_deterministic_extraction_of_matching_field(monkeypatch):
    payload = {"data": {"country": "India", "series": [{"year": 2025, "value": 42.0}]}}
    monkeypatch.setattr(
        api_acquirer, "_fetch",
        lambda url: _ok_verification(url, json.dumps(payload).encode("utf-8")),
    )
    llm = _StubLLM({"found": True, "path": ["data", "series", "0", "value"]})

    fact, attempt = api_acquirer.acquire(
        llm, _SOURCE, "India", unit="tonnes", context="ctx", year=2025,
    )
    assert fact is not None
    assert fact["value"] == 42.0
    assert attempt["found"] is True
    assert attempt["field_path"] == "data.series.0.value"


def test_llm_field_mapping_is_used_but_code_traverses_deterministically(monkeypatch):
    """The LLM only ever proposes the PATH -- it never reads the value
    itself. Prove a wrong/malicious-looking value embedded in the LLM's own
    response (if it tried to state one) is ignored; only the traversed
    value from the real payload is trusted."""
    payload = {"result": {"value": 8_900_000}}
    monkeypatch.setattr(
        api_acquirer, "_fetch",
        lambda url: _ok_verification(url, json.dumps(payload).encode("utf-8")),
    )
    llm = _StubLLM({"found": True, "path": ["result", "value"], "value": 999})  # extraneous, must be ignored

    fact, attempt = api_acquirer.acquire(
        llm, _SOURCE, "India", unit="barrels", context="ctx", year=None,
    )
    assert fact["value"] == 8_900_000.0


# --------------------------------------------------------------------------- #
# Verified-observed fact -- full provenance
# --------------------------------------------------------------------------- #
def test_verified_observed_api_fact_carries_full_provenance(monkeypatch):
    payload = {"observations": [{"year": 2025, "value": 3.14}]}
    monkeypatch.setattr(
        api_acquirer, "_fetch",
        lambda url: _ok_verification(url, json.dumps(payload).encode("utf-8")),
    )
    llm = _StubLLM({"found": True, "path": ["observations", "0", "value"]})

    fact, attempt = api_acquirer.acquire(
        llm, _SOURCE, "India", unit="units", context="ctx", year=2025,
    )
    assert fact["evidence_type"] == "verified_observed"
    assert fact["verification_status"] == "verified_observed"
    assert fact["source"] == "OpenData API"
    assert fact["source_url"] == "https://api.example.com/India"
    assert fact["field_path"] == "path=observations.0.value"
    assert fact["endpoint"] == "https://api.example.com/{country}"
    assert fact["geography"] == "India"
    assert fact["response_status"] == 200
    assert attempt["source_url"] == "https://api.example.com/India"
    assert attempt["resolved_url"] == "https://api.example.com/India"
    assert attempt["response_status"] == 200
    assert attempt["endpoint"] == "https://api.example.com/{country}"


# --------------------------------------------------------------------------- #
# endpoint_not_found -- 404, other 4xx, malformed URL, HTML error page
# --------------------------------------------------------------------------- #
def test_404_endpoint_is_endpoint_not_found(monkeypatch):
    monkeypatch.setattr(
        api_acquirer, "_fetch",
        lambda url: _failed_verification(url, rejection_reason="download_failed", http_status=404),
    )
    fact, attempt = api_acquirer.acquire(
        object(), _SOURCE, "India", unit="u", context="ctx", year=None,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "endpoint_not_found"
    assert attempt["response_status"] == 404


def test_other_4xx_is_also_endpoint_not_found(monkeypatch):
    monkeypatch.setattr(
        api_acquirer, "_fetch",
        lambda url: _failed_verification(url, rejection_reason="download_failed", http_status=403),
    )
    fact, attempt = api_acquirer.acquire(
        object(), _SOURCE, "India", unit="u", context="ctx", year=None,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "endpoint_not_found"


def test_html_error_page_masquerading_as_api_is_endpoint_not_found(monkeypatch):
    monkeypatch.setattr(
        api_acquirer, "_fetch",
        lambda url: _failed_verification(
            url, rejection_reason="html_landing_page_not_asset", http_status=200,
        ),
    )
    fact, attempt = api_acquirer.acquire(
        object(), _SOURCE, "India", unit="u", context="ctx", year=None,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "endpoint_not_found"


def test_malformed_locator_url_is_endpoint_not_found():
    fact, attempt = api_acquirer.acquire(
        object(), {"source_name": "X", "locator": "not-a-real-url"}, "India",
        unit="u", context="ctx", year=None,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "endpoint_not_found"


# --------------------------------------------------------------------------- #
# http_error -- 5xx
# --------------------------------------------------------------------------- #
def test_http_500_is_http_error(monkeypatch):
    monkeypatch.setattr(
        api_acquirer, "_fetch",
        lambda url: _failed_verification(url, rejection_reason="download_failed", http_status=500),
    )
    fact, attempt = api_acquirer.acquire(
        object(), _SOURCE, "India", unit="u", context="ctx", year=None,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "http_error"
    assert attempt["response_status"] == 500


# --------------------------------------------------------------------------- #
# rate_limited -- 429
# --------------------------------------------------------------------------- #
def test_429_is_rate_limited(monkeypatch):
    monkeypatch.setattr(
        api_acquirer, "_fetch",
        lambda url: _failed_verification(url, rejection_reason="download_failed", http_status=429),
    )
    fact, attempt = api_acquirer.acquire(
        object(), _SOURCE, "India", unit="u", context="ctx", year=None,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "rate_limited"
    assert attempt["response_status"] == 429


# --------------------------------------------------------------------------- #
# network_error -- DNS/connection failure, redirect loop
# --------------------------------------------------------------------------- #
def test_network_failure_is_network_error(monkeypatch):
    monkeypatch.setattr(
        api_acquirer, "_fetch",
        lambda url: _failed_verification(url, rejection_reason="download_failed", http_status=None),
    )
    fact, attempt = api_acquirer.acquire(
        object(), _SOURCE, "India", unit="u", context="ctx", year=None,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "network_error"
    assert attempt["response_status"] is None


def test_redirect_loop_is_network_error(monkeypatch):
    monkeypatch.setattr(
        api_acquirer, "_fetch",
        lambda url: _failed_verification(url, rejection_reason="redirect_loop", http_status=None),
    )
    fact, attempt = api_acquirer.acquire(
        object(), _SOURCE, "India", unit="u", context="ctx", year=None,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "network_error"


def test_network_failure_end_to_end_via_real_asset_verifier(monkeypatch):
    """Same as above but through the REAL, unmodified `asset_verifier`
    (Phase 1) -- proves the classification holds against an actual
    `requests` exception shape, not just a hand-built AssetVerification."""
    _patch_get(monkeypatch, lambda url, **kw: (_ for _ in ()).throw(
        requests.exceptions.ConnectionError("no dns")
    ))
    fact, attempt = api_acquirer.acquire(
        object(), _SOURCE, "India", unit="u", context="ctx", year=None,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "network_error"


# --------------------------------------------------------------------------- #
# invalid_json -- malformed JSON body
# --------------------------------------------------------------------------- #
def test_malformed_json_body_is_invalid_json(monkeypatch):
    # Not valid JSON, not HTML, not CSV -- asset_verifier's own sniff can't
    # confirm it as JSON, so `matches_expected_type` is False with
    # `wrong_content_type`, which Phase 5 maps to `invalid_json`.
    monkeypatch.setattr(
        api_acquirer, "_fetch",
        lambda url: _failed_verification(
            url, rejection_reason="wrong_content_type", http_status=200,
        ),
    )
    fact, attempt = api_acquirer.acquire(
        object(), _SOURCE, "India", unit="u", context="ctx", year=None,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "invalid_json"


def test_malformed_json_end_to_end_via_real_asset_verifier(monkeypatch):
    _patch_get(monkeypatch, lambda url, **kw: _FakeResponse(
        url=url, status_code=200, content=b"{not valid json!!", content_type="application/json",
    ))
    fact, attempt = api_acquirer.acquire(
        object(), _SOURCE, "India", unit="u", context="ctx", year=None,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "invalid_json"


# --------------------------------------------------------------------------- #
# no_data_for_query -- endpoint real, valid JSON, nothing for this query
# --------------------------------------------------------------------------- #
def test_llm_says_not_found_in_a_real_valid_response_is_no_data_for_query(monkeypatch):
    payload = {"data": {"country": "Brazil"}}  # real, valid JSON, just not this geography
    monkeypatch.setattr(
        api_acquirer, "_fetch",
        lambda url: _ok_verification(url, json.dumps(payload).encode("utf-8")),
    )
    llm = _StubLLM({"found": False})

    fact, attempt = api_acquirer.acquire(
        llm, _SOURCE, "India", unit="u", context="ctx", year=None,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "no_data_for_query"


def test_empty_result_array_is_no_data_for_query(monkeypatch):
    payload = {"observations": []}
    monkeypatch.setattr(
        api_acquirer, "_fetch",
        lambda url: _ok_verification(url, json.dumps(payload).encode("utf-8")),
    )
    llm = _StubLLM({"found": False})

    fact, attempt = api_acquirer.acquire(
        llm, _SOURCE, "India", unit="u", context="ctx", year=None,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "no_data_for_query"


def test_explicit_json_null_at_a_resolved_path_is_no_data_for_query(monkeypatch):
    """The path resolves all the way through to a real slot -- it's just
    null for this country/year (a common shape for e.g. World Bank/FRED
    "no observation" responses) -- distinct from the path not existing at
    all (schema_mismatch, tested below)."""
    payload = {"data": {"value": None}}
    monkeypatch.setattr(
        api_acquirer, "_fetch",
        lambda url: _ok_verification(url, json.dumps(payload).encode("utf-8")),
    )
    llm = _StubLLM({"found": True, "path": ["data", "value"]})

    fact, attempt = api_acquirer.acquire(
        llm, _SOURCE, "India", unit="u", context="ctx", year=None,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "no_data_for_query"


# --------------------------------------------------------------------------- #
# schema_mismatch -- valid endpoint, valid JSON, wrong/unexpected shape
# --------------------------------------------------------------------------- #
def test_path_not_present_in_actual_structure_is_schema_mismatch(monkeypatch):
    monkeypatch.setattr(
        api_acquirer, "_fetch",
        lambda url: _ok_verification(url, json.dumps({"a": 1}).encode("utf-8")),
    )
    llm = _StubLLM({"found": True, "path": ["b", "c"]})  # doesn't exist in the payload

    fact, attempt = api_acquirer.acquire(
        llm, _SOURCE, "India", unit="u", context="ctx", year=None,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "schema_mismatch"


def test_path_resolves_to_non_numeric_value_is_schema_mismatch(monkeypatch):
    payload = {"data": {"value": {"nested": "object, not a scalar"}}}
    monkeypatch.setattr(
        api_acquirer, "_fetch",
        lambda url: _ok_verification(url, json.dumps(payload).encode("utf-8")),
    )
    llm = _StubLLM({"found": True, "path": ["data", "value"]})

    fact, attempt = api_acquirer.acquire(
        llm, _SOURCE, "India", unit="u", context="ctx", year=None,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "schema_mismatch"


def test_out_of_range_list_index_is_schema_mismatch(monkeypatch):
    payload = {"series": [1, 2, 3]}
    monkeypatch.setattr(
        api_acquirer, "_fetch",
        lambda url: _ok_verification(url, json.dumps(payload).encode("utf-8")),
    )
    llm = _StubLLM({"found": True, "path": ["series", "99"]})

    fact, attempt = api_acquirer.acquire(
        llm, _SOURCE, "India", unit="u", context="ctx", year=None,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "schema_mismatch"


# --------------------------------------------------------------------------- #
# Redirect to a valid API endpoint
# --------------------------------------------------------------------------- #
def test_redirect_to_valid_api_endpoint_uses_resolved_url(monkeypatch):
    payload = {"value": 7.0}
    verification = asset_verifier.AssetVerification(
        resolved_url="https://api.example.com/v2/India/final", is_accessible=True,
        actual_content_type="application/json", matches_expected_type=True, http_status=200,
        content=json.dumps(payload).encode("utf-8"),
    )
    monkeypatch.setattr(api_acquirer, "_fetch", lambda url: verification)
    llm = _StubLLM({"found": True, "path": ["value"]})

    fact, attempt = api_acquirer.acquire(
        llm, _SOURCE, "India", unit="u", context="ctx", year=None,
    )
    assert fact is not None
    assert fact["value"] == 7.0
    assert fact["source_url"] == "https://api.example.com/v2/India/final"
    assert attempt["source_url"] == "https://api.example.com/v2/India/final"


def test_redirect_to_valid_api_endpoint_end_to_end_via_real_asset_verifier(monkeypatch):
    payload = {"value": 9.0}
    _patch_get(monkeypatch, lambda url, **kw: _FakeResponse(
        url="https://api.example.com/v2/India/final", status_code=200,
        content=json.dumps(payload).encode("utf-8"), content_type="application/json",
    ))
    llm = _StubLLM({"found": True, "path": ["value"]})

    fact, attempt = api_acquirer.acquire(
        llm, _SOURCE, "India", unit="u", context="ctx", year=None,
    )
    assert fact is not None
    assert fact["source_url"] == "https://api.example.com/v2/India/final"


# --------------------------------------------------------------------------- #
# LLM-call failure during field-path mapping -- distinct from a data problem
# --------------------------------------------------------------------------- #
def test_llm_call_failure_during_field_mapping_is_extraction_failed(monkeypatch):
    monkeypatch.setattr(
        api_acquirer, "_fetch",
        lambda url: _ok_verification(url, json.dumps({"a": 1}).encode("utf-8")),
    )

    class _RaisingLLM:
        def complete_json(self, system, user):
            raise RuntimeError("LLM unavailable")

    fact, attempt = api_acquirer.acquire(
        _RaisingLLM(), _SOURCE, "India", unit="u", context="ctx", year=None,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "extraction_failed"


# --------------------------------------------------------------------------- #
# Missing locator
# --------------------------------------------------------------------------- #
def test_missing_locator_is_no_source_found():
    fact, attempt = api_acquirer.acquire(
        object(), {"source_name": "X", "locator": ""}, "India", unit="u", context="ctx", year=None,
    )
    assert fact is None
    assert attempt["rejection_reason"] == "no_source_found"


# --------------------------------------------------------------------------- #
# _classify_preflight -- direct unit sweep of the deterministic classifier
# --------------------------------------------------------------------------- #
def test_classify_preflight_full_taxonomy_sweep():
    cases = [
        (dict(http_status=429, rejection_reason="download_failed"), "rate_limited"),
        (dict(http_status=404, rejection_reason="download_failed"), "endpoint_not_found"),
        (dict(http_status=401, rejection_reason="download_failed"), "endpoint_not_found"),
        (dict(http_status=500, rejection_reason="download_failed"), "http_error"),
        (dict(http_status=503, rejection_reason="download_failed"), "http_error"),
        (dict(http_status=None, rejection_reason="download_failed"), "network_error"),
        (dict(http_status=None, rejection_reason="redirect_loop"), "network_error"),
        (dict(http_status=None, rejection_reason="malformed_url"), "endpoint_not_found"),
        (dict(http_status=200, rejection_reason="html_landing_page_not_asset"), "endpoint_not_found"),
        (dict(http_status=200, rejection_reason="wrong_content_type"), "invalid_json"),
    ]
    for kwargs, expected in cases:
        verification = asset_verifier.AssetVerification(
            resolved_url="https://api.example.com/x", is_accessible=False,
            actual_content_type="", matches_expected_type=False, **kwargs,
        )
        assert api_acquirer._classify_preflight(verification) == expected, kwargs

    # A fully successful verification -> proceed, no rejection classification.
    ok = asset_verifier.AssetVerification(
        resolved_url="https://api.example.com/x", is_accessible=True, actual_content_type="application/json",
        matches_expected_type=True, http_status=200, content=b"{}",
    )
    assert api_acquirer._classify_preflight(ok) is None


# --------------------------------------------------------------------------- #
# Unverified fallback -- the API tier exhausts, the router's Phase 2
# last-resort rung produces an honestly-labeled `source_found_unverified`
# claim, never a false `observed`.
# --------------------------------------------------------------------------- #
def test_api_tier_failure_falls_through_to_unverified_claim_not_a_false_observed(monkeypatch):
    monkeypatch.setattr(
        api_acquirer, "_fetch",
        lambda url: _failed_verification(url, rejection_reason="download_failed", http_status=None),
    )

    class _ClaimLLM:
        def complete_json(self, system, user):
            if "LAST-RESORT" in system:
                return {"candidates": [{
                    "source_name": "IEA World Energy Statistics", "document_title": "",
                    "source_url": "", "value": 123.0, "year": 2025,
                    "evidence_text": "", "citation": "", "reasoning": "best recollection",
                }]}
            return {"found": False}

    plan = {
        "bucket": "b", "quantity_name": "q", "unit": "u", "kind": "demand driver",
        "sources": [dict(_SOURCE, source_type="api")],
    }
    fact, attempts = evidence_router.execute_evidence_plan(
        _ClaimLLM(), plan, "India", context="ctx",
    )
    assert fact is not None
    assert fact["evidence_type"] == "source_found_unverified"
    assert fact["verification_status"] == "source_found_unverified"
    api_attempt = next(a for a in attempts if a["source_type"] == "api")
    assert api_attempt["rejection_reason"] == "network_error"


# --------------------------------------------------------------------------- #
# Structured-fact acceptance gate itself -- unaffected by this phase, but
# confirms an api_acquirer fact still passes the existing gate correctly.
# --------------------------------------------------------------------------- #
def test_api_fact_passes_the_existing_structured_fact_gate():
    fact = evidence.build_structured_fact(
        value=42.0, unit="tonnes", year=2025, source_name="OpenData API",
        source_url="https://api.example.com/India", location_ref="path=data.value",
        geography_matched=True, ambiguous=False,
    )
    assert fact["evidence_type"] == "verified_observed"
    assert fact["verification_status"] == "verified_observed"
    assert fact["source_url"] == "https://api.example.com/India"

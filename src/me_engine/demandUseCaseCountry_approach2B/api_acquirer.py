"""Generic structured-data / API evidence acquirer.

    API/dataset URL -> Phase 1 URL verification -> endpoint pre-flight
    classification -> JSON parse -> LLM identifies which field/JSON-path
    represents the required metric from a truncated schema+sample -> CODE
    does the deterministic traversal -> structured fact

Deliberately generic -- no bespoke per-API connectors (that would violate
"do not hardcode market-specific source mappings"). If the LLM can't name a
real, well-known public endpoint for this fact, this tier is simply skipped
by the router (see `evidence_router.py`) -- never forced.

Phase 5 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) hardens this acquirer so the system
can reliably tell apart:
    - the endpoint genuinely doesn't exist / can't be reached at all
      (`endpoint_not_found`, `network_error`)
    - the endpoint is real and reachable but this specific query returned
      nothing (`no_data_for_query`)
    - the endpoint is real but the response isn't the structured data we
      expected (`invalid_json`, `schema_mismatch`)
    - the request failed for reasons unrelated to whether the endpoint
      exists (`rate_limited`, `http_error`)
These are never collapsed into a single generic failure -- see
`_classify_preflight` below, which is the deterministic (no-LLM) code that
makes this distinction from the single verified GET Phase 1's
`asset_verifier` already performs (no second network round trip needed --
`AssetVerification.content`/`http_status`/`rejection_reason` already carry
everything this classification needs).
"""
from __future__ import annotations

import json
from typing import Any

from me_engine.generation.llm import GenLLM

from . import asset_verifier, evidence, fact_validator

_MAX_SAMPLE_CHARS = 4000

# Sentinel distinct from a legitimate JSON `null` -- `_traverse` returns this
# when a path segment simply doesn't exist in the structure at all (a real
# schema/shape mismatch), vs. returning `None` when the path resolves all the
# way to an explicit JSON null (the endpoint has a slot for this data, it's
# just empty for this specific query).
_PATH_NOT_FOUND = object()


def _fetch(url: str) -> asset_verifier.AssetVerification:
    """Deterministically verify + fetch a candidate API endpoint --
    delegates redirect-following, HTTP-status, and JSON-parseability
    checking to the shared `asset_verifier` module (Phase 1,
    `DEMAND_SIDE_EVIDENCE_PLAN.md`), so a 200 status with an HTML error-page
    body is distinguished from a genuine JSON response, not just trusted.
    Reused byte-for-byte -- Phase 5 does not modify `asset_verifier.py`."""
    return asset_verifier.verify_asset(url, "json")


def _classify_preflight(verification: asset_verifier.AssetVerification) -> str | None:
    """Phase 5's deterministic endpoint-existence/reachability classifier.

    Reads the single already-performed `AssetVerification` (HTTP status,
    accessibility, rejection reason) and maps it onto the finer-grained API
    rejection taxonomy this phase introduces. Returns `None` when the
    endpoint appears to genuinely exist and served real, well-typed content
    -- the caller should proceed to parse/traverse it. Never issues a
    second network request; this is pure classification of what Phase 1's
    verifier already observed."""
    status = verification.http_status
    if status == 429:
        return "rate_limited"
    if status == 404:
        return "endpoint_not_found"
    if status is not None and status >= 500:
        return "http_error"
    if status is not None and status >= 400:
        # Any other 4xx (401/403/etc.) -- the specific resource/path this
        # locator names is not a usable, real endpoint for us either way.
        return "endpoint_not_found"
    if verification.rejection_reason == "malformed_url":
        return "endpoint_not_found"
    if verification.rejection_reason in ("redirect_loop",):
        return "network_error"
    if verification.rejection_reason == "download_failed" and status is None:
        # No HTTP response at all -- DNS failure, connection refused, or a
        # request-level timeout/exception (see `asset_verifier.verify_asset`).
        return "network_error"
    if verification.rejection_reason == "html_landing_page_not_asset":
        # A 200 status whose body is actually an HTML error/landing page is
        # a common false-positive for "this API endpoint exists" (the plan's
        # own diagnosis) -- treat it as effectively not-found, not a generic
        # download failure.
        return "endpoint_not_found"
    if verification.rejection_reason == "wrong_content_type":
        # Reachable, not HTML, but the body still isn't parseable JSON
        # (`asset_verifier`'s own JSON-parseability check already failed).
        return "invalid_json"
    if not verification.is_accessible:
        return "network_error"
    return None


def _traverse(data: Any, path: list[str]) -> Any:
    """Deterministic field-path traversal. Returns `_PATH_NOT_FOUND` (a
    distinct sentinel) when a path segment doesn't resolve at all --
    distinguished from a legitimate JSON `null` reached at the end of a
    valid path, which is returned as `None`."""
    cur = data
    for key in path:
        if isinstance(cur, list):
            try:
                idx = int(key)
            except (ValueError, TypeError):
                return _PATH_NOT_FOUND
            if not (-len(cur) <= idx < len(cur)):
                return _PATH_NOT_FOUND
            cur = cur[idx]
        elif isinstance(cur, dict):
            if key not in cur:
                return _PATH_NOT_FOUND
            cur = cur[key]
        else:
            return _PATH_NOT_FOUND
    return cur


def acquire(
    llm: GenLLM, source: dict[str, Any], geography: str, *, unit: str, context: str, year: int | None,
    emitter: Any | None = None, stage: str = "evidence_search", bucket: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    endpoint = str(source.get("locator") or "")
    attempt: dict[str, Any] = {
        "source_type": "api", "source_name": source["source_name"], "found": False,
        "endpoint": endpoint, "geography": geography, "context": context,
    }
    url = endpoint.replace("{country}", geography)
    attempt["source_url"] = url
    if not url:
        attempt["rejection_reason"] = "no_source_found"
        return None, attempt

    # Phase 1 URL verification + endpoint pre-flight, merged into the one
    # verified GET `asset_verifier` already performs (no redundant second
    # request just to "check existence" before "fetching") -- Phase 5's
    # `_classify_preflight` then deterministically reads that single result.
    verification = _fetch(url)
    attempt["resolved_url"] = verification.resolved_url
    attempt["response_status"] = verification.http_status
    if not verification.is_accessible or not verification.matches_expected_type or verification.content is None:
        attempt["source_url"] = verification.resolved_url or url
        attempt["rejection_reason"] = _classify_preflight(verification) or verification.rejection_reason or "download_failed"
        if emitter is not None:
            emitter.emit(
                stage, "source_attempt", bucket=bucket, country=geography if geography != "global" else None,
                source={"name": source.get("source_name"), "url": attempt["source_url"], "type": "api"},
                status="failed", error=attempt["rejection_reason"],
            )
        return None, attempt

    # Provenance requirement (Phase 1): the persisted fact's source_url must
    # be the RESOLVED (post-redirect) URL, never the originally-guessed one.
    url = verification.resolved_url or url
    attempt["source_url"] = url
    try:
        data = json.loads(verification.content.decode("utf-8"))
    except Exception:  # noqa: BLE001
        # Belt-and-suspenders: `asset_verifier` already JSON-parses the body
        # as part of its own type-matching check, so this should be rare in
        # practice, but never silently treat an unparseable body as anything
        # other than the invalid-JSON case it is.
        attempt["rejection_reason"] = "invalid_json"
        return None, attempt

    sample = json.dumps(data, default=str)[:_MAX_SAMPLE_CHARS]
    system = (
        "You are an API-response analyst. Given a real JSON response, identify the JSON path "
        f"(a list of keys/indices) leading to the value for: {context} (target unit: {unit}"
        + (f", year {year}" if year else "") + f", geography '{geography}'). Propose the PATH only "
        "-- you do NOT read the value yourself, code will traverse it deterministically. If this "
        "response doesn't plausibly contain it, say so honestly by returning found=false. If the "
        "field you'd point to actually measures something DIFFERENT from what was requested -- "
        "e.g. a dollar market-value field instead of a physical volume field, a capacity field "
        "instead of a production field -- you must return found=false rather than pointing to it "
        "anyway."
    )
    user = (
        f"{context}\nTarget unit: {unit}\n\nJSON response (truncated):\n{sample}\n\n"
        'Return JSON: {"found": <true|false>, "path": ["<key1>", "<key2>", ...], '
        '"field_meaning": "<in your own words, what this field actually measures, based on its '
        'key name(s) and nearby fields>", "matched_year": <the year this specific value is FOR, '
        "as actually stated by a sibling field/key in the response, or null if unstated -- never "
        'guess>"}'
    )
    llm_error: str | None = None
    try:
        resp = llm.complete_json(system, user)
    except Exception as exc:  # noqa: BLE001
        resp = None
        llm_error = str(exc)
    if emitter is not None:
        emitter.emit(
            stage, "llm_call", bucket=bucket, country=geography if geography != "global" else None,
            llm={"prompt": f"{system}\n\n{user}"[:4000], "response": str(resp)[:2000] if resp else "", "error": llm_error},
            status="failed" if llm_error else "success",
        )
    if llm_error is not None:
        attempt["rejection_reason"] = "extraction_failed"
        return None, attempt
    if not resp or not resp.get("found"):
        # Endpoint is real, response is genuinely structured JSON -- the LLM
        # just couldn't find anything in it plausibly answering this query.
        attempt["rejection_reason"] = "no_data_for_query"
        return None, attempt

    if emitter is not None:
        emitter.emit(
            stage, "source_attempt", bucket=bucket, country=geography if geography != "global" else None,
            source={"name": source.get("source_name"), "url": attempt["source_url"], "type": "api"},
            status="success",
        )

    path = [str(p) for p in (resp.get("path") or [])]
    attempt["field_path"] = ".".join(path)
    value_raw = _traverse(data, path)
    if value_raw is _PATH_NOT_FOUND:
        # The LLM's proposed path doesn't resolve against the ACTUAL
        # structure at all -- the response's real shape doesn't match what
        # was expected, a genuine schema mismatch, not "no data."
        attempt["rejection_reason"] = "schema_mismatch"
        return None, attempt
    if value_raw is None:
        # The path resolves all the way through -- the response genuinely
        # has a slot for this value, it's just null/empty for this specific
        # query (e.g. no observation for this country/year).
        attempt["rejection_reason"] = "no_data_for_query"
        return None, attempt
    try:
        value = float(value_raw)
    except (TypeError, ValueError):
        # The path resolved to something real, but not a scalar number --
        # e.g. a nested dict/list where a numeric leaf was expected.
        attempt["rejection_reason"] = "schema_mismatch"
        return None, attempt

    location_ref = f"path={'.'.join(path)}"

    # Extraction-quality gate (`fact_validator.py`, post-Phase-12 addendum):
    # a real, well-formed JSON response was found and traversed, but the
    # field the LLM pointed to -- by its own path keys and declared
    # meaning -- measures something different from what was requested.
    try:
        matched_year = int(resp.get("matched_year")) if resp.get("matched_year") is not None else None
    except (TypeError, ValueError):
        matched_year = None
    rejection = fact_validator.validate_candidate(
        requested_unit=unit, requested_context=context, requested_year=year,
        matched_metric_label=f"{location_ref} {resp.get('field_meaning', '')}",
        matched_year=matched_year, evidence_text=location_ref, is_direct_figure=True,
    )
    if rejection:
        attempt["rejection_reason"] = rejection
        return None, attempt

    fact = evidence.build_structured_fact(
        value=value, unit=unit, year=year, source_name=source["source_name"], source_url=url,
        location_ref=location_ref, geography_matched=True, ambiguous=False,
    )
    fact["field_path"] = location_ref
    fact["endpoint"] = endpoint
    fact["geography"] = geography
    fact["response_status"] = verification.http_status
    attempt.update({
        "found": True, "value": value, "unit": unit, "year": year, "source_url": url,
        "location_ref": location_ref, "evidence_type": fact["evidence_type"],
    })
    return fact, attempt

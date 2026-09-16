"""Phase 0 (DEMAND_SIDE_EVIDENCE_PLAN.md) -- additive schema contract tests.

Asserts the `VerificationStatus` literal has exactly the 4 specified values,
and that `legacy_evidence_type()` maps each one down to today's 3-value
`evidence_type` conservatively (an unverified claim must never look stronger
than a bare assumption to code that hasn't migrated to the 4-value enum yet).

This phase changes no node behavior -- these tests only cover the new,
additive pieces of `evidence.py`.
"""
from __future__ import annotations

import typing

from me_engine.demand_usage.evidence import VerificationStatus, legacy_evidence_type


def test_verification_status_has_exactly_four_values() -> None:
    values = set(typing.get_args(VerificationStatus))
    assert values == {
        "verified_observed",
        "source_found_unverified",
        "derived",
        "assumption",
    }


def test_legacy_evidence_type_mapping() -> None:
    assert legacy_evidence_type("verified_observed") == "observed"
    # Conservative: unverified must map to the WEAKEST tier, never "observed".
    assert legacy_evidence_type("source_found_unverified") == "assumption"
    assert legacy_evidence_type("derived") == "derived"
    assert legacy_evidence_type("assumption") == "assumption"


def test_legacy_evidence_type_unknown_defaults_to_assumption() -> None:
    # Any unrecognized/missing value must default to the safest (weakest)
    # interpretation, never crash and never silently promote.
    assert legacy_evidence_type("") == "assumption"
    assert legacy_evidence_type("something_unexpected") == "assumption"

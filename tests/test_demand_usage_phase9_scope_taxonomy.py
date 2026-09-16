"""Phase 9 (DEMAND_SIDE_EVIDENCE_PLAN.md) -- Scope <-> Taxonomy Validation.

Covers the aggregate "scope likely too narrow" signal, the richer per-bucket
verdict shape (evidence/confidence/source), the deterministic structural-
contradiction override, the approval-gate enforcement of out_of_scope
usages, and backward compatibility with pre-Phase-9 draft/approved shapes.

Run with: pytest tests/test_demand_usage_phase9_scope_taxonomy.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from me_engine.demand_usage import agents, checkpoints, quality, taxonomy


# --------------------------------------------------------------------------- #
# Per-bucket classification -- in_scope / ambiguous / out_of_scope, each with
# the richer evidence/confidence/source shape this phase adds.
# --------------------------------------------------------------------------- #
class _ScopeLLM:
    def __init__(self, verdicts: dict[str, dict]):
        self._verdicts = verdicts

    def complete_json(self, system, user):
        return {"buckets": [{"name": name, **v} for name, v in self._verdicts.items()]}


def _state(scope_text: str, buckets: list[dict], llm) -> dict:
    return {
        "llm": llm, "market_name": "Test Market", "log": [],
        "market_definition": {"text": scope_text},
        "usage_buckets": buckets,
    }


def test_clearly_in_scope_usage_is_classified_in_scope():
    llm = _ScopeLLM({
        "Residential Construction": {
            "classification": "in_scope", "reason": "core construction use",
            "evidence": "scope covers construction cement", "confidence": 0.95,
        },
    })
    state = _state(
        "Construction Cement -- Portland/blended cement for residential and infrastructure construction.",
        [{"name": "Residential Construction", "description": "cement used in residential building"}],
        llm,
    )
    out = agents.scope_taxonomy_validation_node(state)
    flag = out["scope_validation_flags"]["Residential Construction"]
    assert flag["classification"] == "in_scope"
    assert flag["source"] == "llm"
    assert 0.0 <= flag["confidence"] <= 1.0


def test_clearly_out_of_scope_usage_is_flagged_and_kept_visible():
    llm = _ScopeLLM({
        "Concrete": {"classification": "in_scope", "reason": "core product", "evidence": "", "confidence": 0.9},
        "Orthopedic Bone Cement": {
            "classification": "out_of_scope",
            "reason": "unrelated medical product sharing a name",
            "evidence": "scope text says nothing about medical/surgical products",
            "confidence": 0.9,
        },
    })
    state = _state(
        "Construction cement -- Portland/blended cement for concrete and masonry.",
        [
            {"name": "Concrete", "description": "structural concrete"},
            {"name": "Orthopedic Bone Cement", "description": "surgical bone cement used in joint replacement"},
        ],
        llm,
    )
    out = agents.scope_taxonomy_validation_node(state)
    flags = out["scope_validation_flags"]
    assert flags["Concrete"]["classification"] == "in_scope"
    assert flags["Orthopedic Bone Cement"]["classification"] == "out_of_scope"
    assert flags["Orthopedic Bone Cement"]["evidence"]
    # Flag only -- never auto-removed from the bucket list itself.
    assert "usage_buckets" not in out


def test_ambiguous_usage_is_classified_ambiguous_not_forced_either_way():
    llm = _ScopeLLM({
        "Petrochemical Feedstock": {
            "classification": "ambiguous",
            "reason": "scope text doesn't clearly say whether feedstock use counts",
            "evidence": "scope mentions 'fuel and industrial use' without detail",
            "confidence": 0.5,
        },
    })
    state = _state(
        "Crude oil as fuel and industrial input.",
        [{"name": "Petrochemical Feedstock", "description": "crude used as chemical feedstock"}],
        llm,
    )
    out = agents.scope_taxonomy_validation_node(state)
    assert out["scope_validation_flags"]["Petrochemical Feedstock"]["classification"] == "ambiguous"


# --------------------------------------------------------------------------- #
# Downstream/refined-product conflict -- methodology fix (2026-08-26,
# CRUDE_OIL_METHODOLOGY_FIX): a literal, structural exclusion-phrase match
# is now only a FLOOR of 'ambiguous', never a forced 'out_of_scope' -- it
# cannot tell a legitimate downstream-demand derivation pathway (e.g.
# "Transportation Fuels" for a Crude Oil market) apart from a genuinely
# excluded activity using purely syntactic word overlap. Surfacing the
# tension (still deterministic, still overrides a plain LLM 'in_scope')
# without forcing an override-gated rejection is the whole point of the fix
# -- see `_deterministic_exclusion_conflict`'s docstring.
# --------------------------------------------------------------------------- #
def test_deterministic_layer_floors_to_ambiguous_on_literal_exclusion_conflict():
    llm = _ScopeLLM({
        "Refined Petroleum Products Distribution": {
            "classification": "in_scope", "reason": "seems related to oil", "evidence": "", "confidence": 0.7,
        },
    })
    state = _state(
        "Crude oil upstream production and trade; refined petroleum products excluded.",
        [{"name": "Refined Petroleum Products Distribution", "description": "distribution of refined petroleum products"}],
        llm,
    )
    out = agents.scope_taxonomy_validation_node(state)
    flag = out["scope_validation_flags"]["Refined Petroleum Products Distribution"]
    # No longer forced to out_of_scope (which would require a human override
    # to approve) -- floors to ambiguous instead, still surfaced, still
    # deterministic, never silently dropped back to a bare 'in_scope'.
    assert flag["classification"] == "ambiguous"
    assert flag["source"] == "deterministic"
    assert flag["confidence"] >= 0.6
    assert "excludes" in flag["reason"]


def test_deterministic_layer_never_downgrades_an_llm_out_of_scope_verdict():
    """If the LLM itself already reasoned to out_of_scope (e.g. a pure
    trade/distribution activity with no genuine target-commodity demand
    link), the deterministic floor must not weaken that to ambiguous."""
    llm = _ScopeLLM({
        "Refined Petroleum Products Distribution": {
            "classification": "out_of_scope",
            "reason": "pure resale/distribution of an already-refined product creates no new crude-oil demand",
            "evidence": "", "confidence": 0.8,
        },
    })
    state = _state(
        "Crude oil upstream production and trade; refined petroleum products excluded.",
        [{"name": "Refined Petroleum Products Distribution", "description": "distribution of refined petroleum products"}],
        llm,
    )
    out = agents.scope_taxonomy_validation_node(state)
    flag = out["scope_validation_flags"]["Refined Petroleum Products Distribution"]
    assert flag["classification"] == "out_of_scope"
    assert flag["source"] == "llm"


def test_semantic_downstream_conflict_relies_on_llm_reasoning():
    """The crude-oil / transportation-fuels case from the plan: no literal
    word overlap with the scope's exclusion clause, so the deterministic
    layer stays silent and the (here-stubbed) LLM verdict is what surfaces
    the conflict -- this test locks in that the LLM's out_of_scope verdict
    is not overwritten back to in_scope by anything in this node."""
    llm = _ScopeLLM({
        "Transportation Fuels": {
            "classification": "out_of_scope",
            "reason": "transportation fuels are a refined/downstream product of crude oil, "
                       "not the raw-material scope itself",
            "evidence": "scope text: 'crude oil; refined products excluded'",
            "confidence": 0.85,
        },
    })
    state = _state(
        "Crude oil; refined products excluded.",
        [{"name": "Transportation Fuels", "description": "gasoline/diesel/jet fuel end use"}],
        llm,
    )
    out = agents.scope_taxonomy_validation_node(state)
    flag = out["scope_validation_flags"]["Transportation Fuels"]
    assert flag["classification"] == "out_of_scope"
    assert flag["source"] == "llm"


# --------------------------------------------------------------------------- #
# Unrelated ambiguous market category -- a bucket from a wholly different
# industry that merely shares a word/similar name with the market.
# --------------------------------------------------------------------------- #
def test_unrelated_industry_sharing_a_name_is_flagged_out_of_scope():
    llm = _ScopeLLM({
        "Avocado Oil": {"classification": "in_scope", "reason": "core product", "evidence": "", "confidence": 0.9},
        "Motor Oil Lubricants": {
            "classification": "out_of_scope",
            "reason": "unrelated automotive lubricant category that merely shares the word 'oil'",
            "evidence": "scope is about edible avocado oil, not industrial lubricants",
            "confidence": 0.88,
        },
    })
    state = _state(
        "Avocado oil for culinary and cosmetic use.",
        [
            {"name": "Avocado Oil", "description": "edible avocado oil"},
            {"name": "Motor Oil Lubricants", "description": "automotive engine lubricant"},
        ],
        llm,
    )
    out = agents.scope_taxonomy_validation_node(state)
    assert out["scope_validation_flags"]["Motor Oil Lubricants"]["classification"] == "out_of_scope"


# --------------------------------------------------------------------------- #
# Aggregate "scope likely too narrow" signal -- deterministic threshold math
# on top of the per-bucket verdicts, no LLM call of its own.
# --------------------------------------------------------------------------- #
def _agg_state(n_buckets: int, n_flagged: int) -> dict:
    verdicts = {}
    buckets = []
    for i in range(n_buckets):
        name = f"Bucket {i}"
        buckets.append({"name": name, "description": "x"})
        verdicts[name] = {
            "classification": "out_of_scope" if i < n_flagged else "in_scope",
            "reason": "r", "evidence": "", "confidence": 0.8,
        }
    return _state("some scope", buckets, _ScopeLLM(verdicts))


def test_all_buckets_flagged_sets_likely_scope_too_narrow_true():
    out = agents.scope_taxonomy_validation_node(_agg_state(6, 6))
    agg = out["scope_taxonomy_aggregate"]
    assert agg["flagged_count"] == 6
    assert agg["total_buckets"] == 6
    assert agg["flagged_ratio"] == 1.0
    assert agg["likely_scope_too_narrow"] is True


def test_no_buckets_flagged_sets_likely_scope_too_narrow_false():
    out = agents.scope_taxonomy_validation_node(_agg_state(4, 0))
    agg = out["scope_taxonomy_aggregate"]
    assert agg["flagged_ratio"] == 0.0
    assert agg["likely_scope_too_narrow"] is False


def test_threshold_boundary_exactly_50_percent_is_not_too_narrow():
    out = agents.scope_taxonomy_validation_node(_agg_state(4, 2))  # exactly 50%
    agg = out["scope_taxonomy_aggregate"]
    assert agg["flagged_ratio"] == 0.5
    assert agg["likely_scope_too_narrow"] is False


def test_threshold_boundary_just_above_50_percent_is_too_narrow():
    out = agents.scope_taxonomy_validation_node(_agg_state(5, 3))  # 60%
    agg = out["scope_taxonomy_aggregate"]
    assert agg["flagged_ratio"] == pytest.approx(0.6)
    assert agg["likely_scope_too_narrow"] is True


def test_threshold_boundary_just_below_50_percent_is_not_too_narrow():
    out = agents.scope_taxonomy_validation_node(_agg_state(5, 2))  # 40%
    agg = out["scope_taxonomy_aggregate"]
    assert agg["flagged_ratio"] == pytest.approx(0.4)
    assert agg["likely_scope_too_narrow"] is False


def test_llm_call_failure_yields_zero_flagged_ratio_never_a_false_warning():
    class _ThrowingLLM:
        def complete_json(self, system, user):
            raise RuntimeError("network down")

    state = _state("scope", [{"name": "A", "description": "x"}], _ThrowingLLM())
    out = agents.scope_taxonomy_validation_node(state)
    assert out["scope_taxonomy_aggregate"]["likely_scope_too_narrow"] is False


# --------------------------------------------------------------------------- #
# Approval gate -- user approval/edit persistence, no silent deletion,
# backward compatibility.
# --------------------------------------------------------------------------- #
def _draft_with_flag(classification: str) -> dict:
    return {
        "market_name": "Crude Oil",
        "usage_buckets": [
            {"name": "Transportation Fuels", "description": "fuel use", "bucket_category": "physical_consumption"},
        ],
        "drivers": {
            "Transportation Fuels": {"driver_name": "Fuel consumption", "unit": "barrels", "final_demand_unit": "barrels"},
        },
        "excluded_usage_buckets": [], "missing_usage_flags": [], "coverage_pct": 80.0, "overlap_flags": [],
        "scope_validation_flags": {
            "Transportation Fuels": {
                "classification": classification, "reason": "downstream refined product",
                "evidence": "scope excludes refined products", "confidence": 0.9, "source": "llm",
            },
        },
        "scope_taxonomy_aggregate": {
            "flagged_count": 1 if classification != "in_scope" else 0, "total_buckets": 1,
            "flagged_ratio": 1.0 if classification != "in_scope" else 0.0,
            "likely_scope_too_narrow": classification != "in_scope",
        },
    }


def test_out_of_scope_bucket_is_rejected_without_explicit_override(tmp_path):
    draft = _draft_with_flag("out_of_scope")
    with pytest.raises(ValueError, match="out_of_scope"):
        taxonomy.approve_taxonomy(draft, root=tmp_path)


def test_out_of_scope_bucket_is_accepted_with_explicit_override_and_persisted(tmp_path):
    draft = _draft_with_flag("out_of_scope")
    draft["scope_overrides"] = {"Transportation Fuels": "Client confirmed refined fuels should be in scope this cycle."}
    approved = taxonomy.approve_taxonomy(draft, root=tmp_path)
    assert approved["usage_buckets"][0]["name"] == "Transportation Fuels"
    assert approved["scope_overrides"] == {
        "Transportation Fuels": "Client confirmed refined fuels should be in scope this cycle.",
    }
    assert "Transportation Fuels" in approved["scope_amendment_text"]
    # Persistence: reload from disk gets the same override + amendment text.
    reloaded = taxonomy.load_approved_taxonomy("Crude Oil", root=tmp_path)
    assert reloaded == approved


def test_empty_override_justification_does_not_count_as_an_override(tmp_path):
    draft = _draft_with_flag("out_of_scope")
    draft["scope_overrides"] = {"Transportation Fuels": "   "}  # whitespace-only, not a real justification
    with pytest.raises(ValueError, match="out_of_scope"):
        taxonomy.approve_taxonomy(draft, root=tmp_path)


def test_ambiguous_bucket_does_not_require_an_override(tmp_path):
    draft = _draft_with_flag("ambiguous")
    approved = taxonomy.approve_taxonomy(draft, root=tmp_path)
    assert approved["usage_buckets"][0]["name"] == "Transportation Fuels"
    assert approved["scope_overrides"] == {}


def test_removing_the_flagged_bucket_needs_no_override(tmp_path):
    draft = _draft_with_flag("out_of_scope")
    draft["usage_buckets"] = []
    with pytest.raises(ValueError):  # rejected for being empty, not for scope -- proves removal path works
        taxonomy.approve_taxonomy(draft, root=tmp_path)


def test_scope_validation_flags_never_silently_dropped_for_removed_buckets(tmp_path):
    """A bucket the user removed is gone from `usage_buckets`, but its scope
    flag stays in the audit trail -- never silently deleted."""
    draft = _draft_with_flag("in_scope")
    draft["usage_buckets"].append(
        {"name": "Extra Ambiguous Bucket", "description": "x", "bucket_category": "physical_consumption"}
    )
    draft["drivers"]["Extra Ambiguous Bucket"] = {"driver_name": "d", "unit": "u", "final_demand_unit": "u"}
    draft["scope_validation_flags"]["Extra Ambiguous Bucket"] = {
        "classification": "ambiguous", "reason": "r", "evidence": "e", "confidence": 0.5, "source": "llm",
    }
    # User removes the ambiguous bucket before approving.
    draft["usage_buckets"] = [b for b in draft["usage_buckets"] if b["name"] != "Extra Ambiguous Bucket"]
    del draft["drivers"]["Extra Ambiguous Bucket"]

    approved = taxonomy.approve_taxonomy(draft, root=tmp_path)
    assert "Extra Ambiguous Bucket" not in [b["name"] for b in approved["usage_buckets"]]
    # Flag itself is still on record, for audit -- not deleted.
    assert "Extra Ambiguous Bucket" in approved["scope_validation_flags"]


def test_stale_override_for_a_removed_bucket_is_dropped_not_carried_forward(tmp_path):
    draft = _draft_with_flag("out_of_scope")
    draft["scope_overrides"] = {"Transportation Fuels": "kept", "Some Other Bucket": "stale, bucket never existed"}
    approved = taxonomy.approve_taxonomy(draft, root=tmp_path)
    assert "Some Other Bucket" not in approved["scope_overrides"]
    assert approved["scope_overrides"] == {"Transportation Fuels": "kept"}


def test_backward_compatibility_draft_with_no_scope_validation_flags_approves_cleanly(tmp_path):
    """A pre-Phase-9 draft (or a hand-built fixture) with no
    `scope_validation_flags`/`scope_taxonomy_aggregate` at all must approve
    exactly as before -- the new gate is additive, never a new hard
    requirement on old data."""
    draft = {
        "market_name": "Crude Oil",
        "usage_buckets": [
            {"name": "Transportation Fuels", "description": "x", "bucket_category": "physical_consumption"},
        ],
        "drivers": {
            "Transportation Fuels": {"driver_name": "Fuel consumption", "unit": "barrels", "final_demand_unit": "barrels"},
        },
        "excluded_usage_buckets": [], "missing_usage_flags": [], "coverage_pct": 80.0, "overlap_flags": [],
    }
    approved = taxonomy.approve_taxonomy(draft, root=tmp_path)
    assert approved["approved"] is True
    assert approved["scope_overrides"] == {}
    assert approved["scope_taxonomy_aggregate"] == {}


# --------------------------------------------------------------------------- #
# Aggregate warning surfaces at final-report/quality time too, if the
# taxonomy was approved despite it (informational -- never alters sizing).
# --------------------------------------------------------------------------- #
def test_quality_flags_surface_scope_too_narrow_warning():
    state = {
        "scope_taxonomy_aggregate": {
            "flagged_count": 6, "total_buckets": 6, "flagged_ratio": 1.0, "likely_scope_too_narrow": True,
        },
    }
    flags = quality.compute_provenance_quality_flags(state)
    codes = [f["code"] for f in flags]
    assert "scope_too_narrow_for_taxonomy" in codes


def test_quality_flags_surface_scope_override_info():
    state = {
        "scope_overrides": {"Transportation Fuels": "explicitly retained"},
        "scope_amendment_text": "Approved scope was extended...",
    }
    flags = quality.compute_provenance_quality_flags(state)
    codes = [f["code"] for f in flags]
    assert "scope_override_applied" in codes


def test_quality_flags_stay_empty_when_no_scope_signals_present():
    flags = quality.compute_provenance_quality_flags({})
    codes = [f["code"] for f in flags]
    assert "scope_too_narrow_for_taxonomy" not in codes
    assert "scope_override_applied" not in codes

"""Phase 2 (Major Use Case + India Actual Use Case + Demand Driver Draft)
tests -- the SECOND user approval gate for the India-only variant.

Mirrors `tests/test_demand_usage_taxonomy.py`'s conventions: `approve_taxonomy`
is pure validation (no LLM/network); `run_taxonomy_phase` is exercised with
the real node functions (both the reused `me_engine.demand_usage.agents.*`
ones and this package's own `actual_use_case_agents.india_actual_use_case_node`)
but a stubbed LLM/no-web config.

Run with: pytest tests/test_demand_usage_india_taxonomy.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from me_engine.demand_usage_india import actual_use_case_agents, taxonomy


def _valid_draft(**overrides) -> dict:
    base = {
        "market_name": "Vinegar",
        "major_use_cases": [
            {"name": "Food Preservation", "description": "Preserving food via acidity", "bucket_category": "physical_consumption"},
        ],
        "actual_use_cases": [
            {
                "name": "Household Pickle Preservation", "description": "Home pickling in Indian households",
                "bucket_category": "physical_consumption", "major_use_case": "Food Preservation",
            },
        ],
        "drivers": {
            "Household Pickle Preservation": {"driver_name": "Household pickle volume", "unit": "tonnes per year", "final_demand_unit": "tonnes per year"},
        },
        "excluded_usage_buckets": [],
        "missing_usage_flags": [],
        "coverage_pct": 82.0,
        "overlap_flags": [],
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# approve_taxonomy -- pure validation, no LLM/network
# --------------------------------------------------------------------------- #
def test_approve_taxonomy_writes_checkpoint_and_returns_normalized_dict(tmp_path):
    approved = taxonomy.approve_taxonomy(_valid_draft(), root=tmp_path)
    assert approved["approved"] is True
    assert approved["actual_use_cases"][0]["name"] == "Household Pickle Preservation"
    assert approved["actual_use_cases"][0]["major_use_case"] == "Food Preservation"
    # Phase 3 reads `usage_buckets` -- the flattened, approved Actual Use Cases.
    assert approved["usage_buckets"] == approved["actual_use_cases"]
    assert taxonomy.load_approved_taxonomy("Vinegar", root=tmp_path) == approved


def test_approve_taxonomy_rejects_empty_actual_use_case_list(tmp_path):
    with pytest.raises(ValueError):
        taxonomy.approve_taxonomy(_valid_draft(actual_use_cases=[]), root=tmp_path)


def test_approve_taxonomy_rejects_actual_use_case_missing_major_use_case_tag(tmp_path):
    bad = _valid_draft()
    bad["actual_use_cases"][0].pop("major_use_case")
    with pytest.raises(ValueError):
        taxonomy.approve_taxonomy(bad, root=tmp_path)


def test_approve_taxonomy_rejects_actual_use_case_with_no_driver(tmp_path):
    bad = _valid_draft()
    bad["actual_use_cases"].append({
        "name": "New Actual Use Case", "description": "x",
        "bucket_category": "physical_consumption", "major_use_case": "Food Preservation",
    })
    with pytest.raises(ValueError):
        taxonomy.approve_taxonomy(bad, root=tmp_path)


def test_load_approved_taxonomy_raises_when_never_approved(tmp_path):
    with pytest.raises(FileNotFoundError):
        taxonomy.load_approved_taxonomy("Never Approved Market", root=tmp_path)


# --------------------------------------------------------------------------- #
# india_actual_use_case_node -- the Step-4 guardrail
# --------------------------------------------------------------------------- #
class _SegmentationCandidateLLM:
    """One genuine demand pathway, one segmentation-dimension-in-disguise
    candidate ('Bottled Vinegar' -- a Form/Channel split), for the SAME
    Major Use Case -- exercises both the deterministic regex hit and the
    LLM classification pass."""

    def complete_json(self, system, user):
        if "INDIAN market" in system:
            return {"actual_use_cases": [
                {"name": "Household Pickle Preservation", "description": "Home pickling (achaar) in Indian households",
                 "evidence_type": "reasoning_only", "evidence_snippet": ""},
                {"name": "Bottled Vinegar Form", "description": "Vinegar sold in a bottled packaging form/channel",
                 "evidence_type": "reasoning_only", "evidence_snippet": ""},
            ]}
        if "SEGMENTATION DIMENSION" in system:
            return {"verdicts": [
                {"name": "Household Pickle Preservation", "is_genuine_use_case": True, "reason": "real consumption pathway"},
                {"name": "Bottled Vinegar Form", "is_genuine_use_case": False, "reason": "this is a packaging Form split, not a use case"},
            ]}
        return {}


class _StubConfig:
    enable_web = False


def test_india_actual_use_case_node_rejects_segmentation_dimension_candidate():
    state = {
        "market_name": "Vinegar", "config": _StubConfig(), "llm": _SegmentationCandidateLLM(), "log": [],
        "usage_buckets": [{"name": "Food Preservation", "description": "Preserving food via acidity"}],
    }
    out = actual_use_case_agents.india_actual_use_case_node(state)
    names = [a["name"] for a in out["actual_use_cases"]]
    assert "Household Pickle Preservation" in names
    assert "Bottled Vinegar Form" not in names
    rejected_names = [r["name"] for r in out["rejected_actual_use_case_candidates"]]
    assert "Bottled Vinegar Form" in rejected_names
    # Every retained Actual Use Case carries the major_use_case tag.
    for auc in out["actual_use_cases"]:
        assert auc["major_use_case"] == "Food Preservation"


def test_india_actual_use_case_node_deterministic_regex_catches_region_split_even_if_llm_says_genuine():
    class _LLMThatWronglyApprovesRegionSplit:
        def complete_json(self, system, user):
            if "INDIAN market" in system:
                return {"actual_use_cases": [
                    {"name": "North India Regional Demand", "description": "Demand specific to the North India region",
                     "evidence_type": "reasoning_only", "evidence_snippet": ""},
                ]}
            if "SEGMENTATION DIMENSION" in system:
                # LLM (wrongly) says this is genuine -- the deterministic
                # regex hit must still win (never solely LLM-gated).
                return {"verdicts": [{"name": "North India Regional Demand", "is_genuine_use_case": True, "reason": "seems fine"}]}
            return {}

    state = {
        "market_name": "Vinegar", "config": _StubConfig(), "llm": _LLMThatWronglyApprovesRegionSplit(), "log": [],
        "usage_buckets": [{"name": "Food Preservation", "description": "Preserving food via acidity"}],
    }
    out = actual_use_case_agents.india_actual_use_case_node(state)
    names = [a["name"] for a in out["actual_use_cases"]]
    assert "North India Regional Demand" not in names


# --------------------------------------------------------------------------- #
# run_taxonomy_phase -- runs the real reused nodes + the new India node
# end to end (stubbed LLM, web disabled) from an approved Phase 1 setup.
# --------------------------------------------------------------------------- #
class _FullPipelineStubLLM:
    def complete_json(self, system, user):
        if "usage/application buckets" in system or "usage-decomposition" in system.lower():
            return {"buckets": [{"name": "Food Preservation", "description": "Preserving food via acidity"}]}
        if "GENUINE PHYSICAL CONSUMPTION" in system:
            return {"buckets": [{"name": "Food Preservation", "is_physical_end_use": True,
                                  "classification": "physical_consumption", "reason": "genuine consumption", "confidence": 0.9}]}
        if "coverage auditor" in system.lower():
            return {"missing": [], "estimated_coverage_pct": 80.0}
        if "INDIAN market" in system:
            return {"actual_use_cases": [
                {"name": "Household Pickle Preservation", "description": "Home pickling in Indian households",
                 "evidence_type": "reasoning_only", "evidence_snippet": ""},
            ]}
        if "SEGMENTATION DIMENSION" in system:
            return {"verdicts": [{"name": "Household Pickle Preservation", "is_genuine_use_case": True, "reason": "real pathway"}]}
        if "demand-driver analyst" in system.lower():
            return {"driver_name": "Household pickle volume", "unit": "tonnes per year", "requires_conversion": False,
                     "final_demand_unit": "tonnes per year", "conversion_formula": "driver_value",
                     "required_evidence": ["industry pickle-production statistics"], "rationale": "direct measure"}
        return {}


def test_run_taxonomy_phase_flattens_actual_use_cases_with_major_use_case_tag(tmp_path, monkeypatch):
    import me_engine.demand_usage_india.taxonomy as taxonomy_mod

    monkeypatch.setattr(taxonomy_mod, "GenLLM", lambda config, cache_path=None: _FullPipelineStubLLM())

    approved_setup = {
        "market_name": "Vinegar",
        "scope": "Vinegar as a food-grade acidulant and preservative.",
        "geography": ["India"],
        "base_year": 2025,
        "market_unit": "USD Mn",
        "physical_unit": "Tonnes",
        "grounded": True,
    }
    draft = taxonomy.run_taxonomy_phase(approved_setup, _StubConfig(), root=tmp_path)

    assert draft["market_name"] == "Vinegar"
    assert len(draft["major_use_cases"]) == 1
    assert draft["major_use_cases"][0]["name"] == "Food Preservation"
    assert len(draft["actual_use_cases"]) == 1
    auc = draft["actual_use_cases"][0]
    assert auc["name"] == "Household Pickle Preservation"
    assert auc["major_use_case"] == "Food Preservation"
    assert "Household Pickle Preservation" in draft["drivers"]

    from me_engine.demand_usage_india import checkpoints
    on_disk = checkpoints.read_json(checkpoints.taxonomy_draft_path("Vinegar", root=tmp_path))
    assert on_disk == draft

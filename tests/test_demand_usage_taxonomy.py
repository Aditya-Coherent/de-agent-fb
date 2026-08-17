"""Phase 2 (Taxonomy + Demand Driver Draft) tests -- the SECOND user
approval gate.

`approve_taxonomy` is pure validation (no LLM/network); `run_taxonomy_phase`
is exercised with the real `agents.*` node functions but a stubbed LLM/no-web
config, matching the style already used for `calculate_node` in
`test_units_contract.py`.

Run with: pytest tests/test_demand_usage_taxonomy.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from me_engine.demand_usage import taxonomy


def _valid_draft(**overrides) -> dict:
    base = {
        "market_name": "Crude Oil",
        "usage_buckets": [
            {"name": "Transportation Fuels", "description": "Gasoline/diesel/jet fuel end use", "bucket_category": "physical_consumption"},
        ],
        "drivers": {
            "Transportation Fuels": {"driver_name": "Fuel consumption", "unit": "barrels per day", "final_demand_unit": "barrels per year"},
        },
        "excluded_usage_buckets": [],
        "missing_usage_flags": [],
        "coverage_pct": 82.0,
        "overlap_flags": [],
    }
    base.update(overrides)
    return base


def test_approve_taxonomy_writes_checkpoint_and_returns_normalized_dict(tmp_path):
    approved = taxonomy.approve_taxonomy(_valid_draft(), root=tmp_path)
    assert approved["approved"] is True
    assert approved["usage_buckets"][0]["name"] == "Transportation Fuels"
    assert taxonomy.load_approved_taxonomy("Crude Oil", root=tmp_path) == approved


def test_approve_taxonomy_rejects_empty_bucket_list(tmp_path):
    with pytest.raises(ValueError):
        taxonomy.approve_taxonomy(_valid_draft(usage_buckets=[]), root=tmp_path)


def test_approve_taxonomy_rejects_bucket_missing_required_field(tmp_path):
    bad = _valid_draft()
    bad["usage_buckets"][0].pop("bucket_category")
    with pytest.raises(ValueError):
        taxonomy.approve_taxonomy(bad, root=tmp_path)


def test_approve_taxonomy_rejects_bucket_with_no_driver(tmp_path):
    """Spec: 'Only approved usages and drivers enter the sizing workflow' --
    a bucket the user added without giving it a driver must be rejected, not
    silently skipped downstream."""
    bad = _valid_draft()
    bad["usage_buckets"].append({"name": "New Bucket With No Driver", "description": "x", "bucket_category": "physical_consumption"})
    with pytest.raises(ValueError):
        taxonomy.approve_taxonomy(bad, root=tmp_path)


def test_user_added_bucket_with_driver_is_accepted(tmp_path):
    draft = _valid_draft()
    draft["usage_buckets"].append({"name": "Petrochemical Feedstock", "description": "x", "bucket_category": "physical_consumption"})
    draft["drivers"]["Petrochemical Feedstock"] = {"driver_name": "Feedstock volume", "unit": "tonnes per year", "final_demand_unit": "tonnes per year"}
    approved = taxonomy.approve_taxonomy(draft, root=tmp_path)
    assert len(approved["usage_buckets"]) == 2
    assert "Petrochemical Feedstock" in approved["drivers"]


def test_load_approved_taxonomy_raises_when_never_approved(tmp_path):
    with pytest.raises(FileNotFoundError):
        taxonomy.load_approved_taxonomy("Never Approved Market", root=tmp_path)


# --------------------------------------------------------------------------- #
# run_taxonomy_phase -- runs the real agents.* nodes end to end (stubbed LLM,
# web disabled) from an approved Phase 1 setup, never re-deriving it.
# --------------------------------------------------------------------------- #
class _StubLLM:
    """Returns a plausible answer for whichever node calls it next --
    good enough to exercise the taxonomy orchestration path without a real
    LLM/network call."""

    def complete_json(self, system, user):
        if "usage/application buckets" in system or "usage-decomposition" in system.lower():
            return {"buckets": [{"name": "Transportation Fuels", "description": "Gasoline/diesel/jet fuel end use"}]}
        if "physical consumption" in system.lower() or "GENUINE PHYSICAL CONSUMPTION" in system:
            return {"buckets": [{"name": "Transportation Fuels", "is_physical_end_use": True,
                                  "classification": "physical_consumption", "reason": "fuel is consumed", "confidence": 0.9}]}
        if "coverage auditor" in system.lower():
            return {"missing": [], "estimated_coverage_pct": 80.0}
        if "demand-driver analyst" in system.lower():
            return {"driver_name": "Fuel consumption", "unit": "barrels per day", "requires_conversion": False,
                     "final_demand_unit": "barrels per day", "conversion_formula": "driver_value",
                     "required_evidence": ["EIA consumption statistics"], "rationale": "direct measure"}
        return {}


class _StubConfig:
    enable_web = False


def test_run_taxonomy_phase_bundles_usage_and_drivers_from_approved_setup(tmp_path, monkeypatch):
    import me_engine.demand_usage.taxonomy as taxonomy_mod

    monkeypatch.setattr(taxonomy_mod, "GenLLM", lambda config, cache_path=None: _StubLLM())

    approved_setup = {
        "market_name": "Crude Oil",
        "scope": "Crude oil as feedstock and fuel input.",
        "geography": ["United States", "China"],
        "base_year": 2025,
        "market_unit": "USD Mn",
        "physical_unit": "Barrels",
        "grounded": True,
    }
    draft = taxonomy.run_taxonomy_phase(approved_setup, _StubConfig(), root=tmp_path)

    assert draft["market_name"] == "Crude Oil"
    assert len(draft["usage_buckets"]) == 1
    assert draft["usage_buckets"][0]["bucket_category"] == "physical_consumption"
    assert "Transportation Fuels" in draft["drivers"]
    # Never re-derived scope/geography -- Phase 2 read them from approved setup.
    from me_engine.demand_usage import checkpoints
    on_disk = checkpoints.read_json(checkpoints.taxonomy_draft_path("Crude Oil", root=tmp_path))
    assert on_disk == draft

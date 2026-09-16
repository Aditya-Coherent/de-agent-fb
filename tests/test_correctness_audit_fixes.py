"""Regression tests for the correctness-audit fixes (2026-08-26), each tied
to a specific finding in `output/demand_usage/cement/CEMENT_FORENSIC_AUDIT.md`:

  1. Zero-intensity guard               (finding A-1 / C-3)
  2. Cross-bucket driver-reuse veto      (finding A-2)
  3. Double-conversion / unit reconciliation (finding A-3 / C-1 / C-2)
  4. Benchmark unit normalization        (finding F)
  6. Pre-write correctness gate

Run with: pytest tests/test_correctness_audit_fixes.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from me_engine.demand_usage import agents, correctness_gate, driver_library, evidence_router, units


# --------------------------------------------------------------------------- #
# 1. Zero-intensity guard
# --------------------------------------------------------------------------- #
class _StubConfig:
    enable_web = True
    evidence_workers = 2


class _StubLLM:
    def complete_json(self, system, user, **kwargs):
        return {"value": 5.0, "rationale": "stub assumption"}


def _quant_state(countries, progress=None):
    return {
        "config": _StubConfig(),
        "llm": _StubLLM(),
        "market_name": "Stub Market",
        "log": [],
        "usage_buckets": [{"name": "B", "description": "d", "bucket_category": "physical_consumption"}],
        "drivers": {"B": {
            "driver_name": "d", "unit": "housing units", "final_demand_unit": "housing units",
            "consumption_unit": "metric tons of cement per housing unit",
        }},
        "countries": list(countries),
        "progress": progress,
    }


def _driver_fetch_ok(*a, **kw):
    return ({"value": 1000.0, "unit": "housing units", "evidence_type": "verified_observed",
             "verification_status": "verified_observed", "confidence": 0.9, "source": "real source",
             "source_url": "https://example.org", "citation": "1000 housing units", "call_failed": False},
            [{"source_type": "web_page", "found": True}])


def test_zero_intensity_with_no_citation_is_rejected_falls_back_to_assumption(monkeypatch):
    monkeypatch.setattr(evidence_router, "propose_driver_evidence_plan",
                         lambda *a, **kw: {"unit": "housing units", "sources": []})
    monkeypatch.setattr(evidence_router, "propose_intensity_evidence_plan",
                         lambda *a, **kw: {"unit": "metric tons of cement per housing unit", "sources": []})
    monkeypatch.setattr(evidence_router, "execute_evidence_plan", _driver_fetch_ok)

    call_count = {"n": 0}
    real_execute = evidence_router.execute_evidence_plan

    def fetch_dispatch(llm, plan, geography, **kw):
        if geography == "global":  # intensity call
            return ({"value": 0.0, "unit": "metric tons of cement per housing unit",
                      "evidence_type": "source_found_unverified", "verification_status": "source_found_unverified",
                      "confidence": 0.3, "source": "GCCA", "source_url": "https://gccassociation.org/x",
                      "citation": "", "call_failed": False},
                     [{"source_type": "llm_claim", "found": True}])
        return real_execute(llm, plan, geography, **kw)

    monkeypatch.setattr(evidence_router, "execute_evidence_plan", fetch_dispatch)

    out = agents.demand_quantification_node(_quant_state(["US"]))
    assert out["zero_intensity_rejected_buckets"] == ["B"]
    # The real, verified_observed driver evidence (1000 housing units) must NOT be zeroed out.
    assert out["quantities"]["B"]["US"]["value"] != 0.0


def test_zero_intensity_with_strong_citation_is_accepted(monkeypatch):
    monkeypatch.setattr(evidence_router, "propose_driver_evidence_plan",
                         lambda *a, **kw: {"unit": "housing units", "sources": []})
    monkeypatch.setattr(evidence_router, "propose_intensity_evidence_plan",
                         lambda *a, **kw: {"unit": "metric tons of cement per housing unit", "sources": []})
    real_execute = evidence_router.execute_evidence_plan

    def fetch_dispatch(llm, plan, geography, **kw):
        if geography == "global":
            return ({"value": 0.0, "unit": "metric tons of cement per housing unit",
                      "evidence_type": "verified_observed", "verification_status": "verified_observed",
                      "confidence": 0.95, "source": "GCCA study",
                      "source_url": "https://gccassociation.org/study",
                      "citation": "intensity is documented as 0.0 tons per unit for this specific case",
                      "call_failed": False},
                     [{"source_type": "pdf", "found": True}])
        return _driver_fetch_ok(llm, plan, geography, **kw)

    monkeypatch.setattr(evidence_router, "execute_evidence_plan", fetch_dispatch)

    out = agents.demand_quantification_node(_quant_state(["US"]))
    assert out["zero_intensity_rejected_buckets"] == []  # genuinely evidenced zero -- not rejected


def test_zero_intensity_rejection_visible_in_progress(monkeypatch, tmp_path):
    from me_engine.demand_usage.progress import ProgressRecorder, progress_path
    import json

    monkeypatch.setattr(evidence_router, "propose_driver_evidence_plan",
                         lambda *a, **kw: {"unit": "housing units", "sources": []})
    monkeypatch.setattr(evidence_router, "propose_intensity_evidence_plan",
                         lambda *a, **kw: {"unit": "metric tons of cement per housing unit", "sources": []})
    real_execute = evidence_router.execute_evidence_plan

    def fetch_dispatch(llm, plan, geography, **kw):
        if geography == "global":
            return ({"value": 0.0, "unit": "u", "evidence_type": "assumption",
                      "verification_status": "assumption", "confidence": 0.1, "source": "", "citation": "",
                      "call_failed": False},
                     [{"source_type": "llm_claim", "found": True}])
        return _driver_fetch_ok(llm, plan, geography, **kw)

    monkeypatch.setattr(evidence_router, "execute_evidence_plan", fetch_dispatch)
    rec = ProgressRecorder("Stub Market", source="cli", root=tmp_path)
    agents.demand_quantification_node(_quant_state(["US"], progress=rec))
    # No crash / no attribute error touching the recorder with the new key -- progress file still valid.
    json.loads(progress_path("Stub Market", root=tmp_path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 2. Cross-bucket driver-reuse veto
# --------------------------------------------------------------------------- #
def test_scope_conflict_detects_residential_vs_infrastructure():
    assert driver_library._scope_conflict(
        "Cement used in large-scale public works like roads, bridges, dams, tunnels, airports, ports",
        "Cement used in building homes, apartments, and other residential structures",
    ) is True


def test_scope_conflict_false_for_same_scope():
    assert driver_library._scope_conflict(
        "Use of cement in building homes and apartments",
        "Cement used in building homes, apartments, and other residential structures",
    ) is False


def test_scope_conflict_false_when_neither_side_names_a_scope():
    assert driver_library._scope_conflict(
        "Global population needing this service", "Worldwide demand for this product",
    ) is False


def test_lookup_rejects_high_similarity_scope_mismatched_entry(tmp_path, monkeypatch):
    path = tmp_path / "lib.json"
    fake_vec = [1.0, 0.0, 0.0]

    def fake_embed(text):
        return fake_vec  # identical vector -> similarity 1.0, forces the match branch

    monkeypatch.setattr("me_engine.generation.semantic_search.embed", fake_embed)
    driver_library.append(
        usage_description="Cement used in building homes, apartments, and other residential structures",
        driver_name="Residential construction starts", driver_unit="housing units",
        consumption_unit="metric tons of cement per housing unit",
        conversion_formula="driver_value * intensity_value", required_evidence=[], market_name="Cement",
        path=path, final_demand_unit="",
    )
    log: list[str] = []
    result = driver_library.lookup(
        "Cement used in large-scale public works like roads, bridges, dams, tunnels", path=path, log=log,
    )
    assert result is None
    assert any("REJECTED reuse" in line for line in log)


def test_lookup_accepts_high_similarity_same_scope_entry(tmp_path, monkeypatch):
    path = tmp_path / "lib.json"
    fake_vec = [1.0, 0.0, 0.0]
    monkeypatch.setattr("me_engine.generation.semantic_search.embed", lambda text: fake_vec)
    driver_library.append(
        usage_description="Cement used in building homes, apartments, and other residential structures",
        driver_name="Residential construction starts", driver_unit="housing units",
        consumption_unit="metric tons of cement per housing unit",
        conversion_formula="driver_value * intensity_value", required_evidence=[], market_name="Cement",
        path=path, final_demand_unit="metric tons of cement",
    )
    result = driver_library.lookup("Use of cement in building homes and other residential dwellings", path=path)
    assert result is not None
    assert result["driver_name"] == "Residential construction starts"


# --------------------------------------------------------------------------- #
# 3. Double-conversion / final_demand_unit reconciliation
# --------------------------------------------------------------------------- #
def test_reconcile_final_demand_unit_corrects_the_china_style_case():
    corrected, was_corrected = units.reconcile_final_demand_unit(
        final_demand_unit="housing units", driver_unit="housing units",
        consumption_unit="metric tons of cement per housing unit",
    )
    assert was_corrected is True
    assert corrected == "metric tons of cement"


def test_reconcile_final_demand_unit_is_a_noop_when_already_correct():
    corrected, was_corrected = units.reconcile_final_demand_unit(
        final_demand_unit="metric tons of cement", driver_unit="housing units",
        consumption_unit="metric tons of cement per housing unit",
    )
    assert was_corrected is False
    assert corrected == "metric tons of cement"


def test_reconcile_final_demand_unit_is_a_noop_without_consumption_unit():
    corrected, was_corrected = units.reconcile_final_demand_unit(
        final_demand_unit="metric tons of precast concrete products",
        driver_unit="metric tons of precast concrete products", consumption_unit="",
    )
    assert was_corrected is False
    assert corrected == "metric tons of precast concrete products"


def test_intensity_target_unit_parses_rate_string():
    assert units.intensity_target_unit("metric tons of cement per housing unit") == "metric tons of cement"
    assert units.intensity_target_unit("") is None
    assert units.intensity_target_unit("fraction (0-1)") is None


def test_demand_driver_node_persists_final_demand_unit_to_library(tmp_path, monkeypatch):
    """Regression test for the exact bug: `driver_library.append` previously
    never persisted `final_demand_unit` at all, so EVERY reuse fell back to
    the raw driver unit. Confirms it's now saved.

    NOTE: `driver_library.append`/`lookup` default to a MODULE-LEVEL
    `path=_DEFAULT_PATH` argument bound at function-definition time --
    monkeypatching the `_DEFAULT_PATH` constant does NOT redirect a caller
    (like `agents.demand_driver_node`) that never passes `path=` explicitly
    (a real Python default-argument gotcha, not specific to this codebase).
    Wrapping the functions themselves is what actually isolates this test
    from the real `output/demand_usage/driver_library.json`."""
    import functools

    lib_path = tmp_path / "lib.json"
    monkeypatch.setattr(driver_library, "append", functools.partial(driver_library.append, path=lib_path))
    monkeypatch.setattr(driver_library, "lookup", functools.partial(driver_library.lookup, path=lib_path))

    class _NoLibConfig:
        enable_web = False

    class _StubLLM2:
        def complete_json(self, system, user, **kwargs):
            return {
                "driver_name": "X starts", "unit": "housing units", "requires_conversion": True,
                "intensity_kind": "rate_per_driver_unit",
                "consumption_unit": "metric tons of cement per housing unit",
                "final_demand_unit": "housing units",  # LLM returns the (buggy) raw unit
                "conversion_formula": "driver_value * intensity_value",
                "required_evidence": [], "rationale": "r",
            }

    state = {
        "config": _NoLibConfig(), "llm": _StubLLM2(), "market_name": "Stub Market", "log": [],
        "usage_buckets": [{"name": "B", "description": "brand new bucket never seen before xyz123"}],
        "countries": ["US"],
    }
    out = agents.demand_driver_node(state)
    assert out["drivers"]["B"]["final_demand_unit"] == "metric tons of cement"  # corrected, not "housing units"

    entries = driver_library._load(tmp_path / "lib.json")
    assert entries[-1]["final_demand_unit"] == "metric tons of cement"


# --------------------------------------------------------------------------- #
# 4. Benchmark unit normalization
# --------------------------------------------------------------------------- #
def test_normalize_monetary_value_reproduces_the_cement_bug_case():
    """The exact CEMENT_FORENSIC_AUDIT.md finding F case: value already
    expanded to raw dollars, unit still says 'USD Bn'."""
    value, unit = units.normalize_monetary_value(425_500_000_000.0, "USD Bn")
    assert unit == "USD Mn"
    assert value == pytest.approx(425_500.0, rel=1e-6)


def test_normalize_monetary_value_well_formed_billion():
    value, unit = units.normalize_monetary_value(425.50, "USD Bn")
    assert unit == "USD Mn"
    assert value == pytest.approx(425_500.0, rel=1e-6)


def test_normalize_monetary_value_million_is_unity():
    value, unit = units.normalize_monetary_value(1234.5, "USD Mn")
    assert value == pytest.approx(1234.5)
    assert unit == "USD Mn"


def test_normalize_monetary_value_trillion():
    value, unit = units.normalize_monetary_value(1.2, "USD Tn")
    assert value == pytest.approx(1_200_000.0)


def test_normalize_monetary_value_bare_usd_treated_as_raw_dollars():
    value, unit = units.normalize_monetary_value(5_000_000.0, "USD")
    assert value == pytest.approx(5.0)
    assert unit == "USD Mn"


def test_normalize_monetary_value_none_value_passthrough():
    value, unit = units.normalize_monetary_value(None, "USD Bn")
    assert value is None
    assert unit == "USD Mn"


def test_triangulation_node_normalizes_benchmark_before_gap_calculation(monkeypatch):
    from me_engine.demand_usage import triangulation, evidence as evidence_mod

    class _Cfg:
        enable_web = False

    class _LLM:
        pass

    def fake_extract(llm, snippets, unit, context):
        return {
            "value": 425_500_000_000.0, "unit": "USD Bn", "year": 2025,
            "source": "Cement Market Size Report", "citation": "USD 425.50 Billion in 2025",
            "evidence_type": "verified_observed", "confidence": 0.9,
        }

    monkeypatch.setattr(evidence_mod, "extract_structured_fact", fake_extract)
    state = {
        "config": _Cfg(), "llm": _LLM(), "market_name": "Cement", "log": [],
        "total_market_value": 111221.635, "market_unit": "USD Mn",
    }
    out = triangulation.triangulation_node(state)
    bench = out["triangulation"][0]
    assert bench["unit"] == "USD Mn"
    assert bench["value"] == pytest.approx(425_500.0, rel=1e-6)
    # gap_pct must now be a real, informative number -- not the -100.0 floor artifact.
    assert bench["gap_pct"] > -100.0
    assert bench["gap_pct"] == pytest.approx(-73.86, abs=0.5)


# --------------------------------------------------------------------------- #
# 6. Pre-write correctness gate
# --------------------------------------------------------------------------- #
def test_gate_hard_fails_when_validation_missing():
    hard, soft = correctness_gate.run_pre_write_gate({"usage_buckets": [{"name": "B"}], "total_market_value": 1.0})
    assert any("validation" in h for h in hard)


def test_gate_hard_fails_on_nan_total():
    state = {"usage_buckets": [{"name": "B"}], "total_market_value": float("nan"), "validation": {"x": 1}}
    hard, soft = correctness_gate.run_pre_write_gate(state)
    assert any("NaN" in h for h in hard)


def test_gate_passes_clean_state():
    state = {
        "usage_buckets": [{"name": "B"}], "total_market_value": 100.0, "validation": {"x": 1},
        "drivers": {}, "price_basis_records": {}, "triangulation": [], "scope_taxonomy_aggregate": {},
    }
    hard, soft = correctness_gate.run_pre_write_gate(state)
    assert hard == []
    assert soft == []


def test_gate_soft_flags_zero_intensity_rejection():
    state = {
        "usage_buckets": [{"name": "B"}], "total_market_value": 100.0, "validation": {"x": 1},
        "drivers": {}, "price_basis_records": {}, "triangulation": [], "scope_taxonomy_aggregate": {},
        "zero_intensity_rejected_buckets": ["B"],
    }
    hard, soft = correctness_gate.run_pre_write_gate(state)
    assert hard == []
    assert any(f["code"] == "gate_zero_intensity_rejected" for f in soft)


def test_gate_soft_flags_double_conversion_risk_reproducing_china_case():
    state = {
        "usage_buckets": [{"name": "Commercial and Institutional Construction"}],
        "total_market_value": 100.0, "validation": {"x": 1},
        "drivers": {
            "Commercial and Institutional Construction": {
                "unit": "housing units", "final_demand_unit": "housing units",  # NOT reconciled -- simulates a stale/uncovered case
                "consumption_unit": "metric tons of cement per housing unit",
            },
        },
        "price_basis_records": {
            "Commercial and Institutional Construction": {
                "same_as_final_unit": False, "conversion_factor_unit": "metric tons of cement per housing unit",
            },
        },
        "triangulation": [], "scope_taxonomy_aggregate": {},
    }
    hard, soft = correctness_gate.run_pre_write_gate(state)
    assert hard == []
    assert any(f["code"] == "gate_double_conversion_risk" for f in soft)


def test_gate_no_double_conversion_flag_once_unit_is_reconciled():
    """After `reconcile_final_demand_unit` has already corrected the label
    (the normal, fixed path), the gate must NOT flag a risk."""
    state = {
        "usage_buckets": [{"name": "B"}], "total_market_value": 100.0, "validation": {"x": 1},
        "drivers": {
            "B": {
                "unit": "housing units", "final_demand_unit": "metric tons of cement",  # corrected
                "consumption_unit": "metric tons of cement per housing unit",
            },
        },
        "price_basis_records": {"B": {"same_as_final_unit": True}},
        "triangulation": [], "scope_taxonomy_aggregate": {},
    }
    hard, soft = correctness_gate.run_pre_write_gate(state)
    assert soft == []

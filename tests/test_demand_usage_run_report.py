"""Tests for the consolidated `SIZE_RUN_RESULT.md` / `.partial.md` reporting
layer (reporting/observability addendum, 2026-08-26). Pure reporting -- these
tests assert the report reflects real state/progress data, not that any
number is computed correctly (that's each node's own test file's job).

Run with: pytest tests/test_demand_usage_run_report.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from me_engine.demandUseCaseCountry_approach2B import run_report
from me_engine.demandUseCaseCountry_approach2B.progress import ProgressRecorder


def _fake_progress(market: str, root: Path, *, source: str = "cli") -> None:
    """Simulates a real run's progress trail, including per-node timings,
    so `phase_durations_seconds` has real deltas to diff."""
    rec = ProgressRecorder(market, source=source, root=root)
    for node in ["demand_quantification", "pricing", "calculate", "driver_magnitude_validation",
                 "triangulation", "magnitude_cross_validation", "validation"]:
        time.sleep(0.01)
        rec.node_done(node)
    rec.finished("completed")


def _complete_state(market: str = "Stub Market") -> dict:
    return {
        "market_name": market,
        "usage_buckets": [{"name": "A"}, {"name": "B"}],
        "countries": ["US", "China", "India"],
        "total_market_value": 111221.6,
        "related_demand_value": 0.0,
        "retrieval_attempts": {"A": {"US": [{"found": True}], "China": [{"found": False}]}},
        "intensity_retrieval_attempts": {"A": {"global": [{"found": False}]}},
        "price_retrieval_attempts": {"A": {"global": [{"found": False}]}},
        "price_conversion_retrieval_attempts": {},
        "validation": {
            "residual_implied_usd_mn": 12358.0,
            "reported_market_view_usd_mn": 123579.6,
            "deterministic_score": 0.8,
            "plausibility_score": 0.5,
            "evidence_quality": {
                "observed_pct": 0.0, "derived_pct": 0.0,
                "source_found_unverified_pct": 1.1, "assumption_pct": 98.9, "total_inputs": 90,
            },
        },
        "quality_flags": [],
    }


def test_successful_run_creates_size_run_result_md(tmp_path):
    market = "Stub Market"
    _fake_progress(market, tmp_path)
    state = _complete_state(market)

    out = run_report.write_success_report(
        state, market, json_path=tmp_path / "02_sizing.json", xlsx_path=tmp_path / "02_sizing.xlsx", root=tmp_path,
    )
    assert out == run_report.report_path(market, root=tmp_path)
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "STATUS: SUCCESS" in text


def test_report_contains_final_validation_state(tmp_path):
    market = "Stub Market"
    _fake_progress(market, tmp_path)
    state = _complete_state(market)
    out = run_report.write_success_report(state, market, json_path=None, xlsx_path=None, root=tmp_path)
    text = out.read_text(encoding="utf-8")
    assert "$111,221.6M" in text  # Included Demand
    assert "$123,579.6M" in text  # Reported Market View
    assert "$12,358.0M" in text   # Residual
    assert "80.0%" in text        # deterministic score
    assert "50.0%" in text        # plausibility score


def test_report_contains_runtime_breakdown_with_all_nodes(tmp_path):
    market = "Stub Market"
    _fake_progress(market, tmp_path)
    state = _complete_state(market)
    out = run_report.write_success_report(state, market, json_path=None, xlsx_path=None, root=tmp_path)
    text = out.read_text(encoding="utf-8")
    assert "## Runtime breakdown" in text
    for node in ["demand_quantification", "pricing", "calculate", "driver_magnitude_validation",
                 "triangulation", "magnitude_cross_validation", "validation"]:
        assert node in text
    assert "**Total**" in text


def test_report_contains_evidence_quality_counts_and_percentages(tmp_path):
    market = "Stub Market"
    _fake_progress(market, tmp_path)
    state = _complete_state(market)
    out = run_report.write_success_report(state, market, json_path=None, xlsx_path=None, root=tmp_path)
    text = out.read_text(encoding="utf-8")
    assert "verified_observed: 0.0%" in text
    assert "derived: 0.0%" in text
    assert "source_found_unverified: 1.1%" in text
    assert "assumption: 98.9%" in text
    assert "total inputs: 90" in text
    # Evidence attempts derived from retrieval_attempts (1 driver found, 1 driver miss, etc.)
    assert "Driver: 2 attempted, 1 found" in text


def test_report_contains_output_integrity_information(tmp_path):
    market = "Stub Market"
    _fake_progress(market, tmp_path)
    state = _complete_state(market)
    json_path = tmp_path / "02_sizing.json"
    json_path.write_text("{}", encoding="utf-8")
    out = run_report.write_success_report(state, market, json_path=json_path, xlsx_path=None, root=tmp_path)
    text = out.read_text(encoding="utf-8")
    assert "## Output integrity" in text
    assert str(json_path) in text
    assert "bytes" in text
    assert ".size.lock` released: yes" in text
    assert "`validation` present and populated: yes" in text


def test_write_success_report_refuses_incomplete_state(tmp_path):
    market = "Stub Market"
    _fake_progress(market, tmp_path)
    state = _complete_state(market)
    state["validation"] = None  # simulates the exact bug this session already found once
    with pytest.raises(ValueError, match="validation"):
        run_report.write_success_report(state, market, json_path=None, xlsx_path=None, root=tmp_path)


def test_failed_run_does_not_overwrite_previous_successful_report(tmp_path):
    market = "Stub Market"
    _fake_progress(market, tmp_path)
    state = _complete_state(market)
    success_path = run_report.write_success_report(state, market, json_path=None, xlsx_path=None, root=tmp_path)
    original_text = success_path.read_text(encoding="utf-8")

    # A second, LATER attempt fails partway through.
    rec2 = ProgressRecorder(market, source="cli", root=tmp_path)
    rec2.node_done("demand_quantification")
    rec2.failed(RuntimeError("simulated crash"))

    partial = run_report.write_failure_report(market, root=tmp_path, error="simulated crash")
    assert partial == run_report.partial_report_path(market, root=tmp_path)
    assert partial.exists()
    assert "STATUS: INCOMPLETE / FAILED" in partial.read_text(encoding="utf-8")

    # The ORIGINAL success report must be byte-identical -- never touched.
    assert success_path.read_text(encoding="utf-8") == original_text


def test_successful_rerun_clears_a_stale_partial_report(tmp_path):
    """If a PRIOR attempt failed (leaving a .partial.md) and a later rerun of
    the SAME market succeeds, the stale partial must not linger next to the
    new success report."""
    market = "Stub Market"
    rec = ProgressRecorder(market, source="cli", root=tmp_path)
    rec.node_done("demand_quantification")
    rec.failed(RuntimeError("first attempt crashed"))
    partial = run_report.write_failure_report(market, root=tmp_path, error="first attempt crashed")
    assert partial.exists()

    _fake_progress(market, tmp_path)  # simulates the rerun succeeding
    state = _complete_state(market)
    run_report.write_success_report(state, market, json_path=None, xlsx_path=None, root=tmp_path)

    assert not partial.exists()
    assert run_report.report_path(market, root=tmp_path).exists()


def test_write_failure_report_returns_none_when_no_progress_file_exists(tmp_path):
    assert run_report.write_failure_report("Never Started Market", root=tmp_path) is None


def test_format_terminal_summary_contains_key_figures(tmp_path):
    market = "Stub Market"
    _fake_progress(market, tmp_path)
    state = _complete_state(market)
    summary = run_report.build_success_summary(state, market, json_path="j.json", xlsx_path="x.xlsx", root=tmp_path)
    text = run_report.format_terminal_summary(summary, "report.md")
    assert "SIZE RUN COMPLETE" in text
    assert market in text
    assert "Included Demand" in text
    assert "Evidence Quality" in text
    assert "Evidence Attempts" in text

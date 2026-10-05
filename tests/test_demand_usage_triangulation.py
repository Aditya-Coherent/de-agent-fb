"""External benchmark triangulation tests (spec section 11).

`evidence.extract_structured_fact` is monkeypatched (no real LLM/network
call). The core guarantee under test: triangulation is INFORMATIONAL ONLY --
it must never change `total_market_value` and must correctly flag a large
gap without "fixing" it.

Run with: pytest tests/test_demand_usage_triangulation.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from me_engine.demandUseCaseCountry_approach2B import triangulation


class _StubConfig:
    enable_web = False


def _base_state(total_market_value: float) -> dict:
    return {
        "market_name": "Crude Oil",
        "config": _StubConfig(),
        "llm": object(),
        "log": [],
        "total_market_value": total_market_value,
        "market_unit": "USD Mn",
    }


def test_no_benchmarks_found_is_not_an_error(monkeypatch):
    monkeypatch.setattr(triangulation.evidence, "extract_structured_fact", lambda *a, **k: None)
    state = _base_state(1_000_000.0)
    out = triangulation.triangulation_node(state)
    assert out["triangulation"] == []
    assert out["triangulation_large_gap_flags"] == []
    assert state["total_market_value"] == 1_000_000.0  # untouched


def test_never_mutates_total_market_value(monkeypatch):
    monkeypatch.setattr(
        triangulation.evidence, "extract_structured_fact",
        lambda *a, **k: {"value": 50_000.0, "unit": "USD Mn", "source": "Published Report", "citation": "..."},
    )
    state = _base_state(1_000_000.0)  # wildly different from the "benchmark" -- must not be pulled toward it
    triangulation.triangulation_node(state)
    assert state["total_market_value"] == 1_000_000.0


def test_large_gap_vs_published_estimate_is_flagged(monkeypatch):
    monkeypatch.setattr(
        triangulation.evidence, "extract_structured_fact",
        lambda *a, **k: {"value": 500_000.0, "unit": "USD Mn", "source": "Published Report", "citation": "...", "evidence_type": "verified_observed"},
    )
    state = _base_state(1_000_000.0)  # 100% gap vs. 500,000 benchmark
    out = triangulation.triangulation_node(state)
    published = [b for b in out["triangulation"] if b["category"] == "published market-size estimate"][0]
    assert published["gap_pct"] == 100.0
    assert out["triangulation_large_gap_flags"]  # flagged, not silently absorbed


def test_small_gap_is_not_flagged(monkeypatch):
    monkeypatch.setattr(
        triangulation.evidence, "extract_structured_fact",
        lambda *a, **k: {"value": 980_000.0, "unit": "USD Mn", "source": "Published Report", "citation": "...", "evidence_type": "verified_observed"},
    )
    state = _base_state(1_000_000.0)  # ~2% gap
    out = triangulation.triangulation_node(state)
    assert out["triangulation_large_gap_flags"] == []


def test_every_benchmark_category_is_attempted(monkeypatch):
    calls = []

    def _track(llm, snippets, *, unit, context):
        calls.append(context)
        return {"value": 1.0, "unit": unit, "source": "src", "citation": "c", "evidence_type": "assumption"}

    monkeypatch.setattr(triangulation.evidence, "extract_structured_fact", _track)
    triangulation.triangulation_node(_base_state(1000.0))
    assert len(calls) == len(triangulation._BENCHMARK_CATEGORIES)

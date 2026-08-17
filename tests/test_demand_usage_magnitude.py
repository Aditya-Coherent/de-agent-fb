"""Driver-magnitude validation tests (spec section 16) -- generic,
market-independent sanity check. `evidence.extract_structured_fact` is
monkeypatched (no real LLM/network call) so the test controls exactly what
the "independent reference" evidence says.

Run with: pytest tests/test_demand_usage_magnitude.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from me_engine.demand_usage import magnitude


class _StubConfig:
    enable_web = False  # gather() never called; extract_structured_fact is monkeypatched anyway


def _base_state(used_value: float, driver_unit: str = "barrels per day") -> dict:
    return {
        "market_name": "Crude Oil",
        "config": _StubConfig(),
        "llm": object(),  # never called directly -- extract_structured_fact is monkeypatched
        "log": [],
        "usage_buckets": [{"name": "Transportation Fuels", "description": "x", "bucket_category": "physical_consumption"}],
        "drivers": {"Transportation Fuels": {"driver_name": "Fuel consumption", "unit": driver_unit}},
        "quantities": {"Transportation Fuels": {"US": {"value": used_value, "unit": driver_unit}}},
    }


def test_no_flag_when_no_independent_reference_available(monkeypatch):
    monkeypatch.setattr(magnitude.evidence, "extract_structured_fact", lambda *a, **k: None)
    out = magnitude.driver_magnitude_validation_node(_base_state(1000.0))
    assert out["driver_magnitude_flags"] == []


def test_no_flag_when_reference_is_close_to_used_value(monkeypatch):
    monkeypatch.setattr(
        magnitude.evidence, "extract_structured_fact",
        lambda *a, **k: {"value": 1050.0, "unit": "barrels per day", "source": "EIA", "citation": "..."},
    )
    out = magnitude.driver_magnitude_validation_node(_base_state(1000.0))
    assert out["driver_magnitude_flags"] == []


def test_flags_when_reference_diverges_3x_or_more(monkeypatch):
    monkeypatch.setattr(
        magnitude.evidence, "extract_structured_fact",
        lambda *a, **k: {"value": 4000.0, "unit": "barrels per day", "source": "EIA", "citation": "..."},
    )
    used_value = 1000.0
    out = magnitude.driver_magnitude_validation_node(_base_state(used_value))
    flags = out["driver_magnitude_flags"]
    assert len(flags) == 1
    flag = flags[0]
    assert flag["bucket"] == "Transportation Fuels"
    assert flag["code"] == "driver_magnitude_implausible"
    assert flag["ratio"] == 4.0
    # The original used value must remain fully visible and UNCHANGED.
    assert flag["used_value"] == used_value


def test_never_mutates_the_original_quantity(monkeypatch):
    """Flag-only: quantities in state must be byte-for-byte unchanged."""
    monkeypatch.setattr(
        magnitude.evidence, "extract_structured_fact",
        lambda *a, **k: {"value": 9999.0, "unit": "barrels per day", "source": "EIA", "citation": "..."},
    )
    state = _base_state(1000.0)
    original_quantities = {k: dict(v) for k, v in state["quantities"].items()}
    magnitude.driver_magnitude_validation_node(state)
    assert state["quantities"] == original_quantities


def test_no_flag_when_bucket_has_zero_or_no_quantity(monkeypatch):
    called = {"n": 0}

    def _tracked(*a, **k):
        called["n"] += 1
        return {"value": 9999.0, "unit": "barrels per day", "source": "EIA", "citation": "..."}

    monkeypatch.setattr(magnitude.evidence, "extract_structured_fact", _tracked)
    state = _base_state(0.0)
    out = magnitude.driver_magnitude_validation_node(state)
    assert out["driver_magnitude_flags"] == []
    assert called["n"] == 0  # never searches for a bucket with nothing built yet

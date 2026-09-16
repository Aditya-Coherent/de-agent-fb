"""Price-basis resolution tests (spec section 8/12) -- never search for a
nonsensical price like "USD per vehicle-kilometer"; resolve the REAL
sellable commodity unit first, and treat a bridging conversion factor as
optional, evidence-backed, never silently assumed equal to 1.

Run with: pytest tests/test_demand_usage_price_basis.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from me_engine.demand_usage import agents


class _StubLLM:
    def __init__(self, response: dict):
        self.response = response

    def complete_json(self, system, user):
        return self.response


def test_resolve_price_basis_same_as_final_unit_by_default():
    llm = _StubLLM({"price_basis_unit": "tonne", "same_as_final_unit": True, "conversion_factor_name": "", "conversion_factor_unit": "", "rationale": "r"})
    basis = agents._resolve_price_basis(llm, "Cement", "Concrete production", "desc", "tonnes of cement")
    assert basis["same_as_final_unit"] is True
    assert basis["conversion_factor_unit"] == ""


def test_resolve_price_basis_flags_a_conversion_when_units_differ():
    llm = _StubLLM({
        "price_basis_unit": "barrel", "same_as_final_unit": False,
        "conversion_factor_name": "Fuel per vehicle-km", "conversion_factor_unit": "barrels of fuel per vehicle-km",
        "rationale": "vehicle-km is not itself priced -- the fuel consumed is",
    })
    basis = agents._resolve_price_basis(llm, "Crude Oil", "Transportation Fuels", "desc", "vehicle-kilometers")
    assert basis["price_basis_unit"] == "barrel"
    assert basis["same_as_final_unit"] is False
    assert basis["conversion_factor_unit"] == "barrels of fuel per vehicle-km"


def test_resolve_price_basis_forces_same_as_final_when_no_conversion_unit_given():
    """Even if the LLM says same_as_final_unit=False, without an actual
    conversion_factor_unit there is nothing to bridge with -- fall back to
    treating the price basis as the final_demand_unit rather than leaving
    the pipeline in an inconsistent state."""
    llm = _StubLLM({"price_basis_unit": "barrel", "same_as_final_unit": False, "conversion_factor_name": "", "conversion_factor_unit": "", "rationale": "r"})
    basis = agents._resolve_price_basis(llm, "M", "B", "desc", "tonnes")
    assert basis["same_as_final_unit"] is True


def test_resolve_price_basis_survives_llm_call_failure():
    class _ThrowingLLM:
        def complete_json(self, system, user):
            raise RuntimeError("network down")

    basis = agents._resolve_price_basis(_ThrowingLLM(), "M", "B", "desc", "tonnes")
    assert basis["price_basis_unit"] == "tonnes"  # falls back to final_unit, never crashes
    assert basis["same_as_final_unit"] is True


# --------------------------------------------------------------------------- #
# Phase 8 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) -- pricing_node integration:
# Price Evidence Strategy consumption, verified/unverified/derived/assumption
# handling, circular-price derivation rejection, no fallback to a fabricated
# price.
# --------------------------------------------------------------------------- #
class _WebConfig:
    enable_web = True


class _NoWebConfig:
    enable_web = False


class _PricingLLM:
    """Routes `_resolve_price_basis`'s call to `basis`, every other call
    (the assumption-fallback prompts) to `fallback` -- distinguished by the
    system prompt text, exactly like the pipeline's own real prompts."""

    def __init__(self, basis, fallback=None):
        self.basis = basis
        self.fallback = fallback or {"value": 0.0, "rationale": "no evidence found"}

    def complete_json(self, system, user):
        if "commodity-pricing analyst" in system:
            return self.basis
        return self.fallback


def _pricing_state(llm, cfg, *, final_unit="tonnes of cement", bucket_name="Cement Production"):
    bucket = {"name": bucket_name, "description": "Concrete production usage of cement"}
    drivers = {bucket_name: {"driver_name": "Cement output", "final_demand_unit": final_unit, "unit": final_unit}}
    return {
        "config": cfg, "llm": llm, "market_name": "Cement", "log": [],
        "usage_buckets": [bucket], "drivers": drivers,
    }


def _fake_execute_returning(fact):
    def _execute(llm_, plan, geo, *, context, keywords=None, year=None, **_kwargs):
        return fact, [{"source_type": "web_page", "source_name": "stub", "found": fact is not None}]
    return _execute


def _capture_propose(sink):
    def _propose(llm_, market, name, desc, priced_unit, quantity_name, strategy=None, **_kwargs):
        sink["strategy"] = strategy
        return {
            "bucket": name, "quantity_name": quantity_name, "unit": f"USD per {priced_unit}",
            "kind": "average selling price (ASP)", "sources": [],
        }
    return _propose


def test_pricing_node_verified_observed_price_and_evidence_strategy_integration(monkeypatch):
    basis = {"price_basis_unit": "tonne", "same_as_final_unit": True,
              "conversion_factor_name": "", "conversion_factor_unit": "", "rationale": "cement is sold by the tonne"}
    llm = _PricingLLM(basis)
    fact = {
        "value": 120.0, "unit": "USD per tonne", "year": 2025, "source": "GCCA",
        "source_url": "https://gcca.example/report", "citation": "cement sold at $120 per tonne",
        "evidence_text": "cement sold at $120 per tonne", "is_direct_figure": True,
        "derivation_basis": "", "evidence_type": "verified_observed", "confidence": 0.9,
        "verification_status": "verified_observed",
    }
    captured = {}
    monkeypatch.setattr(agents.evidence_router, "propose_pricing_evidence_plan", _capture_propose(captured))
    monkeypatch.setattr(agents.evidence_router, "execute_evidence_plan", _fake_execute_returning(fact))

    out = agents.pricing_node(_pricing_state(llm, _WebConfig()))
    price = out["prices"]["Cement Production"]
    assert price["evidence_type"] == "verified_observed"
    assert price["asp_usd_per_unit"] == 120.0

    norm = price["price_normalization"]
    assert norm["normalization_status"] == "ok"
    assert norm["normalized_price"] == 120.0
    # priced_unit stays final_demand_unit when same_as_final_unit is True
    # (existing pricing_node behavior, unchanged by Phase 8).
    assert norm["normalized_unit"] == "USD per tonnes of cement"
    assert norm["source"] == "GCCA"
    assert norm["verification_status"] == "verified_observed"

    strategy_entry = out["price_evidence_strategies"]["Cement Production"]
    assert strategy_entry["strategy"]["strategy_source"] == "deterministic_price_basis"
    assert strategy_entry["flags"] == []
    # The strategy actually reached the Evidence Router call, not just built and discarded.
    assert captured["strategy"]["physical_unit"] == "USD per tonne"


def test_pricing_node_source_found_unverified_price(monkeypatch):
    basis = {"price_basis_unit": "tonne", "same_as_final_unit": True,
              "conversion_factor_name": "", "conversion_factor_unit": "", "rationale": "r"}
    llm = _PricingLLM(basis)
    fact = {
        "value": 95.0, "unit": "USD per tonne", "year": 2024, "source": "Industry newsletter",
        "source_url": "", "citation": "roughly $95/tonne", "evidence_text": "",
        "derivation_basis": "recalled from a prior report", "evidence_type": "source_found_unverified",
        "confidence": 0.3, "verification_status": "source_found_unverified",
    }
    monkeypatch.setattr(agents.evidence_router, "propose_pricing_evidence_plan", _capture_propose({}))
    monkeypatch.setattr(agents.evidence_router, "execute_evidence_plan", _fake_execute_returning(fact))

    out = agents.pricing_node(_pricing_state(llm, _WebConfig()))
    price = out["prices"]["Cement Production"]
    assert price["evidence_type"] == "source_found_unverified"
    assert price["grounded"] is False
    assert price["price_normalization"]["verification_status"] == "source_found_unverified"


def test_pricing_node_genuine_derived_price_is_accepted(monkeypatch):
    basis = {"price_basis_unit": "tonne", "same_as_final_unit": True,
              "conversion_factor_name": "", "conversion_factor_unit": "", "rationale": "r"}
    llm = _PricingLLM(basis)
    fact = {
        "value": 110.0, "unit": "USD per tonne", "year": 2025, "source": "LME",
        "source_url": "https://lme.example/kg-price", "citation": "$0.11/kg",
        "evidence_text": "$0.11 per kg", "is_direct_figure": False,
        "derivation_basis": "Derived from a verified per-kg LME price x 1000 (kg-to-tonne conversion)",
        "evidence_type": "derived", "confidence": 0.6, "verification_status": "derived",
    }
    monkeypatch.setattr(agents.evidence_router, "propose_pricing_evidence_plan", _capture_propose({}))
    monkeypatch.setattr(agents.evidence_router, "execute_evidence_plan", _fake_execute_returning(fact))

    out = agents.pricing_node(_pricing_state(llm, _WebConfig()))
    price = out["prices"]["Cement Production"]
    assert price["evidence_type"] == "derived"  # never rejected -- not circular


def test_pricing_node_rejects_circular_market_size_derivation(monkeypatch):
    """The plan's explicit anti-pattern: a 'derived' price that is actually
    just market size divided by an assumed quantity. Must be rejected and
    fall through to the ordinary assumption fallback, never accepted as
    'derived'."""
    basis = {"price_basis_unit": "tonne", "same_as_final_unit": True,
              "conversion_factor_name": "", "conversion_factor_unit": "", "rationale": "r"}
    fallback = {"value": 88.0, "rationale": "best-informed assumption"}
    llm = _PricingLLM(basis, fallback=fallback)
    circular_fact = {
        "value": 999.0, "unit": "USD per tonne", "year": 2025, "source": "Guess",
        "source_url": "", "citation": "", "evidence_text": "",
        "derivation_basis": "Implied price = total market size divided by the assumed shipped quantity",
        "evidence_type": "derived", "confidence": 0.6, "verification_status": "derived",
    }
    monkeypatch.setattr(agents.evidence_router, "propose_pricing_evidence_plan", _capture_propose({}))
    monkeypatch.setattr(agents.evidence_router, "execute_evidence_plan", _fake_execute_returning(circular_fact))

    out = agents.pricing_node(_pricing_state(llm, _WebConfig()))
    price = out["prices"]["Cement Production"]
    assert price["evidence_type"] == "assumption"
    assert price["asp_usd_per_unit"] == 88.0  # the fallback value, never the rejected circular one


def test_pricing_node_logs_the_circular_rejection(monkeypatch):
    basis = {"price_basis_unit": "tonne", "same_as_final_unit": True,
              "conversion_factor_name": "", "conversion_factor_unit": "", "rationale": "r"}
    llm = _PricingLLM(basis, fallback={"value": 50.0, "rationale": "assumption"})
    circular_fact = {
        "value": 999.0, "unit": "USD per tonne", "year": 2025, "source": "Guess", "source_url": "",
        "citation": "", "evidence_text": "", "derivation_basis": "backed out of the market value",
        "evidence_type": "derived", "confidence": 0.6, "verification_status": "derived",
    }
    monkeypatch.setattr(agents.evidence_router, "propose_pricing_evidence_plan", _capture_propose({}))
    monkeypatch.setattr(agents.evidence_router, "execute_evidence_plan", _fake_execute_returning(circular_fact))
    state = _pricing_state(llm, _WebConfig())
    agents.pricing_node(state)
    # pricing_node mutates state["log"] in place (state.setdefault("log", [])) rather than
    # returning it -- assert against the input state, not the returned dict.
    assert any("circularly derived" in line for line in state["log"])


def test_pricing_node_no_fallback_to_fabricated_price_when_no_evidence(monkeypatch):
    """With web evidence disabled entirely, the ASP must land as a plain,
    honestly-labeled assumption -- never presented as observed/derived just
    because a number was produced."""
    basis = {"price_basis_unit": "tonne", "same_as_final_unit": True,
              "conversion_factor_name": "", "conversion_factor_unit": "", "rationale": "r"}
    llm = _PricingLLM(basis, fallback={"value": 42.0, "rationale": "best-informed guess"})
    out = agents.pricing_node(_pricing_state(llm, _NoWebConfig()))
    price = out["prices"]["Cement Production"]
    assert price["evidence_type"] == "assumption"
    assert price["asp_usd_per_unit"] == 42.0
    assert price["call_failed"] is False


def test_pricing_node_flags_period_mismatch_between_strategy_and_final_unit(monkeypatch):
    basis = {"price_basis_unit": "tonne", "same_as_final_unit": True,
              "conversion_factor_name": "", "conversion_factor_unit": "", "rationale": "r"}
    llm = _PricingLLM(basis)
    monkeypatch.setattr(agents.evidence_router, "propose_pricing_evidence_plan", _capture_propose({}))
    monkeypatch.setattr(agents.evidence_router, "execute_evidence_plan", _fake_execute_returning(None))

    out = agents.pricing_node(_pricing_state(llm, _WebConfig(), final_unit="tonnes per day"))
    flags = out["price_evidence_strategies"]["Cement Production"]["flags"]
    codes = {f["code"] for f in flags}
    assert "price_period_mismatch" in codes  # strategy period defaults to 'year', final_unit says 'day'


def test_pricing_node_flags_invalid_price_basis_unit(monkeypatch):
    """A currency-tainted/unparseable price_basis_unit (an LLM misfire) is
    flagged, not silently trusted."""
    basis = {"price_basis_unit": "USD", "same_as_final_unit": True,
              "conversion_factor_name": "", "conversion_factor_unit": "", "rationale": "r"}
    llm = _PricingLLM(basis)
    monkeypatch.setattr(agents.evidence_router, "propose_pricing_evidence_plan", _capture_propose({}))
    monkeypatch.setattr(agents.evidence_router, "execute_evidence_plan", _fake_execute_returning(None))

    out = agents.pricing_node(_pricing_state(llm, _WebConfig()))
    flags = out["price_basis_records"]["Cement Production"]["flags"]
    assert flags[0]["code"] == "missing_or_unparseable_price_unit"


def test_build_report_dict_surfaces_price_evidence_strategy_and_normalization():
    from me_engine.demand_usage import buildup_writer

    state = {
        "usage_buckets": [{"name": "Cement Production", "description": "d"}],
        "countries": [], "drivers": {"Cement Production": {"final_demand_unit": "tonnes of cement"}},
        "quantities": {}, "usage_values": {}, "driver_facts": {}, "unit_contract": {},
        "prices": {
            "Cement Production": {
                "asp_usd_per_unit": 120.0, "evidence_type": "verified_observed",
                "price_normalization": {
                    "raw_price": 120.0, "raw_unit": "USD per tonne", "normalized_price": 120.0,
                    "normalized_unit": "USD per tonne", "normalization_status": "ok",
                    "source": "GCCA", "source_url": "u", "year": 2025, "geography": "global",
                    "verification_status": "verified_observed", "conversion_formula": "",
                },
            },
        },
        "price_facts": {},
        "price_evidence_strategies": {
            "Cement Production": {
                "strategy": {"metric_required": "Spot/benchmark market price of tonne", "strategy_source": "deterministic_price_basis"},
                "flags": [],
            },
        },
    }
    report = buildup_writer.build_report_dict(state)
    entry = report["price_evidence_strategies"]["Cement Production"]
    assert entry["strategy"]["strategy_source"] == "deterministic_price_basis"
    assert report["prices"]["Cement Production"]["price_normalization"]["normalized_price"] == 120.0

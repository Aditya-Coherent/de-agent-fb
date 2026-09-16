"""Phase 7 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) -- explicit, auditable per-driver
Evidence Strategy.

Covers: a valid strategy, each individual validation flag (missing metric,
invalid unit, ambiguous period, invalid driver/usage relationship, an
unsupported derivation conversion, a geography scope contradiction),
editable strategy persistence through `taxonomy.approve_taxonomy`, the
strategy flowing into `evidence_router`'s retrieval plan, derivation-path
preservation into the audit trail, and backward compatibility with
strategy-less drivers.

Run with: pytest tests/test_demand_usage_evidence_strategy.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from me_engine.demand_usage import agents, evidence_router, evidence_strategy, taxonomy


def _valid_strategy(**overrides) -> dict:
    base = {
        "driver_name": "Road fuel consumption",
        "metric_required": "Annual physical fuel consumption",
        "physical_unit": "barrels per year",
        "period": "year",
        "geography": ["United States", "China"],
        "primary_source_types": ["government_statistics", "international_organization"],
        "preferred_sources": ["EIA", "IEA"],
        "search_query_templates": ["{country} road fuel consumption barrels per year"],
        "fallback_source_types": ["industry_association"],
        "fallback_queries": ["global road fuel consumption total"],
        "derivation_path": "population_share: country_value = regional_value * population_share(country)",
        "expected_evidence": "A national statistics bulletin stating annual road fuel consumption in barrels.",
        "why_this_driver_measures_the_usage": "Road fuel consumed is the direct physical measure of transportation fuel demand.",
        "strategy_source": "llm_proposed",
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# validate_evidence_strategy -- one test per required validation check.
# --------------------------------------------------------------------------- #
def test_valid_evidence_strategy_has_no_flags(monkeypatch):
    monkeypatch.setattr(evidence_strategy, "embed", lambda text: [1.0, 0.0])
    flags = evidence_strategy.validate_evidence_strategy(
        _valid_strategy(), bucket_name="Transportation Fuels",
        bucket_description="Gasoline/diesel/jet fuel end use",
        approved_countries=["United States", "China"],
    )
    assert flags == []


def test_missing_metric_is_flagged():
    strategy = _valid_strategy(metric_required="")
    flags = evidence_strategy.validate_evidence_strategy(
        strategy, bucket_name="Transportation Fuels", bucket_description="Gasoline/diesel/jet fuel end use",
    )
    assert any(f["code"] == "no_measurable_metric" for f in flags)


def test_invalid_unit_is_flagged():
    strategy = _valid_strategy(physical_unit="USD per barrel")  # currency -- unparseable as a physical unit
    flags = evidence_strategy.validate_evidence_strategy(
        strategy, bucket_name="Transportation Fuels", bucket_description="Gasoline/diesel/jet fuel end use",
    )
    assert any(f["code"] == "missing_or_unparseable_unit" for f in flags)


def test_empty_unit_is_flagged():
    strategy = _valid_strategy(physical_unit="")
    flags = evidence_strategy.validate_evidence_strategy(
        strategy, bucket_name="Transportation Fuels", bucket_description="Gasoline/diesel/jet fuel end use",
    )
    assert any(f["code"] == "missing_or_unparseable_unit" for f in flags)


def test_ambiguous_period_is_flagged():
    strategy = _valid_strategy(period="sometimes annually, sometimes not")
    flags = evidence_strategy.validate_evidence_strategy(
        strategy, bucket_name="Transportation Fuels", bucket_description="Gasoline/diesel/jet fuel end use",
    )
    assert any(f["code"] == "ambiguous_period" for f in flags)


def test_recognized_period_synonyms_are_not_flagged(monkeypatch):
    monkeypatch.setattr(evidence_strategy, "embed", lambda text: [1.0, 0.0])
    for period in ("year", "annual", "annually", "month", "quarter", "day", ""):
        strategy = _valid_strategy(period=period)
        flags = evidence_strategy.validate_evidence_strategy(
            strategy, bucket_name="Transportation Fuels", bucket_description="Gasoline/diesel/jet fuel end use",
        )
        assert not any(f["code"] == "ambiguous_period" for f in flags), period


def test_invalid_driver_usage_relationship_is_flagged(monkeypatch):
    """A driver whose metric is semantically unrelated to the usage bucket it's
    supposed to measure must be flagged, not silently accepted."""
    monkeypatch.setattr(evidence_strategy, "embed", lambda text: [1.0, 0.0] if "unrelated" not in text.lower() else [0.0, 1.0])
    strategy = _valid_strategy(
        metric_required="unrelated widget count", why_this_driver_measures_the_usage="unrelated widget reasoning",
    )
    flags = evidence_strategy.validate_evidence_strategy(
        strategy, bucket_name="Transportation Fuels", bucket_description="Gasoline/diesel/jet fuel end use",
    )
    assert any(f["code"] == "driver_usage_relationship_unclear" for f in flags)


def test_clearly_relevant_driver_is_not_flagged_for_relationship(monkeypatch):
    monkeypatch.setattr(evidence_strategy, "embed", lambda text: [1.0, 0.0])
    flags = evidence_strategy.validate_evidence_strategy(
        _valid_strategy(), bucket_name="Transportation Fuels", bucket_description="Gasoline/diesel/jet fuel end use",
    )
    assert not any(f["code"] == "driver_usage_relationship_unclear" for f in flags)


def test_derivation_path_requiring_unsupported_conversion_is_flagged():
    strategy = _valid_strategy(
        physical_unit="barrels per year",
        derivation_path="convert reported tonnes of fuel to barrels using an assumed density factor",
    )
    flags = evidence_strategy.validate_evidence_strategy(
        strategy, bucket_name="Transportation Fuels", bucket_description="Gasoline/diesel/jet fuel end use",
    )
    assert any(f["code"] == "derivation_requires_unsupported_conversion" for f in flags)


def test_derivation_path_using_a_supported_shape_is_not_flagged():
    strategy = _valid_strategy(derivation_path="population_share: country_value = regional_value * share")
    flags = evidence_strategy.validate_evidence_strategy(
        strategy, bucket_name="Transportation Fuels", bucket_description="Gasoline/diesel/jet fuel end use",
    )
    assert not any(f["code"] == "derivation_requires_unsupported_conversion" for f in flags)


def test_scope_contradiction_is_flagged():
    strategy = _valid_strategy(geography=["United States", "Mars"])
    flags = evidence_strategy.validate_evidence_strategy(
        strategy, bucket_name="Transportation Fuels", bucket_description="Gasoline/diesel/jet fuel end use",
        approved_countries=["United States", "China"],
    )
    assert any(f["code"] == "scope_contradiction" for f in flags)


def test_global_geography_is_never_a_scope_contradiction():
    strategy = _valid_strategy(geography=["global"])
    flags = evidence_strategy.validate_evidence_strategy(
        strategy, bucket_name="Transportation Fuels", bucket_description="Gasoline/diesel/jet fuel end use",
        approved_countries=["United States", "China"],
    )
    assert not any(f["code"] == "scope_contradiction" for f in flags)


def test_missing_strategy_produces_no_flags():
    """Absence is its own valid state -- never treated as an error."""
    assert evidence_strategy.validate_evidence_strategy(None, bucket_name="X", bucket_description="y") == []
    empty = evidence_strategy.empty_strategy("Fuel consumption", "barrels per year", ["United States"])
    assert evidence_strategy.validate_evidence_strategy(empty, bucket_name="X", bucket_description="y") == []


# --------------------------------------------------------------------------- #
# demand_driver_node -- proposes a well-formed strategy in the SAME LLM call.
# --------------------------------------------------------------------------- #
class _DriverLLM:
    def complete_json(self, system, user):
        assert "EVIDENCE STRATEGY" in system  # Phase 7 fragment actually reached the LLM
        return {
            "driver_name": "Road fuel consumption", "unit": "barrels per day", "requires_conversion": False,
            "final_demand_unit": "barrels per year", "conversion_formula": "driver_value",
            "required_evidence": ["EIA consumption statistics"], "rationale": "direct measure",
            "evidence_strategy": {
                "metric_required": "Annual physical fuel consumption", "physical_unit": "barrels per year",
                "period": "year", "geography": ["United States"],
                "primary_source_types": ["government_statistics"], "preferred_sources": ["EIA"],
                "search_query_templates": ["{country} road fuel consumption barrels per year"],
                "fallback_source_types": ["industry_association"], "fallback_queries": ["global fuel total"],
                "derivation_path": "population_share: country_value = regional_value * share",
                "expected_evidence": "A national statistics bulletin.",
                "why_this_driver_measures_the_usage": "Fuel consumed is the direct measure of this usage.",
            },
        }


class _NoWebConfig:
    enable_web = False


def _state_for_driver_node(countries=("United States",)):
    return {
        "config": _NoWebConfig(), "llm": _DriverLLM(), "market_name": "Crude Oil", "log": [],
        "countries": list(countries),
        "usage_buckets": [{"name": "Transportation Fuels", "description": "Gasoline/diesel/jet fuel end use"}],
    }


def test_demand_driver_node_produces_well_formed_evidence_strategy(monkeypatch):
    monkeypatch.setattr(agents.driver_library, "lookup", lambda desc, path=None, log=None: None)
    monkeypatch.setattr(agents.driver_library, "append", lambda **kw: None)
    # Deterministic, no real model load -- the relationship check itself is
    # covered on its own above; here we only care that the strategy shape
    # round-trips correctly through the node.
    monkeypatch.setattr(evidence_strategy, "embed", lambda text: [1.0, 0.0])

    out = agents.demand_driver_node(_state_for_driver_node())
    driver = out["drivers"]["Transportation Fuels"]
    strategy = driver["evidence_strategy"]
    for field in evidence_strategy.STRATEGY_FIELDS:
        assert field in strategy
    assert strategy["metric_required"] == "Annual physical fuel consumption"
    assert strategy["physical_unit"] == "barrels per year"
    assert strategy["strategy_source"] == "llm_proposed"
    assert driver["evidence_strategy_flags"] == []


def test_demand_driver_node_reused_library_driver_gets_deterministic_strategy(monkeypatch):
    lib_entry = {
        "driver_name": "Fuel consumption", "driver_unit": "barrels per year", "final_demand_unit": "barrels per year",
        "consumption_unit": "", "conversion_formula": "driver_value", "required_evidence": ["EIA statistics"],
        "intensity_kind": "rate_per_driver_unit", "unit_contract_status": "ok", "match_similarity": 0.91,
        "markets_seen": ["Diesel"],
    }
    monkeypatch.setattr(agents.driver_library, "lookup", lambda desc, path=None, log=None: lib_entry)
    monkeypatch.setattr(evidence_strategy, "embed", lambda text: [1.0, 0.0])

    out = agents.demand_driver_node(_state_for_driver_node())
    driver = out["drivers"]["Transportation Fuels"]
    assert driver["library_status"] == "reused"
    strategy = driver["evidence_strategy"]
    assert strategy["strategy_source"] == "reused_library_default"
    assert strategy["physical_unit"] == "barrels per year"
    assert driver["evidence_strategy_flags"] == []  # metric_required defaults to driver_name -- never blank


def test_demand_driver_node_missing_strategy_in_llm_response_is_non_blocking(monkeypatch):
    """Backward compatibility: an LLM that never returns `evidence_strategy`
    at all (predates Phase 7's prompt fragment) must not crash the node --
    the driver just carries a strategy that reads as largely empty."""
    monkeypatch.setattr(agents.driver_library, "lookup", lambda desc, path=None, log=None: None)
    monkeypatch.setattr(agents.driver_library, "append", lambda **kw: None)

    class _OldLLM:
        def complete_json(self, system, user):
            return {
                "driver_name": "Fuel consumption", "unit": "barrels per day", "requires_conversion": False,
                "final_demand_unit": "barrels per year", "conversion_formula": "driver_value",
                "required_evidence": [], "rationale": "direct measure",
            }

    state = _state_for_driver_node()
    state["llm"] = _OldLLM()
    out = agents.demand_driver_node(state)
    driver = out["drivers"]["Transportation Fuels"]
    assert driver["evidence_strategy"]["strategy_source"] == "llm_proposed"
    assert driver["evidence_strategy"]["metric_required"] == ""
    # No metric named -> flagged for review, but the run itself never crashes/blocks.
    assert any(f["code"] == "no_measurable_metric" for f in driver["evidence_strategy_flags"])


# --------------------------------------------------------------------------- #
# taxonomy.approve_taxonomy -- editable strategy persistence + backward compat.
# --------------------------------------------------------------------------- #
def _draft_with_strategy(**strategy_overrides) -> dict:
    return {
        "market_name": "Crude Oil",
        "usage_buckets": [
            {"name": "Transportation Fuels", "description": "Gasoline/diesel/jet fuel end use", "bucket_category": "physical_consumption"},
        ],
        "drivers": {
            "Transportation Fuels": {
                "driver_name": "Fuel consumption", "unit": "barrels per day", "final_demand_unit": "barrels per year",
                "evidence_strategy": _valid_strategy(**strategy_overrides),
            },
        },
        "excluded_usage_buckets": [], "missing_usage_flags": [], "coverage_pct": 82.0, "overlap_flags": [],
    }


def test_approve_taxonomy_persists_user_edited_strategy(tmp_path):
    draft = _draft_with_strategy(metric_required="User-edited metric name")
    approved = taxonomy.approve_taxonomy(draft, root=tmp_path)
    strategy = approved["drivers"]["Transportation Fuels"]["evidence_strategy"]
    assert strategy["metric_required"] == "User-edited metric name"
    reloaded = taxonomy.load_approved_taxonomy("Crude Oil", root=tmp_path)
    assert reloaded["drivers"]["Transportation Fuels"]["evidence_strategy"]["metric_required"] == "User-edited metric name"


def test_approve_taxonomy_recomputes_flags_after_user_edit(tmp_path):
    """A user editing the strategy's metric to blank must be caught by a
    fresh validation pass at approval time, not silently accepted."""
    draft = _draft_with_strategy(metric_required="")
    approved = taxonomy.approve_taxonomy(draft, root=tmp_path)
    flags = approved["drivers"]["Transportation Fuels"]["evidence_strategy_flags"]
    assert any(f["code"] == "no_measurable_metric" for f in flags)


def test_approve_taxonomy_backward_compatible_with_no_strategy(tmp_path):
    """A pre-Phase-7 taxonomy draft (no evidence_strategy field at all) must
    still approve successfully -- enrichment, never a hard requirement."""
    draft = {
        "market_name": "Crude Oil",
        "usage_buckets": [
            {"name": "Transportation Fuels", "description": "x", "bucket_category": "physical_consumption"},
        ],
        "drivers": {
            "Transportation Fuels": {"driver_name": "Fuel consumption", "unit": "barrels per day", "final_demand_unit": "barrels per year"},
        },
        "excluded_usage_buckets": [], "missing_usage_flags": [], "coverage_pct": 82.0, "overlap_flags": [],
    }
    approved = taxonomy.approve_taxonomy(draft, root=tmp_path)
    assert "evidence_strategy" not in approved["drivers"]["Transportation Fuels"]
    assert approved["approved"] is True


# --------------------------------------------------------------------------- #
# evidence_router -- the approved strategy flows into evidence retrieval.
# --------------------------------------------------------------------------- #
class _RouterLLM:
    def complete_json(self, system, user):
        assert "approved Evidence Strategy already exists" in system
        return {"sources": [{
            "source_name": "Generic Source", "source_type": "web_page", "locator": "",
            "search_query_template": "{country} llm proposed query", "rationale": "x",
        }]}


def test_strategy_flows_into_evidence_router_plan_as_priority_sources():
    driver = {
        "driver_name": "Road fuel consumption", "unit": "barrels per year",
        "evidence_strategy": _valid_strategy(),
    }
    plan = evidence_router.propose_driver_evidence_plan(_RouterLLM(), "Crude Oil", "Transportation Fuels", "desc", driver)
    source_names = [s["source_name"] for s in plan["sources"]]
    # The strategy's own preferred sources are tried FIRST, ahead of the LLM's.
    assert source_names[:2] == ["EIA", "IEA"]
    assert plan["sources"][0]["origin"] == "evidence_strategy"
    assert "{country}" in plan["sources"][0]["search_query_template"]
    assert "Generic Source" in source_names  # LLM-proposed source still present, just lower priority


def test_router_plan_without_strategy_is_unaffected():
    class _PlainLLM:
        def complete_json(self, system, user):
            assert "approved Evidence Strategy already exists" not in system
            return {"sources": [{
                "source_name": "EIA", "source_type": "web_page", "locator": "",
                "search_query_template": "{country} fuel", "rationale": "x",
            }]}

    driver = {"driver_name": "Road fuel consumption", "unit": "barrels per year"}
    plan = evidence_router.propose_driver_evidence_plan(_PlainLLM(), "Crude Oil", "Transportation Fuels", "desc", driver)
    assert plan["sources"][0]["source_name"] == "EIA"
    assert "origin" not in plan["sources"][0]


def test_strategy_template_missing_country_placeholder_is_skipped():
    driver = {
        "driver_name": "Road fuel consumption", "unit": "barrels per year",
        "evidence_strategy": _valid_strategy(preferred_sources=["EIA"], search_query_templates=["no placeholder here"]),
    }
    sources = evidence_router._strategy_sources(driver["evidence_strategy"], "barrels per year")
    assert sources == []


# --------------------------------------------------------------------------- #
# derivation_path preservation into the audit trail.
# --------------------------------------------------------------------------- #
class _QuantificationLLM:
    """Every extraction call returns nothing found -- forces the
    regional/global -> population-share fallback path deterministically."""

    def complete_json(self, system, user):
        return {}


def test_derivation_path_from_strategy_is_preserved_in_audit_trail(monkeypatch):
    from me_engine.demand_usage import reference_data

    monkeypatch.setattr(reference_data, "population_share", lambda country, countries: 0.5)
    monkeypatch.setattr(
        agents.evidence, "extract_structured_fact",
        lambda llm, snippets, unit, context, **_kwargs: {
            "value": 1000.0, "unit": unit, "evidence_type": "verified_observed",
            "verification_status": "verified_observed", "confidence": 0.9, "source": "IEA",
            "citation": "1000", "evidence_text": "1000", "year": 2025,
        },
    )

    state = {
        "config": _NoWebConfig(), "llm": _QuantificationLLM(), "market_name": "Crude Oil", "log": [],
        "countries": ["United States", "China"],
        "usage_buckets": [{"name": "Transportation Fuels", "description": "Gasoline/diesel/jet fuel end use"}],
        "drivers": {
            "Transportation Fuels": {
                "driver_name": "Road fuel consumption", "unit": "barrels per year",
                "final_demand_unit": "barrels per year", "consumption_unit": "",
                "evidence_strategy": _valid_strategy(derivation_path="observed daily consumption x 365"),
            },
        },
    }
    out = agents.demand_quantification_node(state)
    basis = out["driver_facts"]["Transportation Fuels"]["United States"]["driver_raw"].get("derivation_basis", "")
    assert "observed daily consumption x 365" in basis
    assert "population_share" in basis  # the actual ad-hoc formula is still shown, never silently replaced


# --------------------------------------------------------------------------- #
# buildup_writer -- surfaces the strategy when present.
# --------------------------------------------------------------------------- #
def test_build_report_dict_surfaces_evidence_strategy():
    from me_engine.demand_usage import buildup_writer

    state = {
        "market_name": "Crude Oil", "usage_buckets": [{"name": "Transportation Fuels", "description": "x"}],
        "countries": ["United States"],
        "drivers": {
            "Transportation Fuels": {
                "driver_name": "Road fuel consumption", "unit": "barrels per year",
                "evidence_strategy": _valid_strategy(), "evidence_strategy_flags": [],
            },
        },
        "quantities": {}, "prices": {}, "usage_values": {}, "driver_facts": {}, "price_facts": {},
        "unit_contract": {},
    }
    report = buildup_writer.build_report_dict(state)
    entry = report["evidence_strategies"]["Transportation Fuels"]
    assert entry["strategy"]["metric_required"] == "Annual physical fuel consumption"
    assert entry["flags"] == []


def test_build_report_dict_reports_missing_strategy_explicitly():
    from me_engine.demand_usage import buildup_writer

    state = {
        "market_name": "Crude Oil", "usage_buckets": [{"name": "Transportation Fuels", "description": "x"}],
        "countries": ["United States"],
        "drivers": {"Transportation Fuels": {"driver_name": "Road fuel consumption", "unit": "barrels per year"}},
        "quantities": {}, "prices": {}, "usage_values": {}, "driver_facts": {}, "price_facts": {},
        "unit_contract": {},
    }
    report = buildup_writer.build_report_dict(state)
    assert report["evidence_strategies"]["Transportation Fuels"]["strategy"] is None

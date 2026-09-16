"""Tests for bounded-parallel driver-evidence acquisition (runtime
optimization, 2026-08-26) -- `demand_quantification_node`'s per-country
`execute_evidence_plan` fetch, the dominant cost of a sizing run (~85% of
total evidence attempts), is now bounded-parallel-fetched instead of
sequential. No evidence-selection/retry/acceptance logic changed -- only
WHEN independent per-country fetches execute.

Run with: pytest tests/test_demand_usage_evidence_parallel.py
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from me_engine.demand_usage import agents, evidence_router
from me_engine.demand_usage.progress import ProgressRecorder, progress_path


class _StubConfig:
    enable_web = True
    evidence_workers = 8


class _StubLLM:
    def complete_json(self, system, user, **kwargs):
        return {"value": 1.0, "rationale": "stub"}


def _base_state(countries, config=None, progress=None):
    return {
        "config": config or _StubConfig(),
        "llm": _StubLLM(),
        "market_name": "Stub Market",
        "log": [],
        "usage_buckets": [{"name": "B", "description": "d", "bucket_category": "physical_consumption"}],
        "drivers": {"B": {"driver_name": "Widgets consumed", "unit": "units", "final_demand_unit": "units"}},
        "countries": list(countries),
        "progress": progress,
    }


def _patch_plan_proposal(monkeypatch):
    monkeypatch.setattr(
        evidence_router, "propose_driver_evidence_plan",
        lambda llm, market, name, desc, driver, emitter=None: {"unit": "units", "sources": [{"source_name": "S", "source_type": "web_page"}]},
    )
    monkeypatch.setattr(
        evidence_router, "propose_intensity_evidence_plan",
        lambda llm, market, name, desc, driver, emitter=None: {"unit": "units", "sources": []},
    )


def test_independent_country_fetches_actually_overlap(monkeypatch):
    """8 countries, each stub fetch sleeping 0.2s, 8 workers -- sequential
    would take >= 1.6s; bounded-parallel must take well under that."""
    _patch_plan_proposal(monkeypatch)
    countries = [f"Country{i}" for i in range(8)]

    def slow_fetch(llm, plan, geography, *, context, keywords=None, year=None, emitter=None, bucket=None, **kw):
        time.sleep(0.2)
        return ({"value": 1.0, "unit": "units", "evidence_type": "assumption",
                  "verification_status": "assumption", "confidence": 0.2, "source": "s",
                  "call_failed": False}, [{"source_type": "web_page", "found": True}])

    monkeypatch.setattr(evidence_router, "execute_evidence_plan", slow_fetch)

    start = time.time()
    out = agents.demand_quantification_node(_base_state(countries))
    elapsed = time.time() - start

    assert "quantities" in out
    assert elapsed < 1.0, f"expected overlap (<1.0s for 8x0.2s fetches on 8 workers), got {elapsed:.2f}s"


def test_deterministic_country_ordering_survives_out_of_order_completion(monkeypatch):
    """Countries finish in a DIFFERENT order than submitted (later-submitted
    ones sleep less) -- final `quantities`/`retrieval_attempts` insertion
    order must still match the original `countries` list, every time."""
    _patch_plan_proposal(monkeypatch)
    countries = ["Zeta", "Alpha", "Mu", "Beta"]
    # Reverse-ish sleep durations so completion order != submission order.
    sleeps = {"Zeta": 0.15, "Alpha": 0.10, "Mu": 0.05, "Beta": 0.01}

    def fetch(llm, plan, geography, *, context, keywords=None, year=None, emitter=None, bucket=None, **kw):
        time.sleep(sleeps[geography])
        return ({"value": 1.0, "unit": "units", "evidence_type": "assumption",
                  "verification_status": "assumption", "confidence": 0.2, "source": "s",
                  "call_failed": False}, [{"source_type": "web_page", "found": True}])

    monkeypatch.setattr(evidence_router, "execute_evidence_plan", fetch)

    for _ in range(3):  # repeat -- ordering must be stable across runs, not lucky once
        out = agents.demand_quantification_node(_base_state(countries))
        assert list(out["quantities"]["B"].keys()) == countries
        assert list(out["retrieval_attempts"]["B"].keys()) == countries


def test_one_worker_failure_does_not_crash_others(monkeypatch):
    """One country's fetch raises; the other three must still complete with
    real results, and the failed one gets a structured extraction_failed
    attempt rather than crashing the whole node."""
    _patch_plan_proposal(monkeypatch)
    countries = ["A", "B_ok", "C_raises", "D_ok"]

    def fetch(llm, plan, geography, *, context, keywords=None, year=None, emitter=None, bucket=None, **kw):
        if geography == "C_raises":
            raise RuntimeError("simulated network failure")
        return ({"value": 1.0, "unit": "units", "evidence_type": "assumption",
                  "verification_status": "assumption", "confidence": 0.2, "source": "s",
                  "call_failed": False}, [{"source_type": "web_page", "found": True}])

    monkeypatch.setattr(evidence_router, "execute_evidence_plan", fetch)

    out = agents.demand_quantification_node(_base_state(countries))
    assert set(out["quantities"]["B"].keys()) == set(countries)  # all 4 present, none dropped
    failed_attempts = out["retrieval_attempts"]["B"]["C_raises"]
    assert failed_attempts and failed_attempts[0]["rejection_reason"] == "extraction_failed"
    assert "simulated network failure" in failed_attempts[0].get("error", "")


def test_progress_reflects_correct_cumulative_attempts_no_double_counting(monkeypatch, tmp_path):
    """Multiple parallel tasks completing must sum to the EXACT total attempt
    count, whether recorded live (parallel phase) or at cell-assembly time --
    never double-counted, never dropped."""
    _patch_plan_proposal(monkeypatch)
    countries = [f"C{i}" for i in range(6)]

    def fetch(llm, plan, geography, *, context, keywords=None, year=None, emitter=None, bucket=None, **kw):
        return (None, [{"source_type": "web_page", "found": False, "rejection_reason": "no_source_found"}] * 3)

    monkeypatch.setattr(evidence_router, "execute_evidence_plan", fetch)

    rec = ProgressRecorder("Stub Market", source="cli", root=tmp_path)
    agents.demand_quantification_node(_base_state(countries, progress=rec))

    data = json.loads(progress_path("Stub Market", root=tmp_path).read_text(encoding="utf-8"))
    assert data["evidence_attempts"]["driver"] == 6 * 3  # 6 countries x 3 attempts each, no double count
    assert data["completed_cells"] == 6


def test_progress_file_never_corrupted_under_many_completions(monkeypatch, tmp_path):
    _patch_plan_proposal(monkeypatch)
    countries = [f"C{i}" for i in range(20)]

    def fetch(llm, plan, geography, *, context, keywords=None, year=None, emitter=None, bucket=None, **kw):
        return ({"value": 1.0, "unit": "units", "evidence_type": "assumption",
                  "verification_status": "assumption", "confidence": 0.2, "source": "s",
                  "call_failed": False}, [{"source_type": "web_page", "found": True}])

    monkeypatch.setattr(evidence_router, "execute_evidence_plan", fetch)

    rec = ProgressRecorder("Stub Market", source="cli", root=tmp_path)
    agents.demand_quantification_node(_base_state(countries, progress=rec))

    # Must parse cleanly -- any interleaved/partial write would raise here.
    data = json.loads(progress_path("Stub Market", root=tmp_path).read_text(encoding="utf-8"))
    assert data["completed_cells"] == 20


def test_gen_llm_cache_safe_under_concurrent_reads_and_writes(tmp_path):
    """Direct test of the SQLite cache hardening (WAL + busy_timeout) --
    many threads writing/reading DIFFERENT keys to the SAME cache file
    concurrently must never raise 'database is locked' and every value must
    read back correctly."""
    from me_engine.generation.config import GenConfig
    from me_engine.generation.llm import GenLLM

    config = GenConfig(
        api_key="stub", base_url="https://example.invalid", model="m", timeout=1.0,
        neon_url=None, enable_web=False, max_loop_iters=1, min_loop_iters=1,
        reward_threshold=0.9, per_region_shares=False,
    )
    llm = GenLLM(config, cache_path=tmp_path / "cache.db")

    errors: list[Exception] = []

    def worker(i: int) -> None:
        try:
            key = f"key-{i}"
            llm._cache_put(key, json.dumps({"i": i}))
            got = llm._cache_get(key)
            assert got == json.dumps({"i": i})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent cache access raised: {errors}"


def test_evidence_workers_respects_config_and_defaults_when_absent(monkeypatch):
    """A stub config with no `evidence_workers` attribute at all must not
    crash -- `getattr(cfg, "evidence_workers", 8)` covers it."""
    _patch_plan_proposal(monkeypatch)

    class _NoWorkersConfig:
        enable_web = True  # deliberately no `evidence_workers` attribute

    def fetch(llm, plan, geography, *, context, keywords=None, year=None, emitter=None, bucket=None, **kw):
        return ({"value": 1.0, "unit": "units", "evidence_type": "assumption",
                  "verification_status": "assumption", "confidence": 0.2, "source": "s",
                  "call_failed": False}, [{"source_type": "web_page", "found": True}])

    monkeypatch.setattr(evidence_router, "execute_evidence_plan", fetch)
    out = agents.demand_quantification_node(_base_state(["X", "Y"], config=_NoWorkersConfig()))
    assert set(out["quantities"]["B"].keys()) == {"X", "Y"}

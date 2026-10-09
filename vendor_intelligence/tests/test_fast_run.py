"""Tests for the fast-run pieces: time budget, coverage loop, majors, CSV size columns."""
from __future__ import annotations

import asyncio
import csv

import pytest

from vendor_intel.pipeline import major_players as mp
from vendor_intel.pipeline.time_budget import TimeBudget


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


# ---------------------------------------------------------------- time budget


def test_time_budget_thresholds():
    clk = FakeClock()
    b = TimeBudget(600, stop_fraction=0.75, discovery_fraction=0.45, clock=clk)
    assert b.limited
    assert not b.discovery_should_stop() and not b.wrap_up()
    clk.t = 0.46 * 600
    assert b.discovery_should_stop()
    assert not b.wrap_up()
    clk.t = 0.76 * 600
    assert b.wrap_up()
    assert b.remaining() == pytest.approx(0.24 * 600)


def test_time_budget_unlimited_never_stops():
    clk = FakeClock()
    b = TimeBudget(None, clock=clk)
    clk.t = 10_000
    assert not b.limited
    assert not b.discovery_should_stop()
    assert not b.wrap_up()
    assert b.remaining() == float("inf")


def test_discovery_fraction_never_exceeds_stop_fraction():
    b = TimeBudget(100, stop_fraction=0.5, discovery_fraction=0.9, clock=FakeClock())
    assert b.discovery_fraction == 0.5


def test_discovery_loop_stops_on_budget(monkeypatch):
    """_discover_live must stop issuing searches once the budget says discovery is over."""
    from vendor_intel.pipeline import time_budget as tb
    from vendor_intel.stages import b_discovery

    clk = FakeClock()
    monkeypatch.setattr(tb, "_current", TimeBudget(100, discovery_fraction=0.45, clock=clk))
    calls: list[str] = []

    async def fake_batch(router, batch, **kw):
        calls.extend(p["text"] for p in batch)
        clk.t += 30  # each batch burns 30% of the budget
        return [0.5] * len(batch)

    async def fake_one(router, p, **kw):
        calls.append(p["text"])
        clk.t += 30
        return 0.5

    monkeypatch.setattr(b_discovery, "_run_prompt_batch", fake_batch)
    monkeypatch.setattr(b_discovery, "_run_prompt", fake_one)
    monkeypatch.setattr(b_discovery, "seed_discovery_hits", lambda scope: [])
    prompts = [{"id": f"P{i}", "level": "discovery", "text": f"olive oil query {i}"} for i in range(40)]
    asyncio.run(
        b_discovery._discover_live(
            object(), prompts, scope={"market": "olive oil"}, fallback_query="olive oil",
            settings=None, allow_mutations=False,
        )
    )
    # 30% per batch, stop at 45% -> only 2 batches can run
    batch = b_discovery._discovery_parallel_batch_size()
    assert len(calls) <= 2 * batch
    assert len(calls) < len(prompts)


# ---------------------------------------------------------------- yield stop


def test_yield_window_larger_than_buffer_still_stops():
    from vendor_intel.discovery.discovery_query_engine import QueryYieldTracker

    t = QueryYieldTracker()
    for _ in range(8):
        t.recent_yields.append(0.01)
    # LOW_YIELD_CONSECUTIVE=10 > deque maxlen 8 used to make this unreachable
    assert t.should_stop_on_yield(500, min_unique=100, threshold=0.05, window=10,
                                  min_prompts_run=1, prompts_run=20)


# ---------------------------------------------------------------- coverage loop


def _run(coro):
    return asyncio.run(coro)


def test_coverage_loop_reaches_target():
    cov = {"v": 40.0}

    async def expand(i):
        cov["v"] += 20.0
        return 3

    res = _run(mp.run_coverage_loop(lambda: cov["v"], expand, target_pct=75, budget=None, max_rounds=5))
    assert res["stop_reason"] == "target_reached"
    assert res["coverage_pct"] >= 75
    assert res["rounds"] == 2


def test_coverage_loop_stops_when_nothing_new():
    async def expand(i):
        return 0

    res = _run(mp.run_coverage_loop(lambda: 30.0, expand, target_pct=75, budget=None, max_rounds=5))
    assert res["stop_reason"] == "no_new_players"
    assert res["rounds"] == 1


def test_coverage_loop_respects_time_budget():
    clk = FakeClock()
    b = TimeBudget(100, clock=clk)
    n = {"calls": 0}

    async def expand(i):
        n["calls"] += 1
        clk.t += 40  # each round burns 40% of the budget
        return 2

    res = _run(mp.run_coverage_loop(lambda: 10.0, expand, target_pct=75, budget=b,
                                    max_rounds=10, min_remaining_sec=0))
    assert res["stop_reason"] == "time_budget"
    assert n["calls"] == 2  # at 80% (>= 75% wrap-up) no further round starts


def test_coverage_loop_max_rounds():
    async def expand(i):
        return 1

    res = _run(mp.run_coverage_loop(lambda: 10.0, expand, target_pct=75, budget=None, max_rounds=3))
    assert res["stop_reason"] == "max_rounds"
    assert res["rounds"] == 3


def test_coverage_loop_no_rounds_when_already_covered():
    async def expand(i):  # pragma: no cover - must not be called
        raise AssertionError("should not expand")

    res = _run(mp.run_coverage_loop(lambda: 80.0, expand, target_pct=75, budget=None))
    assert res["rounds"] == 0 and res["stop_reason"] == "target_reached"


def test_estimate_coverage_counts_only_manufacturers_and_caps_100():
    grouped = [
        ("Manufacturers of Olive Oil", [
            {"company": "A", "est_market_share_pct": 60, "is_relevant": True},
            {"company": "B", "est_market_share_pct": 50, "is_relevant": True},
        ]),
        ("Distributors of Olive Oil", [{"company": "C", "est_market_share_pct": 30, "is_relevant": True}]),
        ("Other Component Manufacturers", [{"company": "D", "est_market_share_pct": 30}]),
    ]
    assert mp.estimate_coverage(grouped) == 100.0
    grouped[0] = ("Manufacturers of Olive Oil", [{"company": "A", "est_market_share_pct": 12.5}])
    assert mp.estimate_coverage(grouped) == 12.5


# ---------------------------------------------------------------- majors


def test_normalize_majors_fills_and_scales():
    raw = {
        "market_size_usd_mn": 10000,
        "companies": [
            {"name": "Deoleo SA", "domain": "https://www.deoleo.com/", "est_revenue_usd_mn": 1000},
            {"name": "Sovena", "est_market_share_pct": "5%"},
            {"name": "Unknown Co"},
            {"name": "Deoleo S.A."},  # duplicate after name normalisation
        ],
    }
    out = mp.normalize_majors(raw)
    names = [c["name"] for c in out["companies"]]
    assert names == ["Deoleo SA", "Sovena", "Unknown Co"]  # sorted by revenue, unknown last
    deo = out["companies"][0]
    assert deo["domain"] == "deoleo.com"
    assert deo["est_market_share_pct"] == 10.0
    assert out["companies"][1]["est_revenue_usd_mn"] == 500.0
    over = mp.normalize_majors({"companies": [
        {"name": "X", "est_market_share_pct": 80}, {"name": "Y", "est_market_share_pct": 80}]})
    assert sum(c["est_market_share_pct"] for c in over["companies"]) == pytest.approx(100.0, abs=0.05)


def test_match_major_by_domain_and_name():
    majors = [{"name": "Deoleo", "domain": "deoleo.com"}, {"name": "Sovena Group", "domain": ""}]
    assert mp.match_major({"company": "X", "domain": "www.deoleo.com"}, majors)["name"] == "Deoleo"
    assert mp.match_major({"company": "Sovena Portugal", "domain": "sovena.pt"}, majors)["name"] == "Sovena Group"
    assert mp.match_major({"company": "Other", "domain": "other.com"}, majors) is None


def test_cap_never_drops_majors():
    from vendor_intel.pipeline.orchestrator import cap_candidates

    cleaned = [{"name": f"c{i}", "domain": f"c{i}.com"} for i in range(300)]
    cleaned += [{"name": f"m{i}", "domain": f"m{i}.com", "is_major": True} for i in range(25)]
    out = cap_candidates(cleaned, 150)
    assert len(out) == 150
    assert all(f"m{i}.com" in {c["domain"] for c in out} for i in range(25))
    # majors are classified right after seeds (i.e. first here)
    assert all(c.get("is_major") for c in out[:25])
    # even a cap smaller than the number of majors keeps every major
    tiny = cap_candidates(cleaned, 10)
    assert sum(1 for c in tiny if c.get("is_major")) == 25


def test_cap_trims_tail_when_budget_late_but_keeps_majors():
    from vendor_intel.pipeline.orchestrator import cap_candidates

    clk = FakeClock()
    b = TimeBudget(100, clock=clk)
    clk.t = 80
    cleaned = [{"name": f"c{i}", "domain": f"c{i}.com"} for i in range(300)]
    cleaned += [{"name": f"m{i}", "domain": f"m{i}.com", "is_major": True} for i in range(5)]
    out = cap_candidates(cleaned, 150, b)
    assert sum(1 for c in out if c.get("is_major")) == 5
    assert len(out) == 5 + 60


# ---------------------------------------------------------------- CSV output


def test_csv_column_order_and_revenue_sort(tmp_path):
    from vendor_intel.pipeline.orchestrator import save_pipeline_csv

    rows = [
        {"company": "Small Mill", "domain": "smallmill.com", "role": "Manufacturer", "is_relevant": True,
         "_forced_section": "Manufacturers of Olive Oil", "company_summary": "Makes olive oil."},
        {"company": "Big Oil Co", "domain": "bigoil.com", "role": "Manufacturer", "is_relevant": True,
         "_forced_section": "Manufacturers of Olive Oil", "company_summary": "Makes olive oil.",
         "est_revenue_usd_mn": 900.0, "est_market_share_pct": 6.0, "revenue_rank": 1},
        {"company": "Mid Oil", "domain": "midoil.com", "role": "Manufacturer", "is_relevant": True,
         "_forced_section": "Manufacturers of Olive Oil", "company_summary": "Makes olive oil.",
         "est_revenue_usd_mn": 300.0, "est_market_share_pct": 2.0, "revenue_rank": 2},
    ]
    result = {
        "query_context": {"industry": "Olive Oil Market", "country": "global", "sections": []},
        "scope": {"market": "Olive Oil Market"},
        "relevant_companies": rows,
        "unverified_companies": [{"company": "Ghost Ltd", "domain": "", "reason": "no site"}],
    }
    out = tmp_path / "x.csv"
    save_pipeline_csv(result, str(out))
    with out.open(encoding="utf-8") as f:
        data = list(csv.reader(f))
    assert data[0] == [
        "#", "Company", "Brand", "Parent_or_Independent", "Website", "Functionality",
        "Geography", "Is_Relevant", "Summary", "Rank", "Est_Revenue_USD_Mn", "Est_Market_Share_Pct",
    ]
    sections = [r[0] for r in data if r and r[0].startswith("=== ")]
    assert any(s.startswith("=== Manufacturers") and s.endswith(") ===") for s in sections)
    comp_rows = [r for r in data if r and r[0].strip().isdigit() and r[1] != "Ghost Ltd"]
    assert [r[1] for r in comp_rows] == ["Big Oil Co", "Mid Oil", "Small Mill"]
    assert comp_rows[0][9:] == ["1", "900.0", "6.0"]
    assert comp_rows[2][9:] == ["", "", ""]  # unknown -> blank
    ghost = [r for r in data if r and len(r) > 1 and r[1] == "Ghost Ltd"][0]
    assert len(ghost) == 12


def test_env_wins_over_yaml(monkeypatch):
    from vendor_intel.config import Settings

    monkeypatch.setenv("YIELD_STOP_THRESHOLD", "0.123")
    monkeypatch.setenv("WIDEN_LOOP_MAX", "1")
    s = Settings.load()
    assert s.yield_stop_threshold == pytest.approx(0.123)
    assert s.widen_loop_max == 1


def test_fast_cap_sets_discovery_limits():
    from vendor_intel.config import Settings
    from vendor_intel.pipeline.cap_profiles import CLI_DEFAULT_CAP, apply_cap, is_exhaustive
    from vendor_intel.pipeline.geo_limits import pipeline_limits

    s = apply_cap(Settings.load(), CLI_DEFAULT_CAP)
    lim = pipeline_limits(s, recall=False, country="global")
    assert lim["discover"] == 150
    assert lim["smoke_prompts"] == 0  # 0 must not be turned into the default 10
    assert s.widen_loop_max == 1
    assert s.discovery_batch_min_growth == pytest.approx(0.03)
    assert is_exhaustive("maximum") and not is_exhaustive("fast")


def test_brand_of_listed_group_not_double_counted():
    out = mp.normalize_majors({"market_size_usd_mn": 1000, "companies": [
        {"name": "Deoleo", "est_revenue_usd_mn": 100},
        {"name": "Bertolli (Deoleo)", "est_revenue_usd_mn": 20},
    ]})
    by = {c["name"]: c for c in out["companies"]}
    assert by["Bertolli (Deoleo)"]["part_of"] == "Deoleo"
    rows = [{"company": "Deoleo", "is_relevant": True}, {"company": "Bertolli", "domain": "", "is_relevant": True}]
    majors = out["companies"]
    mp.apply_estimates(rows[:1], majors, 1000)
    rows[1].update({"est_market_share_pct": 2.0, "part_of": "Deoleo"})
    assert mp.estimate_coverage([("Manufacturers of Olive Oil", rows)]) == 10.0

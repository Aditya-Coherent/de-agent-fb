import importlib
import sys

import pytest

SAMPLE = {
    "base_year": 2025,
    "base_year_value_usd_mn": 4300.0,
    "cagr_pct": 11.3,
    "historical_usd_mn": {"2020": 2500.0, "2024": 3900.0},
    "regions": [{"name": "Europe", "share_pct": 35.5}, {"name": "Asia Pacific", "share_pct": 30}],
}


def test_off_by_default_returns_equal_copy(randomizer_off):
    out = randomizer_off.randomize_json(SAMPLE)
    assert out == SAMPLE
    assert out is not SAMPLE
    out["regions"][0]["share_pct"] = 0
    assert SAMPLE["regions"][0]["share_pct"] == 35.5      # a deep copy, not an alias


def test_switch_reads_env_at_import(monkeypatch):
    monkeypatch.delenv("AIFLEET_RANDOMIZE", raising=False)
    import AiAgentsFleet.randomizer as r
    assert importlib.reload(r).ENABLED is False
    monkeypatch.setenv("AIFLEET_RANDOMIZE", "1")
    assert importlib.reload(r).ENABLED is True
    monkeypatch.delenv("AIFLEET_RANDOMIZE")
    importlib.reload(r)


def test_env_on_changes_values(monkeypatch):
    monkeypatch.setenv("AIFLEET_RANDOMIZE", "1")
    import AiAgentsFleet.randomizer as r
    r = importlib.reload(r)
    try:
        out = r.randomize_json(SAMPLE)
        assert out["base_year"] == 2025                     # years are never jittered
        assert out["base_year_value_usd_mn"] != SAMPLE["base_year_value_usd_mn"]
        assert out["cagr_pct"] != SAMPLE["cagr_pct"]
        ratio = out["base_year_value_usd_mn"] / SAMPLE["base_year_value_usd_mn"]
        assert 0.89 <= ratio <= 1.11
    finally:
        monkeypatch.delenv("AIFLEET_RANDOMIZE")
        importlib.reload(r)


def test_require_off(monkeypatch, randomizer_off):
    r = randomizer_off
    monkeypatch.setenv("AIFLEET_RANDOMIZE", "1")
    with pytest.raises(SystemExit, match="AIFLEET_RANDOMIZE"):
        r.require_off(allow=False)
    r.require_off(allow=True)                               # explicit opt-in: no error
    monkeypatch.delenv("AIFLEET_RANDOMIZE")
    monkeypatch.setattr(r, "ENABLED", True)
    r.require_off(allow=False)
    assert r.ENABLED is False


def _run_cmi_main(monkeypatch, argv):
    from AiAgentsFleet.cmi_report import run_cmi_report as rcr

    class Reached(Exception):
        pass

    def stop(*_a, **_k):
        raise Reached
    monkeypatch.setattr(rcr, "ensure_fleet", stop)          # past the guard; don't touch output/
    monkeypatch.setattr(sys, "argv", ["run_cmi_report.py", "--market-slug", "x-market", *argv])
    try:
        rcr.main()
    except Reached:
        return "reached"


def test_run_cmi_report_refuses_randomize_without_flag(monkeypatch, randomizer_off):
    monkeypatch.setenv("AIFLEET_RANDOMIZE", "1")
    with pytest.raises(SystemExit, match="--allow-randomize"):
        _run_cmi_main(monkeypatch, [])


def test_run_cmi_report_allows_randomize_with_flag(monkeypatch, randomizer_off):
    monkeypatch.setenv("AIFLEET_RANDOMIZE", "1")
    assert _run_cmi_main(monkeypatch, ["--allow-randomize"]) == "reached"


def test_run_cmi_report_forces_off(monkeypatch, randomizer_off):
    monkeypatch.setattr(randomizer_off, "ENABLED", True)
    assert _run_cmi_main(monkeypatch, []) == "reached"
    assert randomizer_off.ENABLED is False


def test_run_batch_refuses_randomize_without_flag(monkeypatch, tmp_path, randomizer_off):
    from AiAgentsFleet.cmi_report import run_batch
    markets = tmp_path / "m.txt"
    markets.write_text("", encoding="utf-8")
    monkeypatch.setenv("AIFLEET_RANDOMIZE", "1")
    monkeypatch.setattr(sys, "argv", ["run_batch.py", "--markets", str(markets)])
    with pytest.raises(SystemExit, match="--allow-randomize"):
        run_batch.main()

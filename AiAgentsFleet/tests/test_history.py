import copy

from AiAgentsFleet import consistency


def ms(hist, base=1900.0, cagr=8.0):
    return {"base_year": 2025, "forecast_year": 2033, "base_year_value_usd_mn": base, "cagr_pct": cagr,
            "forecast_value_usd_mn": 0, "historical_usd_mn": dict(hist)}


def test_smooth_history_passes_and_is_rescaled():
    hist = {"2020": 1080.0, "2021": 1160.0, "2022": 1250.0, "2023": 1350.0, "2024": 1450.0}
    changes: list[str] = []
    out, *_ = consistency._reconcile_market_size(ms(hist), changes)
    assert out["history_check"] == {"ok": True, "warnings": []}
    assert out["historical_usd_mn"]["2024"] == round(1900 / 1.08, 2)  # leads into the base at the CAGR
    assert any("rescaled" in c for c in changes)


def test_decreasing_history_keeps_raw_and_fails():
    hist = {"2022": 1500.0, "2023": 1700.0, "2024": 1650.0}  # Vitamin C style: 2023 > 2024
    changes: list[str] = []
    out, *_ = consistency._reconcile_market_size(ms(hist, base=1780.0), changes)
    assert out["history_check"]["ok"] is False
    assert out["historical_usd_mn"] == hist                  # raw values kept
    assert any(c.startswith("WARNING") and "non-decreasing" in c for c in changes)


def test_jump_over_twice_cagr_fails():
    # Shrimp: 2021->2022 +30% against an ~8% CAGR
    hist = {"2020": 1000.0, "2021": 1080.0, "2022": 1404.0, "2023": 1516.0, "2024": 1637.0}
    changes: list[str] = []
    out, *_ = consistency._reconcile_market_size(ms(hist, base=1768.0, cagr=8.0), changes)
    assert out["history_check"]["ok"] is False
    assert any("2021->2022" in w for w in out["history_check"]["warnings"])
    assert out["historical_usd_mn"] == hist


def test_market_size_differs_from_raw_only_where_changes_say():
    """fields.market_size == fields_raw.market_size apart from the reconciliation
    changes listed in consistency_changes."""
    raw = ms({"2023": 1600.0, "2024": 1760.0}, base=1900.0)
    raw["forecast_value_usd_mn"] = round(1900.0 * 1.08 ** 8, 2)
    raw["forecast_series_usd_mn"] = {str(y): round(1900.0 * 1.08 ** (y - 2025), 2) for y in range(2025, 2034)}
    raw["published_2025"] = []
    fixed, changes = consistency.reconcile({"market_size": copy.deepcopy(raw)}, "X Market")
    listed = " ".join(changes.get("market_size", []))
    diff = {k for k in set(raw) | set(fixed["market_size"])
            if raw.get(k) != fixed["market_size"].get(k)} - {"history_check"}
    assert all(k in listed for k in diff), (diff, listed)

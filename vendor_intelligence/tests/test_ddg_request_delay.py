"""DDG HTTPS throttle (random 2–4s)."""
import os

from vendor_intel.clients import duckduckgo as ddg


def test_wait_before_ddg_respects_bounds(monkeypatch):
    monkeypatch.setenv("DDG_REQUEST_DELAY", "true")
    monkeypatch.setenv("DDG_REQUEST_DELAY_MIN", "2")
    monkeypatch.setenv("DDG_REQUEST_DELAY_MAX", "4")
    slept: list[float] = []

    def fake_sleep(sec: float) -> None:
        slept.append(sec)

    monkeypatch.setattr(ddg.time, "sleep", fake_sleep)
    ddg.wait_before_ddg_https_request()
    assert len(slept) == 1
    assert 2.0 <= slept[0] <= 4.0


def test_wait_before_ddg_disabled(monkeypatch):
    monkeypatch.setenv("DDG_REQUEST_DELAY", "false")
    slept: list[float] = []

    def fake_sleep(sec: float) -> None:
        slept.append(sec)

    monkeypatch.setattr(ddg.time, "sleep", fake_sleep)
    assert ddg.wait_before_ddg_https_request() == 0.0
    assert slept == []

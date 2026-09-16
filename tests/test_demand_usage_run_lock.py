"""Tests for the market-level sizing concurrency lock (`run_lock.py`) --
runtime-hardening addendum, post-regression-incident (2026-08-24): two
independent CLI processes both ran `size --market Cement` at once, with
nothing to prevent or even warn about it.

Run with: pytest tests/test_demand_usage_run_lock.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from me_engine.demand_usage import run_lock


def test_first_run_acquires_lock(tmp_path):
    lock = run_lock.acquire_size_lock("Cement", source="cli", root=tmp_path)
    try:
        path = run_lock.lock_path("Cement", root=tmp_path)
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["pid"] == os.getpid()
        assert data["market"] == "Cement"
        assert data["source"] == "cli"
        assert "start_time" in data
    finally:
        lock.release()


def test_second_same_market_run_is_rejected_while_first_is_live(tmp_path):
    lock = run_lock.acquire_size_lock("Cement", source="cli", root=tmp_path)
    try:
        with pytest.raises(run_lock.SizingLockError, match="already in progress"):
            run_lock.acquire_size_lock("Cement", source="backend", root=tmp_path)
    finally:
        lock.release()


def test_different_markets_acquire_independent_locks(tmp_path):
    lock_a = run_lock.acquire_size_lock("Cement", source="cli", root=tmp_path)
    lock_b = run_lock.acquire_size_lock("Avocado Oil", source="cli", root=tmp_path)
    try:
        assert lock_a.path != lock_b.path
        assert lock_a.path.exists() and lock_b.path.exists()
    finally:
        lock_a.release()
        lock_b.release()


def test_stale_lock_from_a_dead_pid_is_safely_reclaimed(tmp_path):
    path = run_lock.lock_path("Cement", root=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A PID essentially guaranteed not to be alive right now.
    dead_pid = 999_999
    path.write_text(json.dumps({
        "pid": dead_pid, "start_time": "2026-01-01T00:00:00+00:00",
        "market": "Cement", "source": "cli", "run_id": None, "hostname": "old-host",
    }), encoding="utf-8")

    lock = run_lock.acquire_size_lock("Cement", source="cli", root=tmp_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["pid"] == os.getpid()  # reclaimed, not the dead pid
    finally:
        lock.release()


def test_live_owner_lock_is_not_stolen(tmp_path):
    lock = run_lock.acquire_size_lock("Cement", source="cli", root=tmp_path)
    try:
        with pytest.raises(run_lock.SizingLockError):
            run_lock.acquire_size_lock("Cement", source="cli", root=tmp_path)
        # The original lock (our own live PID) must still be intact.
        data = json.loads(lock.path.read_text(encoding="utf-8"))
        assert data["pid"] == os.getpid()
        assert data["start_time"] == lock.metadata["start_time"]
    finally:
        lock.release()


def test_lock_released_after_success(tmp_path):
    lock = run_lock.acquire_size_lock("Cement", source="cli", root=tmp_path)
    path = lock.path
    lock.release()
    assert not path.exists()
    # A fresh acquire must succeed immediately afterwards.
    lock2 = run_lock.acquire_size_lock("Cement", source="cli", root=tmp_path)
    lock2.release()


def test_lock_released_after_exception():
    """Simulates the CLI/backend `try/finally: lock.release()` pattern."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        lock = run_lock.acquire_size_lock("Cement", source="cli", root=root)
        try:
            try:
                raise RuntimeError("boom")
            finally:
                lock.release()
        except RuntimeError:
            pass
        assert not lock.path.exists()


def test_malformed_lock_metadata_is_treated_as_stale(tmp_path):
    path = run_lock.lock_path("Cement", root=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")

    lock = run_lock.acquire_size_lock("Cement", source="cli", root=tmp_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["pid"] == os.getpid()
    finally:
        lock.release()


def test_release_never_deletes_a_lock_it_does_not_own(tmp_path):
    """If the file has since been replaced by another process's lock (e.g.
    after this lock was mistakenly considered stale and reclaimed), release()
    must not delete the new owner's file."""
    lock = run_lock.acquire_size_lock("Cement", source="cli", root=tmp_path)
    # Simulate another process reclaiming the (now-stale-looking) lock file.
    other_metadata = {**lock.metadata, "pid": lock.metadata["pid"] + 1, "start_time": "2099-01-01T00:00:00+00:00"}
    lock.path.write_text(json.dumps(other_metadata), encoding="utf-8")

    lock.release()
    assert lock.path.exists()
    data = json.loads(lock.path.read_text(encoding="utf-8"))
    assert data["pid"] == other_metadata["pid"]


def test_context_manager_releases_on_exit(tmp_path):
    with run_lock.acquire_size_lock("Cement", source="cli", root=tmp_path) as lock:
        assert lock.path.exists()
    assert not lock.path.exists()


def test_context_manager_releases_on_exception(tmp_path):
    with pytest.raises(ValueError):
        with run_lock.acquire_size_lock("Cement", source="cli", root=tmp_path) as lock:
            raise ValueError("boom")
    assert not lock.path.exists()

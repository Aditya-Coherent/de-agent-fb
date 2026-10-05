"""Cross-process, market-level lock for the Phase 3 (sizing) run --
runtime-hardening addendum, post-regression-incident (2026-08-24): a live
Cement `size --market Cement` run was independently started twice from two
separate terminals, running for hours in parallel with no warning and no
persisted trace of either.

This module does NOT touch sizing methodology, evidence logic, pricing
logic, taxonomy logic, or validation logic -- it only decides whether a
`size` run for a given market is allowed to START, at the process level, so
CLI and the backend SSE route -- which are separate OS processes and share
NOTHING in memory -- both refuse to run the same market concurrently.

The lock is a single JSON file at `<market run dir>/.size.lock`, created
atomically (`O_CREAT | O_EXCL`) so two processes racing to acquire it can
never both succeed. Its presence + the recorded PID's liveness is the only
signal checked -- no in-memory registry, since CLI and backend do not share
one.
"""
from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import checkpoints


class SizingLockError(RuntimeError):
    """Raised when another (live) process already holds the sizing lock for
    this market. Carries the holder's metadata in the message so the caller
    can surface a clear, actionable error."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def lock_path(market_name: str, root: Path | None = None) -> Path:
    return checkpoints.run_dir(market_name, root) / ".size.lock"


def _is_pid_alive(pid: object) -> bool:
    """Best-effort, dependency-free liveness check (no `psutil` in this
    project). Errs on the side of treating a PID as ALIVE when we can't be
    sure -- a false 'stale' verdict risks two processes racing on the same
    market; a false 'alive' verdict merely blocks a new run with a clear
    error the operator can investigate."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists, just owned by someone else
    except OSError:
        return False
    return True


def _read_lock(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _lock_is_stale(data: dict[str, Any] | None) -> bool:
    """A missing/unreadable/malformed lock file is treated as stale (safe to
    reclaim -- there is no live owner to protect). A well-formed lock is
    stale only if its recorded PID is no longer alive."""
    if not data:
        return True
    return not _is_pid_alive(data.get("pid"))


class SizingLock:
    """Handle to a held sizing lock. Use as a context manager, or call
    `.release()` explicitly (e.g. from a `finally` block) -- release is
    idempotent and safe to call more than once."""

    def __init__(self, path: Path, metadata: dict[str, Any]) -> None:
        self.path = path
        self.metadata = metadata
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        current = _read_lock(self.path)
        # Only remove the file if it is still the exact lock we wrote --
        # never delete a lock some other process has since (re)acquired
        # (e.g. after this process crashed and another correctly reclaimed
        # a stale lock before this `finally` block ever ran).
        if (
            current
            and current.get("pid") == self.metadata.get("pid")
            and current.get("start_time") == self.metadata.get("start_time")
        ):
            try:
                self.path.unlink()
            except OSError:
                pass

    def __enter__(self) -> "SizingLock":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.release()
        return False


def acquire_size_lock(
    market_name: str,
    source: str,
    root: Path | None = None,
    run_id: str | None = None,
) -> SizingLock:
    """Acquire the market-level sizing lock. Raises `SizingLockError` if a
    LIVE process already holds it. A lock whose recorded PID is no longer
    alive (or whose file is malformed/unreadable) is treated as stale and
    safely replaced -- a live owner's lock is NEVER deleted out from under
    it."""
    path = lock_path(market_name, root)
    metadata = {
        "pid": os.getpid(),
        "start_time": _now_iso(),
        "market": market_name,
        "source": source,
        "run_id": run_id,
        "hostname": socket.gethostname(),
    }
    payload = json.dumps(metadata, indent=2, ensure_ascii=False).encode("utf-8")

    def _try_create() -> bool:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)
        return True

    if _try_create():
        return SizingLock(path, metadata)

    existing = _read_lock(path)
    if not _lock_is_stale(existing):
        assert existing is not None  # not stale implies well-formed
        raise SizingLockError(
            f"A sizing run for '{market_name}' is already in progress "
            f"(pid={existing.get('pid')}, source={existing.get('source', 'unknown')}, "
            f"started={existing.get('start_time', 'unknown')}). Wait for it to finish, or confirm "
            f"that process is genuinely dead before removing the lock file at {path}."
        )

    # Stale (dead owner, or malformed/missing file) -- reclaim it.
    try:
        path.unlink()
    except OSError:
        pass
    if _try_create():
        return SizingLock(path, metadata)

    # Lost a race against another process reclaiming the same stale lock in
    # the window between our unlink and our create -- re-check who owns it
    # now rather than silently overwriting a brand-new live lock.
    existing = _read_lock(path)
    if existing and not _lock_is_stale(existing):
        raise SizingLockError(
            f"A sizing run for '{market_name}' just started in another process "
            f"(pid={existing.get('pid')}) while recovering a stale lock. Try again."
        )
    raise SizingLockError(f"Could not acquire the sizing lock for '{market_name}' -- try again.")

"""Cooperative cancel for long pipeline runs (Stop button in UI).

Supports multiple concurrent pipelines: each run_id has its own cancel flag.
Worker threads bind their run_id so ``is_cancelled()`` only sees that run.
"""
from __future__ import annotations

import threading


class PipelineCancelled(Exception):
    """Raised when the user stops a run midway."""


_cancel_by_run: dict[str, threading.Event] = {}
_cancel_lock = threading.Lock()
_current = threading.local()
# Legacy process-wide flag (kept for any code that still sets it)
_cancel = threading.Event()


def bind_run(run_id: str) -> None:
    """Call at the start of a pipeline worker thread."""
    rid = (run_id or "").strip()
    _current.run_id = rid
    if not rid:
        return
    with _cancel_lock:
        _cancel_by_run.setdefault(rid, threading.Event()).clear()


def unbind_run(run_id: str | None = None) -> None:
    rid = (run_id or getattr(_current, "run_id", None) or "").strip()
    _current.run_id = None
    if not rid:
        return
    with _cancel_lock:
        _cancel_by_run.pop(rid, None)


def clear_cancel() -> None:
    """Clear cancel for the bound run (or the legacy global flag)."""
    rid = getattr(_current, "run_id", None)
    if rid:
        with _cancel_lock:
            ev = _cancel_by_run.get(rid)
            if ev is not None:
                ev.clear()
    _cancel.clear()


def request_cancel(run_id: str | None = None) -> None:
    """Request stop for a specific run, or the bound/legacy global flag."""
    rid = (run_id or getattr(_current, "run_id", None) or "").strip()
    if rid:
        with _cancel_lock:
            _cancel_by_run.setdefault(rid, threading.Event()).set()
        return
    _cancel.set()


def is_cancelled() -> bool:
    rid = getattr(_current, "run_id", None)
    if rid:
        with _cancel_lock:
            ev = _cancel_by_run.get(rid)
            if ev is not None and ev.is_set():
                return True
            return False
    return _cancel.is_set()


def check_cancelled(where: str = "") -> None:
    if is_cancelled():
        hint = f" ({where})" if where else ""
        raise PipelineCancelled(f"Stopped by user{hint}.")

"""Per-run wall-clock budget.

One process-wide budget is started at the beginning of a pipeline run (``start_budget``). Long
loops (discovery, widening, directory mining, the coverage loop, optional LLM passes) ask it
whether they should stop, so a run converges on its time target instead of exhausting every
search/LLM opportunity.

Two thresholds:
- ``discovery_should_stop()``: discovery / widening / list mining stop (default at 45% of the
  budget, or at ``stop_fraction`` - whichever comes first) so enrichment + classification still
  have room to finish.
- ``wrap_up()``: at ``stop_fraction`` (default 75%) everything optional stops (extra widening,
  coverage expansion, second-pass "strengthen" LLM calls) and the run moves straight to export.

Without a started budget every check returns False (unlimited), so library callers and tests
that never call ``start_budget`` behave exactly as before.
"""
from __future__ import annotations

import os
import time
from typing import Callable


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


class TimeBudget:
    def __init__(
        self,
        budget_seconds: float | None,
        *,
        stop_fraction: float = 0.75,
        discovery_fraction: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.budget_seconds = float(budget_seconds) if budget_seconds and budget_seconds > 0 else None
        self.stop_fraction = float(stop_fraction)
        if discovery_fraction is None:
            discovery_fraction = _env_float("TIME_BUDGET_DISCOVERY_FRAC", 0.45)
        self.discovery_fraction = min(float(discovery_fraction), self.stop_fraction)
        self._clock = clock
        self.started = clock()
        self._announced: set[str] = set()

    @property
    def limited(self) -> bool:
        return self.budget_seconds is not None

    def elapsed(self) -> float:
        return self._clock() - self.started

    def fraction(self) -> float:
        if not self.budget_seconds:
            return 0.0
        return self.elapsed() / self.budget_seconds

    def remaining(self) -> float:
        if not self.budget_seconds:
            return float("inf")
        return self.budget_seconds - self.elapsed()

    def seconds_until(self, fraction: float) -> float:
        """Seconds left until the given fraction of the budget is used (inf when unlimited)."""
        if not self.budget_seconds:
            return float("inf")
        return self.budget_seconds * fraction - self.elapsed()

    def _note(self, key: str, msg: str) -> None:
        if key not in self._announced:
            self._announced.add(key)
            print(msg, flush=True)

    def discovery_should_stop(self) -> bool:
        if not self.budget_seconds:
            return False
        if self.fraction() >= self.discovery_fraction:
            self._note(
                "disc",
                f"  [budget] {self.elapsed() / 60:.1f} min used "
                f"({self.fraction():.0%} of {self.budget_seconds / 60:.0f} min) - "
                f"stopping discovery/widening, moving on with what exists",
            )
            return True
        return False

    def wrap_up(self) -> bool:
        if not self.budget_seconds:
            return False
        if self.fraction() >= self.stop_fraction:
            self._note(
                "wrap",
                f"  [budget] {self.fraction():.0%} of the time budget used - skipping optional "
                f"passes and moving to classification/export",
            )
            return True
        return False


_current: TimeBudget | None = None


def start_budget(minutes: float | None, **kwargs) -> TimeBudget:
    global _current
    _current = TimeBudget((minutes or 0) * 60.0 if minutes else None, **kwargs)
    return _current


def get_budget() -> TimeBudget:
    """The active run budget (an unlimited one if none was started)."""
    global _current
    if _current is None:
        _current = TimeBudget(None)
    return _current


def clear_budget() -> None:
    global _current
    _current = None

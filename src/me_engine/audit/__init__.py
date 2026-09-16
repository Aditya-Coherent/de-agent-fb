"""Additive, append-only audit/observability layer for market-sizing
pipelines.

This package records what already happens inside a pipeline run (evidence
searches, LLM calls, fact validations, derivations, magnitude checks, scope
overrides, stage boundaries, ...) to a per-run `audit_log.jsonl` file, purely
for later inspection/debugging/UI timelines. It never changes any formula,
threshold, taxonomy/derivation/validation logic, or control flow -- every
integration point takes an OPTIONAL `emitter` parameter that is a no-op when
`None`, so no existing caller or test is affected.

Currently supports the `"usage_based"` demand-side pipeline
(`me_engine.demand_usage`). A `"desk_research"` approach value is reserved
for a future pipeline but not implemented here.
"""
from __future__ import annotations

from .events import AuditEvent
from .store import AuditEmitter, list_runs, read_events, run_audit_dir

__all__ = ["AuditEvent", "AuditEmitter", "run_audit_dir", "read_events", "list_runs"]

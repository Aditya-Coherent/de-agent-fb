"""`AuditEvent` -- one append-only record in a run's `audit_log.jsonl`.

Field/event_type conventions (documented here, not enforced -- `event_type`
is deliberately free-form so new stages/instrumentation sites can add their
own without touching this module):

    event_type values in use today:
        "stage_start" / "stage_end"   -- a graph node/phase began or finished
        "llm_call"                    -- a direct LLM prompt/response (or a
                                          swallowed exception in its place)
        "search_query"                -- a web/tabular/API search was issued
        "source_attempt"               -- one candidate source was tried
                                          during evidence acquisition
        "fact_validation"              -- `fact_validator.validate_candidate`
                                          (or an equivalent gate) ran
        "derivation"                   -- `derivation.build_derived_fact` ran
        "magnitude_check"               -- `magnitude_validation.evaluate_magnitude` ran
        "taxonomy_generated"           -- one usage bucket was proposed
        "scope_override"               -- a user justified retaining an
                                          out-of-scope bucket at approval time

    stage values in use today (also see `me_engine.demandUseCaseCountry_approach2B.progress.NODE_NAMES`):
        "setup", "taxonomy", "taxonomy_approval", "evidence_search",
        "fact_extraction", "derivation", "magnitude_check",
        "demand_quantification", "pricing", "calculate",
        "driver_magnitude_validation", "triangulation",
        "magnitude_cross_validation", "validation"

`approach` is a string literal for now -- `"usage_based"` is the only value
actually produced; `"desk_research"` is reserved for a future pipeline and
intentionally not implemented anywhere in this package yet.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Literal

Approach = Literal["usage_based", "desk_research"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_event_id() -> str:
    return uuid.uuid4().hex


@dataclass
class AuditEvent:
    event_id: str
    run_id: str
    approach: str
    market: str
    stage: str
    event_type: str
    timestamp: str
    seq: int
    country: str | None = None
    bucket: str | None = None
    llm: dict[str, Any] | None = None
    query: dict[str, Any] | None = None
    source: dict[str, Any] | None = None
    raw_evidence: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None
    derivation: dict[str, Any] | None = None
    calculation: dict[str, Any] | None = None
    input_data: dict[str, Any] | None = None
    output_data: dict[str, Any] | None = None
    status: str | None = None
    error: str | None = None
    data: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditEvent":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        approach: str,
        market: str,
        stage: str,
        event_type: str,
        seq: int,
        **kwargs: Any,
    ) -> "AuditEvent":
        """Convenience constructor -- auto-fills `event_id`/`timestamp` with
        real values at call time (never fabricated placeholders)."""
        return cls(
            event_id=new_event_id(),
            run_id=run_id,
            approach=approach,
            market=market,
            stage=stage,
            event_type=event_type,
            timestamp=_now_iso(),
            seq=seq,
            **kwargs,
        )

"""Phase 3 (India-only): thin wrapper over the EXISTING sizing graph -- no
new sizing logic at all.

`build_sizing_graph()`/`sizing_init_state()` are re-exported UNCHANGED from
`me_engine.demandUseCaseCountry_approach2B.graph` -- Phase 3's nodes (`demand_quantification`,
`pricing`, `calculate`, `driver_magnitude_validation`, `triangulation`,
`magnitude_cross_validation`, `validation`) already operate generically over
"a list of buckets" x "a list of countries", reading only `name`/
`description`/`bucket_category` off each bucket dict -- the India Actual Use
Case list (each carrying an extra, inert `major_use_case` tag) and
`countries = ["India"]` slot in with zero changes required.

`run_sizing_phase` here calls `demand_usage.graph.run_sizing_phase` directly
(same streaming/progress/correctness-gate loop, reused by direct call --
NOT duplicated) and then applies the one genuinely new step: the
deterministic `aggregation.rollup_major_use_cases` regrouping, added onto
the same final state.

Known, deliberate limitation (documented, not fixed here): `run_sizing_phase`
internally names its progress-recorder/on-disk-artifact paths off
`approved_setup["market_name"]` via `me_engine.demandUseCaseCountry_approach2B.checkpoints`
(NOT this package's own `checkpoints.py`) -- so the ephemeral
`02_sizing.progress.json` milestone file for an India run lands under
`output/demand_usage/<slug>/`, the SAME tree a non-India run for the same
market name would use (the final `02_sizing.json`/`.xlsx` artifacts
themselves are unaffected -- this package's own `buildup_writer.py` writes
those under `output/demand_usage_india/<slug>/`, fully separate). Refactoring
`demand_usage/graph.py::run_sizing_phase` to take an injectable checkpoints
module was judged not worth the risk of touching that function's internals
for an ephemeral progress file; the market-level SIZING LOCK (the one
collision that actually matters -- see `backend/demand_usage_india_routes.py`
and `demand_usage_india_pipeline.py`) is kept genuinely distinct by suffixing
the lock's own market-name key with " (India)".
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from me_engine.demandUseCaseCountry_approach2B.graph import build_sizing_graph, sizing_init_state
from me_engine.demandUseCaseCountry_approach2B.graph import run_sizing_phase as _run_sizing_phase_shared
from me_engine.generation.config import GenConfig

from . import aggregation

__all__ = ["build_sizing_graph", "sizing_init_state", "run_sizing_phase"]


def run_sizing_phase(
    approved_setup: dict[str, Any],
    approved_taxonomy: dict[str, Any],
    config: GenConfig,
    cache_path: Path | None = None,
    root: Path | None = None,
    run_id: str | None = None,
    source: str = "cli",
) -> dict[str, Any]:
    """Runs the UNCHANGED Phase 3 sizing graph (via `demand_usage.graph.
    run_sizing_phase`) over the approved India setup/taxonomy, then adds the
    one genuinely new step: `aggregation.rollup_major_use_cases`, merged
    onto the same final state (never replacing any of Phase 3's own keys)."""
    final_state = _run_sizing_phase_shared(
        approved_setup, approved_taxonomy, config,
        cache_path=cache_path, root=root, run_id=run_id, source=source,
    )
    rollup = aggregation.rollup_major_use_cases(final_state)
    final_state = dict(final_state)
    final_state.update(rollup)
    if rollup.get("rollup_flags"):
        final_state["quality_flags"] = list(final_state.get("quality_flags") or []) + rollup["rollup_flags"]
    return final_state

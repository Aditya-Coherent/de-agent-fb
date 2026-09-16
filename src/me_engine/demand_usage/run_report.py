"""Human-readable, single-file consolidated result report for a completed
Phase 3 (sizing) run -- reporting/observability addendum (2026-08-26).

Writes `SIZE_RUN_RESULT.md` (success) or `SIZE_RUN_RESULT.partial.md`
(incomplete/failed) next to `02_sizing.json` -- see `checkpoints.run_dir`.
This module NEVER computes a number itself: every figure here is read back
from either the graph's own final `state` (the same object
`buildup_writer.write_buildup_json` already serialized) or the just-finished
run's `02_sizing.progress.json` (real, measured elapsed times/counts, never
an estimate). It is purely a reporting layer -- no sizing/evidence/pricing/
taxonomy/validation logic lives here, and it never modifies `02_sizing.json`,
`02_sizing.xlsx`, `02_sizing.progress.json`, or `.size.lock`.

`write_success_report` must only ever be called with a `state` that already
passed `graph.run_sizing_phase`'s own "validation must be present" guard
(see `graph.py`) -- it re-asserts that here too (never writes a "success"
report from an incomplete state, even if called some other way in future).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import checkpoints
from .progress import NODE_NAMES, progress_path


def report_path(market_name: str, root: Path | None = None) -> Path:
    return checkpoints.run_dir(market_name, root) / "SIZE_RUN_RESULT.md"


def partial_report_path(market_name: str, root: Path | None = None) -> Path:
    return checkpoints.run_dir(market_name, root) / "SIZE_RUN_RESULT.partial.md"


def _read_progress(market_name: str, root: Path | None) -> dict[str, Any] | None:
    path = progress_path(market_name, root)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _phase_durations(phase_timings: dict[str, float]) -> list[tuple[str, float]]:
    """Diffs consecutive cumulative `phase_timings` entries (in the graph's
    fixed node order) into each node's OWN duration -- real measured deltas,
    never an estimate. A node missing from `phase_timings` (run didn't reach
    it) is simply omitted."""
    out: list[tuple[str, float]] = []
    prev = 0.0
    for node in NODE_NAMES:
        if node not in phase_timings:
            continue
        cumulative = float(phase_timings[node])
        out.append((node, round(cumulative - prev, 1)))
        prev = cumulative
    return out


def _hit_and_total(attempts_by_bucket: dict[str, dict[str, list[dict[str, Any]]]]) -> tuple[int, int]:
    atts = [a for per_geo in attempts_by_bucket.values() for lst in per_geo.values() for a in lst]
    return sum(1 for a in atts if a.get("found")), len(atts)


def _file_stat_line(path: Path | str | None) -> str:
    if not path:
        return "(not written)"
    p = Path(path)
    if not p.exists():
        return f"{p} (MISSING -- not found on disk)"
    st = p.stat()
    mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).astimezone().isoformat(timespec="seconds")
    return f"{p}  ({st.st_size:,} bytes, written {mtime})"


def _fmt_usd_mn(value: Any) -> str:
    try:
        return f"${float(value or 0):,.1f}M"
    except (TypeError, ValueError):
        return "n/a"


def build_success_summary(
    state: dict[str, Any],
    market_name: str,
    *,
    json_path: Path | str | None,
    xlsx_path: Path | str | None,
    worker_count: int | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Pure computation (no I/O) shared by the Markdown report and the CLI's
    compact terminal summary, so both always show identical numbers."""
    validation = state.get("validation") or {}
    eq = validation.get("evidence_quality") or {}

    n_driver_hits, n_driver_total = _hit_and_total(state.get("retrieval_attempts") or {})
    n_intensity_hits, n_intensity_total = _hit_and_total(state.get("intensity_retrieval_attempts") or {})
    n_price_hits, n_price_total = _hit_and_total(state.get("price_retrieval_attempts") or {})
    n_conv_hits, n_conv_total = _hit_and_total(state.get("price_conversion_retrieval_attempts") or {})

    progress = _read_progress(market_name, root) or {}
    durations = _phase_durations(progress.get("phase_timings") or {})

    return {
        "market": market_name,
        "run_id": progress.get("run_id"),
        "source": progress.get("source", "cli"),
        "start_time": progress.get("start_time"),
        "end_time": progress.get("last_update"),
        "total_runtime_seconds": progress.get("elapsed_seconds"),
        "worker_count": worker_count,
        "n_buckets": len(state.get("usage_buckets") or []),
        "n_countries": len(state.get("countries") or []),
        "n_completed_cells": progress.get("completed_cells"),
        "included_demand_usd_mn": state.get("total_market_value"),
        "related_demand_usd_mn": state.get("related_demand_value"),
        "residual_usd_mn": validation.get("residual_implied_usd_mn"),
        "reported_market_view_usd_mn": validation.get("reported_market_view_usd_mn"),
        "deterministic_score_pct": (validation.get("deterministic_score") or 0) * 100,
        "plausibility_score_pct": (validation.get("plausibility_score") or 0) * 100,
        "evidence_quality": {
            "verified_observed_pct": eq.get("observed_pct", 0.0),
            "derived_pct": eq.get("derived_pct", 0.0),
            "source_found_unverified_pct": eq.get("source_found_unverified_pct", 0.0),
            "assumption_pct": eq.get("assumption_pct", 0.0),
            "total_inputs": eq.get("total_inputs"),
        },
        "evidence_attempts": {
            "driver": n_driver_total, "driver_found": n_driver_hits,
            "intensity": n_intensity_total, "intensity_found": n_intensity_hits,
            "price": n_price_total, "price_found": n_price_hits,
            "price_conversion": n_conv_total, "price_conversion_found": n_conv_hits,
            "total": n_driver_total + n_intensity_total + n_price_total + n_conv_total,
            "total_found": n_driver_hits + n_intensity_hits + n_price_hits + n_conv_hits,
        },
        "phase_durations_seconds": durations,
        "json_path": str(json_path) if json_path else None,
        "xlsx_path": str(xlsx_path) if xlsx_path else None,
        "lock_released": not (checkpoints.run_dir(market_name, root) / ".size.lock").exists(),
        "validation_present": bool(state.get("validation")),
        "quality_flags": state.get("quality_flags") or [],
    }


def write_success_report(
    state: dict[str, Any],
    market_name: str,
    *,
    json_path: Path | str | None,
    xlsx_path: Path | str | None,
    worker_count: int | None = None,
    root: Path | None = None,
) -> Path:
    """Writes `SIZE_RUN_RESULT.md`. Never called for an incomplete state --
    re-asserts `validation` is present (the same guarantee `run_sizing_phase`
    already enforces before returning) rather than trusting the caller."""
    if not state.get("validation"):
        raise ValueError(
            "write_success_report called with no 'validation' in state -- refusing to write a "
            "success report for an incomplete run. This must never happen if callers only invoke "
            "this after `run_sizing_phase` returns normally (it already guards this)."
        )

    s = build_success_summary(
        state, market_name, json_path=json_path, xlsx_path=xlsx_path, worker_count=worker_count, root=root,
    )
    eq = s["evidence_quality"]
    ea = s["evidence_attempts"]

    lines: list[str] = []
    lines.append(f"# Sizing Run Result — {s['market']}")
    lines.append("")
    lines.append("STATUS: SUCCESS")
    lines.append("")
    lines.append("## Run information")
    lines.append("")
    lines.append(f"- Market: {s['market']}")
    lines.append(f"- Run ID: {s['run_id'] or '(none -- CLI run)'}")
    lines.append(f"- Start time: {s['start_time']}")
    lines.append(f"- End time: {s['end_time']}")
    if s["total_runtime_seconds"] is not None:
        lines.append(f"- Total runtime: {s['total_runtime_seconds']:.1f}s ({s['total_runtime_seconds']/60:.1f} min)")
    lines.append(f"- Source: {s['source']}")
    lines.append(f"- Worker count: {s['worker_count'] if s['worker_count'] is not None else 'n/a'}")
    lines.append(f"- Usage buckets: {s['n_buckets']}")
    lines.append(f"- Geographies: {s['n_countries']}")
    lines.append(f"- Completed cells: {s['n_completed_cells']}")
    lines.append("")
    lines.append("## Final result")
    lines.append("")
    lines.append(f"- Included Demand: {_fmt_usd_mn(s['included_demand_usd_mn'])}")
    lines.append(f"- Related Demand (inv/reserve): {_fmt_usd_mn(s['related_demand_usd_mn'])}")
    lines.append(f"- Residual/Uncovered (implied): {_fmt_usd_mn(s['residual_usd_mn'])}")
    lines.append(f"- Reported Market View: {_fmt_usd_mn(s['reported_market_view_usd_mn'])}")
    lines.append(f"- Deterministic score: {s['deterministic_score_pct']:.1f}%")
    lines.append(f"- Plausibility score: {s['plausibility_score_pct']:.1f}%")
    lines.append("")
    lines.append("## Evidence quality (final, per-cell)")
    lines.append("")
    lines.append(f"- verified_observed: {eq['verified_observed_pct']:.1f}%")
    lines.append(f"- derived: {eq['derived_pct']:.1f}%")
    lines.append(f"- source_found_unverified: {eq['source_found_unverified_pct']:.1f}%")
    lines.append(f"- assumption: {eq['assumption_pct']:.1f}%")
    lines.append(f"- total inputs: {eq['total_inputs']}")
    lines.append("")
    lines.append("## Evidence attempts")
    lines.append("")
    lines.append(f"- Driver: {ea['driver']} attempted, {ea['driver_found']} found")
    lines.append(f"- Intensity: {ea['intensity']} attempted, {ea['intensity_found']} found")
    lines.append(f"- Price: {ea['price']} attempted, {ea['price_found']} found")
    lines.append(f"- Price conversion: {ea['price_conversion']} attempted, {ea['price_conversion_found']} found")
    lines.append(f"- **Total: {ea['total']} attempted, {ea['total_found']} found**")
    lines.append("")
    lines.append("## Runtime breakdown (actual measured timings)")
    lines.append("")
    lines.append("| Node | Duration | % of total |")
    lines.append("|---|---|---|")
    total_rt = s["total_runtime_seconds"] or 0
    for node, dur in s["phase_durations_seconds"]:
        pct = f"{(dur / total_rt * 100):.1f}%" if total_rt else "n/a"
        lines.append(f"| {node} | {dur:.1f}s | {pct} |")
    if total_rt:
        lines.append(f"| **Total** | **{total_rt:.1f}s ({total_rt/60:.1f} min)** | **100%** |")
    lines.append("")
    lines.append("## Output integrity")
    lines.append("")
    lines.append(f"- `02_sizing.json`: {_file_stat_line(s['json_path'])}")
    lines.append(f"- `02_sizing.xlsx`: {_file_stat_line(s['xlsx_path'])}")
    lines.append(f"- `.size.lock` released: {'yes' if s['lock_released'] else 'NO -- still present'}")
    lines.append(f"- `validation` present and populated: {'yes' if s['validation_present'] else 'NO'}")
    lines.append("- Final output written successfully: yes")
    lines.append("")
    lines.append("## Errors / warnings")
    lines.append("")
    if s["quality_flags"]:
        for f in s["quality_flags"]:
            lines.append(f"- [{f.get('severity', '?')}] {f.get('code', '?')}: {f.get('message', '')}")
    else:
        lines.append("Errors: None")
        lines.append("Warnings: None")
    lines.append("")

    out_path = report_path(market_name, root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")

    # A fresh success supersedes any earlier partial/failure report for this
    # market -- remove it so a stale "INCOMPLETE" file never sits next to a
    # now-successful `SIZE_RUN_RESULT.md` and gets mistaken for current.
    stale_partial = partial_report_path(market_name, root)
    if stale_partial.exists():
        try:
            stale_partial.unlink()
        except OSError:
            pass

    return out_path


def write_failure_report(market_name: str, root: Path | None = None, error: str | None = None) -> Path | None:
    """Writes `SIZE_RUN_RESULT.partial.md` from whatever `02_sizing.progress.json`
    last recorded -- NEVER touches `SIZE_RUN_RESULT.md` (a prior success is
    never overwritten by a later failed attempt). Returns None (writes
    nothing) if no progress file exists at all (e.g. the run never even
    acquired the lock)."""
    progress = _read_progress(market_name, root)
    if progress is None:
        return None

    lines: list[str] = []
    lines.append(f"# Sizing Run Result — {market_name} (INCOMPLETE)")
    lines.append("")
    lines.append("STATUS: INCOMPLETE / FAILED")
    lines.append("")
    if error:
        lines.append(f"Triggering error (this attempt): {error}")
        lines.append("")
    lines.append("## Last known progress")
    lines.append("")
    lines.append(f"- Market: {progress.get('market')}")
    lines.append(f"- Run ID: {progress.get('run_id') or '(none -- CLI run)'}")
    lines.append(f"- Source: {progress.get('source')}")
    lines.append(f"- Last update: {progress.get('last_update')}")
    lines.append(f"- Elapsed time: {progress.get('elapsed_seconds')}s")
    lines.append(f"- Recorded status: {progress.get('status')}")
    lines.append(f"- Current/last phase: {progress.get('phase')}")
    lines.append(f"- Completed buckets: {progress.get('completed_buckets')}")
    lines.append(f"- Completed cells: {progress.get('completed_cells')}")
    lines.append(f"- Evidence attempts: {progress.get('evidence_attempts')}")
    errors = progress.get("errors") or []
    lines.append("")
    lines.append("## Errors")
    lines.append("")
    if errors:
        for e in errors:
            lines.append(f"- {e}")
    else:
        lines.append("Errors: None recorded (run may have been externally interrupted -- see the "
                      "process-level diagnosis, not this file, for that case)")
    lines.append("")
    lines.append("This run did NOT complete -- do not treat any figure above as a final result. "
                  "A previously successful `SIZE_RUN_RESULT.md` for this market, if one exists, was "
                  "NOT modified or overwritten by this file.")
    lines.append("")

    out_path = partial_report_path(market_name, root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def format_terminal_summary(summary: dict[str, Any], report_path_str: str) -> str:
    """Compact terminal summary -- same numbers as the Markdown report,
    printed after a successful CLI run."""
    eq = summary["evidence_quality"]
    ea = summary["evidence_attempts"]
    rt = summary["total_runtime_seconds"] or 0
    lines = [
        "=" * 60,
        f"SIZE RUN COMPLETE — {summary['market']}",
        "=" * 60,
        "",
        f"Runtime:                  {rt/60:.1f} min",
        f"Included Demand:         {_fmt_usd_mn(summary['included_demand_usd_mn'])}",
        f"Reported Market View:    {_fmt_usd_mn(summary['reported_market_view_usd_mn'])}",
        f"Residual:                {_fmt_usd_mn(summary['residual_usd_mn'])}",
        "",
        f"Deterministic Score:     {summary['deterministic_score_pct']:.0f}%",
        f"Plausibility Score:      {summary['plausibility_score_pct']:.0f}%",
        "",
        "Evidence Quality:",
        f"  Verified observed:     {eq['verified_observed_pct']:.1f}%",
        f"  Derived:               {eq['derived_pct']:.1f}%",
        f"  Source-found:          {eq['source_found_unverified_pct']:.1f}%",
        f"  Assumption:            {eq['assumption_pct']:.1f}%",
        "",
        "Evidence Attempts:",
        f"  Driver:                {ea['driver']}",
        f"  Intensity:             {ea['intensity']}",
        f"  Price:                 {ea['price']}",
        f"  Price conversion:      {ea['price_conversion']}",
        f"  Total:                 {ea['total']}",
        "",
        "Output:",
        f"  JSON:                  {summary['json_path']}",
        f"  XLSX:                  {summary['xlsx_path']}",
        f"  Report:                {report_path_str}",
        "",
        "=" * 60,
    ]
    return "\n".join(lines)

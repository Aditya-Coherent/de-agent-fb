"""Autonomous 2A agent: runs every 2A phase end to end with the LLM (DeepSeek,
via `GenConfig`) as the brain, replacing the human approval gates with
checks and a review loop (`src/newinfo/system.md` §8).

    setup -> [driver -> driver-validity gate -> size (+ per-country price)
              -> ledger -> hard/soft checks -> reviewer rubric
              -> plausibility score] x up to N passes
          -> keep the best pass -> segments -> full ledger + checks
          -> (optional Publisher) -> write outputs only if no hard check failed

Each pass that is not accepted feeds the reviewer's redo instructions into
the next pass's driver and sizing prompts. A pass is accepted when its
plausibility score (0-10, `plausibility.py`) reaches `agent.min_score` with
no hard flag and no critical issue. The best pass is chosen by plausibility
(ties -> higher verified-value share), never by closeness to a published
market size.

Outputs:
    output/demand_country_2a/<slug>/            02_sizing / 04_market (as before)
    output/demand_country_2a/<slug>/runs/<RUN>/ summary.md, ledger.{json,csv,parquet},
                                                documentation.md, progress.mermaid,
                                                market_tables.xlsx
    05_agent_summary.json                       every pass and why it was kept or redone
A hard failure writes only the run report (summary.md + 05_agent_summary.json).
"""
from __future__ import annotations

import math
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from me_engine.consensus.combine import design_blend
from me_engine.generation.config import GenConfig
from me_engine.generation.llm import GenLLM

from . import checkpoints, checks, design_config, driver, plausibility, review, segmentation, setup
from .engine import run_lock
from .graph import run_sizing_phase, write_sizing_outputs
from .ledger import Ledger, fmt_pct, fmt_usd, write_documentation
from .ledger_2a import build_2a_ledger
from .progress import Progress

APPROVER = "2A agent"
_LOCK_SUFFIX = " (2A)"
_UNUSED_NODES = ("B_TAX", "B_DRV", "C_TAX", "C_MIX", "ANCH", "ANC")


def summary_path(market_name: str, root: Path | None = None) -> Path:
    return checkpoints.run_dir(market_name, root) / "05_agent_summary.json"


def next_run_id(market_name: str, root: Path | None = None) -> str:
    runs = checkpoints.run_dir(market_name, root) / "runs"
    n = max((int(m.group(1)) for p in runs.glob("R*") if (m := re.fullmatch(r"R(\d+)", p.name))), default=0)
    return f"R{n + 1:03d}"


def _size(approved_setup: dict[str, Any], approved_driver: dict[str, Any], config: GenConfig,
          cache_path: Path | None, root: Path | None) -> dict[str, Any]:
    lock = run_lock.acquire_size_lock(approved_setup["market_name"] + _LOCK_SUFFIX, source="agent", root=root)
    try:
        return run_sizing_phase(approved_setup, approved_driver, config, cache_path=cache_path, root=root, source="agent")
    finally:
        lock.release()


def align_unit_to_official_driver(approved_setup: dict[str, Any], drv: dict[str, Any], root: Path | None,
                                  say: Callable[[str], None]) -> dict[str, Any]:
    """An official-statistics driver (FAOSTAT: tonnes) fixes the physical unit;
    a setup that chose another unit (e.g. litres) would raise the engine's
    target-commodity unit mismatch on every pass, and no redo can fix it."""
    unit = drv.get("final_demand_unit") or drv.get("unit")
    if drv.get("library_status") != "official_data" or not unit:
        return approved_setup
    if str(approved_setup.get("physical_unit") or "").strip().lower() == unit.strip().lower():
        return approved_setup
    say(f"[setup] physical unit '{approved_setup.get('physical_unit')}' -> '{unit}' (the official-statistics driver's unit)")
    aligned = {**approved_setup, "physical_unit": unit,
               "unit_note": f"aligned from '{approved_setup.get('physical_unit')}' to the official driver's unit"}
    checkpoints.write_json(checkpoints.setup_approved_path(approved_setup["market_name"], root), aligned)
    return aligned


def _mark_sizing(prog: Progress, state: dict[str, Any]) -> None:
    """Data-layer nodes of the flowchart, from what the sizing actually used."""
    bucket = next(iter(state.get("drivers") or {}), None)
    q = ((state.get("quantities") or {}).get(bucket) or {}) if bucket else {}
    drv = (state.get("drivers") or {}).get(bucket) or {}
    n = len(q)
    official = drv.get("library_status") == "official_data"
    prog.set("PLAN", "done")
    prog.set("T1", "skipped")
    prog.set("T2", "done" if official else "skipped", f"{n}/{n} countries" if official else None)
    prog.set("T3", "done" if state.get("country_prices") else "skipped",
             f"customs prices {sum(1 for p in (state.get('country_prices') or {}).get('prices', {}).values() if p['method'].startswith('customs'))}"
             if state.get("country_prices") else None)
    prog.set("T4", "skipped")
    prog.set("T5", "skipped" if official else "done")
    prog.set("AIO", "skipped")
    for node in ("EXTRACT", "VERIFY", "REJECT", "LEDGER", "GAP"):
        prog.set(node, "done")
    prog.set("SIGNAL", "skipped")
    prog.set("PRICE", "done")
    prog.set("CALC", "done")
    prog.set("ROLL", "done", f"{n} countries → {len(state.get('region_totals') or {})} regions")
    prog.set("XCHK", "flagged" if any(abs(t.get("gap_pct") or 0) > 25 for t in state.get("triangulation") or [])
             else "done")


def run_agent(
    market_name: str,
    config: GenConfig,
    cache_path: Path | None = None,
    root: Path | None = None,
    max_passes: int | None = None,
    say: Callable[[str], None] = print,
    design: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = design_config.load(design)
    max_passes = max_passes or cfg["agent"]["max_passes"]
    llm = GenLLM(config, cache_path=cache_path)
    started = datetime.now(timezone.utc).isoformat()
    run_id = next_run_id(market_name, root)
    run_out = checkpoints.run_dir(market_name, root) / "runs" / run_id
    prog = Progress(run_out / "progress.mermaid")
    band = design_config.per_capita_band(cfg, market_name)
    say(f"[run] {run_id} · config {design_config.config_hash(cfg)}")

    prog.set("START", "done")
    prog.set("SCOPE", "running")
    say(f"[setup] {market_name}")
    approved_setup = setup.approve_setup(
        setup.run_setup_phase(market_name, config, cache_path=cache_path, root=root), root, approved_by=APPROVER,
    )
    say(f"[setup] {len(approved_setup['geography'])} countries, unit '{approved_setup['physical_unit']}', "
        f"base year {approved_setup['base_year']}")
    prog.set("SCOPE", "done", f"{len(approved_setup['geography'])} countries, {approved_setup['base_year']}")
    prog.set("STRAT", "done", "2A")
    for node in _UNUSED_NODES:
        prog.set(node, "skipped")

    passes: list[dict[str, Any]] = []
    states: dict[int, dict[str, Any]] = {}
    feedback: str | None = None
    for n in range(1, max_passes + 1):
        prog.set("A_DRV", "running")
        prog.set("PASS", "running", f"pass {n}/{max_passes}")
        draft = driver.run_driver_phase(approved_setup, config, cache_path=cache_path, root=root, feedback=feedback)
        bucket = draft["usage_buckets"][0]["name"]
        drv = draft["drivers"].get(bucket, {})
        record: dict[str, Any] = {"pass": n, "feedback_in": feedback, "driver": {
            k: drv.get(k) for k in ("driver_name", "unit", "final_demand_unit", "library_status")}}
        say(f"[pass {n}] driver: {drv.get('driver_name')} ({drv.get('final_demand_unit') or drv.get('unit')})")
        prog.set("A_DRV", "done")

        gate = review.product_gate(drv, market_name, llm)
        if gate:
            record.update(status="rejected_before_sizing", reason=gate, score=0.0)
            passes.append(record)
            say(f"[pass {n}] REJECTED before sizing: {gate}")
            feedback = f"{gate}. Use a driver that measures {market_name} itself."
            continue

        approved_setup = align_unit_to_official_driver(approved_setup, drv, root, say)
        approved_driver = driver.approve_driver(draft, root, approved_by=APPROVER)
        state = _size(approved_setup, approved_driver, config, cache_path, root)
        states[n] = {"state": state, "draft": draft}
        _mark_sizing(prog, state)

        led, facts = build_2a_ledger(state, run_id, n)
        result = checks.evaluate(cfg, state, led, facts, band)
        issues = review.deterministic_issues(state)
        critical = [i for i in issues if i["severity"] == "critical"]
        verdict = review.llm_review(llm, approved_setup, state, issues + [
            {"severity": f["severity"], "code": f["check"], "message": f["message"]}
            for f in result["hard"] + result["soft"]])
        plaus = plausibility.score(cfg, verdict["rubric_mean"], facts.get("per_capita_kg") or {}, band,
                                   result["verified_share"], result["soft"], verdict["rubric"].get("mix"))
        ok = plausibility.accepted(cfg, plaus, result["hard"]) and not critical
        record.update(
            status="accepted" if ok else "redo",
            global_usd_mn=state.get("total_market_value"),
            country_totals_usd_mn=state.get("country_totals"),
            evidence_quality=(state.get("validation") or {}).get("evidence_quality", {}),
            verified_value_share=result["verified_share"],
            hard_flags=result["hard"], soft_flags=result["soft"], issues=issues, review=verdict,
            plausibility=plaus, score=plaus["score"], ledger_numbers=len(led.rows),
        )
        passes.append(record)
        say(f"[pass {n}] global ${state.get('total_market_value', 0):,.1f}M | rubric {verdict['rubric_mean']:.1f}/10 | "
            f"verified value {result['verified_share']:.0%} | hard {len(result['hard'])} soft {len(result['soft'])} "
            f"critical {len(critical)} | plausibility {plaus['score']:.2f}/10 | {'ACCEPTED' if ok else 'redo'}")
        for f in result["hard"] + result["soft"]:
            say(f"           [{f['severity']}] {f['check']}: {f['message']}")
        for p in verdict["problems"]:
            say(f"           - {p}")
        prog.set("CHECKS", "failed" if result["hard"] else "done")
        prog.set("REVIEW", "done")
        prog.set("SCORE", "done", f"{plaus['score']:.2f}/10")
        if ok:
            break
        prev = next((p for p in reversed(passes[:-1]) if p.get("global_usd_mn") is not None), None)
        if prev and prev["driver"] == record["driver"] and math.isclose(prev["global_usd_mn"], record["global_usd_mn"],
                                                                         rel_tol=1e-9):
            say(f"[pass {n}] identical to pass {prev['pass']} (same driver, same total) -- a redo can't change it; stopping")
            break
        feedback = review.feedback_for_next_pass(verdict, critical)
        prog.set("REDO", "done", f"redo pass {n + 1}" if n < max_passes else "max passes")

    best_rec = plausibility.best_pass(passes)
    if best_rec is None:
        raise RuntimeError(f"2A agent: no pass for '{market_name}' produced a driver for the market's own product -- "
                           + "; ".join(p.get("reason", "") for p in passes))
    best = {"pass": best_rec["pass"], "score": best_rec["score"], **states[best_rec["pass"]]}
    prog.set("BEST", "done", f"pass {best['pass']}")

    # Re-approve and re-write the kept pass, so the run folder matches it.
    driver.approve_driver(best["draft"], root, approved_by=APPROVER)
    write_sizing_outputs(best["state"], root)
    say(f"[keep] pass {best['pass']} (plausibility {best['score']:.2f}/10)")

    say("[segments] proposing segments and per-country shares")
    prog.set("SEG", "running")
    segmentation.approve_segments(
        segmentation.run_segments_phase(market_name, config, cache_path=cache_path, root=root), root,
        approved_by=APPROVER,
    )
    report, _, _ = segmentation.run_report_phase(market_name, root, write=False)
    prog.set("SEG", "done", f"{len(report.get('segmentation') or [])} dimensions")

    state = best["state"]
    led, facts = build_2a_ledger(state, run_id, best["pass"], report=report)
    final = checks.evaluate(cfg, state, led, facts, band, report=report)

    pub = None
    if cfg["publisher"]["enabled"]:
        pub = _publish(led, facts, state, cfg)
        prog.set("PUB", "done")
        prog.set("BLEND", "done", f"k = {pub['k']:.3f}")
    else:
        prog.set("PUB", "done", "off")
        prog.set("BLEND", "skipped")

    hard = final["hard"]
    prog.set("CHECKS", "failed" if hard else "done", f"hard {len(hard)} / soft {len(final['soft'])}")
    outputs: dict[str, str] = {}
    if hard:
        prog.set("STOP", "failed")
        prog.set("OUT", "skipped")
        say(f"[checks] {len(hard)} HARD failure(s) -- nothing written to outputs")
    else:
        prog.set("STOP", "skipped")
        report_json, report_xlsx = segmentation.write_report(report, market_name, root)
        paths = led.write(run_out)
        shutil.copyfile(report_xlsx, run_out / "market_tables.xlsx")
        doc = write_documentation(led, run_out / "documentation.md", {
            "market": market_name, "base_year": approved_setup.get("base_year"),
            "config_hash": design_config.config_hash(cfg),
            "headline": f"{fmt_usd(state.get('total_market_value', 0) * 1e6)} global ({market_name}, "
                        f"{approved_setup.get('base_year')}), weakest input of the global total: "
                        f"{facts['global'].weakest_input_label if facts.get('global') else 'n/a'}",
            "notes": _doc_notes(state, pub)})
        outputs = {"report_json": str(report_json), "report_xlsx": str(report_xlsx), "documentation": str(doc),
                   **{f"ledger_{k}": str(v) for k, v in paths.items()}, "market_tables": str(run_out / "market_tables.xlsx")}
        prog.set("OUT", "done", f"{len(led.rows)} ledger numbers")

    summary = {
        "market_name": market_name,
        "approach": "2A",
        "run_id": run_id,
        "config_hash": design_config.config_hash(cfg),
        "model": config.model,
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "status": "failed_hard_checks" if hard else "written",
        "kept_pass": best["pass"],
        "accepted": any(p["status"] == "accepted" for p in passes),
        "global_usd_mn": state.get("total_market_value"),
        "regions_usd_mn": state.get("region_totals"),
        "countries_usd_mn": state.get("country_totals"),
        "verified_value_share": final["verified_share"],
        "hard_flags": hard,
        "soft_flags": final["soft"],
        "publisher": pub,
        "published_sizes_usd_mn": checks.published_sizes(state),
        "pricing": {k: v for k, v in (state.get("country_prices") or {}).items()
                    if k in ("basis", "basis_note", "hs", "curve", "outliers_dropped")},
        "ledger": {"numbers": len(led.rows), "labels": led.label_counts(), "mismatches": led.mismatches},
        "segments": [
            {"dimension": t.get("dimension"), "global_values_usd_mn": t.get("global_values")}
            for t in report.get("segmentation") or []
        ],
        "quality_flags": state.get("quality_flags") or [],
        "passes": passes,
        "outputs": outputs,
    }
    checkpoints.write_json(summary_path(market_name, root), summary)
    write_summary_md(summary, run_out / "summary.md")
    return summary


def _publish(led: Ledger, facts: dict[str, Any], state: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """Publisher (system.md §12): price-level factor k, written to the ledger
    beside the raw bottom-up figure. Quantities are never touched."""
    b = float(state.get("total_market_value") or 0.0)
    pub = design_blend(b, checks.published_sizes(state), cfg["publisher"]["weight_bottom_up"])
    glob = facts.get("global")
    if glob is not None and pub["n_published"]:
        k = led.fact("Publisher price-level factor k", pub["k"], "ratio", "global", "FAC", "derived",
                     module="publisher", weakest_input_label="source_found_unverified",
                     note=f"P = {pub['weight_bottom_up']:.2f}·B + {pub['weight_published']:.2f}·C; C = median of "
                          f"{pub['n_published']} published sizes ${pub['published_median_usd_mn']:,.0f}M "
                          "(source_found_unverified)")
        led.scale("Value, global (published, after k)", glob, k,
                  note="raw bottom-up value is the input row; k applied to price only")
    return pub


def _doc_notes(state: dict[str, Any], pub: dict[str, Any] | None) -> list[str]:
    notes = []
    table = state.get("country_prices")
    if table:
        notes.append(f"Value basis: {table['basis']} ({table['basis_note']}); HS {', '.join(table['hs']['codes'])}"
                     + (f"; excluded: {table['hs'].get('excluded')}" if table['hs'].get('excluded') else ""))
    if pub:
        notes.append(f"Publisher: raw bottom-up {fmt_usd(pub['bottom_up_usd_mn'] * 1e6)} → published "
                     f"{fmt_usd(pub['value_usd_mn'] * 1e6)} (k = {pub['k']:.3f})")
    return notes


def write_summary_md(s: dict[str, Any], path: Path) -> Path:
    total = float(s.get("global_usd_mn") or 0.0)
    L = [f"# {s['market_name']} — 2A run {s['run_id']}", "",
         f"**Headline:** {fmt_usd(total * 1e6)} (raw bottom-up, base year), status **{s['status']}**, "
         f"kept pass {s['kept_pass']} of {len(s['passes'])} — "
         + ("accepted by review" if s["accepted"] else "best available, NOT accepted (plausibility below threshold)"),
         "", f"Evidence coverage: {fmt_pct(s['verified_value_share'])} of value rests on verified_observed inputs. "
         f"Ledger: {s['ledger']['numbers']} numbers ({', '.join(f'{k} {v}' for k, v in s['ledger']['labels'].items() if v)}). "
         f"Config `{s['config_hash']}`, model {s['model']}.", ""]
    if s.get("publisher"):
        p = s["publisher"]
        L += [f"**Publisher:** raw {fmt_usd(p['bottom_up_usd_mn'] * 1e6)} → {fmt_usd(p['value_usd_mn'] * 1e6)} "
              f"(k = {p['k']:.3f}, w = {p['weight_bottom_up']:.2f}, n = {p['n_published']})", ""]
    if s.get("published_sizes_usd_mn"):
        L += ["Published sizes seen (context only, never used to adjust): "
              + ", ".join(fmt_usd(v * 1e6) for v in s["published_sizes_usd_mn"]), ""]
    L += ["## Flags", ""]
    flags = s["hard_flags"] + s["soft_flags"]
    L += [f"- **{f['severity']}** `{f['check']}` {f['message']}" for f in flags] or ["- none"]
    L += ["", "## Passes", "", "| Pass | Status | Global | Rubric | Verified value | Hard / soft | Plausibility | Driver |",
          "|---|---|---|---|---|---|---|---|"]
    for p in s["passes"]:
        drv = (p.get("driver") or {}).get("driver_name")
        if p["status"] == "rejected_before_sizing":
            L.append(f"| {p['pass']} | rejected before sizing | — | — | — | — | 0 | {drv} |")
            continue
        L.append(f"| {p['pass']} | {p['status']} | {fmt_usd(p['global_usd_mn'] * 1e6)} | {p['review']['rubric_mean']:.1f} | "
                 f"{fmt_pct(p['verified_value_share'])} | {len(p['hard_flags'])} / {len(p['soft_flags'])} | "
                 f"{p['plausibility']['score']:.2f} | {drv} |")
    kept = next((p for p in s["passes"] if p["pass"] == s["kept_pass"]), None)
    if kept and kept.get("plausibility"):
        comps = kept["plausibility"]["components"]
        L += ["", "Kept pass plausibility components (0-10): " + ", ".join(
            f"{k} {'n/a' if v is None else f'{v:.1f}'}" for k, v in comps.items()), ""]
        if kept["review"].get("rubric"):
            L.append("Reviewer rubric: " + ", ".join(f"{k} {v:g}" for k, v in kept["review"]["rubric"].items()))
        L += [f"- {p}" for p in kept["review"].get("problems") or []]
    L += ["", "## Regions", ""] + [f"- {r}: {fmt_usd(v * 1e6)}" for r, v in (s.get("regions_usd_mn") or {}).items()]
    L += ["", "## Countries", ""] + [f"- {c}: {fmt_usd(v * 1e6)}" for c, v in
                                     sorted((s.get("countries_usd_mn") or {}).items(), key=lambda kv: -kv[1])]
    if s.get("segments"):
        L += ["", "## Segments (top-down)", ""]
        for seg in s["segments"]:
            vals = seg.get("global_values_usd_mn") or {}
            tot = sum(vals.values()) or 1.0
            L.append(f"- **{seg['dimension']}**: " + ", ".join(f"{k} {fmt_usd(v * 1e6)} ({fmt_pct(v / tot)})"
                                                               for k, v in vals.items()))
    if s.get("outputs"):
        L += ["", "## Outputs", ""] + [f"- {k}: `{v}`" for k, v in s["outputs"].items()]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L), encoding="utf-8")
    return path

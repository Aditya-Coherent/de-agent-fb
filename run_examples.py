"""Run every implemented approach on a set of example markets, end to end,
and collect the results in one place for review.

Per market:
    1  Desk Research  -> generate_synthetic_input.py --from-db (subprocess)
    2A Demand         -> setup, driver, size, segments, report (in-process)
    2B Demand         -> setup, taxonomy, size, segments, report (in-process)
    3  Supply-Side    -> compare_demand_supply.py --method both --discover (subprocess)

Every approval gate is auto-approved with the draft AS PROPOSED, with one
exception: in 2B, use cases flagged out_of_scope by the scope check are
dropped (what a reviewer would do) and that is recorded in the results. This
is an unattended demo run; a production run should have a human review each
draft.

Results go to approaches/examples/:
    <market-slug>/result.json      everything collected for that market
    <market-slug>/*.xlsx           copies of each approach's workbook
    EXAMPLES_SUMMARY.md / .json / .xlsx   side-by-side comparison

Usage:
    python run_examples.py                 # all default markets, 2 at a time
    python run_examples.py --market "Olive Oil" --vertical "Food & Beverages"
    python run_examples.py --summary-only  # rebuild the summary from result.json files
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

RESULTS_DIR = ROOT / "approaches" / "examples"
CACHE = ROOT / "output" / ".gen_cache.db"

DEFAULT_MARKETS = [
    ("Olive Oil", "Food & Beverages"),
    ("Disposable Medical Gloves", "Healthcare"),
    ("Urea Fertilizer", "Agriculture & Chemicals"),
    ("Automotive Tires", "Automotive"),
    ("Smartphones", "Consumer Electronics"),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(name: str) -> str:
    from me_engine.demandUseCaseCountry_approach2B.checkpoints import slugify

    return slugify(name)


def _timed(fn: Callable[[], dict[str, Any]], log: list[str], label: str) -> dict[str, Any]:
    start = time.time()
    log.append(f"[{_now()}] {label}: start")
    try:
        out = fn()
        out.setdefault("status", "ok")
    except Exception as exc:  # noqa: BLE001 -- one approach failing must not stop the others
        out = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()[-3000:]}
    out["minutes"] = round((time.time() - start) / 60, 1)
    log.append(f"[{_now()}] {label}: {out['status']} in {out['minutes']} min")
    return out


def _copy(src: Path, dest_dir: Path, name: str) -> str | None:
    if src and Path(src).exists():
        dest = dest_dir / name
        shutil.copy2(src, dest)
        return str(dest.relative_to(ROOT))
    return None


def _report_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "global_usd_mn": report["geography"]["global"],
        "regions_usd_mn": report["geography"]["regions"],
        "countries_usd_mn": report["geography"]["countries"],
        "segments": [
            {
                "dimension": t["dimension"], "method": t["method"],
                "evidence_status": t.get("evidence_status"),
                "taxonomy_source": t.get("taxonomy_source"),
                "global_values_usd_mn": t["global_values"],
            }
            for t in report["segmentation"]
        ],
        "consistency_checks_passed": all(c["passed"] for c in report.get("consistency_checks", [])),
    }


def _sizing_quality(state: dict[str, Any]) -> dict[str, Any]:
    eq = (state.get("validation") or {}).get("evidence_quality") or {}
    return {
        "evidence_quality_pct": {
            "verified_observed": eq.get("observed_pct"), "derived": eq.get("derived_pct"),
            "source_found_unverified": eq.get("source_found_unverified_pct"), "assumption": eq.get("assumption_pct"),
        },
        "quality_flags": [f"[{f.get('severity')}] {f.get('code')}: {f.get('message')}" for f in state.get("quality_flags") or []],
    }


# --------------------------------------------------------------------------- #
# Approaches
# --------------------------------------------------------------------------- #
def run_desk(market: str, out_dir: Path) -> dict[str, Any]:
    slug = _slug(market)
    sheet = ROOT / "agentGeneratedInputSheet" / f"Data File - {market} (examples).xlsx"
    state_path = ROOT / "output" / "example_runs" / slug / "desk_state.json"
    proc = subprocess.run(
        [sys.executable, "generate_synthetic_input.py", "--market", market, "--from-db",
         "--output", str(sheet), "--save-state", str(state_path)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600,
    )
    (out_dir / "desk_research.log").write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")
    if proc.returncode != 0 or not state_path.exists():
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-5:]
        return {"status": "failed", "error": " | ".join(tail)}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    return {
        "global_usd_mn": state.get("global_anchor_2025", state.get("global_anchor_2026")),
        "anchor_year": 2025 if "global_anchor_2025" in state else 2026,
        "global_cagr": state.get("global_cagr"),
        "regional": state.get("regional"),
        "reward": state.get("reward"),
        "iterations": state.get("iterations"),
        "input_sheet": _copy(sheet, out_dir, "1_desk_research_input_sheet.xlsx"),
    }


def run_2a(market: str, out_dir: Path, cfg: Any) -> dict[str, Any]:
    from me_engine.demandCountry_approach2A import checkpoints, driver, segmentation, setup
    from me_engine.demandCountry_approach2A.graph import run_sizing_phase
    from me_engine.demandCountry_approach2A.engine.buildup_writer import build_report_dict, write_buildup_workbook

    approved_setup = setup.approve_setup(setup.run_setup_phase(market, cfg, cache_path=CACHE))
    approved_driver = driver.approve_driver(driver.run_driver_phase(approved_setup, cfg, cache_path=CACHE))
    state = run_sizing_phase(approved_setup, approved_driver, cfg, cache_path=CACHE, source="examples")

    prefix = checkpoints.sizing_output_prefix(market)
    report = build_report_dict(state)
    report["methodology"] = "demand_side_country_total_2a"
    report["country_region"] = state.get("country_region", {})
    report["region_totals_usd_mn"] = state.get("region_totals", {})
    checkpoints.write_json(prefix.with_suffix(".json"), report)
    sizing_xlsx = write_buildup_workbook(state, prefix.with_suffix(".xlsx"))

    segmentation.approve_segments(segmentation.run_segments_phase(market, cfg, cache_path=CACHE))
    final, _json, xlsx = segmentation.run_report_phase(market)
    driver_def = next(iter(approved_driver["drivers"].values()), {})
    return {
        "setup": {k: approved_setup[k] for k in ("scope", "geography", "base_year", "physical_unit")},
        "driver": {k: driver_def.get(k) for k in ("driver_name", "unit", "final_demand_unit", "conversion_formula")},
        **_report_summary(final),
        **_sizing_quality(state),
        "workbooks": {
            "market": _copy(xlsx, out_dir, "2A_market.xlsx"),
            "sizing_buildup": _copy(sizing_xlsx, out_dir, "2A_sizing_buildup.xlsx"),
        },
        "run_folder": str(checkpoints.run_dir(market)),
    }


def _drop_out_of_scope(draft: dict[str, Any]) -> list[str]:
    flags = draft.get("scope_validation_flags") or {}
    dropped = [n for n, f in flags.items() if (f or {}).get("classification") == "out_of_scope"]
    if dropped:
        draft["usage_buckets"] = [b for b in draft.get("usage_buckets") or [] if b.get("name") not in dropped]
    return dropped


def run_2b(market: str, out_dir: Path, cfg: Any) -> dict[str, Any]:
    from me_engine.demandUseCaseCountry_approach2B import checkpoints, run_lock, segmentation, setup, taxonomy
    from me_engine.demandUseCaseCountry_approach2B.buildup_writer import write_buildup_json, write_buildup_workbook
    from me_engine.demandUseCaseCountry_approach2B.graph import run_sizing_phase

    approved_setup = setup.approve_setup(setup.run_setup_phase(market, cfg, cache_path=CACHE))
    draft = taxonomy.run_taxonomy_phase(approved_setup, cfg, cache_path=CACHE)
    dropped = _drop_out_of_scope(draft)
    approved_tax = taxonomy.approve_taxonomy(draft)

    lock = run_lock.acquire_size_lock(market, source="examples")
    try:
        state = run_sizing_phase(approved_setup, approved_tax, cfg, cache_path=CACHE, source="examples")
    finally:
        lock.release()
    prefix = checkpoints.sizing_output_prefix(market)
    write_buildup_json(state, prefix.with_suffix(".json"))
    sizing_xlsx = write_buildup_workbook(state, prefix.with_suffix(".xlsx"))

    segmentation.approve_segments(segmentation.run_segments_phase(market, cfg, cache_path=CACHE))
    final, _json, xlsx = segmentation.run_report_phase(market)
    return {
        "setup": {k: approved_setup[k] for k in ("scope", "geography", "base_year", "physical_unit")},
        "use_cases": [b["name"] for b in approved_tax["usage_buckets"]],
        "use_cases_dropped_out_of_scope": dropped,
        "related_demand_usd_mn": state.get("related_demand_value"),
        **_report_summary(final),
        **_sizing_quality(state),
        "workbooks": {
            "market": _copy(xlsx, out_dir, "2B_market.xlsx"),
            "sizing_buildup": _copy(sizing_xlsx, out_dir, "2B_sizing_buildup.xlsx"),
        },
        "run_folder": str(checkpoints.run_dir(market)),
    }


def run_supply(market: str, out_dir: Path, desk_value: float | None) -> dict[str, Any]:
    report_path = ROOT / "output" / "supply_side" / f"{_slug(market)}-examples.json"
    # Company list from vendor intelligence (existing report, or a fresh run of
    # vendor_intelligence/); quick LLM discovery only if that fails.
    cmd = [sys.executable, "compare_demand_supply.py", "--market", market, "--method", "both",
           "--vendor-intel", "--save-report", str(report_path)]
    if desk_value:
        cmd += ["--desk-research-value", str(desk_value)]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
    (out_dir / "supply_side.log").write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")
    if not report_path.exists():
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-5:]
        return {"status": "failed", "error": " | ".join(tail)}
    rep = json.loads(report_path.read_text(encoding="utf-8"))
    company = rep.get("company_based") or {}
    evidence = rep.get("evidence_based") or {}
    shares: dict[str, float] = {}
    for c in company.get("country_breakdown") or []:
        for name, share in (c.get("company_shares") or {}).items():
            shares[name] = shares.get(name, 0.0) + share * (c.get("country_total_usd_mn") or 0.0)
    total = company.get("total_usd_mn") or 0.0
    top = sorted(shares.items(), key=lambda kv: kv[1], reverse=True)[:10]
    return {
        "company_method_global_usd_mn": company.get("total_usd_mn"),
        "evidence_method_global_usd_mn": evidence.get("total_usd_mn") if evidence.get("has_real_data") else None,
        "top_companies_global_share": [{"company": n, "share": round(v / total, 4) if total else None} for n, v in top],
        "countries": company.get("countries"),
        "flags": [f"{f.get('code')}: {f.get('message')}" for f in
                  (company.get("quality_flags") or []) + (evidence.get("quality_flags") or [])],
        "report_json": _copy(report_path, out_dir, "3_supply_side.json"),
        "company_list_source": rep.get("company_list_source"),
        "vendor_intel_coverage_pct": (rep.get("vendor_intel_coverage") or {}).get("estimated_coverage_pct"),
    }


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run_market(market: str, vertical: str, approaches: list[str]) -> dict[str, Any]:
    from me_engine.generation.config import GenConfig

    cfg = GenConfig.load(ROOT)
    out_dir = RESULTS_DIR / _slug(market)
    out_dir.mkdir(parents=True, exist_ok=True)
    result_path = out_dir / "result.json"
    result: dict[str, Any] = {"market": market, "vertical": vertical, "started_at": _now()}
    log: list[str] = []

    def save() -> None:
        # Another run may have updated this market meanwhile: merge into the
        # file as it is now, adding only what this run produced.
        current = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
        current.update({k: v for k, v in result.items() if k != "log"})
        current["log"] = [line for line in current.get("log", []) if line not in log] + log
        result_path.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")

    if "desk" in approaches:
        result["1_desk_research"] = _timed(lambda: run_desk(market, out_dir), log, "1 Desk Research")
        save()
    if "2a" in approaches:
        result["2A_demand_country_total"] = _timed(lambda: run_2a(market, out_dir, cfg), log, "2A Demand")
        save()
    if "2b" in approaches:
        result["2B_demand_usecase_country"] = _timed(lambda: run_2b(market, out_dir, cfg), log, "2B Demand")
        save()
    if "supply" in approaches:
        saved = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
        desk_value = (result.get("1_desk_research") or saved.get("1_desk_research") or {}).get("global_usd_mn") or (result.get("1_desk_research") or saved.get("1_desk_research") or {}).get("global_usd_mn_2026")
        result["3_supply_side"] = _timed(lambda: run_supply(market, out_dir, desk_value), log, "3 Supply-Side")
        save()
    result["finished_at"] = _now()
    save()
    return result


def _fmt(v: Any) -> str:
    return f"{v:,.0f}" if isinstance(v, (int, float)) else "—"


def build_summary() -> Path:
    rows = []
    for path in sorted(RESULTS_DIR.glob("*/result.json")):
        r = json.loads(path.read_text(encoding="utf-8"))
        d, a, b, s = (r.get(k) or {} for k in ("1_desk_research", "2A_demand_country_total", "2B_demand_usecase_country", "3_supply_side"))
        rows.append({
            "market": r["market"], "vertical": r.get("vertical"), "folder": str(path.parent.relative_to(ROOT)),
            "desk_value": d.get("global_usd_mn", d.get("global_usd_mn_2026")), "desk_year": d.get("anchor_year", 2026 if "global_usd_mn_2026" in d else None), "desk_status": d.get("status"),
            "2a_global": a.get("global_usd_mn"), "2a_base_year": (a.get("setup") or {}).get("base_year"), "2a_status": a.get("status"),
            "2b_global": b.get("global_usd_mn"), "2b_base_year": (b.get("setup") or {}).get("base_year"), "2b_status": b.get("status"),
            "supply_company": s.get("company_method_global_usd_mn"), "supply_evidence": s.get("evidence_method_global_usd_mn"),
            "supply_status": s.get("status"), "result": r,
            # A critical quality flag (e.g. a price-unit mismatch) means the total is not usable as-is.
            "2a_critical": [f for f in a.get("quality_flags", []) if f.startswith("[critical]")],
            "2b_critical": [f for f in b.get("quality_flags", []) if f.startswith("[critical]")],
        })

    (RESULTS_DIR / "EXAMPLES_SUMMARY.json").write_text(
        json.dumps([{k: v for k, v in row.items() if k != "result"} for row in rows], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    md = [
        "# Five-Industry Examples — All Approaches",
        "",
        f"_Generated {_now()} by `run_examples.py`. All values are USD Mn. Every approval gate was "
        "auto-approved as drafted (unattended demo run), so treat these as first drafts, not reviewed estimates._",
        "",
        "| Market | Vertical | 1 Desk Research | 2A Demand: country total | 2B Demand: use case × country | 3 Supply: companies | 3 Supply: web evidence |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        def cell(value, status, year=None, critical=None):
            if status in ("failed", "pending rerun"):
                return status
            text = _fmt(value) + (f" ({year})" if year and value is not None else "")
            return text + (f" ⚠ {len(critical)} critical flag(s)" if critical else "")
        md.append(
            f"| {row['market']} | {row['vertical']} | {cell(row['desk_value'], row['desk_status'], row['desk_year'])} | "
            f"{cell(row['2a_global'], row['2a_status'], row['2a_base_year'], row['2a_critical'])} | "
            f"{cell(row['2b_global'], row['2b_status'], row['2b_base_year'], row['2b_critical'])} | "
            f"{cell(row['supply_company'], row['supply_status'])} | {cell(row['supply_evidence'], row['supply_status'])} |"
        )
    md += ["", "⚠ = the run raised critical quality flags (for example a price quoted per box applied to a count of single "
           "items); that total is not usable until the flagged inputs are corrected. Details under each market.",
           "", "2C (product type × application × country) is not implemented, so it has no column.", ""]

    for row in rows:
        r = row["result"]
        md += [f"## {r['market']} ({r.get('vertical')})", "", f"Files: `{row['folder']}/`", ""]
        for key, title in (("2A_demand_country_total", "2A"), ("2B_demand_usecase_country", "2B")):
            a = r.get(key) or {}
            if a.get("status") != "ok":
                md.append(f"- **{title}:** {a.get('status', 'not run')} {a.get('error', '')}".rstrip())
                continue
            regions = ", ".join(f"{k} {_fmt(v)}" for k, v in (a.get("regions_usd_mn") or {}).items())
            eq = a.get("evidence_quality_pct") or {}
            md.append(f"- **{title}:** global {_fmt(a.get('global_usd_mn'))} (base year {a['setup']['base_year']}); {regions}")
            if key.startswith("2B"):
                md.append(f"  - Use cases: {', '.join(a.get('use_cases', []))}")
                if a.get("use_cases_dropped_out_of_scope"):
                    md.append(f"  - Dropped as out of scope: {', '.join(a['use_cases_dropped_out_of_scope'])}")
            else:
                md.append(f"  - Driver: {(a.get('driver') or {}).get('driver_name')}")
            md.append(
                f"  - Evidence: verified {eq.get('verified_observed') or 0:.0f}%, derived {eq.get('derived') or 0:.0f}%, "
                f"unverified {eq.get('source_found_unverified') or 0:.0f}%, assumption {eq.get('assumption') or 0:.0f}%"
            )
            for seg in a.get("segments", []):
                vals = ", ".join(f"{k} {_fmt(v)}" for k, v in seg["global_values_usd_mn"].items())
                md.append(f"  - {seg['dimension']} ({seg['method']}, {seg.get('evidence_status')}): {vals}")
            for f in a.get("quality_flags", []):
                if f.startswith("[critical]"):
                    md.append(f"  - ⚠ {f[:400]}")
            warnings = [f for f in a.get("quality_flags", []) if f.startswith("[warning]") or f.startswith("[error]")]
            if warnings:
                md.append(f"  - Quality warnings: {len(warnings)} (see result.json)")
        d = r.get("1_desk_research") or {}
        md.append(f"- **1 Desk Research:** {d.get('status', 'not run')} — global {_fmt(d.get('global_usd_mn', d.get('global_usd_mn_2026')))} ({d.get('anchor_year', 2026)})"
                  + (f", CAGR {d['global_cagr']:.1%}" if isinstance(d.get("global_cagr"), (int, float)) else "")
                  + (f" ({d['error']})" if d.get("error") else ""))
        s = r.get("3_supply_side") or {}
        top = ", ".join(f"{c['company']} {c['share']:.0%}" for c in s.get("top_companies_global_share", [])[:5] if c.get("share") is not None)
        md.append(f"- **3 Supply-Side:** {s.get('status', 'not run')} — companies {_fmt(s.get('company_method_global_usd_mn'))}, "
                  f"web evidence {_fmt(s.get('evidence_method_global_usd_mn'))}" + (f"; top shares: {top}" if top else "")
                  + (f" ({s['error']})" if s.get("error") else ""))
        md.append("")
    md_path = RESULTS_DIR / "EXAMPLES_SUMMARY.md"
    md_path.write_text("\n".join(md), encoding="utf-8")

    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Comparison"
    header = ["Market", "Vertical", "1 Desk Research (USD Mn)", "2A Demand (USD Mn)", "2A base year",
              "2B Demand (USD Mn)", "2B base year", "3 Supply companies (USD Mn)", "3 Supply web evidence (USD Mn)", "Folder"]
    ws.append(header)
    for c in ws[1]:
        c.font = Font(bold=True)
    for row in rows:
        ws.append([row["market"], row["vertical"], row["desk_value"], row["2a_global"], row["2a_base_year"],
                   row["2b_global"], row["2b_base_year"], row["supply_company"], row["supply_evidence"], row["folder"]])
    for col in "CDFHI":
        for cell in ws[col][1:]:
            cell.number_format = "#,##0"
    for i, w in enumerate([26, 24, 22, 20, 12, 20, 12, 24, 26, 40], start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    wb.save(RESULTS_DIR / "EXAMPLES_SUMMARY.xlsx")
    return md_path


def main() -> int:
    p = argparse.ArgumentParser(description="Run all implemented approaches on example markets.")
    p.add_argument("--market", default=None)
    p.add_argument("--vertical", default="")
    p.add_argument("--approaches", default="desk,2a,2b,supply")
    p.add_argument("--parallel", type=int, default=2, help="markets run at the same time (default 2)")
    p.add_argument("--summary-only", action="store_true")
    args = p.parse_args()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    approaches = [a.strip().lower() for a in args.approaches.split(",") if a.strip()]

    if args.summary_only:
        print(f"Summary: {build_summary()}")
        return 0
    if args.market:
        run_market(args.market, args.vertical, approaches)
        print(f"Summary: {build_summary()}")
        return 0

    # One subprocess per market so markets can run side by side.
    pending = list(DEFAULT_MARKETS)
    running: list[tuple[str, subprocess.Popen]] = []
    logs_dir = RESULTS_DIR / "_logs"
    logs_dir.mkdir(exist_ok=True)
    while pending or running:
        while pending and len(running) < args.parallel:
            market, vertical = pending.pop(0)
            log_file = open(logs_dir / f"{_slug(market)}.log", "w", encoding="utf-8")
            proc = subprocess.Popen(
                [sys.executable, __file__, "--market", market, "--vertical", vertical, "--approaches", args.approaches],
                cwd=ROOT, stdout=log_file, stderr=subprocess.STDOUT,
            )
            running.append((market, proc))
            print(f"[{_now()}] started {market}", flush=True)
        for market, proc in list(running):
            if proc.poll() is not None:
                running.remove((market, proc))
                print(f"[{_now()}] finished {market} (exit {proc.returncode})", flush=True)
        time.sleep(10)
    print(f"Summary: {build_summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
run_query.py — Interactive Vendor Intelligence query runner.

Run one market query at a time: type a query, get a CSV, then run another or quit.

Usage:
  python run_query.py
  python run_query.py --query "Atomic Clock Market"
  python run_query.py --query "Atomic Clock Market" --country Germany
  python run_query.py --file my_queries.txt
  python run_query.py --profile recall

Output (default demo folder — override with MARKET_QUERY_OUTPUT_DIR in .env):
  output/demo/<slug>.csv
  output/demo/<slug>.json
  output/demo/session_log.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

def _refresh_output_dir() -> Path:
    global OUTPUT_DIR
    from vendor_intel.pipeline.output_paths import market_query_output_dir

    OUTPUT_DIR = market_query_output_dir(ROOT)
    return OUTPUT_DIR


OUTPUT_DIR = ROOT / "output" / "demo"

# Run-wide defaults set from the CLI (used by batch / interactive modes too).
_RUN_DEFAULTS: dict = {"time_budget_min": None, "target_coverage": 0.75}


def _validate_env() -> list[str]:
    warnings: list[str] = []
    mock = os.getenv("USE_MOCK_DATA", "true").strip().lower()
    if mock not in ("false", "0", "no", "off"):
        warnings.append(
            "USE_MOCK_DATA is not 'false' — results will be empty/fake.\n"
            "  Fix: open .env and set USE_MOCK_DATA=false"
        )
    provider = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
    key_map = {
        "anthropic": "ANTHROPIC_API_KEY",
        "opencode": "OPENCODE_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "groq": "GROQ_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }
    key_var = key_map.get(provider)
    if key_var and not os.getenv(key_var, "").strip():
        warnings.append(
            f"LLM_PROVIDER={provider} but {key_var} is not set.\n"
            f"  Fix: open .env and add {key_var}=your_key_here"
        )
    return warnings


def _slug(query: str, country: str) -> str:
    text = f"{query}_{country}".lower()
    return "".join(c if c.isalnum() else "_" for c in text).strip("_")[:80]


def _parse_sections(raw: str | None) -> list[str]:
    """Parse a CEO section list. Separator is ';' if present, else ','."""
    if not raw or not raw.strip():
        return []
    sep = ";" if ";" in raw else ","
    return [s.strip() for s in raw.split(sep) if s.strip()]


def _load_seeds_file(path: str | None) -> list[tuple[str, str | None]]:
    """Read must-have companies from a file. Each line is one company, optionally with an
    intended section after a '|':  'Phillips 66 | Recovered Oil / Base Oil Downstream Offtake'.
    Returns [(name, section_or_None)]. '#' comments ignored."""
    if not path or not path.strip():
        return []
    p = Path(path)
    if not p.exists():
        print(f"  WARNING: seeds file not found: {path}")
        return []
    rows: list[tuple[str, str | None]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "|" in line:
            name, sec = line.split("|", 1)
            name, sec = name.strip(), sec.strip()
            if name:
                rows.append((name, sec or None))
        else:
            for n in _parse_sections(line):  # legacy: ';'/','-separated names, no section
                rows.append((n, None))
    return rows


# Generic market words shared across unrelated markets — they must NOT drive a seed-file match
# (so 'Avocado Oil Market' never matches 'waste_oil_market_global' on shared 'oil/market/global').
_GENERIC_MARKET_TOKENS = {
    "market", "markets", "global", "the", "and", "for", "oil", "gas", "services", "service",
    "company", "companies", "group", "systems", "system", "solutions", "solution", "industry",
    "sector", "world", "international", "based", "products", "product", "equipment", "technology",
    "technologies",
}


def _auto_seed_names(query: str, country: str) -> list[tuple[str, str | None]]:
    """Find the curated seed file for this market by its DISTINCTIVE market words (robust to
    phrasing like 'Brazil Rupture Disc Market' vs 'Rupture Disc Market | Brazil', but strict so
    'Avocado Oil' never matches 'Waste Oil' on the shared generic word 'oil')."""
    seeds_dir = ROOT / "queries" / "seeds"
    if not seeds_dir.exists():
        return []
    run_tokens = {t for t in _slug(query, country).split("_") if len(t) > 2}
    run_distinct = run_tokens - _GENERIC_MARKET_TOKENS
    if not run_distinct:
        return []
    best_file, best_n = None, 0
    for f in seeds_dir.glob("*.txt"):
        ftoks = {t for t in f.stem.split("_") if len(t) > 2}
        f_distinct = ftoks - _GENERIC_MARKET_TOKENS
        # require ALL of the file's distinctive market words to be present in the query
        if f_distinct and f_distinct <= run_distinct and len(f_distinct) > best_n:
            best_file, best_n = f, len(f_distinct)
    return _load_seeds_file(str(best_file)) if best_file else []


# Marker that introduces the desired functional categories inside a query string.
_FUNC_MARKER = re.compile(
    r"\b(?:functionality|sections?|include|company\s+types?|roles?|profiles?)\s*[:=\-]\s*(.+)$",
    re.I,
)


def _parse_input(raw: str) -> tuple[str, str, list[str]]:
    """Parse one detailed query string into (market, country, sections).

    Accepts, in one input box:
      - "Market | Country | Section A; Section B; Section C"
      - "Market | Country"
      - "Market ; functionality: Section A, Section B, Section C"
      - "Market | Country ; sections: A; B; C"
    The functionality/sections clause may use the keywords functionality / sections /
    include / company types / roles / profiles followed by ':' '=' or '-'.
    """
    raw = (raw or "").strip()
    sections: list[str] = []
    country = ""

    m = _FUNC_MARKER.search(raw)
    if m:
        sections = _parse_sections(m.group(1))
        raw = raw[: m.start()].strip().rstrip("|,;:- ").strip()

    if "|" in raw:
        parts = [p.strip() for p in raw.split("|")]
        market = parts[0]
        if len(parts) > 1 and parts[1]:
            country = parts[1]
        if len(parts) > 2 and not sections:
            sections = _parse_sections(parts[2])
    else:
        market = raw

    return market, (country or "global"), sections


def _load_settings(profile: str):
    from vendor_intel.placeholders.load_keys import apply_env_overrides
    from vendor_intel.config import Settings

    apply_env_overrides()
    settings = Settings.load()
    return settings.model_copy(
        update={
            "use_mock_data": False,
            "mock_mode": False,
            "pipeline_profile": profile,
            "pipeline_recall_mode": profile == "recall",
            "pipeline_use_ssc": True,
            "pipeline_min_export_confidence": 0.50,
        }
    )


def run_one_query(
    query: str,
    country: str,
    settings,
    *,
    enrich_limit: int | None = None,
    classify_limit: int | None = None,
    sections: list[str] | None = None,
    seeds: list[str] | None = None,
    out_name: str | None = None,
    exclude_segments: list[str] | None = None,
    market_definition: str = "",
    seed_hint: str = "",
    time_budget_min: float | None = None,
    target_coverage: float | None = None,
) -> dict:
    from vendor_intel.pipeline.orchestrator import (
        run_pipeline_sync,
        save_pipeline_csv,
        save_pipeline_docx,
        save_pipeline_xlsx,
    )

    query = query.strip()
    country = country.strip() or "global"
    if time_budget_min is None:
        time_budget_min = _RUN_DEFAULTS.get("time_budget_min")
    if target_coverage is None:
        target_coverage = _RUN_DEFAULTS.get("target_coverage")
    # Auto-load curated must-have companies for this market by convention, so the run
    # command stays clean (no seed list shown). File: queries/seeds/<market_slug>.txt
    # Each seed may carry an intended section ("Name | Section") which is honored at export.
    merged_seeds: list[str] = list(seeds or [])
    seed_sections: dict[str, str] = {}
    # Match the curated seed file by an explicit hint (e.g. the brief filename) when given —
    # the AI may rename the market in brief mode, which would otherwise break name matching.
    auto = _auto_seed_names(seed_hint, country) if seed_hint else []
    if not auto:
        auto = _auto_seed_names(query, country)
    for name, sec in auto:
        if name not in merged_seeds:
            merged_seeds.append(name)
        if sec:
            seed_sections[name] = sec
    if auto:
        print(
            f"  [seeds] auto-loaded {len(auto)} curated companies "
            f"({len(seed_sections)} with a fixed section)",
            flush=True,
        )
    elif not merged_seeds:
        print("  [seeds] no curated seed file matched this market (pure discovery)", flush=True)
    merged_seeds = list(dict.fromkeys(merged_seeds))
    query_context = {
        "industry": query,
        "country": country,
        "functions": [],
        "sections": list(sections or []),
        "seed_companies": merged_seeds,
        "seed_sections": seed_sections,
        "exclude_segments": list(exclude_segments or []),
        "market_definition": str(market_definition or ""),
    }
    result = run_pipeline_sync(
        query_context,
        settings,
        enrich_limit=enrich_limit,
        classify_limit=classify_limit,
        time_budget_min=time_budget_min,
        target_coverage=target_coverage,
    )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if out_name:
        base = Path(str(out_name)).stem  # drop any extension
        slug = "".join(c if c.isalnum() else "_" for c in base.lower()).strip("_")[:90]
    else:
        slug = _slug(query, country)
    csv_path = OUTPUT_DIR / f"{slug}.csv"
    json_path = OUTPUT_DIR / f"{slug}.json"
    docx_path = OUTPUT_DIR / f"{slug}.docx"
    xlsx_path = OUTPUT_DIR / f"{slug}.xlsx"
    save_pipeline_csv(result, str(csv_path))
    try:
        save_pipeline_xlsx(result, str(xlsx_path))
        result["_xlsx_path"] = str(xlsx_path)
    except Exception as exc:
        print(f"  [pipeline] Excel export failed: {exc}", flush=True)
    try:
        save_pipeline_docx(result, str(docx_path))
        result["_docx_path"] = str(docx_path)
    except Exception as exc:
        print(f"  [pipeline] DOCX export failed: {exc}", flush=True)

    # Durable copy — container disk is ephemeral on Cloud Run; a GCS-backed
    # URL survives restarts/redeploys/refreshes even if the local file is gone.
    # Written to a sidecar JSON next to the result file so any UI code path
    # (not just this function's in-memory result dict) can find the links.
    from vendor_intel.storage.gcs_export import gcs_enabled, upload_run_file

    if gcs_enabled():
        gcs_urls: dict[str, str] = {}
        for key, path in (("xlsx", xlsx_path), ("docx", docx_path), ("csv", csv_path)):
            url = upload_run_file(path, slug=slug)
            if url:
                result[f"_{key}_gcs_url"] = url
                gcs_urls[key] = url
        if gcs_urls:
            try:
                (OUTPUT_DIR / f"{slug}.gcs_urls.json").write_text(
                    json.dumps(gcs_urls, indent=2), encoding="utf-8"
                )
            except Exception as exc:
                print(f"  [gcs] could not write sidecar urls file: {exc}", flush=True)
                result[key] = url
    try:
        json_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass
    audit_md = str(result.get("recall_audit_md") or "")
    if audit_md.strip():
        audit_path = OUTPUT_DIR / f"{slug}_recall_audit.md"
        try:
            audit_path.write_text(audit_md, encoding="utf-8")
            result["_recall_audit_path"] = str(audit_path)
            print(f"  [audit] independent-recall report -> {audit_path}", flush=True)
        except Exception as exc:
            print(f"  [audit] could not write recall report: {exc}", flush=True)
    # Coverage sidecar: market size / coverage / majors (all figures are ESTIMATES).
    cov = dict(result.get("coverage") or {})
    if cov:
        cov_path = OUTPUT_DIR / f"{slug}_coverage.json"
        llm = result.get("llm_usage") or {}
        rows = [r for r in (result.get("relevant_companies") or []) if r.get("is_relevant")]
        sidecar = {
            "market": cov.get("market") or query,
            "geography": cov.get("geography") or country,
            "total_market_size_estimate_usd_mn": cov.get("total_market_size_estimate_usd_mn"),
            "market_size_source": cov.get("market_size_source"),
            "estimated_coverage_pct": cov.get("estimated_coverage_pct"),
            "target_coverage_pct": cov.get("target_coverage_pct"),
            "coverage_stop_reason": cov.get("coverage_stop_reason"),
            "coverage_rounds": cov.get("coverage_rounds"),
            "n_companies": _count_csv_companies(csv_path) or len(rows),
            "n_manufacturers": cov.get("n_manufacturers"),
            "n_majors": cov.get("n_majors"),
            "n_majors_kept": cov.get("n_majors_kept"),
            "runtime_seconds": cov.get("runtime_seconds", result.get("elapsed_seconds")),
            "llm_calls": llm.get("llm_calls_total", cov.get("llm_calls")),
            "time_budget_min": cov.get("time_budget_min"),
            "majors": cov.get("majors") or [],
            "evidence_sources": cov.get("evidence_sources") or [],
            "note": cov.get("note")
            or "All revenue / share / market-size figures are ESTIMATES (LLM + web search).",
        }
        try:
            cov_path.write_text(json.dumps(sidecar, indent=2, default=str), encoding="utf-8")
            result["_coverage_path"] = str(cov_path)
        except Exception as exc:
            print(f"  [coverage] could not write coverage sidecar: {exc}", flush=True)
    result["_csv_path"] = str(csv_path)
    result["_json_path"] = str(json_path)
    return result


def _count_csv_companies(path) -> int:
    """Numbered company rows in the exported CSV (what a user sees), excluding Not-Verified."""
    import csv as _csv

    n = 0
    try:
        with open(path, encoding="utf-8") as f:
            in_unverified = False
            for r in _csv.reader(f):
                if r and r[0].startswith("=== "):
                    in_unverified = r[0].startswith("=== Not Verified")
                elif r and r[0].strip().isdigit() and not in_unverified:
                    n += 1
    except Exception:
        return 0
    return n


def _print_result(query: str, country: str, result: dict, elapsed: float) -> None:
    rows = result.get("relevant_companies") or []
    seeded = sum(1 for r in rows if r.get("is_seed"))
    # Count exactly what a user sees in the Excel: the numbered company rows actually written to
    # the CSV (after section grouping + dedupe). Reading the file is the only count that matches.
    companies = 0
    try:
        import csv as _csv
        with open(result.get("_csv_path", ""), encoding="utf-8") as _f:
            for _r in _csv.reader(_f):
                if _r and _r[0].strip().isdigit():
                    companies += 1
    except Exception:
        companies = sum(1 for r in rows if r.get("is_relevant"))
    # Seed-audit mode: the benchmark list was NOT pinned — report recall against the WHOLE list.
    audit_found = result.get("seed_audit_found")
    audit_total = result.get("seed_audit_total")
    # Normal (pinned) mode: of the pinned seeds, how many the system ALSO surfaced on its own.
    seeds_total = result.get("seeds_pinned_total", seeded)
    seeds_indep = result.get("seeds_found_independently_count")
    llm = result.get("llm_usage") or {}
    bar = "-" * 60
    print(f"\n{bar}")
    print(f"  Done: {query} ({country})")
    print(f"  Companies in Excel : {companies}  (total rows exported to the CSV / Word)")
    if audit_found is not None and audit_total:
        print(f"  Found by system    : {audit_found} of {audit_total}  "
              f"(of your list, discovered independently)")
    elif seeds_indep is not None and seeds_total:
        print(f"  Found by system    : {seeds_indep} of {seeds_total}  "
              f"(system surfaced these on its own)")
    print(f"  Time               : {elapsed / 60:.1f} min ({elapsed:.0f}s)")
    print(f"  LLM calls          : {llm.get('llm_calls_total', '?')}")
    print(f"  Est. cost          : ${llm.get('estimated_cost_usd', '?')}")
    print(f"  CSV                : {result.get('_csv_path', '')}")
    print(f"  Excel              : {result.get('_xlsx_path', '')}")
    print(f"  Word doc           : {result.get('_docx_path', '')}")
    print(f"  JSON               : {result.get('_json_path', '')}")
    cov = result.get("coverage") or {}
    if cov.get("estimated_coverage_pct") is not None:
        print(f"  Est. coverage      : {cov.get('estimated_coverage_pct')}% of an estimated "
              f"{cov.get('total_market_size_estimate_usd_mn')} USD Mn market (ESTIMATE; target "
              f"{cov.get('target_coverage_pct')}%)")
    if result.get("_coverage_path"):
        print(f"  Coverage JSON      : {result.get('_coverage_path', '')}")
    print(f"{bar}\n")


def _load_session_log() -> list[dict]:
    log_path = OUTPUT_DIR / "session_log.json"
    if log_path.exists():
        try:
            return json.loads(log_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_session_log(log: list[dict]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "session_log.json").write_text(
        json.dumps(log, indent=2, default=str), encoding="utf-8"
    )


def _parse_query_and_country(raw: str) -> tuple[str, str]:
    if "|" in raw:
        parts = raw.split("|", 1)
        return parts[0].strip(), (parts[1].strip() or "global")
    return raw.strip(), "global"


def _print_history(log: list[dict]) -> None:
    if not log:
        print("\n  No queries run yet.\n")
        return
    print(f"\n  Session history ({len(log)} queries):")
    for i, entry in enumerate(log, 1):
        status = "OK  " if entry.get("status") == "ok" else "FAIL"
        q = entry.get("query", "?")
        c = entry.get("country", "global")
        n = entry.get("companies_exported", "?")
        t = entry.get("elapsed_minutes", "?")
        ran = (entry.get("ran_at") or "")[:16].replace("T", " ")
        print(f"    {i:2}. [{status}] {q} ({c}) — {n} rows, {t} min  [{ran}]")
    print()


def interactive_mode(
    settings, enrich_cap: int, classify_cap: int,
    sections: list[str] | None = None, seeds: list[str] | None = None,
) -> None:
    session_log = _load_session_log()
    print("\n" + "=" * 60)
    print("  INTERACTIVE QUERY MODE")
    print("  Type a market query and press Enter.")
    print("  With geography : \"Atomic Clock Market | Germany\"")
    print("  With sections  : \"Drone Battery Market | global | "
          "Battery Pack Manufacturers; OEM Battery Manufacturers; BMS Manufacturers\"")
    print("  Or keyword     : \"Drone Battery Market ; functionality: "
          "Battery Pack Manufacturers, BMS Manufacturers, Contract Manufacturer\"")
    print("  Commands: history, quit")
    print("=" * 60)

    while True:
        print()
        try:
            raw = input("  Query: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Exiting.")
            break

        if not raw:
            continue
        if raw.lower() in ("quit", "exit", "q"):
            break
        if raw.lower() == "history":
            _print_history(session_log)
            continue

        query, country, typed_sections = _parse_input(raw)
        q_sections = typed_sections or list(sections or [])
        geo = f"({country})" if country != "global" else "(global)"
        sect_note = f" | sections: {', '.join(q_sections)}" if q_sections else ""
        print(f'\n  Will run: "{query}" {geo}{sect_note}')
        print(f"  Profile: {settings.pipeline_profile} | ~15-25 min | output -> {OUTPUT_DIR}")
        try:
            confirm = input("  Press Enter to start, or 'skip': ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Exiting.")
            break
        if confirm in ("skip", "s", "n", "no"):
            continue

        t0 = time.perf_counter()
        print(f"\n  [{datetime.now().strftime('%H:%M:%S')}] Starting pipeline...")
        try:
            result = run_one_query(
                query, country, settings,
                enrich_limit=enrich_cap,
                classify_limit=classify_cap,
                sections=q_sections,
                seeds=seeds,
            )
            elapsed = round(time.perf_counter() - t0, 1)
            _print_result(query, country, result, elapsed)
            llm = result.get("llm_usage") or {}
            log_entry = {
                "query": query,
                "country": country,
                "status": "ok",
                "companies_exported": len(result.get("relevant_companies") or []),
                "elapsed_seconds": elapsed,
                "elapsed_minutes": round(elapsed / 60, 2),
                "llm_calls": llm.get("llm_calls_total"),
                "estimated_cost_usd": llm.get("estimated_cost_usd"),
                "csv_file": result.get("_csv_path", ""),
                "ran_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as exc:
            elapsed = round(time.perf_counter() - t0, 1)
            print(f"\n  FAILED after {elapsed:.0f}s: {exc}")
            import traceback
            traceback.print_exc()
            log_entry = {
                "query": query,
                "country": country,
                "status": "error",
                "error": str(exc),
                "elapsed_seconds": elapsed,
                "ran_at": datetime.now(timezone.utc).isoformat(),
            }

        session_log.append(log_entry)
        _save_session_log(session_log)
        print("  [Enter] another query  |  [q] quit  |  [h] history")
        try:
            nxt = input("  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break
        if nxt in ("q", "quit", "exit"):
            break
        if nxt in ("h", "history"):
            _print_history(session_log)


def batch_mode(
    query_file: str, settings, enrich_cap: int, classify_cap: int,
    sections: list[str] | None = None, seeds: list[str] | None = None,
) -> None:
    path = Path(query_file)
    if not path.exists():
        print(f"  ERROR: File not found: {query_file}")
        sys.exit(1)
    # Line format: "Query | Country | Section1; Section2; ..."  (country/sections optional)
    # or keyword form: "Query ; functionality: Section1, Section2, ..."
    queries: list[tuple[str, str, list[str]]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        q, c, line_sections = _parse_input(line)
        queries.append((q, c, line_sections or list(sections or [])))
    if not queries:
        print(f"  No queries in {query_file}")
        sys.exit(1)

    session_log = _load_session_log()
    for idx, (query, country, line_sections) in enumerate(queries, 1):
        print(f"\n{'=' * 60}\n  [{idx}/{len(queries)}] {query} ({country})\n{'=' * 60}")
        t0 = time.perf_counter()
        try:
            result = run_one_query(
                query, country, settings,
                enrich_limit=enrich_cap,
                classify_limit=classify_cap,
                sections=line_sections,
                seeds=seeds,
            )
            elapsed = round(time.perf_counter() - t0, 1)
            _print_result(query, country, result, elapsed)
            llm = result.get("llm_usage") or {}
            session_log.append({
                "query": query,
                "country": country,
                "status": "ok",
                "companies_exported": len(result.get("relevant_companies") or []),
                "elapsed_seconds": elapsed,
                "elapsed_minutes": round(elapsed / 60, 2),
                "llm_calls": llm.get("llm_calls_total"),
                "estimated_cost_usd": llm.get("estimated_cost_usd"),
                "csv_file": result.get("_csv_path", ""),
                "ran_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as exc:
            elapsed = round(time.perf_counter() - t0, 1)
            print(f"  FAILED: {exc}")
            import traceback
            traceback.print_exc()
            session_log.append({
                "query": query,
                "country": country,
                "status": "error",
                "error": str(exc),
                "elapsed_seconds": elapsed,
                "ran_at": datetime.now(timezone.utc).isoformat(),
            })
        _save_session_log(session_log)


def _utf8_console() -> None:
    """Pipeline logs contain non-cp1252 characters (arrows, dashes). On a Windows console or when
    output is redirected, printing them raised UnicodeEncodeError inside search/LLM helpers, which
    swallowed the error and silently returned no results. Force UTF-8 with replacement."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main() -> None:
    _utf8_console()
    parser = argparse.ArgumentParser(description="Vendor Intelligence — interactive query runner")
    parser.add_argument("--query", "-q", help="Single query (non-interactive)")
    parser.add_argument("--country", "-c", default="global", help="Geography (default: global)")
    parser.add_argument("--file", "-f", help="Batch: one query per line")
    parser.add_argument(
        "--profile", "-p",
        choices=("quality", "balanced", "recall"),
        default=None,
        help="quality (default) | recall = more rows, noisier",
    )
    parser.add_argument(
        "--sections", "-s",
        default=None,
        help="Exact CEO section categories, ';'- or ','-separated. "
        "e.g. \"Battery Pack Manufacturers; OEM Battery Manufacturers; BMS Manufacturers; Contract Manufacturer\"",
    )
    parser.add_argument(
        "--seeds", "-S",
        default=None,
        help="Known companies to guarantee in the output (from a report), ';'- or ','-separated. "
        "e.g. \"FUNKE; Wieland Onda; Hexonic; Xylem; Kaori\"",
    )
    parser.add_argument(
        "--seeds-file",
        default=None,
        help="File with must-have company names (one per line; '#' comments ok). "
        "Keeps the run command short for presentations.",
    )
    parser.add_argument(
        "--cap",
        choices=("fast", "focused", "standard", "broad", "maximum"),
        default=None,
        help="Result cap (default: fast). fast = ~10-15 min, discover<=150, early stopping, all "
        "majors kept | focused ~25-50 | standard ~50-90 | broad ~90-160 | maximum = the old "
        "exhaustive mode (no time budget unless --time-budget-min is given).",
    )
    parser.add_argument(
        "--time-budget-min",
        type=float,
        default=None,
        help="Overall wall-clock budget in minutes (default 15; 0 = unlimited). At ~45%% discovery "
        "stops; at ~75%% widening/coverage expansion/optional LLM passes stop and the run exports.",
    )
    parser.add_argument(
        "--target-coverage",
        type=float,
        default=0.75,
        help="Target estimated market coverage (fraction, default 0.75). While the estimated share "
        "of the market held by kept manufacturers is below it, targeted searches look for more "
        "large players (within the time budget).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output basename (no extension), e.g. waste_oil_market_global_v2. Single-query mode only.",
    )
    parser.add_argument(
        "--no-geo-filter",
        action="store_true",
        help="Keep global players that operate in the target geography (don't drop foreign-HQ majors). "
        "Use for country markets that include international suppliers (e.g. 'Brazil X Market').",
    )
    parser.add_argument(
        "--shuffle",
        action="store_true",
        help="Randomize company order within each section (no quality/seed clustering).",
    )
    parser.add_argument(
        "--seed-audit",
        action="store_true",
        help="Diagnostic: do NOT pin seeds into the final; report which seed companies pure "
        "discovery found / kept / rejected (with reasons). For understanding recall.",
    )
    parser.add_argument(
        "--brief", help="Free-form market brief — the system interprets it (market, segments to "
        "include, segments to exclude) with an LLM, then runs that scope.",
    )
    parser.add_argument("--brief-file", help="Path to a file containing the market brief (see --brief).")
    args = parser.parse_args()
    if getattr(args, "seed_audit", False):
        os.environ["SEED_AUDIT"] = "1"
    cli_sections = _parse_sections(args.sections)
    cli_seeds = _parse_sections(args.seeds) + [n for n, _ in _load_seeds_file(args.seeds_file)]
    # de-dupe preserving order
    cli_seeds = list(dict.fromkeys(cli_seeds))

    from vendor_intel.placeholders.load_keys import apply_env_overrides
    from vendor_intel.utils.output_filter import install_stderr_filter

    install_stderr_filter()
    apply_env_overrides()
    _refresh_output_dir()

    for w in _validate_env():
        print(f"  WARNING: {w}")

    profile = args.profile or os.getenv("PIPELINE_PROFILE", "quality").strip()
    settings = _load_settings(profile)
    from vendor_intel.pipeline.cap_profiles import (
        CLI_DEFAULT_CAP,
        apply_cap,
        cap_describe,
        is_exhaustive,
    )

    cap_key = args.cap or CLI_DEFAULT_CAP
    settings = apply_cap(settings, cap_key)
    print(f"  Cap: {cap_describe(cap_key)}")
    if args.time_budget_min is not None:
        time_budget_min = args.time_budget_min if args.time_budget_min > 0 else None
    else:
        time_budget_min = None if is_exhaustive(cap_key) else 15.0
    target_coverage = max(0.0, min(1.0, float(args.target_coverage)))
    _RUN_DEFAULTS.update({"time_budget_min": time_budget_min, "target_coverage": target_coverage})
    print(
        f"  Time budget: {f'{time_budget_min:g} min' if time_budget_min else 'unlimited'} | "
        f"target coverage: {target_coverage:.0%} (estimated)"
    )
    if args.no_geo_filter:
        settings = settings.model_copy(update={"pipeline_strict_geo": False})
        print("  Geo filter: OFF - keeping global players active in the target geography")
    if args.shuffle:
        settings = settings.model_copy(update={"pipeline_shuffle_export": True})
        print("  Order: randomized within sections")
    from vendor_intel.pipeline.geo_limits import pipeline_limits

    lim = pipeline_limits(settings, recall=(profile == "recall"), country="global")
    enrich_cap = int(lim["enrich"])
    classify_cap = int(lim["discover"])
    print(f"  Profile: {profile} | discover cap: {classify_cap} | enrich cap: {enrich_cap}")

    brief_text = ""
    if args.brief:
        brief_text = args.brief
    elif args.brief_file and Path(args.brief_file).exists():
        brief_text = Path(args.brief_file).read_text(encoding="utf-8")

    try:
        if brief_text.strip():
            from vendor_intel.funnel.brief_interpreter import interpret_brief

            print("  [brief] interpreting your market brief with the AI...", flush=True)
            spec = interpret_brief(brief_text, settings)
            market = spec["market"]
            country = spec["geography"] if (not args.country or args.country == "global") else args.country
            sections = spec["sections"] or cli_sections
            print(f"  [brief] understood mode='{spec['mode']}' | market='{market}' | geo='{country}'", flush=True)
            if sections:
                print(f"  [brief] segments to INCLUDE: {'; '.join(sections)}", flush=True)
            if spec["exclude"]:
                print(f"  [brief] segments to EXCLUDE: {'; '.join(spec['exclude'])}", flush=True)
            # Match curated seeds by the brief filename (stable) since the AI renames the market.
            seed_hint = Path(args.brief_file).stem if args.brief_file else ""
            t0 = time.perf_counter()
            result = run_one_query(
                market, country, settings,
                enrich_limit=enrich_cap, classify_limit=classify_cap,
                sections=sections, seeds=cli_seeds, out_name=args.out,
                exclude_segments=spec["exclude"], market_definition=spec["definition"],
                seed_hint=seed_hint,
                time_budget_min=time_budget_min, target_coverage=target_coverage,
            )
            _print_result(market, country, result, round(time.perf_counter() - t0, 1))
        elif args.file:
            batch_mode(args.file, settings, enrich_cap, classify_cap, sections=cli_sections, seeds=cli_seeds)
        elif args.query:
            query, country, q_sections = _parse_input(args.query)
            if args.country and args.country != "global":
                country = args.country
            final_sections = q_sections or cli_sections
            t0 = time.perf_counter()
            result = run_one_query(
                query, country, settings,
                enrich_limit=enrich_cap,
                classify_limit=classify_cap,
                sections=final_sections,
                seeds=cli_seeds,
                out_name=args.out,
                time_budget_min=time_budget_min,
                target_coverage=target_coverage,
            )
            _print_result(query, country, result, round(time.perf_counter() - t0, 1))
        else:
            interactive_mode(settings, enrich_cap, classify_cap, sections=cli_sections, seeds=cli_seeds)
    finally:
        try:
            from vendor_intel.clients.ddg_worker_pool import shutdown_ddg_pool
            shutdown_ddg_pool(wait=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()

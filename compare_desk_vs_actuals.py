"""Compare Desk Research global 2025 values with actuals_2025.csv.

Two accuracy measures per market:
    pct_error           (ours - actual) / actual, where "actual" is the median
                        of the published values (as before)
    in_published_range  ours lies between the lowest and highest published
                        value (Value_1..Value_3), with +/-5% tolerance

The publishers themselves disagree by ~11% (median gap from their median), so
in_published_range says whether we are within that disagreement.

"before" = the Desk results in approaches/examples/<slug>/result.json.
"after"  = fresh runs written by --run to output/desk_rerun/<slug>/.

Usage:
    python compare_desk_vs_actuals.py --run              # rerun Desk for the 7 markets, then compare
    python compare_desk_vs_actuals.py                    # compare existing reruns only
    python compare_desk_vs_actuals.py --run --market Televisions
    python compare_desk_vs_actuals.py --run --scope "Urea (total, all uses)=Total urea market, fertilizer + industrial uses"
    python compare_desk_vs_actuals.py --eval             # all 'ok' markets, before/current/after, tuning vs held-out
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import median
from typing import Any

ROOT = Path(__file__).resolve().parent
ACTUALS = ROOT / "actuals_2025.csv"
EXAMPLES = ROOT / "approaches" / "examples"
RERUN_DIR = ROOT / "output" / "desk_rerun"
RANGE_TOLERANCE = 0.05

DESK_MARKETS = [
    ("Smartphones", "smartphones"),
    ("Watches", "watches"),
    ("Smartwatches", "smartwatches"),
    ("Televisions", "televisions"),
    ("Olive Oil", "olive-oil"),
    ("Disposable Medical Gloves", "disposable-medical-gloves"),
    ("Urea (total, all uses)", "urea-fertilizer"),
]


def load_actuals(path: Path = ACTUALS) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            published = []
            for i in (1, 2, 3):
                try:
                    published.append(float(row.get(f"Value_{i}") or ""))
                except ValueError:
                    continue
            out[row["Market"]] = {
                "actual": float(row["Actual_2025_USD_Mn"]), "published": published,
                "confidence": row.get("Confidence", ""), "notes": row.get("Notes", ""),
            }
    return out


def score(estimate: float | None, actual: dict[str, Any]) -> dict[str, Any]:
    """pct_error vs the median and in_published_range (+/-5%)."""
    if estimate is None:
        return {"pct_error": None, "in_published_range": None}
    pub = actual["published"] or [actual["actual"]]
    lo, hi = min(pub) * (1 - RANGE_TOLERANCE), max(pub) * (1 + RANGE_TOLERANCE)
    return {
        "pct_error": (estimate - actual["actual"]) / actual["actual"],
        "in_published_range": lo <= estimate <= hi,
        "published_range": [min(pub), max(pub)],
    }


def _before(slug: str) -> dict[str, Any]:
    """Desk value from the earlier example run, and whether the kept pass was
    grounded (read from its log: the pass whose value matches the result)."""
    res_path = EXAMPLES / slug / "result.json"
    if not res_path.exists():
        return {}
    desk = json.loads(res_path.read_text(encoding="utf-8")).get("1_desk_research") or {}
    value = desk.get("global_usd_mn")
    if value is None:
        return {}
    targets = [round(float(value))]
    if desk.get("global_usd_mn_2026_original"):
        targets.append(round(float(desk["global_usd_mn_2026_original"])))
    grounded, source = None, ""
    log_path = EXAMPLES / slug / "desk_research.log"
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.search(r"Estimated total market size: \$([\d,]+)M.*?\((.*)\)\s*$", line)
            if m and round(float(m.group(1).replace(",", ""))) in targets:
                grounded = m.group(2).startswith("grounded")
                source = m.group(2)[:160]
    return {"value": float(value), "grounded": grounded, "source": source}


def _after(slug: str) -> dict[str, Any]:
    state_path = RERUN_DIR / slug / "desk_state.json"
    if not state_path.exists():
        return {}
    st = json.loads(state_path.read_text(encoding="utf-8"))
    cited = st.get("cited_anchor") or {}
    return {
        "value": st.get("global_anchor_2025"), "grounded": st.get("market_size_grounded"),
        "source": st.get("market_size_source") or "LLM estimate, no cited figure",
        "cited_method": cited.get("method"), "cited_sources": [v.get("source") for v in cited.get("accepted", [])],
        "scope_rejections": [f"{v.get('raw_text')} ({v.get('source')}): {v.get('reason')}"
                             for v in cited.get("rejected", []) if str(v.get("reason", "")).startswith("scope:")],
        "quality_flags": [f.get("code") for f in st.get("quality_flags") or []],
        "scope": st.get("scope", ""),
    }


def run_desk(market: str, slug: str, scope: str = "") -> int:
    out_dir = RERUN_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "generate_synthetic_input.py", "--market", market, "--from-db",
           "--output", str(out_dir / "desk_input_sheet.xlsx"), "--save-state", str(out_dir / "desk_state.json")]
    if scope:
        cmd += ["--scope", scope]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=3600)
    (out_dir / "desk_research.log").write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")
    if scope and (out_dir / "desk_state.json").exists():
        st = json.loads((out_dir / "desk_state.json").read_text(encoding="utf-8"))
        st["scope"] = scope
        (out_dir / "desk_state.json").write_text(json.dumps(st, indent=2, ensure_ascii=False), encoding="utf-8")
    return proc.returncode


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x:+.1%}"


def _yn(x: bool | None) -> str:
    return "—" if x is None else ("yes" if x else "no")


def build_report(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Desk Research vs actual (2025, US$ Mn, global) — before / after the grounding fixes",
        "",
        "% error = (ours − actual) / actual, actual = median of published values. "
        f"In range = within [lowest, highest] published value ±{RANGE_TOLERANCE:.0%}.",
        "",
        "| Market | Actual | Published range | Before | Before err | Before in range | Before grounded | "
        "After | After err | After in range | After grounded | After source |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        b, a = r["before"], r["after"]
        rng = r["after_score"].get("published_range") or r["before_score"].get("published_range") or []
        src = (a.get("cited_method") or "") + (": " + ", ".join(a.get("cited_sources") or []) if a.get("cited_sources") else "")
        lines.append(
            f"| {r['label']} | {r['actual']:,.0f} | {rng[0]:,.0f}–{rng[1]:,.0f} | "
            f"{b.get('value', 0) or 0:,.0f} | {_pct(r['before_score']['pct_error'])} | {_yn(r['before_score']['in_published_range'])} | {_yn(b.get('grounded'))} | "
            f"{(a.get('value') or 0):,.0f} | {_pct(r['after_score']['pct_error'])} | {_yn(r['after_score']['in_published_range'])} | {_yn(a.get('grounded'))} | "
            f"{src or a.get('source', '—')} |"
        )
    for key in ("before", "after"):
        errs = [abs(r[f"{key}_score"]["pct_error"]) for r in rows if r[f"{key}_score"]["pct_error"] is not None
                and not r.get("variant")]
        inr = [r[f"{key}_score"]["in_published_range"] for r in rows if r[f"{key}_score"]["in_published_range"] is not None
               and not r.get("variant")]
        if errs:
            lines.append("")
            lines.append(f"**{key.title()}:** median abs error {median(errs):.1%} over {len(errs)} markets; "
                         f"in published range {sum(inr)}/{len(inr)}.")
    rej = [(r["label"], x) for r in rows for x in r["after"].get("scope_rejections", [])]
    if rej:
        lines += ["", "## Cited figures rejected for scope (after)", ""]
        lines += [f"- {m}: {x}" for m, x in rej]
    return "\n".join(lines) + "\n"

# --------------------------------------------------------------------------- #
# --eval: every "ok" market (+ Urea) across code versions, tuning vs held-out
# --------------------------------------------------------------------------- #
EVAL_DIR = ROOT / "output" / "desk_eval"
SPLIT_FILE = EXAMPLES / "DESK_EVAL_SPLIT.json"
VERSIONS = ("before", "current", "after")
VERSION_LABELS = {"before": "old anchor (HEAD)", "current": "current code", "after": "after this change"}
EXTRA_MARKETS = ["Urea (total, all uses)"]
ABLATIONS = ("title_subject", "size_check", "source_tiers", "weak_consensus")
# Defaults as shipped (config.py). weak_consensus is off: it made held-out
# accuracy worse in the 2026-10-09 evaluation.
SHIPPED_FLAGS = {"title_subject": True, "size_check": True, "source_tiers": True, "weak_consensus": False}


def _eval_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _eval_state(version: str, market: str) -> dict[str, Any] | None:
    path = EVAL_DIR / version / _eval_slug(market) / "desk_state.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _eval_failure(version: str, market: str) -> str:
    log = EVAL_DIR / version / _eval_slug(market) / "desk_research.log"
    if not log.exists():
        return "not run"
    text = log.read_text(encoding="utf-8", errors="replace")
    if "could not build a DB skeleton" in text:
        return "no DB skeleton"
    return "TIMEOUT" if text.startswith("TIMEOUT") else "failed"


def _replay(state: dict[str, Any], market: str, **flags: bool) -> float | None:
    """Anchor the saved evidence would give with some steps switched off.
    None when the replay is ungrounded (its LLM estimate is unknown offline)."""
    sys.path.insert(0, str(ROOT / "src"))
    from me_engine.generation.cited_values import collect_cited_values

    sources = state.get("evidence_sources")
    if sources is None:
        return None
    size_range = (state.get("cited_anchor") or {}).get("size_range")
    lookup = state.get("market_search_name") or market
    anchor = collect_cited_values(
        [tuple(s) for s in sources], lookup, definition="", llm=None,
        size_range=None if flags.get("size_check") is False else size_range,
        title_subject=flags.get("title_subject", True), source_tiers=flags.get("source_tiers", True),
        weak_consensus=flags.get("weak_consensus", SHIPPED_FLAGS["weak_consensus"]),
    )
    return anchor.value_2025_mn


def _summary(rows: list[dict[str, Any]], key: str) -> str:
    errs = [abs(r[key]["pct_error"]) for r in rows if r[key]["pct_error"] is not None]
    inr = [r[key]["in_published_range"] for r in rows if r[key]["in_published_range"] is not None]
    if not errs:
        return "—"
    return f"{median(errs):.1%} (n={len(errs)}), in range {sum(inr)}/{len(inr)}"


def run_eval() -> str:
    actuals = load_actuals()
    split = json.loads(SPLIT_FILE.read_text(encoding="utf-8"))
    markets = [m for m, a in actuals.items() if a["confidence"] == "ok"] + EXTRA_MARKETS
    rows = []
    for m in markets:
        act = actuals[m]
        row: dict[str, Any] = {"market": m, "set": split.get(m, "heldout"), "actual": act["actual"],
                               "published": act["published"]}
        for v in VERSIONS:
            st = _eval_state(v, m)
            value = st.get("global_anchor_2025") if st else None
            row[v] = {"value": value, "grounded": st.get("market_size_grounded") if st else None,
                      "status": "ok" if st else _eval_failure(v, m),
                      "flags": [f.get("code") for f in (st or {}).get("quality_flags") or []],
                      "method": ((st or {}).get("cited_anchor") or {}).get("method"),
                      **score(value, act)}
        after_state = _eval_state("after", m)
        row["ablation"] = {}
        row["shipped"] = {"value": None, **score(None, act)}
        if after_state:
            def _value(flags):
                v = _replay(after_state, m, **flags)
                if v is None and not after_state.get("market_size_grounded"):
                    v = after_state.get("global_anchor_2025")  # ungrounded either way: same LLM estimate
                return v
            shipped = _value({**SHIPPED_FLAGS})
            row["shipped"] = {"value": shipped, **score(shipped, act)}
            for flag in ABLATIONS:
                flags = {**SHIPPED_FLAGS, flag: not SHIPPED_FLAGS[flag]}
                replay = _value(flags)
                row["ablation"][flag] = {"value": replay, **score(replay, act)}
        rows.append(row)

    lines = [
        "# Desk Research vs actual — all 'ok' markets (+ Urea total), three code versions",
        "",
        "% error vs the median of published values; in range = within [min, max] published ±5%. "
        "Tuning/held-out split fixed before any tuning (`DESK_EVAL_SPLIT.json`, hash of the market name). "
        "Web search results differ run to run, so part of every difference is search noise.",
        "",
    ]
    for subset_name, subset in (("Held-out", [r for r in rows if r["set"] == "heldout"]),
                                ("Tuning", [r for r in rows if r["set"] == "tuning"]),
                                ("All", rows)):
        common = [r for r in subset if all(r[v]["value"] is not None for v in VERSIONS)]
        lines += [f"## {subset_name} ({len(subset)} markets; {len(common)} with a value in all three versions)", "",
                  "| Version | Median abs error, all with a value | Median abs error, common markets |",
                  "|---|---|---|"]
        for v in VERSIONS:
            lines.append(f"| {VERSION_LABELS[v]} | {_summary(subset, v)} | {_summary(common, v)} |")
        lines.append(f"| after, shipped defaults (weak_consensus off) | {_summary(subset, 'shipped')} | "
                     f"{_summary(common, 'shipped')} |")
        lines.append("")
        if subset_name != "All":
            lines += ["Ablation (after-code evidence replayed offline, one step flipped from the shipped "
                      "defaults; common markets):", "",
                      "| Step flipped | Median abs error | Shipped defaults on the same markets |", "|---|---|---|"]
            base = [r for r in common if r["ablation"]]
            for flag in ABLATIONS:
                abl = [{"x": r["ablation"][flag]} for r in base if r["ablation"][flag]["pct_error"] is not None]
                both = [r for r in base if r["ablation"][flag]["pct_error"] is not None]
                if not abl:
                    continue
                a_err = median(abs(x["x"]["pct_error"]) for x in abl)
                b_err = median(abs(r["shipped"]["pct_error"]) for r in both)
                state = "on" if not SHIPPED_FLAGS[flag] else "off"
                lines.append(f"| {flag} {state} | {a_err:.1%} (n={len(abl)}) | {b_err:.1%} |")
            lines.append("")

    lines += ["## Every market", "",
              "| Market | Set | Actual | Before | Err | In range | Current | Err | In range | After (as run) | Err | In range | "
              "After (shipped) | Err | In range | Grounded | After method | After flags |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]

    def cell(x):
        if x["value"] is None:
            return f"{x['status']} | — | —"
        return f"{x['value']:,.0f} | {_pct(x['pct_error'])} | {_yn(x['in_published_range'])}"

    for r in sorted(rows, key=lambda r: (r["set"], r["market"])):
        a = r["after"]
        lines.append(f"| {r['market']} | {r['set']} | {r['actual']:,.0f} | {cell(r['before'])} | {cell(r['current'])} | "
                     f"{cell(a)} | {cell({**r['shipped'], 'status': a['status']})} | {_yn(a['grounded'])} | "
                     f"{a['method'] or '—'} | {', '.join(a['flags']) or '—'} |")
    report = "\n".join(lines) + "\n"
    (EXAMPLES / "DESK_EVAL.md").write_text(report, encoding="utf-8")
    (EXAMPLES / "DESK_EVAL.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    return report



def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--run", action="store_true", help="rerun Desk Research before comparing")
    p.add_argument("--market", action="append", help="limit to these markets (repeatable)")
    p.add_argument("--scope", action="append", default=[],
                   help='extra variant run with a market definition: "Market=definition" (repeatable)')
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--eval", action="store_true",
                   help="report all 'ok' markets across output/desk_eval/{before,current,after} (no runs)")
    args = p.parse_args()
    if args.eval:
        sys.stdout.buffer.write(run_eval().encode("utf-8"))
        return 0

    markets = [(m, s) for m, s in DESK_MARKETS if not args.market or m in args.market]
    variants = []
    for spec in args.scope:
        name, _, definition = spec.partition("=")
        slug = dict(DESK_MARKETS).get(name.strip())
        if slug and definition.strip():
            variants.append((name.strip(), f"{slug}-scoped", definition.strip()))

    if args.run:
        jobs = [(m, s, "") for m, s in markets] + variants
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            for (m, _s, _d), rc in zip(jobs, pool.map(lambda j: run_desk(*j), jobs)):
                print(f"{m}: exit {rc}")

    actuals = load_actuals()
    rows = []
    for market, slug in markets:
        act = actuals[market]
        before, after = _before(slug), _after(slug)
        rows.append({"label": market, "actual": act["actual"], "before": before, "after": after,
                     "before_score": score(before.get("value"), act), "after_score": score(after.get("value"), act)})
    for market, slug, definition in variants:
        act = actuals[market]
        after = _after(slug)
        rows.append({"label": f"{market} (scope: {definition})", "variant": True, "actual": act["actual"],
                     "before": {}, "after": after, "before_score": score(None, act),
                     "after_score": score(after.get("value"), act)})

    report = build_report(rows)
    (EXAMPLES / "DESK_VS_ACTUAL.md").write_text(report, encoding="utf-8")
    (EXAMPLES / "DESK_VS_ACTUAL.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    sys.stdout.buffer.write(report.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

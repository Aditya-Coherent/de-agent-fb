"""Build the step-by-step walkthrough pages (one per approach) for presentations.

Every page uses the same engine as the original Desk Research / usage-based
walkthrough (`ppt/_engine/walkthrough_template.html`, extracted from
`pipeline-walkthrough.html`): a step rail, a data-journey strip and, per step,
the agent, its input/output, sources, calculations and validation.

Numbers come from real run files. A step is badged "Real Recorded Run" when
its numbers are read from a completed run and "Illustrative" when they are
not (2C, which is designed but not implemented, is mostly illustrative).

    python ppt/build_ppt_artifacts.py --market "Olive Oil"

Inputs (all produced by the pipelines / run_examples.py):
    output/example_runs/<slug>/desk_state.json          Desk Research
    output/demand_country_2a/<slug>/0*_*.json           2A
    output/demand_usage/<slug>/0*_*.json                2B (and the base for 2C)
    output/supply_side/<slug>-examples.json             Supply-Side
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PPT = ROOT / "ppt"
TEMPLATE = PPT / "_engine" / "walkthrough_template.html"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-") or "market"


def load(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def mn(v: Any, digits: int = 1) -> str:
    if not isinstance(v, (int, float)):
        return "—"
    return f"${v:,.{digits}f}M"


def bn(v: Any) -> str:
    if not isinstance(v, (int, float)):
        return "—"
    return f"${v / 1000:,.2f} Bn" if abs(v) >= 1000 else f"${v:,.1f}M"


def num(v: Any, digits: int = 0) -> str:
    if not isinstance(v, (int, float)):
        return "—"
    return f"{v:,.{digits}f}"


def pct(v: Any, digits: int = 1) -> str:
    return f"{v * 100:.{digits}f}%" if isinstance(v, (int, float)) else "—"


def short(text: Any, n: int = 260) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


EVIDENCE_SOURCE = {"verified_observed": "web", "derived": "derived", "source_found_unverified": "web", "assumption": "assumption"}


def evidence_source(tag: str) -> str:
    return EVIDENCE_SOURCE.get(tag or "", "assumption")


_KEEP_KEYS = {"source", "src", "data_tag", "stage", "id", "code", "severity", "step"}


def short_labels(obj, key: str | None = None):
    """Display text says just "A" for an assumption (the legend explains it once)."""
    if isinstance(obj, dict):
        return {k: short_labels(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [short_labels(v, key) for v in obj]
    if isinstance(obj, str) and key not in _KEEP_KEYS:
        return re.sub(r"\bA — A\b", "A", re.sub(r"\bassumptions?\b", "A", obj, flags=re.IGNORECASE))
    return obj


def render(out_name: str, *, title: str, h1: str, subtitle: str, tab: str, desc: str,
           stages: list[dict], steps: list[dict], flags: list[dict], banner: tuple[str, str],
           badge_real: str = "Real Recorded Run", keep: dict[str, str] | None = None) -> Path:
    if keep:
        # Presentation view: only these steps, in this order, with plain names.
        by_id = {s["id"]: s for s in steps}
        steps = [{**by_id[i], "name": name} for i, name in keep.items() if i in by_id]
        used = {s.get("stage") for s in steps}
        stages = [st for st in stages if st["key"] in used]
        flags = [{**f, "step": f["step"] if f["step"] in keep else steps[-1]["id"]} for f in flags]
    steps, flags = short_labels(steps), short_labels(flags)
    data = (
        f"const BADGE_REAL = {json.dumps(badge_real, ensure_ascii=False)};\n"
        f"const STEPS = {json.dumps(steps, ensure_ascii=False, indent=1)};\n"
        f"const STAGES = {json.dumps(stages, ensure_ascii=False)};\n"
        f"const qualityFlags = {json.dumps(flags, ensure_ascii=False, indent=1)};\n"
        f"const APPROACH_DESC = {json.dumps(desc, ensure_ascii=False)};\n"
    )
    kind, text = banner
    html = TEMPLATE.read_text(encoding="utf-8")
    for key, val in {
        "{{TITLE}}": title, "{{H1}}": h1, "{{SUBTITLE}}": subtitle, "{{TAB_LABEL}}": tab,
        "{{BANNER}}": f'<div class="status-banner {kind}">{text}</div>', "{{DATA}}": data,
    }.items():
        html = html.replace(key, val)
    path = PPT / out_name
    path.write_text(html, encoding="utf-8")
    return path


def quality_flags(raw: list[dict] | None, step_for: dict[str, str], default_step: str) -> list[dict]:
    out = []
    for f in raw or []:
        code = f.get("code", "")
        out.append({
            "code": code, "severity": f.get("severity", "info"),
            "step": step_for.get(code, default_step), "message": f.get("message", ""),
        })
    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(out, key=lambda f: order.get(f["severity"], 3))


def best_row(rows: list[dict]) -> dict:
    """Prefer a country cell backed by verified evidence for the worked example."""
    rank = {"verified_observed": 0, "derived": 1, "source_found_unverified": 2, "assumption": 3}
    named = [r for r in rows if "rest of" not in str(r.get("country", "")).lower() and r.get("value_usd_mn")]
    return sorted(named or rows, key=lambda r: (rank.get(r.get("driver_evidence_type"), 4), -(r.get("value_usd_mn") or 0)))[0]


def search_queries_from_attempts(attempts: list[dict], limit: int = 6) -> list[dict]:
    out = []
    for a in attempts[:limit]:
        found = bool(a.get("found"))
        query = a.get("search_query") or f"({a.get('source_type', 'source')}) {a.get('source_name', '')}"
        result = {
            "name": a.get("source_name") or a.get("document_title") or "source",
            "url": a.get("resolved_url") or a.get("source_url") or "",
            "excerpt": short(a.get("citation") or a.get("excerpt") or (
                "Accepted figure." if found else f"Attempt {a.get('rank', '')}: no usable figure extracted.")),
            "decision": "accepted" if found else "rejected",
        }
        if found and a.get("value") is not None:
            result.update({"extractedValue": num(a.get("value"), 2), "unit": a.get("unit", ""), "year": a.get("year")})
        if not found:
            result["rejectionReason"] = a.get("rejection_reason") or "not found"
        out.append({
            "query": query,
            "purpose": f"Attempt {a.get('rank', '?')} via {a.get('source_type', 'source')} — "
                       + (f"accepted ({a.get('evidence_type', '')})." if found else f"rejected: {a.get('rejection_reason', '')}."),
            "results": [result],
        })
    return out


def region_aggregation(geo: dict, title: str) -> dict:
    return {
        "title": title,
        "rows": [{"label": r, "value": mn(v)} for r, v in geo["regions"].items()],
        "totalLabel": "Global Total", "total": mn(geo["global"]),
    }


def country_aggregation(geo: dict, title: str, limit: int = 14) -> dict:
    items = sorted(geo["countries"].items(), key=lambda kv: -kv[1])[:limit]
    return {
        "title": title,
        "rows": [{"label": c, "value": mn(v)} for c, v in items],
        "totalLabel": "Global Total", "total": mn(geo["global"]),
    }


def segment_aggregation(table: dict) -> dict:
    method = "bottom-up" if table["method"] == "bottom_up" else "top-down"
    return {
        "title": f"{table['dimension']} ({method}) → Global Total",
        "rows": [{"label": s, "value": mn(v)} for s, v in table["global_values"].items()],
        "totalLabel": "Global Total", "total": mn(table["global_total"]),
    }


def segment_steps(report: dict, approach: str, first_id: int) -> list[dict]:
    """Taxonomy -> shares -> top-down split steps shared by 2A and 2B."""
    tables = report["segmentation"]
    top = [t for t in tables if t["method"] == "top_down"]
    bottom = [t for t in tables if t["method"] == "bottom_up"]
    geo = report["geography"]
    steps = []
    sources = {t.get("taxonomy_source") for t in top}
    tax_source = "db" if "cmi_report_db" in sources else "llm"
    steps.append({
        "id": "seg_taxonomy", "name": "Segment Taxonomy", "data_tag": "real", "stage": "segments", "source": tax_source,
        "data_tag_note": "Read from 03_segments_approved.json of the recorded run.",
        "agent": {"name": "Segmentation Agent (CMI report DB → LLM check → LLM fallback)",
                  "role": "Takes the segment list from the matching CMI report when an LLM confirms it describes THIS market; otherwise the LLM proposes MECE dimensions."},
        "inputShort": "Market scope", "outputLabel": f"{len(top)} top-down dimension(s)"
        + (f" + {len(bottom)} bottom-up" if bottom else ""),
        "reason": "TOP-DOWN STARTS HERE: each segment is a share of the bottom-up country totals.",
        "output": [f"{t['dimension']} — {len(t['segments'])} segments ({'CMI report DB' if t.get('taxonomy_source') == 'cmi_report_db' else 'LLM-proposed'})" for t in top]
        + [f"{t['dimension']} — kept BOTTOM-UP from sizing" for t in bottom],
        "validation": [{"label": t["dimension"], "passed": True, "detail": ", ".join(t["segments"])} for t in tables],
        "activity": ["Looking up the CMI report taxonomy", "LLM check: does it describe this market?",
                     "Dropping geography dimensions", "Human approval gate"],
    })
    if top:
        t = top[0]
        sample_country = max(t["shares"], key=lambda c: geo["countries"].get(c, 0))
        verified = [e for e in t.get("evidence", []) if e.get("verified_in_snippets")]
        steps.append({
            "id": "seg_shares", "name": "Segment Shares per Country", "data_tag": "real", "stage": "shares",
            "source": "web" if t.get("evidence_status") == "web_evidence_backed" else "assumption",
            "data_tag_note": f"Shares for {t['dimension']} in {sample_country}, from 03_segments_approved.json.",
            "agent": {"name": "Share Agent (LLM + web snippets)",
                      "role": "One web-grounded LLM call per dimension proposes each segment's share in EVERY country. A share set is tagged web_evidence_backed only if a quoted figure appears verbatim in the fetched snippets."},
            "inputShort": f"{t['dimension']} segments", "outputLabel": f"Evidence: {t.get('evidence_status')}",
            "reason": "Approval gate: every country's shares must sum to 100% before the split runs.",
            "output": [{"l": s, "v": pct(v)} for s, v in t["shares"][sample_country].items()],
            "sources": [{"name": f"Quote used for {e.get('segment') or 'segment'}", "excerpt": e["quote"],
                         "decision": "accepted"} for e in verified[:4]],
            "validation": [{"label": tt["dimension"], "passed": tt.get("evidence_status") == "web_evidence_backed",
                            "detail": f"evidence_status: {tt.get('evidence_status')}; "
                                      f"{sum(1 for e in tt.get('evidence', []) if e.get('verified_in_snippets'))} quote(s) verified in snippets"}
                           for tt in top],
            "researcher_note": {"note": short(t.get("reasoning"), 600) or "No reasoning recorded."},
            "activity": ["Searching web for segment shares", "LLM proposes per-country shares",
                         "Checking quotes verbatim against snippets", "Normalising to 100% per country"],
        })
        seg = max(t["shares"][sample_country], key=t["shares"][sample_country].get)
        share = t["shares"][sample_country][seg]
        ctot = geo["countries"].get(sample_country, 0.0)
        steps.append({
            "id": "seg_split", "name": "Top-Down Split (Country Total × Share)", "data_tag": "real", "stage": "shares",
            "source": "code",
            "data_tag_note": "Deterministic arithmetic over the approved country totals and shares (demand_topdown/split.py).",
            "agent": {"name": "Deterministic Code — split_top_down", "role": "Country total × approved share for every country and segment; the last segment absorbs rounding so each country adds back exactly."},
            "inputShort": f"{sample_country} total × {seg} share", "outputLabel": f"{mn(ctot * share)} for {seg}, {sample_country}",
            "reason": "No LLM touches this number — it is the approved inputs multiplied out.",
            "flowCalc": {
                "a": {"value": mn(ctot), "unit": f"{sample_country} total demand", "src": "code"},
                "opLabel": "×",
                "b": {"value": pct(share), "unit": f"{seg} share", "src": "web" if t.get("evidence_status") == "web_evidence_backed" else "assumption"},
                "result": {"value": mn(ctot * share), "src": "code"},
                "final": {"big": mn(t["global_values"][seg]), "cap": f"{seg}, summed over every country (global)"},
            },
            "calculation": {
                "inputs": [{"label": f"{sample_country} total", "value": mn(ctot)}, {"label": f"{seg} share in {sample_country}", "value": pct(share, 2)}],
                "formula": "segment_value[country] = country_total[country] × share[country][segment]\nglobal[segment] = Σ countries",
                "output": f"{mn(ctot * share)} in {sample_country}; {mn(t['global_values'][seg])} globally",
            },
            "activity": ["Multiplying country totals by shares", "Summing countries → regions → global", "Repeating for every dimension"],
        })
    checks = report.get("consistency_checks", [])
    final_aggs = [region_aggregation(geo, "Region A + Region B + … → Global Total")]
    final_aggs += [segment_aggregation(t) for t in tables]
    steps.append({
        "id": "final_market_size", "name": "Final Market Size", "data_tag": "real", "stage": "final", "source": "code",
        "data_tag_note": f"From 04_market.json of the recorded {approach} run.",
        "agent": {"name": "Deterministic Code", "role": "Sums countries into regions and the global total; every segment table must add back to the same total."},
        "inputShort": f"{len(geo['countries'])} country totals", "outputLabel": f"{bn(geo['global'])} global",
        "reason": "Every dimension below reconciles to the same global number.",
        "big": {"value": bn(geo["global"]), "unit": f"global, base year {report.get('base_year')}"},
        "aggregations": final_aggs,
        "validation": [{"label": f"{c['dimension']} adds back to country totals", "passed": c["passed"],
                        "detail": f"max country gap {c['max_country_gap_usd_mn']} USD Mn, global gap {c['global_gap_usd_mn']} USD Mn"} for c in checks],
        "is_final": True,
        "activity": ["Summing countries → regions → global", "Building every segment table", "Reconciling all tables"],
    })
    return steps


# --------------------------------------------------------------------------- #
# 2A and 2B
# --------------------------------------------------------------------------- #
DEMAND_FLAG_STEPS = {
    "high_assumption_share": "evidence_search", "low_observed_share": "evidence_search", "no_observed_data": "evidence_search",
    "stale_evidence_source": "evidence_search", "country_cell_outlier": "calculation", "low_plausibility": "triangulation",
    "implausible_total": "final_market_size", "dominant_bucket_unit_mismatch_risk": "calculation",
    "target_commodity_unit_mismatch": "pricing", "driver_magnitude_implausible": "driver",
    "magnitude_cross_validation_discrepancy": "triangulation", "triangulation_large_gap": "triangulation",
}


def demand_common_steps(sizing: dict, setup: dict, approach: str) -> list[dict]:
    countries = sizing["countries"]
    rows = sizing["build_up_rows"]
    steps = [
        {
            "id": "setup", "name": "Market Setup", "data_tag": "real", "stage": "market", "source": "llm",
            "data_tag_note": "From 00_setup_approved.json of the recorded run.",
            "agent": {"name": "Setup Agent (LLM + web)", "role": "Drafts scope, country list, base year and units from web snippets; a human approves before anything else runs."},
            "inputShort": f"Market: {sizing['market_name']}", "outputLabel": f"{len(countries)} countries, base year {setup['base_year']}",
            "reason": "Every later step inherits this scope and country list unchanged.",
            "output": [{"l": "Market", "v": sizing["market_name"]}, {"l": "Base year", "v": str(setup["base_year"])},
                       {"l": "Physical unit", "v": setup["physical_unit"]}, {"l": "Countries", "v": f"{len(countries)}"}],
            "activity": ["Searching web for market definition", "Drafting scope + countries", "Human approval gate"],
        },
        {
            "id": "scope", "name": "Scope Statement", "data_tag": "real", "stage": "market", "source": "llm",
            "data_tag_note": "Field 'scope' of 00_setup_approved.json.",
            "agent": {"name": "Setup Agent (LLM)", "role": "Defines what is in and out of the market."},
            "inputShort": "Web snippets", "outputLabel": "Scope fixed",
            "reason": "Used later to reject use cases or segments that belong to another market.",
            "bigText": short(setup["scope"], 900),
            "activity": ["Reading market definitions", "Writing inclusions and exclusions"],
        },
        {
            "id": "geography", "name": "Geography", "data_tag": "real", "stage": "geography", "source": "code",
            "data_tag_note": "Field 'geography' of 00_setup_approved.json.",
            "agent": {"name": "Deterministic Code", "role": "Carries the approved countries into every later step and maps each to its region."},
            "inputShort": "Approved setup", "outputLabel": f"{len(countries)} countries",
            "reason": "Each line is sized once per country, then summed to region and global.",
            "output": countries,
            "activity": ["Loading approved country list", "Mapping countries to regions"],
        },
    ]
    return steps


def evidence_and_calc_steps(sizing: dict, label: str) -> list[dict]:
    rows = sizing["build_up_rows"]
    ex = best_row(rows)
    bucket, country = ex["usage_bucket"], ex["country"]
    attempts = (sizing.get("retrieval_attempts") or {}).get(bucket, {}).get(country, [])
    eq = sizing.get("evidence_quality") or {}
    price = (sizing.get("prices") or {}).get(bucket, {})
    price_attempts = (sizing.get("price_retrieval_attempts") or {}).get(bucket, {})
    price_attempt_list = next(iter(price_attempts.values()), []) if isinstance(price_attempts, dict) else []
    tag_counts: dict[str, int] = {}
    for r in rows:
        tag_counts[r.get("driver_evidence_type") or "assumption"] = tag_counts.get(r.get("driver_evidence_type") or "assumption", 0) + 1
    steps = [
        {
            "id": "evidence_search", "name": "Evidence Search & Retrieval", "data_tag": "real", "stage": "quantity",
            "source": evidence_source(ex.get("driver_evidence_type")),
            "data_tag_note": f"retrieval_attempts for '{bucket}' / {country} in 02_sizing.json — every attempt is recorded, not only the winner.",
            "agent": {"name": "Evidence Router + Web/PDF/Table/API Acquirers", "role": "Runs the driver's evidence plan source by source; a figure is 'verified_observed' only if the value appears in the downloaded source text."},
            "inputShort": f"{bucket} · {country}", "outputLabel": f"{num(ex.get('quantity'))} {ex.get('final_demand_unit')} ({ex.get('driver_evidence_type')})",
            "reason": f"Across all cells: " + ", ".join(f"{k} {v}" for k, v in sorted(tag_counts.items())) + ".",
            "big": {"value": num(ex.get("quantity")), "unit": f"{ex.get('final_demand_unit')} — {ex.get('driver_source') or 'no source'} ({ex.get('driver_year') or 'n/a'}), {ex.get('driver_evidence_type')}"},
            "search_queries": search_queries_from_attempts(attempts),
            "validation": [
                {"label": "Verified observed", "passed": (eq.get("observed_pct") or 0) > 0, "detail": f"{eq.get('observed_pct', 0)}% of driver/price inputs"},
                {"label": "Derived", "passed": True, "detail": f"{eq.get('derived_pct', 0)}%"},
                {"label": "Assumption", "passed": (eq.get("assumption_pct") or 0) < 50, "detail": f"{eq.get('assumption_pct', 0)}% — shown, never hidden"},
            ],
            "feeds_into": f"This quantity is priced in the next step, then multiplied out for {country}.",
            "activity": [f"{len(attempts)} source attempt(s) for {country}", "Content-checking each source", f"Accepted: {ex.get('driver_evidence_type')}"],
        },
        {
            "id": "quantities", "name": f"Quantity per {label}", "data_tag": "real", "stage": "quantity", "source": "code",
            "data_tag_note": "build_up_rows in 02_sizing.json.",
            "agent": {"name": "Demand Quantification (LLM-assisted, evidence-tagged)", "role": "One quantity per line and country, each with its own evidence tag."},
            "inputShort": f"{len(rows)} cells", "outputLabel": "Quantities tagged",
            "reason": (f"Step 5 showed one example; this is every {label.lower()} cell. "
                       f"{sum(1 for r in rows if r.get('driver_evidence_type') in ('verified_observed', 'derived'))} of {len(rows)} "
                       "are verified/derived from a source; the rest are labelled 'assumption' -- the LLM gave a figure "
                       "(often naming a source) but the number could not be found in any downloaded document."),
            "output": [{"l": f"{r['country']}" + (f" · {r['usage_bucket']}" if label != "Country" else ""),
                        "v": (f"{r['units'] / 1e6:,.1f}M {r['units_label']} × {r['kg_per_unit']:,.2f} kg = " if r.get("units") and r["units"] >= 1e6
                              else f"{r['units']:,.0f} {r['units_label']} × {r['kg_per_unit']:,.0f} kg = " if r.get("units") else "")
                             + f"{num(r.get('quantity'))} {r.get('final_demand_unit', '')} · {r.get('driver_evidence_type')}"}
                       for r in sorted(rows, key=lambda r: -(r.get("value_usd_mn") or 0))[:14]],
            "activity": ["Collecting quantities", "Tagging evidence per cell"],
        },
        {
            "id": "pricing", "name": "Pricing", "data_tag": "real", "stage": "price",
            "source": evidence_source(price.get("evidence_type")),
            "data_tag_note": f"prices['{bucket}'] and price_retrieval_attempts in 02_sizing.json.",
            "agent": {"name": "Pricing Agent (LLM + evidence plan)", "role": "Finds a price per demand unit; falls back to a labelled assumption when no source can be verified."},
            "carried_input": f"{num(ex.get('quantity'))} {ex.get('final_demand_unit')} for {country}.",
            "inputShort": f"Price per {price.get('priced_unit') or ex.get('final_demand_unit')}",
            "outputLabel": f"${num(price.get('asp_usd_per_unit'), 2)} ({price.get('evidence_type')})",
            "reason": "Price basis must be the market's own unit — a downstream product's price is flagged.",
            "big": {"value": f"${num(price.get('asp_usd_per_unit'), 2)}", "unit": f"per {price.get('priced_unit') or ex.get('final_demand_unit')} — {price.get('evidence_type')}"},
            "llm_call": {"purpose": f"Price '{bucket}'", "model": "deepseek-chat",
                         "promptSummary": "Find the average selling price per demand unit, with a citation.",
                         "responseSummary": short(price.get("rationale"), 300), "responseFull": short(price.get("rationale"), 2000)},
            "search_queries": search_queries_from_attempts(price_attempt_list, 5),
            "activity": ["Searching price sources", "Normalising price unit", f"Accepted: {price.get('evidence_type')}"],
        },
        {
            "id": "calculation", "name": "Calculation (Quantity × Price)", "data_tag": "real", "stage": "value", "source": "code",
            "data_tag_note": f"build_up_rows['{bucket}' / {country}] in 02_sizing.json — calculate_node, no LLM.",
            "agent": {"name": "Deterministic Code — calculate_node", "role": "value = quantity × price ÷ 1,000,000 (USD Mn). Pure arithmetic."},
            "inputShort": "Quantity × Price", "outputLabel": f"{mn(ex.get('value_usd_mn'))}",
            "reason": "This is the only place a market value is created; everything after is addition.",
            "flowCalc": {
                "a": {"value": num(ex.get("quantity")), "unit": f"{ex.get('final_demand_unit')} · {country}", "src": evidence_source(ex.get("driver_evidence_type"))},
                "opLabel": "×",
                "b": {"value": f"${num(ex.get('asp_usd_per_unit'), 2)}", "unit": f"per {ex.get('final_demand_unit')}", "src": evidence_source(ex.get("asp_evidence_type"))},
                "result": {"value": mn(ex.get("value_usd_mn")), "src": "code"},
                "final": {"big": mn(ex.get("value_usd_mn")), "cap": f"{country} · {bucket}"},
            },
            "calculation": {
                "inputs": [{"label": "Quantity", "value": f"{num(ex.get('quantity'))} {ex.get('final_demand_unit')} ({ex.get('driver_evidence_type')})"},
                           {"label": "Price", "value": f"${num(ex.get('asp_usd_per_unit'), 2)} per {ex.get('final_demand_unit')} ({ex.get('asp_evidence_type')})"}],
                "formula": "value_usd_mn = quantity × conversion_factor × price ÷ 1,000,000",
                "output": mn(ex.get("value_usd_mn")),
            },
            "activity": ["Multiplying every cell", f"{len(rows)} cells calculated"],
        },
    ]
    return steps


def triangulation_step(sizing: dict, total: float) -> dict:
    tri = sizing.get("triangulation") or []
    val = sizing.get("validation") or {}
    rows = [{"name": t.get("source") or t.get("category"), "excerpt": short(t.get("citation"), 300),
             "extractedValue": num(t.get("value")), "unit": t.get("unit"), "year": t.get("year"),
             "decision": "accepted"} for t in tri]
    return {
        "id": "triangulation", "name": "Cross-Check vs Published Estimates", "data_tag": "real", "stage": "validation",
        "source": "web" if tri else "code",
        "data_tag_note": "triangulation + validation in 02_sizing.json. Flag-only: never changes the number.",
        "agent": {"name": "Triangulation + Validation (web + code + LLM judge)", "role": "Compares the bottom-up total with published market sizes and runs plausibility checks. It can only raise flags."},
        "inputShort": f"Bottom-up total {bn(total)}",
        "outputLabel": (f"Published {bn(tri[0].get('value'))} → gap {tri[0].get('gap_pct')}%" if tri else "No published figure found"),
        "reason": "An independent referee: if the gap is large, a researcher reviews before trusting the number.",
        "output": [{"l": "Our bottom-up total", "v": bn(total)}]
        + [{"l": f"Published ({t.get('year')})", "v": f"{bn(t.get('value'))} · gap {t.get('gap_pct')}%"} for t in tri[:3]]
        + [{"l": "Plausibility score", "v": pct(val.get("plausibility_score"), 0)}],
        "sources": rows,
        "validation": [{"label": f"vs {t.get('source')}", "passed": (t.get("gap_pct") or 0) <= 40,
                        "detail": f"gap {t.get('gap_pct')}% (threshold 40%)"} for t in tri]
        + [{"label": f, "passed": False, "detail": "outlier flag"} for f in (val.get("outlier_flags") or [])[:3]]
        + [{"label": f.split(":")[0] + " vs IOC country total", "passed": abs(float(f.rsplit("(", 1)[-1].rstrip("%)"))) <= 25,
            "detail": f} for f in (val.get("ioc_checks") or [])],
        "activity": ["Searching published market sizes", "Computing gap %", "Plausibility judge"],
    }


def review_step(flags: list[dict]) -> dict:
    sev = {s: sum(1 for f in flags if f["severity"] == s) for s in ("critical", "warning", "info")}
    return {
        "id": "review", "name": "Review as Market Researcher", "data_tag": "real", "stage": "validation", "source": "code",
        "data_tag_note": "Every message is verbatim from quality_flags of the recorded run.",
        "agent": {"name": "Deterministic Code — quality.py", "role": "Rule-based checks over the finished run that say which numbers to distrust and why."},
        "inputShort": "The completed run", "outputLabel": f"{len(flags)} flags",
        "reason": "Shows exactly where a researcher should push back before trusting the number.",
        "output": f"{sev['critical']} critical, {sev['warning']} warning, {sev['info']} info",
        "is_review": True,
        "activity": ["Scanning for red flags", f"{len(flags)} flags raised"],
    }


def build_2a(market: str) -> Path | None:
    d = ROOT / "output" / "demand_country_2a" / slugify(market)
    setup, drv, sizing, report = (load(d / f) for f in ("00_setup_approved.json", "01_driver_approved.json", "02_sizing.json", "04_market.json"))
    if not all((setup, drv, sizing, report)):
        print(f"[2A] missing run files in {d}")
        return None
    bucket = drv["usage_buckets"][0]
    driver = drv["drivers"][bucket["name"]]
    strategy = driver.get("evidence_strategy") or {}
    geo = report["geography"]
    steps = demand_common_steps(sizing, setup, "2A")
    steps.append({
        "id": "driver", "name": "Total-Demand Driver (One Line per Country)", "data_tag": "real", "stage": "driver", "source": "llm",
        "data_tag_note": "From 01_driver_approved.json — 2A sizes the WHOLE market per country in one line, no application split.",
        "agent": {"name": "Driver Agent (LLM)", "role": "Picks the measurable driver for total demand and a priority-ordered evidence plan (sources, queries, fallbacks)."},
        "inputShort": bucket["name"], "outputLabel": driver.get("driver_name"),
        "reason": "Approval gate enforces exactly ONE total-demand line per country.",
        "output": [{"l": "Driver", "v": driver.get("driver_name")}, {"l": "Unit", "v": driver.get("final_demand_unit")},
                   {"l": "Formula", "v": driver.get("conversion_formula")},
                   {"l": "Preferred sources", "v": ", ".join(strategy.get("preferred_sources") or [])[:80] or "—"}],
        "llm_call": {"purpose": "Choose total-demand driver + evidence plan", "model": "deepseek-chat",
                     "promptSummary": f"Define the demand driver for '{bucket['name']}' in {setup['physical_unit']}.",
                     "responseSummary": short(driver.get("rationale"), 300),
                     "responseFull": short(strategy.get("derivation_path") or driver.get("rationale"), 2000)},
        "activity": ["Proposing driver", "Writing evidence plan", "Human approval gate"],
    })
    steps += evidence_and_calc_steps(sizing, "Country")
    steps.append({
        "id": "rollup", "name": "Country → Region → Global", "data_tag": "real", "stage": "global", "source": "code",
        "data_tag_note": "country_totals + region rollup (02_sizing.json / 04_market.json).",
        "agent": {"name": "Deterministic Code", "role": "Adds countries into regions, regions into the global total."},
        "inputShort": f"{len(geo['countries'])} country values", "outputLabel": f"{bn(geo['global'])} global",
        "reason": "BOTTOM-UP ENDS HERE: the global number is literally the sum of the countries. Top-down (segments) starts at the Segment Taxonomy step.",
        "aggregations": [country_aggregation(geo, "Country A + Country B + … → Global Total"),
                         region_aggregation(geo, "Region A + Region B + … → Global Total")],
        "activity": ["Summing countries", "Mapping to 5 regions", "Global total"],
    })
    steps.append(triangulation_step(sizing, geo["global"]))
    steps += segment_steps(report, "2A", len(steps))
    flags = quality_flags(sizing.get("quality_flags"), DEMAND_FLAG_STEPS, "final_market_size")
    steps.append(review_step(flags))
    stages = [{"key": k, "label": l} for k, l in (
        ("market", "Market"), ("geography", "Countries"), ("driver", "Total-Demand Driver"), ("quantity", "Quantity"),
        ("price", "Price"), ("value", "Country Value"), ("global", "Region + Global"), ("validation", "Cross-Check"),
        ("segments", "Segments"), ("shares", "Top-Down Split"), ("final", "Final"))]
    return render(
        "2A_Demand_Country_Total_Walkthrough.html",
        title=f"2A Demand-Side (Country Total) — {market}", h1=f"Approach 2A — {market}: Country Demand → Global, Segments Top-Down",
        subtitle="Track the demand of the market at country level, add the countries up to the global total, then split every segment top-down.",
        tab="2A · Demand-Side: Country Total", desc="Countries → demand in each country (quantity × price) → add up to global → split segments top-down.",
        stages=stages, steps=steps, flags=flags,
        banner=("real", f"Real recorded run · {market} · base year {setup['base_year']} · {bn(geo['global'])}"),
    )


def build_2b(market: str) -> Path | None:
    d = ROOT / "output" / "demand_usage" / slugify(market)
    setup, tax, sizing, report = (load(d / f) for f in ("00_setup_approved.json", "01_taxonomy_approved.json", "02_sizing.json", "04_market.json"))
    if not all((setup, tax, sizing, report)):
        print(f"[2B] missing run files in {d}")
        return None
    geo = report["geography"]
    flags_scope = sizing.get("scope_validation_flags") or {}
    drivers = sizing.get("drivers") or {}
    driver_names = [v.get("driver_name") for v in drivers.values()]
    reused = {n for n in driver_names if driver_names.count(n) > 1}
    steps = demand_common_steps(sizing, setup, "2B")
    steps.append({
        "id": "taxonomy", "name": "Use-Case Taxonomy", "data_tag": "real", "stage": "usage", "source": "llm",
        "data_tag_note": "From 01_taxonomy_approved.json + scope_validation_flags.",
        "agent": {"name": "Taxonomy Agent (LLM) + Scope Validator", "role": "Splits the market into end-use applications, then checks each one really consumes THIS commodity."},
        "inputShort": "Approved scope", "outputLabel": f"{len(tax['usage_buckets'])} use cases",
        "reason": "Each use case is sized separately in every country.",
        "output": [b["name"] for b in tax["usage_buckets"]],
        "validation": [{"label": n, "passed": f.get("classification") != "out_of_scope", "detail": f"{f.get('classification')}: {short(f.get('reason'), 200)}"}
                       for n, f in flags_scope.items()],
        "activity": ["Decomposing demand into applications", "Scope check per use case", "Coverage check", "Human approval gate"],
    })
    steps.append({
        "id": "driver", "name": "Demand Driver per Use Case", "data_tag": "real", "stage": "driver", "source": "llm",
        "data_tag_note": "drivers in 01_taxonomy_approved.json.",
        "agent": {"name": "Driver Agent (LLM) + Driver Library", "role": "Picks a measurable driver per use case; may reuse a similar driver from earlier markets."},
        "inputShort": f"{len(drivers)} use cases", "outputLabel": "Drivers chosen",
        "reason": "Quantity × price is computed per use case, per country.",
        "output": [{"l": b, "v": f"{v.get('driver_name')} ({v.get('final_demand_unit')})"} for b, v in drivers.items()],
        "researcher_note": {"note": "Needs attention: these use cases share the SAME driver — "
                                    + "; ".join(f"'{n}' used by " + ", ".join(b for b, v in drivers.items() if v.get('driver_name') == n) for n in reused)
                                    + ". The driver library reused a near-match instead of deriving a use-case-specific driver."} if reused else None,
        "activity": ["Looking up driver library", "Deriving new drivers", "Writing evidence plans"],
    })
    if not steps[-1]["researcher_note"]:
        del steps[-1]["researcher_note"]
    calc_steps = evidence_and_calc_steps(sizing, "Use Case × Country")
    we = sizing.get("worked_example")
    if we:
        # Bottom-up for one country, use case by use case: units x usage per unit.
        calc_steps.insert(2, {
            "id": "worked_example", "name": f"Bottom-Up Worked Example: {we['country']}", "data_tag": "real",
            "stage": "quantity", "source": "web",
            "data_tag_note": "Every line is units × usage per unit, from published sources (see Source Evidence).",
            "agent": {"name": "Bottom-up build (code)", "role": "Each use case sized on its own, then added up for the country."},
            "inputShort": f"{len(we['lines'])} use cases", "outputLabel": f"{we['total_t'] / 1000:,.0f} thousand t in {we['country']}",
            "reason": we.get("check", ""),
            "aggregations": [{"title": f"{we['country']}: use case 1 + use case 2 + … = country total (tonnes)",
                              "rows": [{"label": f"{l['use']}: {l['calc']}", "value": f"{l['tonnes'] / 1000:,.1f}k t"} for l in we["lines"]],
                              "totalLabel": f"{we['country']} total", "total": f"{we['total_t'] / 1000:,.1f}k t"}],
            "sources": [{"name": l["source"], "url": l["url"], "excerpt": f"{l['use']}: {l['calc']}",
                         "extractedValue": f"{l['tonnes']:,.0f}", "unit": "t", "decision": "accepted"} for l in we["lines"]],
            "activity": ["Counting units (people, outlets, factories)", "× usage per unit", "Adding use cases up"],
        })
    steps += calc_steps
    app_table = next((t for t in report["segmentation"] if t["method"] == "bottom_up"), None)
    aggs = []
    if app_table:
        aggs.append(segment_aggregation(app_table))
    aggs += [country_aggregation(geo, "Country A + Country B + … → Global Total"), region_aggregation(geo, "Region A + Region B + … → Global Total")]
    steps.append({
        "id": "rollup", "name": "Use Cases → Country → Region → Global", "data_tag": "real", "stage": "global", "source": "code",
        "data_tag_note": "country_totals_usd_mn + build_up_rows (02_sizing.json), region rollup (04_market.json).",
        "agent": {"name": "Deterministic Code", "role": "Adds use cases into each country, countries into regions, regions into global."},
        "inputShort": f"{len(sizing['build_up_rows'])} use-case × country cells", "outputLabel": f"{bn(geo['global'])} global",
        "reason": "BOTTOM-UP ENDS HERE: applications add up to countries, countries to global. Top-down (other segments) starts at the Segment Taxonomy step.",
        "aggregations": aggs,
        "activity": ["Summing use cases per country", "Summing countries per region", "Global total"],
    })
    steps.append(triangulation_step(sizing, geo["global"]))
    steps += segment_steps(report, "2B", len(steps))
    flags = quality_flags(sizing.get("quality_flags"), DEMAND_FLAG_STEPS, "final_market_size")
    steps.append(review_step(flags))
    stages = [{"key": k, "label": l} for k, l in (
        ("market", "Market"), ("geography", "Countries"), ("usage", "Use Cases"), ("driver", "Drivers"), ("quantity", "Quantity"),
        ("price", "Price"), ("value", "Use-Case Value"), ("global", "Country→Global"), ("validation", "Cross-Check"),
        ("segments", "Other Segments"), ("shares", "Top-Down Split"), ("final", "Final"))]
    return render(
        "2B_Demand_UseCase_Country_Walkthrough.html",
        title=f"2B Demand-Side (Use Case × Country) — {market}",
        h1=f"Approach 2B — {market}: Use Case × Country → Global, Other Segments Top-Down",
        subtitle="Track demand for each application in each country, add applications up to the country and countries up to the global total; other segments are split top-down.",
        tab="2B · Demand-Side: Use Case × Country",
        desc="Countries → applications → quantity × price for each application in each country → add up → global → other segments top-down.",
        stages=stages, steps=steps, flags=flags,
        banner=("real", f"Real recorded run · {market} · base year {setup['base_year']} · {bn(geo['global'])}"),
    )


# --------------------------------------------------------------------------- #
# 2C (illustrative, built on the real 2B run)
# --------------------------------------------------------------------------- #
# Price of each product grade relative to the use case's average price.
# Illustrative: a real 2C run would source a price per product type.
GRADE_PRICE_RATIO_HINTS = [
    (("extra virgin", "premium", "organic"), 1.30), (("virgin",), 1.00), (("refined", "pure"), 0.75),
    (("pomace", "residue", "industrial"), 0.50),
]


def price_ratio(product: str) -> float:
    low = product.lower()
    for words, ratio in GRADE_PRICE_RATIO_HINTS:
        if any(w in low for w in words):
            return ratio
    return 0.85


def build_2c(market: str) -> Path | None:
    d = ROOT / "output" / "demand_usage" / slugify(market)
    setup, tax, sizing, seg = (load(d / f) for f in ("00_setup_approved.json", "01_taxonomy_approved.json", "02_sizing.json", "03_segments_approved.json"))
    if not all((setup, tax, sizing, seg)):
        print(f"[2C] needs a finished 2B run (with segments) in {d}")
        return None
    product_dim = next((t for t in seg.get("top_down_dimensions", []) if "product" in t["dimension"].lower() or "type" in t["dimension"].lower()), None)
    if product_dim is None:
        print("[2C] the 2B run has no product-type dimension to use as the product mix")
        return None
    products = product_dim["segments"]
    mix = product_dim["shares"]  # {country: {product: share}}
    rows = [r for r in sizing["build_up_rows"] if r.get("bucket_category", "physical_consumption") == "physical_consumption"]
    apps = list(dict.fromkeys(r["usage_bucket"] for r in rows))

    # cell = (country, application, product): quantity x price, all code
    cells = []
    for r in rows:
        qty, asp = r.get("quantity") or 0.0, r.get("asp_usd_per_unit") or 0.0
        if not qty or not asp:
            continue
        for p in products:
            share = (mix.get(r["country"]) or {}).get(p, 0.0)
            pq = qty * share
            pp = asp * price_ratio(p)
            cells.append({"country": r["country"], "app": r["usage_bucket"], "product": p, "qty": pq, "price": pp,
                          "unit": r.get("final_demand_unit", ""), "value": pq * pp / 1_000_000})
    if not cells:
        print("[2C] no usable quantity/price cells in the 2B run")
        return None

    def total(filter_fn) -> float:
        return sum(c["value"] for c in cells if filter_fn(c))

    from me_engine.demand_topdown.regions import region_for, REGION_ORDER  # noqa: E402

    countries = list(dict.fromkeys(c["country"] for c in cells))
    country_tot = {k: total(lambda c, k=k: c["country"] == k) for k in countries}
    region_tot: dict[str, float] = {}
    for k, v in country_tot.items():
        region_tot[region_for(k)] = region_tot.get(region_for(k), 0.0) + v
    region_tot = {r: region_tot[r] for r in REGION_ORDER if r in region_tot}
    global_tot = sum(country_tot.values())
    product_tot = {p: total(lambda c, p=p: c["product"] == p) for p in products}
    app_tot = {a: total(lambda c, a=a: c["app"] == a) for a in apps}

    ex_country = max(country_tot, key=country_tot.get)
    ex_app = max(apps, key=lambda a: total(lambda c, a=a: c["app"] == a and c["country"] == ex_country))
    ex_cells = [c for c in cells if c["country"] == ex_country and c["app"] == ex_app]
    ex = max(ex_cells, key=lambda c: c["value"])
    ex_app_total = sum(c["value"] for c in ex_cells)
    note = ("ILLUSTRATIVE — approach 2C is designed but not yet implemented in code. Quantities per use case and "
            f"country come from the real {market} 2B run; the product mix per country comes from that run's "
            "evidence-backed product-type shares; the price ratio between product grades is an illustrative assumption. "
            "A live 2C run would size and price each product type separately with its own evidence.")
    other_dims = [t for t in seg.get("top_down_dimensions", []) if t is not product_dim]

    steps = [
        {
            "id": "setup", "name": "Market Setup", "data_tag": "real", "stage": "market", "source": "llm",
            "data_tag_note": f"Reused from the real {market} 2B run (00_setup_approved.json).",
            "agent": {"name": "Setup Agent (LLM + web)", "role": "Scope, countries, base year and units — identical to 2B."},
            "inputShort": f"Market: {market}", "outputLabel": f"{len(countries)} countries",
            "reason": "2C starts exactly like 2B.",
            "output": countries,
            "activity": ["Reusing approved 2B setup"],
        },
        {
            "id": "usecases", "name": "Use Cases (Applications)", "data_tag": "real", "stage": "usage", "source": "llm",
            "data_tag_note": f"Reused from the real {market} 2B run (01_taxonomy_approved.json).",
            "agent": {"name": "Taxonomy Agent (LLM) + Scope Validator", "role": "Same use-case decomposition as 2B."},
            "inputShort": "Approved scope", "outputLabel": f"{len(apps)} use cases",
            "reason": "2C adds one more level BELOW each use case.",
            "output": apps,
            "activity": ["Reusing approved 2B use cases"],
        },
        {
            "id": "products", "name": "Product Types inside Each Use Case", "data_tag": "illustrative", "data_tag_note": note,
            "stage": "product", "source": "llm",
            "agent": {"name": "Product-Type Agent (LLM) — the one new node 2C needs",
                      "role": "Lists which product types are used inside each use case (same pattern as the India variant's extra 'Actual Use Case' level)."},
            "inputShort": f"{len(apps)} use cases", "outputLabel": f"{len(apps)} × {len(products)} product cells",
            "reason": "Every (use case, product type) pair becomes its own line, sized in every country.",
            "output": [f"{a} → {', '.join(products)}" for a in apps],
            "activity": ["Listing product types per use case", "Checking each product belongs to the market", "Human approval gate"],
        },
        {
            "id": "mix", "name": "Product Volume Mix per Country", "data_tag": "illustrative", "data_tag_note": note,
            "stage": "quantity", "source": "web" if product_dim.get("evidence_status") == "web_evidence_backed" else "assumption",
            "agent": {"name": "Demand Quantification per Product", "role": f"Quantity of each product type inside each use case. Here: the use-case quantity × the product's share in that country ({product_dim['dimension']}, evidence: {product_dim.get('evidence_status')})."},
            "inputShort": f"{ex_app} · {ex_country}", "outputLabel": "Product quantities",
            "reason": "In a live 2C run, each product's quantity would have its own driver and sources.",
            "output": [{"l": p, "v": f"{pct((mix.get(ex_country) or {}).get(p, 0.0))} of volume"} for p in products],
            "activity": [f"Splitting {ex_app} volume in {ex_country}", "Repeating for every use case and country"],
        },
        {
            "id": "price", "name": "Price per Product Type", "data_tag": "illustrative", "data_tag_note": note,
            "stage": "price", "source": "assumption",
            "agent": {"name": "Pricing Agent per Product", "role": "A price per product type. Here: the use case's real 2B price × an illustrative grade ratio."},
            "inputShort": f"{ex_app} average price", "outputLabel": "Product prices",
            "reason": "Product grades sell at very different prices — this is what 2C captures that 2B cannot.",
            "output": [{"l": p, "v": f"${num((next((c['price'] for c in ex_cells if c['product'] == p), 0)), 0)} per {ex['unit']} (×{price_ratio(p):.2f})"} for p in products],
            "activity": ["Pricing each product grade", "Labelled illustrative"],
        },
        {
            "id": "calculation", "name": "Calculation (Product Quantity × Product Price)", "data_tag": "illustrative", "data_tag_note": note,
            "stage": "value", "source": "code",
            "agent": {"name": "Deterministic Code", "role": "value = quantity × price ÷ 1,000,000, for every country × use case × product cell."},
            "inputShort": f"{ex['product']} · {ex_app} · {ex_country}", "outputLabel": mn(ex["value"]),
            "reason": f"{len(cells)} cells calculated this way.",
            "flowCalc": {
                "a": {"value": num(ex["qty"]), "unit": f"{ex['unit']} of {ex['product']}", "src": "derived"},
                "opLabel": "×",
                "b": {"value": f"${num(ex['price'])}", "unit": f"per {ex['unit']}", "src": "assumption"},
                "result": {"value": mn(ex["value"]), "src": "code"},
                "final": {"big": mn(ex["value"]), "cap": f"{ex_country} · {ex_app} · {ex['product']}"},
            },
            "activity": [f"{len(cells)} product cells calculated"],
        },
        {
            "id": "rollup_app", "name": "Products → Use Case (per Country)", "data_tag": "illustrative", "data_tag_note": note,
            "stage": "app", "source": "code",
            "agent": {"name": "Deterministic Code", "role": "Adds product types into their use case, per country."},
            "inputShort": f"{len(products)} products", "outputLabel": f"{ex_app}, {ex_country}: {mn(ex_app_total)}",
            "reason": "First bottom-up rollup level that 2B does not have.",
            "aggregations": [{"title": f"Products → {ex_app} ({ex_country})",
                              "rows": [{"label": c["product"], "value": mn(c["value"])} for c in ex_cells],
                              "totalLabel": ex_app, "total": mn(ex_app_total)}],
            "activity": ["Summing products per use case"],
        },
        {
            "id": "rollup_country", "name": "Use Cases → Country → Region → Global", "data_tag": "illustrative", "data_tag_note": note,
            "stage": "global", "source": "code",
            "agent": {"name": "Deterministic Code", "role": "Use cases into country, countries into region, regions into global."},
            "inputShort": f"{len(countries)} countries", "outputLabel": bn(global_tot),
            "reason": "Same rollup as 2B, one level deeper underneath.",
            "aggregations": [
                {"title": "Use Case A + Use Case B + … → Global", "rows": [{"label": a, "value": mn(v)} for a, v in app_tot.items()], "totalLabel": "Global", "total": mn(global_tot)},
                {"title": "Region A + Region B + … → Global", "rows": [{"label": r, "value": mn(v)} for r, v in region_tot.items()], "totalLabel": "Global", "total": mn(global_tot)},
            ],
            "activity": ["Summing use cases per country", "Summing countries per region", "Global total"],
        },
        {
            "id": "final_market_size", "name": "Final Market Size", "data_tag": "illustrative", "data_tag_note": note,
            "stage": "final", "source": "code",
            "agent": {"name": "Deterministic Code", "role": "Product Type AND Application are both bottom-up tables; any remaining segment (e.g. channel) would be split top-down."},
            "inputShort": f"{len(cells)} cells", "outputLabel": bn(global_tot),
            "reason": "Both Product Type and Application tables come from the build-up, not from shares.",
            "big": {"value": bn(global_tot), "unit": "illustrative global total"},
            "aggregations": [
                {"title": "By Product Type (bottom-up) → Global", "rows": [{"label": p, "value": mn(v)} for p, v in product_tot.items()], "totalLabel": "Global", "total": mn(global_tot)},
                {"title": "By Application (bottom-up) → Global", "rows": [{"label": a, "value": mn(v)} for a, v in app_tot.items()], "totalLabel": "Global", "total": mn(global_tot)},
            ] + [{"title": f"{t['dimension']} (top-down) → Global",
                  "rows": [{"label": s, "value": mn(sum(country_tot[k] * (t['shares'].get(k) or {}).get(s, 0.0) for k in countries))} for s in t["segments"]],
                  "totalLabel": "Global", "total": mn(global_tot)} for t in other_dims],
            "is_final": True,
            "activity": ["Building Product Type table", "Building Application table", "Top-down for remaining segments"],
        },
    ]
    stages = [{"key": k, "label": l} for k, l in (
        ("market", "Market"), ("usage", "Use Cases"), ("product", "Product Types"), ("quantity", "Product Qty"),
        ("price", "Product Price"), ("value", "Cell Value"), ("app", "→ Use Case"), ("global", "→ Country → Global"), ("final", "Final"))]
    return render(
        "2C_Demand_Product_UseCase_Country_Walkthrough.html",
        title=f"2C Demand-Side (Product × Use Case × Country) — {market}",
        h1=f"Approach 2C — {market}: Product Type × Use Case × Country → Global",
        subtitle="Inside each application in each country, track demand for each product type; add product types up to the application, applications to the country, countries to global.",
        tab="2C · Demand-Side: Product × Use Case × Country",
        desc="Market → Countries → Use cases → Product types inside each use case → quantity × price per product → Use case → Country → Region → Global.",
        stages=stages, steps=steps, flags=[],
        banner=("illustrative", f"Design walkthrough · not yet implemented in code · built on the real {market} 2B run"),
    )


# --------------------------------------------------------------------------- #
# Desk Research
# --------------------------------------------------------------------------- #
def desk_validation_step(log: list[str]) -> dict:
    """The Desk Research check, shown once: every pass's score and the rule
    that sends a failing pass back to step 3 (Market Size)."""
    try:
        from me_engine.generation.config import GenConfig

        cfg = GenConfig.load(ROOT)
        threshold, min_it, max_it = cfg.reward_threshold, cfg.min_loop_iters, cfg.max_loop_iters
    except Exception:  # noqa: BLE001
        threshold, min_it, max_it = 0.9, 2, 4
    passes = []
    for line in log:
        m = re.search(r"Review pass (\d+): confidence (\d+)%.*?consistency (\d+)%.*?plausibility (\d+)%", line)
        if m:
            passes.append(tuple(int(x) for x in m.groups()))
    best = max(passes, key=lambda p: p[1]) if passes else None
    rows, checks = [], []
    for n, conf, consist, plaus in passes:
        if conf / 100 >= threshold and n >= min_it:
            decision = "passed → stop"
        elif n == len(passes):
            decision = f"{max_it}-pass limit reached → stop"
        else:
            decision = "below threshold → back to step 3"
        rows.append({"label": f"Pass {n}", "value": f"{conf}% — {decision}"})
        checks.append({"label": f"Pass {n}: score {conf}%", "passed": conf / 100 >= threshold,
                       "detail": f"consistency (code) {consist}%, plausibility (LLM judge) {plaus}% — {decision}"})
    kept = next((l for l in log if l.startswith("Kept the best")), "")
    return {
        "id": "validation", "name": "Validation (Review Score)", "data_tag": "real", "stage": "refine", "source": "code",
        "data_tag_note": "reward_node + route_after_reward — review-pass log lines from the recorded run.",
        "agent": {"name": "Reward Agent: deterministic checks + LLM judge",
                  "role": "Consistency (code): shares add to 100%, regions add up. Plausibility (LLM judge): do the numbers make sense? Combined into one score."},
        "inputShort": "Steps 3–6", "outputLabel": f"best {best[1]}% (pass {best[0]})" if best else "scored",
        "reason": (f"Rule: if the score is below {threshold:.0%}, go back to step 3 (Market Size) and redo steps 3–6 "
                   f"with the reviewer's comments. At least {min_it} and at most {max_it} passes; the best pass is kept. "
                   + (kept or "")),
        "output": [{"l": r["label"], "v": r["value"]} for r in rows]
        + ([{"l": "Kept", "v": f"Pass {best[0]} ({best[1]}%)"}] if best else []),
        "validation": checks,
        "activity": ["Consistency checks (code)", "Plausibility vote (LLM)",
                     f"Below {threshold:.0%} → back to step 3", "Keep the best pass"],
    }


def build_desk(market: str) -> Path | None:
    state = load(ROOT / "output" / "example_runs" / slugify(market) / "desk_state.json")
    if not state:
        print(f"[Desk] no desk_state.json for {market}")
        return None
    log = state.get("log") or []
    # Runs before 2026-10-05 stored a 2026 anchor; newer runs store 2025 (the ME base year).
    year = 2025 if "global_anchor_2025" in state else 2026
    anchor, cagr = state.get("global_anchor_2025", state.get("global_anchor_2026")), state.get("global_cagr")
    regional = state.get("regional") or {}
    geo = state.get("geo") or {}
    shares = state.get("shares") or {}
    asp = state.get("asp") or {}
    arts = state.get("artifacts") or []
    reviews = [l for l in log if l.startswith("Review pass")]
    sizes = [l for l in log if l.startswith("Estimated total market size")]
    region_vals = {r: anchor * v.get("value_share", 0.0) for r, v in regional.items()}
    first_dim = next(iter(shares), None)
    seg_root = ((shares.get(first_dim) or {}).get("__global__") or {}).get("__root__", {}) if first_dim else {}
    best = max(reviews, key=lambda l: float(re.search(r"confidence (\d+)%", l).group(1)) if re.search(r"confidence (\d+)%", l) else 0) if reviews else ""
    steps = [
        {
            "id": "evidence", "name": "Research & Evidence Gathering", "data_tag": "real", "stage": "market", "source": "db",
            "data_tag_note": "structure_node — log line from the recorded run.",
            "agent": {"name": "Structure Agent (CMI report DB + web)", "role": "Finds the matching CMI report (keyword + semantic search, LLM-verified) and live web snippets that ground every later call."},
            "inputShort": f"Market: {market}", "outputLabel": "Evidence pool gathered",
            "reason": "All later estimates are grounded against this same evidence pool.",
            "output": next((l for l in log if l.startswith("Evidence gathered")), "Evidence gathered"),
            "activity": ["Searching CMI report DB", "Verifying the match with the LLM", "Pulling web snippets"],
        },
        {
            "id": "skeleton", "name": "Report Taxonomy (Skeleton)", "data_tag": "real", "stage": "market", "source": "db",
            "data_tag_note": "Segment dimensions and geographies from the CMI report's segmentation tree.",
            "agent": {"name": "Skeleton Builder (DB tree → LLM extraction, regex fallback)", "role": "Turns the report's segmentation into dimensions, segments and geographies — labels only, no numbers."},
            "inputShort": "Matched report", "outputLabel": f"{len(shares)} dimensions, {len(regional)} region(s)",
            "reason": "The Input Sheet must have exactly the report's taxonomy.",
            "output": list(shares.keys()) + [f"Region: {r}" for r in regional],
            "activity": ["Reading segmentation tree", "Extracting dimensions + geographies"],
        },
        {
            "id": "market_size", "name": "Market Size + CAGR", "data_tag": "real", "stage": "size", "source": "llm",
            "data_tag_note": "market_size_node — every iteration's log line is shown below.",
            "agent": {"name": "Market Size Agent (LLM)", "role": "Proposes the base-year global value and CAGR, grounded on a cited figure where one exists; code checks the unit (millions vs billions)."},
            "inputShort": "Evidence pool", "outputLabel": f"{bn(anchor)} · CAGR {pct(cagr)}",
            "reason": "Top-down: the whole market is one number first, then split.",
            "big": {"value": bn(anchor), "unit": f"{year} global · CAGR {pct(cagr)}"},
            "activity": [f"{len(sizes)} sizing pass(es)", "Grounding on cited figures", "Unit check (millions vs billions)"],
        },
        {
            "id": "regional", "name": "Region & Country Split", "data_tag": "real", "stage": "allocate", "source": "llm",
            "data_tag_note": "regional + geo in the recorded state.",
            "agent": {"name": "Geography Agent (LLM)", "role": "Splits the global value into regions, and regions into countries."},
            "inputShort": bn(anchor), "outputLabel": f"{len(regional)} region(s)",
            "reason": "Shares of a top-down total — not built from country data.",
            "aggregations": [{"title": "Region shares × global → region value",
                              "rows": [{"label": f"{r} — {pct(v.get('value_share'))}", "value": mn(region_vals[r])} for r, v in regional.items()],
                              "totalLabel": f"Global ({year})", "total": mn(anchor)}],
            "output": [{"l": c, "v": f"{pct(v.get('share_of_region'))} of {r}"} for r, cs in geo.items() for c, v in cs.items()][:16],
            "activity": ["LLM proposes regional split", "LLM proposes country split"],
        },
        {
            "id": "segments", "name": "Segment Shares", "data_tag": "real", "stage": "allocate", "source": "llm",
            "data_tag_note": "shares in the recorded state.",
            "agent": {"name": "Shares Agent (LLM)", "role": "Splits the market across every segment of every dimension."},
            "inputShort": f"{len(shares)} dimensions", "outputLabel": f"{first_dim}: {len(seg_root)} segments",
            "reason": "Segment values = global value × share.",
            "aggregations": [{"title": f"{first_dim} — share × global",
                              "rows": [{"label": f"{s} — {pct(v)}", "value": mn(anchor * v)} for s, v in seg_root.items()],
                              "totalLabel": "Global", "total": mn(anchor)}] if seg_root else None,
            "activity": ["LLM proposes segment shares", "Normalising to 100%"],
        },
        {
            "id": "asp", "name": "Prices", "data_tag": "real", "stage": "price", "source": "llm",
            "data_tag_note": "asp in the recorded state.",
            "agent": {"name": "ASP Agent (LLM)", "role": "Proposes a price per product, used later to turn value into volume."},
            "inputShort": "Products", "outputLabel": f"{len(asp)} prices",
            "reason": "Volume = value ÷ ASP in the ME workbook.",
            "output": [{"l": p, "v": f"${num(v, 2)}"} for p, v in asp.items()],
            "activity": ["LLM proposes ASPs"],
        },
        desk_validation_step(log),
        {
            "id": "brain", "name": "Justification (Brain)", "data_tag": "real", "stage": "refine", "source": "llm",
            "data_tag_note": "artifacts in the recorded state.",
            "agent": {"name": "Brain Agent (LLM)", "role": "Writes plain-language Q&A explaining the final numbers."},
            "inputShort": "Final numbers", "outputLabel": f"{len(arts)} reasoning notes",
            "reason": "The 'why' behind the numbers, for the report text.",
            "output": [a.get("question", "") for a in arts],
            "llm_call": {"purpose": "Explain the finalized numbers", "model": "deepseek-chat",
                         "promptSummary": arts[0].get("question", "") if arts else "",
                         "responseSummary": short(arts[0].get("answer"), 300) if arts else "",
                         "responseFull": short(arts[0].get("answer"), 2000) if arts else ""},
            "activity": ["Writing justification notes"],
        },
        {
            "id": "final_market_size", "name": "Final Market Size → Input Sheet → ME Workbook", "data_tag": "real", "stage": "final", "source": "code",
            "data_tag_note": "Final state written to the Input Sheet (Data / ASP / Market Value sheets), then expanded to the 2021–2033 ME workbook.",
            "agent": {"name": "Sheet Writer + ME Pipeline (deterministic)", "role": "Writes the Input Sheet; run_pipeline.py expands it to Value / ASP / Volume × 13 years."},
            "inputShort": "Best iteration", "outputLabel": f"{bn(anchor)} ({year})",
            "reason": "Top-down: one global number, then shares.",
            "big": {"value": bn(anchor), "unit": f"{year} global · CAGR {pct(cagr)} · reward {state.get('reward')}"},
            "aggregations": [{"title": f"Region A + Region B + … → Global ({year})",
                              "rows": [{"label": r, "value": mn(v)} for r, v in region_vals.items()],
                              "totalLabel": f"Global ({year})", "total": mn(anchor)}],
            "is_final": True,
            "activity": ["Writing Input Sheet", "Expanding to ME workbook"],
        },
    ]
    for s in steps:
        if s.get("aggregations") is None:
            s.pop("aggregations", None)
    stages = [{"key": k, "label": l} for k, l in (
        ("market", "Research"), ("size", "Global Size"), ("allocate", "Region / Country / Segments"),
        ("price", "Price"), ("refine", "Review Loop"), ("final", "Final"))]
    return render(
        "1_Desk_Research_Walkthrough.html",
        title=f"1 Desk Research — {market}", h1=f"Approach 1 — {market}: Desk Research (Top-Down)",
        subtitle="Collect the market size already published by third parties, competitors and associations — the total and its split by segments, applications and the rest of the taxonomy.",
        tab="1 · Desk Research", desc="Published reports → total market size and growth → split by region and country → split by segment.",
        stages=stages, steps=steps, flags=[],
        banner=("real", f"Real recorded run · {market} · {year} · {bn(anchor)}"),
    )


# --------------------------------------------------------------------------- #
# Supply-Side
# --------------------------------------------------------------------------- #
def build_supply(market: str) -> Path | None:
    rep = load(ROOT / "output" / "supply_side" / f"{slugify(market)}-examples.json")
    if not rep or not rep.get("company_based"):
        print(f"[Supply] no company-based supply report for {market}")
        return None
    comp = rep["company_based"]
    evid = rep.get("evidence_based") or {}
    breakdown = comp.get("country_breakdown") or []
    total = comp.get("total_usd_mn") or 0.0
    sources = comp.get("company_data_sources") or {}
    company_value: dict[str, float] = {}
    for cb in breakdown:
        for name, share in (cb.get("company_shares") or {}).items():
            company_value[name] = company_value.get(name, 0.0) + share * (cb.get("country_total_usd_mn") or 0.0)
    top = sorted(company_value.items(), key=lambda kv: -kv[1])
    named_total = sum(cb.get("named_total_usd_mn") or 0.0 for cb in breakdown)
    ex = max(breakdown, key=lambda cb: cb.get("country_total_usd_mn") or 0.0) if breakdown else {}
    src_count = {k: sum(1 for v in sources.values() if v == k) for k in ("financial_api", "web_evidence", "no_real_data")}
    figures = evid.get("figures") or []
    steps = [
        {
            "id": "vendors", "name": "Vendor List", "data_tag": "real", "stage": "vendors",
            "source": "web" if "vendor-intelligence" in (rep.get("company_list_source") or "") else "llm",
            "data_tag_note": f"Company list source: {rep.get('company_list_source') or 'quick LLM discovery'}.",
            "agent": ({"name": "Vendor Intelligence (company finder)", "role": "Multi-round web search, validation and classification of the companies in this market; only Manufacturers are used."}
                      if "vendor-intelligence" in (rep.get("company_list_source") or "") else
                      {"name": "Quick discovery (LLM + web) — fallback", "role": "Used only when no vendor-intelligence report exists for this market; lists well-known companies and HTTP-checks each website."}),
            "inputShort": f"Market: {market}", "outputLabel": f"{len(sources)} companies"
            + (f" · ≈{cov['estimated_coverage_pct']:.0f}% of market" if (cov := rep.get("vendor_intel_coverage") or {}).get("estimated_coverage_pct") is not None else ""),
            "reason": ("Only direct revenue contributors to THIS market are counted. "
                       + (f"Vendor intelligence estimates the companies found cover ≈{cov['estimated_coverage_pct']:.0f}% of a "
                          f"≈{bn(cov.get('total_market_size_estimate_usd_mn'))} market (an estimate; runtime "
                          f"{(cov.get('runtime_seconds') or 0) / 60:.0f} min)." if cov.get("estimated_coverage_pct") is not None else "")),
            "output": ([{"l": m.get("name") or m.get("company", ""),
                         "v": " · ".join(x for x in (
                             f"≈{bn(m.get('est_revenue_usd_mn') or m.get('Est_Revenue_USD_Mn'))}" if (m.get('est_revenue_usd_mn') or m.get('Est_Revenue_USD_Mn')) else "",
                             f"≈{float(m.get('est_market_share_pct') or m.get('Est_Market_Share_Pct')):.1f}% share" if (m.get('est_market_share_pct') or m.get('Est_Market_Share_Pct')) else "") if x) or "major player"}
                        for m in (cov.get("majors") or [])[:15]]
                       or list(sources.keys())),
            "activity": (["Finding the largest players first", "Multi-round web search", "Validating & classifying companies",
                          "Stopping at ~70-80% market coverage or the time limit"]
                         if "vendor-intelligence" in (rep.get("company_list_source") or "") else ["LLM + web discovery", "Website verification"]),
        },
        {
            "id": "screening", "name": "Screening: Direct Revenue Contributors Only", "data_tag": "real", "stage": "vendors", "source": "code",
            "data_tag_note": "excluded_companies + merged_companies in the recorded supply-side report.",
            "agent": {"name": "Deterministic Code — screen_node", "role": "Drops distributors/retailers (they resell other vendors' products, so counting them double-counts revenue) and merges brands into their parent company."},
            "inputShort": f"{len(sources) + len(comp.get('excluded_companies') or []) + sum(len(m.get('merged') or []) for m in comp.get('merged_companies') or [])} discovered",
            "outputLabel": f"{len(comp.get('excluded_companies') or [])} excluded · {sum(len(m.get('merged') or []) for m in comp.get('merged_companies') or [])} merged",
            "reason": "Only companies that earn revenue directly from this market are counted, each exactly once.",
            "output": [{"l": e.get("name", ""), "v": f"excluded — {e.get('role', '')}"} for e in (comp.get("excluded_companies") or [])[:10]]
            + [{"l": m.get("kept", ""), "v": "kept; merged " + ", ".join(m.get("merged") or [])} for m in (comp.get("merged_companies") or [])[:10]]
            or "No distributors or duplicates found",
            "activity": ["Checking each company's role", "Excluding distributors/retailers", "Merging brands into parents"],
        },
        {
            "id": "revenue", "name": "Company Total Revenue", "data_tag": "real", "stage": "revenue", "source": "web",
            "data_tag_note": "company_data_sources: where each company's revenue came from.",
            "agent": {"name": "Financials Agent (Yahoo Finance + InsightSentry)", "role": "Pulls each company's real total revenue, plus revenue by region and business line where available."},
            "inputShort": f"{len(sources)} companies", "outputLabel": f"{src_count['financial_api']} with real financials",
            "reason": "Real reported revenue caps every later estimate.",
            "output": [{"l": n, "v": {"financial_api": "real financials", "web_evidence": "web evidence", "no_real_data": "no real data"}.get(s, s)} for n, s in sources.items()],
            "validation": [{"label": "Real financial data", "passed": src_count["financial_api"] > 0, "detail": f"{src_count['financial_api']} companies"},
                           {"label": "Web evidence only", "passed": True, "detail": f"{src_count['web_evidence']} companies"},
                           {"label": "No real data", "passed": src_count["no_real_data"] == 0, "detail": f"{src_count['no_real_data']} companies"}],
            "activity": ["Querying Yahoo Finance", "Querying InsightSentry segments"],
        },
        {
            "id": "attributable", "name": "Revenue Attributable to This Market, per Country", "data_tag": "real", "stage": "attributable", "source": "llm",
            "data_tag_note": "named_total_usd_mn per country in the recorded report.",
            "agent": {"name": "Estimator Agent (LLM, capped in code)", "role": "Keeps only the part of each company's revenue that belongs to this market (e.g. only Google's cloud-API revenue, not all of Google), split by country; capped at the company's real total revenue."},
            "inputShort": "Company revenue + business-line mix", "outputLabel": f"{mn(named_total)} named-vendor revenue",
            "reason": "The market-specific slice of each vendor's revenue.",
            "aggregations": [{"title": "Named-vendor revenue by country",
                              "rows": [{"label": cb["country"], "value": mn(cb.get("named_total_usd_mn"))} for cb in breakdown],
                              "totalLabel": "Named vendors, all countries", "total": mn(named_total)}],
            "activity": ["Reading business-line splits", "Estimating market-specific fraction", "Capping at real revenue"],
        },
        {
            "id": "coverage", "name": "Gross-Up for Vendors Not Named", "data_tag": "real", "stage": "coverage", "source": "llm",
            "data_tag_note": "coverage_fraction per country in the recorded report.",
            "agent": {"name": "Coverage Agent (LLM) + Code", "role": "Estimates what fraction of each country's market the named vendors cover; code divides to get the full country market."},
            "inputShort": f"{ex.get('country')}: {mn(ex.get('named_total_usd_mn'))}", "outputLabel": f"{ex.get('country')}: {mn(ex.get('country_total_usd_mn'))}",
            "reason": "Fragmented countries get a low coverage fraction, concentrated ones a high one.",
            "flowCalc": {
                "a": {"value": mn(ex.get("named_total_usd_mn")), "unit": f"named vendors · {ex.get('country')}", "src": "llm"},
                "opLabel": "÷",
                "b": {"value": pct(ex.get("coverage_fraction"), 0), "unit": "coverage fraction", "src": "assumption"},
                "result": {"value": mn(ex.get("country_total_usd_mn")), "src": "code"},
                "final": {"big": mn(ex.get("country_total_usd_mn")), "cap": f"{ex.get('country')} market"},
            } if ex else None,
            "output": [{"l": cb["country"], "v": f"coverage {pct(cb.get('coverage_fraction'), 0)} → {mn(cb.get('country_total_usd_mn'))}"} for cb in breakdown],
            "activity": ["Estimating coverage per country", "country total = named ÷ coverage"],
        },
        {
            "id": "shares", "name": "Company Market Shares", "data_tag": "real", "stage": "shares", "source": "code",
            "data_tag_note": "company_shares × country totals, summed over countries.",
            "agent": {"name": "Deterministic Code — aggregate_node", "role": "Each company's revenue ÷ the market total, per country and globally."},
            "inputShort": "Company revenue per country", "outputLabel": f"Top: {top[0][0]} {pct(top[0][1] / total if total else 0)}" if top else "—",
            "reason": "The supply side also tells us WHO holds the market.",
            "output": [{"l": n, "v": f"{pct(v / total if total else 0)} · {mn(v)}"} for n, v in top[:12]],
            "activity": ["Computing per-country shares", "Ranking companies globally"],
        },
        {
            "id": "evidence_check", "name": "Cross-Check: Published Market Sizes", "data_tag": "real", "stage": "final", "source": "web",
            "data_tag_note": "evidence_based block of the recorded report (no LLM: regex-extracted figures, median).",
            "agent": {"name": "Evidence Estimator (no LLM)", "role": "Collects market-size figures quoted on the web, filters by relevance and takes the median."},
            "inputShort": f"{len(figures)} quoted figures", "outputLabel": bn(evid.get("total_usd_mn")) if evid.get("has_real_data") else "no data",
            "reason": "An independent referee for the company-based total.",
            "sources": [{"name": f"Quoted figure {i + 1}", "excerpt": short(f.get("snippet"), 300), "extractedValue": num(f.get("value_usd_mn")), "unit": "USD Mn", "decision": "accepted"} for i, f in enumerate(figures[:6])],
            "output": [{"l": "Company-based total", "v": bn(total)}, {"l": "Web-evidence median", "v": bn(evid.get("total_usd_mn")) if evid.get("has_real_data") else "—"}],
            "activity": ["Searching quoted market sizes", "Median of relevant figures"],
        },
        {
            "id": "final_market_size", "name": "Final Market Size", "data_tag": "real", "stage": "final", "source": "code",
            "data_tag_note": "total_usd_mn of the company-based method.",
            "agent": {"name": "Deterministic Code", "role": "Global = Σ country totals."},
            "inputShort": f"{len(breakdown)} countries", "outputLabel": bn(total),
            "reason": "Supply-side total, built from vendors' market-specific revenue.",
            "big": {"value": bn(total), "unit": "global (company method)"},
            "aggregations": [{"title": "Country A + Country B + … → Global",
                              "rows": [{"label": cb["country"], "value": mn(cb.get("country_total_usd_mn"))} for cb in breakdown],
                              "totalLabel": "Global", "total": mn(total)}],
            "is_final": True,
            "activity": ["Summing countries"],
        },
    ]
    for s in steps:
        if s.get("flowCalc") is None:
            s.pop("flowCalc", None)
    flags = quality_flags((comp.get("quality_flags") or []) + (evid.get("quality_flags") or []), {}, "final_market_size")
    if flags:
        steps.append(review_step(flags))
    stages = [{"key": k, "label": l} for k, l in (
        ("vendors", "Vendors"), ("revenue", "Total Revenue"), ("attributable", "Market Slice"),
        ("coverage", "Gross-Up"), ("shares", "Company Shares"), ("final", "Final"))]
    return render(
        "3_Supply_Side_Walkthrough.html",
        title=f"3 Supply-Side — {market}", h1=f"Approach 3 — {market}: Supply-Side (Vendor Revenue)",
        subtitle="Take every company that earns revenue directly in this market, keep only the part of its revenue that comes from this market, and add them up. This also gives each company's market share.",
        tab="3 · Supply-Side", desc="Companies → their total revenue → only the part from this market → add up → total market + company shares.",
        stages=stages, steps=steps, flags=flags,
        banner=("real", f"Real recorded run · {market} · {bn(total)}"),
    )


# --------------------------------------------------------------------------- #
# index
# --------------------------------------------------------------------------- #
def build_index(market: str, built: dict[str, Path | None]) -> Path:
    summary = load(ROOT / "approaches" / "examples" / "EXAMPLES_SUMMARY.json") or []
    cards = [
        ("1", "Desk Research", "Implemented", "1_Desk_Research_Walkthrough.html"),
        ("2A", "Demand: Country Total → Global, segments top-down", "Implemented", "2A_Demand_Country_Total_Walkthrough.html"),
        ("2B", "Demand: Use Case × Country → Global, other segments top-down", "Implemented", "2B_Demand_UseCase_Country_Walkthrough.html"),
        ("2C", "Demand: Product × Use Case × Country → Global", "", "2C_Demand_Product_UseCase_Country_Walkthrough.html"),
        ("2C", "Vinegar, India: white vinegar × use case (cakes, ketchup, mayo) — sourced inputs", "", "2C_Vinegar_India_White_Vinegar_Walkthrough.html"),
        ("3", "Supply-Side: vendor revenue", "Implemented", "3_Supply_Side_Walkthrough.html"),
    ]
    card_html = "".join(
        f'<a class="card" href="{f}"><div class="num">{n}</div><div class="name">{t}</div><div class="st {"ok" if s == "Implemented" else "ill"}">{s}</div></a>'
        if (PPT / f).exists() else
        f'<div class="card off"><div class="num">{n}</div><div class="name">{t}</div><div class="st">walkthrough not built yet</div></div>'
        for n, t, s, f in cards
    )
    rows = "".join(
        f"<tr><td>{r['market']}</td><td>{r.get('vertical') or ''}</td><td>{(bn(r.get('desk_value')) + (f" ({r.get('desk_year')})" if r.get('desk_year') else '')) if r.get('desk_status') == 'ok' else r.get('desk_status') or '—'}</td>"
        f"<td>{bn(r.get('2a_global')) if r.get('2a_status') == 'ok' else r.get('2a_status') or '—'}{' ⚠' if r.get('2a_critical') else ''}</td>"
        f"<td>{bn(r.get('2b_global')) if r.get('2b_status') == 'ok' else r.get('2b_status') or '—'}{' ⚠' if r.get('2b_critical') else ''}</td>"
        f"<td>{bn(r.get('supply_company')) if r.get('supply_status') == 'ok' else r.get('supply_status') or '—'}</td>"
        f"<td>{bn(r.get('supply_evidence')) if r.get('supply_status') == 'ok' else '—'}</td></tr>"
        for r in summary
    )
    others = sorted(p.name for p in list(PPT.glob("*.html")) + list(PPT.glob("*.docx")) if p.name not in {c[3] for c in cards} | {"index.html"})
    other_html = "".join(f'<li><a href="{n}">{n}</a></li>' for n in others)
    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Market Estimation — All Approaches</title>
<style>
body{{margin:0;background:#0a0a0c;color:#f1f1f3;font-family:-apple-system,"Segoe UI",Inter,Roboto,sans-serif}}
.wrap{{max-width:1100px;margin:0 auto;padding:32px 20px}} h1{{font-size:26px;margin:0 0 6px}} p{{color:#9a9aa3;font-size:13px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;margin:22px 0}}
.card{{display:block;text-decoration:none;color:inherit;background:#131316;border:1px solid rgba(255,255,255,.1);border-radius:14px;padding:16px}}
.card:hover{{border-color:#818cf8}} .card.off{{opacity:.45}} .num{{font-size:22px;font-weight:800;color:#818cf8}}
.name{{font-size:13px;font-weight:700;margin:6px 0 10px;line-height:1.4}} .st{{font-size:10.5px;font-weight:700;text-transform:uppercase}}
.st.ok{{color:#34d399}} .st.ill{{color:#fbbf24}}
table{{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:8px}} th,td{{padding:8px 10px;border-bottom:1px solid rgba(255,255,255,.08);text-align:left}}
th{{color:#9a9aa3;font-size:11px;text-transform:uppercase}} h2{{font-size:15px;margin-top:30px}} a{{color:#818cf8}}
</style></head><body><div class="wrap">
<h1>Market Estimation — Three Approaches</h1>
<p>Click an approach to walk through how its number is built, step by step, on the {market} market. Generated {date.today().isoformat()}.</p>
<div class="grid">{card_html}</div>
<h2>Five-industry examples (global, USD)</h2>
<table><tr><th>Market</th><th>Vertical</th><th>1 Desk Research</th><th>2A Demand</th><th>2B Demand</th><th>3 Supply: companies</th><th>3 Supply: web evidence</th></tr>{rows}</table>
<p>⚠ = critical quality flags; that total is not usable until the flagged inputs are fixed. Full detail per market: <code>approaches/examples/</code> (EXAMPLES_SUMMARY.md / .xlsx).</p>
<h2>Earlier walkthroughs (historical — made before the October fixes; the problems they show have since been fixed)</h2><ul>{other_html}</ul>
</div></body></html>"""
    path = PPT / "index.html"
    path.write_text(html, encoding="utf-8")
    return path


def main() -> int:
    import sys

    sys.path.insert(0, str(ROOT / "src"))
    p = argparse.ArgumentParser(description="Build the per-approach walkthrough pages into ppt/.")
    p.add_argument("--market", default="Olive Oil")
    args = p.parse_args()
    built = {
        "1": build_desk(args.market), "2A": build_2a(args.market), "2B": build_2b(args.market),
        "2C": build_2c(args.market), "3": build_supply(args.market),
    }
    for k, v in built.items():
        print(f"{k:>3}: {v.relative_to(ROOT) if v else 'skipped'}")
    print(f"index: {build_index(args.market, built).relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

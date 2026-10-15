"""Runs the CMI-template field groups (groups.py) for a market the fleet has
already run, and writes output/<slug>/cmi_fields.json.

Usage:
    python AiAgentsFleet/cmi_report/run_data.py --market-slug agricultural-biostimulants-market [--groups a,b]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
FLEET = HERE.parent
ROOT = FLEET.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from me_engine.generation.config import GenConfig  # noqa: E402
from me_engine.generation.llm import GenLLM  # noqa: E402
from me_engine.generation.web_evidence import gather_with_sources  # noqa: E402

from AiAgentsFleet.market_definition import as_prompt_block  # noqa: E402
from AiAgentsFleet import randomizer  # noqa: E402
from AiAgentsFleet.randomizer import randomize_json  # noqa: E402
from AiAgentsFleet.sources import filter_sources, on_topic, sources_block  # noqa: E402
from AiAgentsFleet.cmi_report.groups import (GEOGRAPHY_GROUP, GROUPS, schema_for,  # noqa: E402
                                             validate_geography)

CACHE_PATH = ROOT / "output" / ".gen_cache.db"
# Keys jittered +/-5-10% in groups that also carry scores or text. Like every
# `randomize` key in groups.py, this only takes effect when the randomizer
# switch is on (off by default; see randomizer.py).
_PARTIAL_RANDOMIZE = {"vendors": ("value_chain", "mea_shares")}


def fleet_context(data: dict) -> dict:
    """The numbers already published for this market, given to every agent."""
    f = data["fields"]
    ms = f.get("market_size", {})
    regions = (f.get("regional") or {}).get("regions") or {}
    return {
        "global_value_usd_mn": {y: ms["forecast_series_usd_mn"][y] for y in ("2025", "2026", "2033")
                                if y in ms.get("forecast_series_usd_mn", {})},
        "regions_2025": {r: {"value_usd_mn": d.get("value_usd_mn"), "cagr_pct": d.get("cagr_2025_2030_pct"),
                             "countries_usd_mn": {c["name"]: c.get("value_usd_mn") for c in d.get("countries") or []}}
                         for r, d in regions.items()},
        "types": [g["name"] for g in (f.get("segmentation_grade_form") or {}).get("by_grade_or_type") or []],
        "top_companies": [c["name"] for c in (f.get("competitive_landscape") or {}).get("companies") or []][:10],
    }


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _publisher(llm_name: str, domain: str) -> str:
    """The LLM's publisher name if the snippet's website backs it up
    ('Grand View Research' / grandviewresearch.com), else the website itself --
    an unattributable figure must not be credited to the wrong firm."""
    key = re.sub(r"[^a-z0-9]", "", (llm_name or "").lower())
    site = re.sub(r"[^a-z0-9]", "", domain.lower().split(".")[0] if domain else "")
    if key and site and (key[:8] in site or site[:8] in key):
        from AiAgentsFleet.cmi_report.model import clean_publisher
        return clean_publisher(llm_name, domain)
    return domain or "Unattributed"


def _verified_published(raw: dict, sourced: list[dict]) -> dict:
    """Keeps a published figure only if its quote is really in a search
    snippet and the figure is really in its quote; the publisher comes from
    that snippet's website. Third-party numbers are never estimated or
    randomized."""
    def source_of(quote: str) -> dict | None:
        q = _norm(quote)[:60]
        if len(q) < 25:
            return None
        return next((s for s in sourced if q in _norm(s["text"])), None)

    out = {"global_2025": [], "references": [], "_rejected": []}
    domains_used: set[str] = set()
    for g in raw.get("global_2025") or []:
        v, src = g.get("value_usd_bn"), source_of(g.get("quote", ""))
        why = None
        if not (isinstance(v, (int, float)) and src):
            why = "quote not found in any search snippet"
        # the figure must be the quote's 2025 value ("3.86 Billion in 2025"), not a 2024 or 2026 one
        elif not re.search(rf"(?<![\d.]){re.escape(f'{v:g}')}\s*(?:billion|bn)[^.;]{{0,40}}?\b2025\b",
                           g["quote"].replace(",", ""), re.I):
            why = "figure is not the quote's 2025 value"
        else:
            pub = _publisher(g.get("publisher"), src.get("domain", ""))
            dom = src.get("domain", "")
            # one figure per website, and only when the site backs up the publisher name
            if pub == dom:
                why = f"publisher '{g.get('publisher')}' not backed by source site {dom}"
            elif dom in domains_used:
                why = f"second figure from {dom}"
        if why:
            out["_rejected"].append({"publisher": g.get("publisher"), "value_usd_bn": v, "why": why})
            continue
        domains_used.add(dom)
        out["global_2025"].append({**g, "publisher": pub, "url": src.get("url")})
    for r in raw.get("references") or []:
        nums = re.findall(r"\d+(?:\.\d+)?", r.get("published_value", "").replace(",", ""))
        quote = (r.get("quote") or "").replace(",", "")
        src = source_of(r.get("quote", ""))
        if src and nums and all(n in quote for n in nums if not 2000 <= float(n) <= 2040):
            pub = _publisher(r.get("name", "").split(":")[0], src.get("domain", ""))
            if pub == src.get("domain"):      # publisher not backed by the website: leave it out
                out["_rejected"].append({"reference": r.get("name"), "why": f"not backed by {pub}"})
                continue
            scope = r.get("name", "").split(":", 1)[-1].strip() if ":" in r.get("name", "") else r.get("scope", "")
            out["references"].append({**r, "name": f"{pub}: {scope}", "url": src.get("url")})
    # one row per distinct published value
    seen, refs = set(), []
    for r in out["references"]:
        if r["published_value"] not in seen:
            seen.add(r["published_value"])
            refs.append(r)
    out["references"] = refs
    return out


def search(market: str, queries: list[str], key: str, log: list, min_hits: int = 4,
           dropped_out: list[dict] | None = None, off_topic_out: list[dict] | None = None) -> list[dict]:
    """Web search that drops blocked sources (sources.py), keeps only
    snippets mentioning the market (sources.on_topic), and retries when the
    search backend is degraded (it sometimes returns unrelated pages for
    every query). For the attempt that was kept, the blocked snippets are
    appended to `dropped_out` and the off-topic ones to `off_topic_out`."""
    best: list[dict] = []
    best_dropped: list[dict] = []
    best_off: list[dict] = []
    for attempt in range(3):
        try:
            sourced = gather_with_sources(queries, per_query=6, cap=24)
        except Exception as exc:  # noqa: BLE001
            log.append(f"[{key}] web search failed: {exc}")
            sourced = []
        sourced, dropped = filter_sources(sourced)
        hits, off = on_topic(sourced, market)
        hits = hits[:15]
        if len(hits) > len(best) or attempt == 0:
            best, best_dropped, best_off = hits, dropped, off
        if len(best) >= min_hits:
            break
        time.sleep(20 * (attempt + 1))
    if best_dropped:
        log.append(f"[{key}] dropped {len(best_dropped)} snippet(s) from blocked sources: "
                   + ", ".join(sorted({d['domain'] for d in best_dropped})))
    if dropped_out is not None:
        dropped_out.extend(best_dropped)
    if off_topic_out is not None:
        off_topic_out.extend(best_off)
    if len(best) < min_hits:
        log.append(f"[{key}] WARNING: only {len(best)} on-topic snippets after retries (search backend degraded?)")
    return best


def definition_block(d: dict) -> str:
    """The market definition every agent must obey (A1), from model.definition()."""
    return ("DEFINITION (what this market measures -- every estimate, segment and company must fit it):\n"
            f"- Measures: {d.get('definition') or '(not stated)'}\n- Includes: {d.get('included') or '(not stated)'}\n"
            f"- Excludes: {d.get('excluded') or '(not stated)'}\n- Unit: {d.get('unit')}\n\n")


def run_group(market: str, g: dict, context: dict, definition: dict, llm: GenLLM, log: list,
              schema: str | None = None) -> dict:
    queries = [q.format(market=market) for q in g["queries"]]
    dropped: list[dict] = []
    off_topic: list[dict] = []
    sourced = search(market, queries, g["key"], log, dropped_out=dropped, off_topic_out=off_topic)
    evidence = [s["text"] for s in sourced]
    user = (f"Market: {market}\n\n{definition_block(definition)}"
            f"CONTEXT (already published for this market):\n{json.dumps(context, ensure_ascii=False)}\n\n"
            + ("WEB EVIDENCE:\n" + "\n".join(f"- {e}" for e in evidence) if evidence else "(no web evidence found)")
            + f"\n\nReturn ONLY valid JSON matching exactly this shape:\n{schema or g['schema']}")
    try:
        raw = llm.complete_json(g["system"], user)
    except Exception as exc:  # noqa: BLE001
        log.append(f"[{g['key']}] LLM failed: {exc}")
        return {"error": str(exc)}
    if g["key"] == "published":
        out = _verified_published(raw, sourced)
        log.append(f"[published] kept {len(out['global_2025'])} global and {len(out['references'])} reference "
                   f"figures found verbatim in the evidence")
    elif not randomizer.ENABLED:
        out = raw                      # jitter off (the default): numbers stay as the agent returned them
    elif isinstance(g["randomize"], tuple) or g["key"] in _PARTIAL_RANDOMIZE:
        keys = g["randomize"] if isinstance(g["randomize"], tuple) else _PARTIAL_RANDOMIZE[g["key"]]
        out = {k: (randomize_json(v) if k in keys else v) for k, v in raw.items()}
    elif g["randomize"]:
        out = randomize_json(raw)
    else:
        out = raw
    out["_sources"] = sources_block(g["key"], sourced, dropped, len(off_topic))
    log.append(f"[{g['key']}] {len(evidence)} snippets" + (" LOW EVIDENCE" if out["_sources"].get("low_evidence") else ""))
    return out


def run(slug: str, only: set[str] | None = None) -> Path:
    market_dir = FLEET / "output" / slug
    data = json.loads((market_dir / "whitepaper_fields.json").read_text(encoding="utf-8"))
    market = data["market_name"]
    from AiAgentsFleet.cmi_report.model import definition as norm_definition
    definition = norm_definition(data)
    out_path = market_dir / "cmi_fields.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    prev = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    fields = dict(prev.get("fields") or {})

    llm = GenLLM(GenConfig.load(ROOT), cache_path=CACHE_PATH)
    context = fleet_context(data)
    log: list[str] = []

    # 1. which countries this market names in each region (everything else depends on it)
    if not only or "geography" in only or "geography" not in fields:
        raw = run_group(market, GEOGRAPHY_GROUP, context, definition, llm, log)
        geo, why, notes = validate_geography(raw)
        fields["geography"] = {"countries": geo, "why": why, "notes": notes, "_sources": raw.get("_sources")}
        log += [f"[geography] {n}" for n in notes]
        print("  done: geography -> " + "; ".join(f"{r}: {', '.join(c)}" for r, c in geo.items()))
    geo = fields["geography"]["countries"]
    context = {**context, "countries_named": geo}

    wanted = [g for g in GROUPS if not only or g["key"] in only]
    # segments_mea needs the demand segments segments_global defines.
    first = [g for g in wanted if g["key"] != "segments_mea"]
    # ddgs throttles parallel searches (results degrade to unrelated pages), so agents run one at a time
    with ThreadPoolExecutor(max_workers=1) as ex:
        for g, res in zip(first, ex.map(lambda g: run_group(market, g, context, definition, llm, log,
                                                            schema_for(g, geo)), first)):
            fields[g["key"]] = res
            print(f"  done: {g['key']}")
    if any(g["key"] == "segments_mea" for g in wanted):
        sg = fields.get("segments_global") or {}
        ctx = {**context, "demand_segments": [c["name"] for c in sg.get("demand_segments") or []]}
        g = next(g for g in GROUPS if g["key"] == "segments_mea")
        fields["segments_mea"] = run_group(market, g, ctx, definition, llm, log, schema_for(g, geo))
        print("  done: segments_mea")

    out_path.write_text(json.dumps({"market_name": market, "generated_at": datetime.now(timezone.utc).isoformat(),
                                    "fields": fields, "log": (prev.get("log") or []) + log},
                                   indent=2, ensure_ascii=False), encoding="utf-8")
    for line in log:
        print("  " + line)
    return out_path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--market-slug", required=True)
    p.add_argument("--groups", default=None)
    a = p.parse_args()
    only = {x.strip() for x in a.groups.split(",")} if a.groups else None
    print(f"Wrote {run(a.market_slug, only)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

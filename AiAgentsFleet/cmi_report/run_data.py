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

from AiAgentsFleet.market_definition import as_prompt_block, generate_market_definition, is_current  # noqa: E402
from AiAgentsFleet import randomizer  # noqa: E402
from AiAgentsFleet.randomizer import randomize_json  # noqa: E402
from AiAgentsFleet.sources import filter_sources, on_topic, sources_block  # noqa: E402
from AiAgentsFleet.cmi_report.groups import (GEOGRAPHY_GROUP, GROUPS, misplaced, rest_members,  # noqa: E402
                                             schema_for, validate_geography)
from AiAgentsFleet.cmi_report import entities, geo_check, segments_check  # noqa: E402
from AiAgentsFleet.field_groups import FIELD_GROUPS  # noqa: E402

CACHE_PATH = ROOT / "output" / ".gen_cache.db"
# Keys jittered +/-5-10% in groups that also carry scores or text. Like every
# `randomize` key in groups.py, this only takes effect when the randomizer
# switch is on (off by default; see randomizer.py). Vendor data is never
# jittered (PLAN-05), so `vendors` is not listed.
_PARTIAL_RANDOMIZE: dict[str, tuple[str, ...]] = {}
# the snippets each group's agent was shown, so a re-ask (geo_check) sees the same evidence
_SHOWN: dict[str, list[dict]] = {}


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
            + (f"- Definition: {d['definition']}\n" if d.get("definition") and d.get("measures")
               and d["definition"] != d["measures"] else "")
            + f"- Measures: {d.get('measures') or d.get('definition') or '(not stated)'}\n"
            + (f"- Value-chain level: {d['value_chain_level']}\n" if d.get("value_chain_level") else "")
            + f"- Includes: {d.get('included') or '(not stated)'}\n"
            f"- Excludes: {d.get('excluded') or '(not stated)'}\n- Unit: {d.get('unit')}\n\n")


def run_group(market: str, g: dict, context: dict, definition: dict, llm: GenLLM, log: list,
              schema: str | None = None, note: str = "") -> dict:
    """One agent call. `note` (a re-ask, geo_check.reask_note) is appended to the
    prompt, and the agent then sees the same snippets as its first answer."""
    queries = [q.format(market=market) for q in g["queries"]]
    dropped: list[dict] = []
    off_topic: list[dict] = []
    if note and g["key"] in _SHOWN:
        sourced = _SHOWN[g["key"]]
    else:
        sourced = search(market, queries, g["key"], log, dropped_out=dropped, off_topic_out=off_topic)
        _SHOWN[g["key"]] = sourced
    evidence = [s["text"] for s in sourced]
    user = (f"Market: {market}\n\n{definition_block(definition)}"
            f"CONTEXT (already published for this market):\n{json.dumps(context, ensure_ascii=False)}\n\n"
            + ("WEB EVIDENCE:\n" + "\n".join(f"- {e}" for e in evidence) if evidence else "(no web evidence found)")
            + f"\n\nReturn ONLY valid JSON matching exactly this shape:\n{schema or g['schema']}" + note)
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


def ask_regional(market: str, data: dict, llm: GenLLM, log: list):
    """The fleet's regional agent (field_groups.py), asked again with `note`."""
    g = next(x for x in FIELD_GROUPS if x["key"] == "regional")
    shown: list[dict] = []

    def ask(note: str) -> dict:
        if not shown:
            shown.extend(search(market, [q.format(market=market) for q in g["queries"]], "regional", log))
        ev = ("WEB EVIDENCE:\n" + "\n".join(f"- {s['text']}" for s in shown[:15]) if shown
              else "(no web evidence found -- use domain knowledge for a reasonable estimate)")
        user = (f"Market: {market}\n\n{as_prompt_block(data.get('market_definition'))}{ev}\n\n"
                f"Return ONLY valid JSON matching exactly this shape (fill every field, no nulls unless the "
                f"shape explicitly allows it):\n{g['schema_hint']}" + note)
        return llm.complete_json(g["system"], user)
    return ask


def _regional(data: dict, fields: dict) -> dict | None:
    """The regional answer the model uses: re-asked if it was, else the agent's raw one."""
    ans = ((fields.get("geo_review") or {}).get("regional") or {}).get("answer")
    return ans or (data.get("fields_raw") or {}).get("regional") or data["fields"].get("regional")


def sanity_review(market: str, data: dict, fields: dict, geo: dict, definition: dict, llm: GenLLM, log: list,
                  ask_group) -> dict:
    """One sceptical review of the final region and country table (geo_check),
    with a re-ask of the country agent for every region with a flagged country,
    and of the regional agent when a region row is flagged."""
    from AiAgentsFleet.cmi_report.model import geography
    gg = geography(data, fields)
    tbl = geo_check.table(gg["countries"], rest_members(geo), gg["reg"])
    ev = search(market, [f"{market} production consumption by country largest",
                         f"{market} top countries market share"], "geo_review", log)
    try:
        raw = llm.complete_json(geo_check.SANITY_SYSTEM, definition_block(definition)
                                + geo_check.sanity_prompt(market, tbl, [s["text"] for s in ev]))
    except Exception as exc:  # noqa: BLE001
        log.append(f"[geo_review] sanity review failed: {exc}")
        return {"error": str(exc), "flags": []}
    rec = geo_check.resolve_flags(raw, tbl, fields, geo, ask_group, log, regional=_regional(data, fields),
                                  ask_regional=ask_regional(market, data, llm, log), global_value=gg["G"][2025],
                                  regional_first=(data.get("fields_raw") or {}).get("regional")
                                  or data["fields"].get("regional"))
    log.append(f"[geo_review] sanity review: {len(rec['flags'])} flag(s), "
               f"{sum(1 for f in rec['flags'] if f.get('open'))} still open")
    return rec


def entity_search(queries: list[str]) -> list[dict]:
    """Search for one company's facts (entities.py): blocked sources dropped, but
    no market-topic filter -- a page on a company's headquarters need not name
    the market."""
    try:
        sourced = gather_with_sources(queries, per_query=4, cap=16, precise=True)   # standard search
    except Exception:  # noqa: BLE001
        return []
    return filter_sources(sourced)[0]


def review_vendors(market: str, data: dict, fields: dict, definition: dict, llm: GenLLM, log: list,
                   ask_group, check_answer: bool = True) -> None:
    """PLAN-05: the vendors answer checked (MEA share quotes, companies outside
    the definition) and re-asked once; then one entity check per vendor (HQ,
    parent, business type, product, entities.check_entities); then, when fewer
    than entities.MIN_VENDORS are confirmed, one re-ask of the vendor list on
    extra searches (entities.reask_if_thin). Writes fields["vendor_review"] and
    fields["entities"]."""
    if check_answer:
        fields["vendor_review"] = entities.review_vendor_answer(fields, _SHOWN.get("vendors") or [], market,
                                                                ask_group, log)
    products = entities.product_phrases(market, definition)
    names = entities.universe(data["fields"], fields.get("vendors"))
    fields["entities"] = entities.check_entities(names, market, definition_block(definition), fields.get("vendors"),
                                                 llm, entity_search, log, products=products)

    def check_new(new: list[str]) -> dict:
        return entities.check_entities(new, market, definition_block(definition), fields.get("vendors"), llm,
                                       entity_search, log, products=products)["records"]
    def ask_thin(system: str, body: str, queries: list[str]) -> dict:
        """The dedicated thin-list re-ask: its own searches, the definition and the evidence -- no
        CONTEXT (top companies) and none of the vendors group's instructions."""
        shown = search(market, [q.format(market=market) for q in queries], "vendors_reask", log)
        ev = ("WEB EVIDENCE:\n" + "\n".join(f"- {s['text']}" for s in shown) if shown
              else "(no web evidence found)")
        return llm.complete_json(system, f"Market: {market}\n\n{definition_block(definition)}{ev}\n\n{body}")
    thin = entities.reask_if_thin(fields, data["fields"], definition, market, ask_thin, check_new, log)
    fields["vendor_review"] = {**(fields.get("vendor_review") or {}), "thin_reask": thin}


def run(slug: str, only: set[str] | None = None) -> Path:
    market_dir = FLEET / "output" / slug
    data = json.loads((market_dir / "whitepaper_fields.json").read_text(encoding="utf-8"))
    market = data["market_name"]
    from AiAgentsFleet.cmi_report.model import definition as norm_definition
    out_path = market_dir / "cmi_fields.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    prev = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    fields = dict(prev.get("fields") or {})

    cfg = GenConfig.load(ROOT)
    llm = GenLLM(cfg, cache_path=CACHE_PATH)
    log: list[str] = []
    # 0. the market definition (PLAN-04): measures, value-chain level, included/excluded lists.
    # One from before PLAN-04 is regenerated, with the client's verbatim definition as its anchor.
    if (only and "market_definition" in only) or not any(is_current(d) for d in (fields.get("market_definition"),
                                                                              data.get("market_definition"))):
        fields["market_definition"] = generate_market_definition(
            market, llm, cfg, log, client=data.get("definition_inclusion_exclusion"))
        print(f"  done: market_definition ({fields['market_definition'].get('value_chain_level')})")
    definition = norm_definition(data, fields)
    context = fleet_context(data)
    review = dict(fields.get("geo_review") or {})      # PLAN-03 checks and re-asks (geo_check.py)
    seg_review = dict(fields.get("segment_review") or {})   # PLAN-04 checks and re-asks (segments_check.py)

    # 1. which countries this market names in each region (everything else depends on it)
    if not only or "geography" in only or "geography" not in fields:
        raw = run_group(market, GEOGRAPHY_GROUP, context, definition, llm, log)
        bad = misplaced(raw)
        review["geography"] = {"problems": bad, "reasked": bool(bad)}
        if bad:                                         # region convention broken: ask once more
            log.append("[geography] " + "; ".join(bad) + " -- re-asking once")
            again = run_group(market, GEOGRAPHY_GROUP, context, definition, llm, log, note=geo_check.reask_note(
                [{"region": "geography", "detail": b} for b in bad], raw))
            if "error" not in again:
                raw = again
            review["geography"]["after"] = misplaced(raw)
        geo, why, notes = validate_geography(raw)
        fields["geography"] = {"countries": geo, "why": why, "notes": notes, "_sources": raw.get("_sources")}
        log += [f"[geography] {n}" for n in notes]
        print("  done: geography -> " + "; ".join(f"{r}: {', '.join(c)}" for r, c in geo.items()))
        # the regional agent's five regions must add up to the global headline (re-asked once if not)
        from AiAgentsFleet.cmi_report.model import global_series
        regional = (data.get("fields_raw") or {}).get("regional") or data["fields"].get("regional")
        review["regional"] = geo_check.reask_regions(regional, global_series(data)[2025],
                                                     ask_regional(market, data, llm, log), log)
        fields["geo_review"] = review
    geo = fields["geography"]["countries"]
    if ((review.get("regional") or {}).get("answer") or {}).get("regions"):
        context = fleet_context({**data, "fields": {**data["fields"], "regional": review["regional"]["answer"]}})
    context = {**context, "countries_named": geo}

    def ask_group(key: str, note: str) -> dict:
        g = next(x for x in GROUPS if x["key"] == key)
        return run_group(market, g, context, definition, llm, log, schema_for(g, geo), note=note)

    wanted = [g for g in GROUPS if not only or g["key"] in only]
    # segments_mea needs the demand segments segments_global defines.
    first = [g for g in wanted if g["key"] != "segments_mea"]
    # ddgs throttles parallel searches (results degrade to unrelated pages), so agents run one at a time
    with ThreadPoolExecutor(max_workers=1) as ex:
        for g, res in zip(first, ex.map(lambda g: run_group(market, g, context, definition, llm, log,
                                                            schema_for(g, geo)), first)):
            fields[g["key"]] = res
            print(f"  done: {g['key']}")
    # segments and heat-map rows kept to the definition: checked and re-asked once (PLAN-04)
    fleet_types = (data["fields"].get("segmentation_grade_form") or {}).get("by_grade_or_type") or []
    replaced = False
    if any(g["key"] == "segments_global" for g in wanted):
        seg_review["segments_global"] = segments_check.review_global(fields, [t["name"] for t in fleet_types],
                                                                     definition, ask_group, log)
        fields["segment_review"] = seg_review
        replaced = seg_review["segments_global"].get("replacement") == "accepted"
    # the type list in use: an accepted replacement taxonomy, else the fleet's types
    types = segments_check.types_of(fleet_types, fields.get("segments_global"))
    if any(g["key"] == "use_cases" for g in wanted):
        seg_review["use_cases"] = segments_check.review_use_cases(fields, definition, ask_group, log)
        fields["segment_review"] = seg_review
    # 2. country splits: checked and re-asked once, then one sceptical review of the final table
    if any(g["key"] in geo_check.GROUP_REGIONS for g in wanted) and all(k in fields for k in geo_check.GROUP_REGIONS):
        review["splits"] = geo_check.review_splits(fields, geo, ask_group, log, regional=_regional(data, fields))
        fields["geo_review"] = review
        review["sanity"] = sanity_review(market, data, fields, geo, definition, llm, log, ask_group)
        if review["sanity"].get("regional_answer"):       # a flagged region, re-asked and coherent
            review["regional"] = {**(review.get("regional") or {}), "reasked": True,
                                  "answer": review["sanity"].pop("regional_answer"),
                                  "sanity_changes": review["sanity"].pop("regional_changes", [])}
        fields["geo_review"] = review
        print("  done: geo_review")
    # a replacement taxonomy renames the types: the MEA mix must be asked again with the new names
    if replaced and not any(g["key"] == "segments_mea" for g in wanted):
        log.append("[segments_mea] type taxonomy replaced -- re-running segments_mea with the new type names")
    if replaced or any(g["key"] == "segments_mea" for g in wanted):
        # the segments the model keeps (excluded ones dropped, merges applied). The global mix is NOT
        # shown on the first call -- the agent answers from the evidence; only a re-ask shows it.
        sg = fields.get("segments_global") or {}
        resolved = segments_check.resolve(sg, [t["name"] for t in types], definition)
        mix = segments_check.global_mix(types, sg, resolved)
        ctx = {**context, "types": resolved["names"]["type"], "demand_segments": resolved["names"]["demand"]}
        g = next(g for g in GROUPS if g["key"] == "segments_mea")

        def ask_mea(key: str, note: str) -> dict:
            return run_group(market, g, ctx, definition, llm, log, schema_for(g, geo), note=note)
        fields["segments_mea"] = ask_mea("segments_mea", "")
        # the MEA mix against the global mix: re-asked once when beyond x2 or a copy of it, never blended
        try:
            from AiAgentsFleet.cmi_report.model import geography
            gg = geography(data, fields)
            units = {u: v["v26"] for r in ("Middle East", "Africa") for u, v in gg["countries"][r].items()}
            seg_review["segments_mea"] = segments_check.review_mea(fields, units, mix, ask_mea, log,
                                                                   merge=resolved["merge"])
        except (KeyError, TypeError, ValueError) as exc:
            log.append(f"[segments_mea] mix check skipped: {exc}")
            seg_review["segments_mea"] = {"problems": [], "reasked": False, "skipped": str(exc)}
        fields["segment_review"] = seg_review
        print("  done: segments_mea")
    # vendors: share quotes and market fit checked (re-asked once), then each vendor's entity facts (PLAN-05);
    # `--groups entities` re-runs only the entity check on the saved vendors answer
    if any(g["key"] == "vendors" for g in wanted) or (only and "entities" in only and "vendors" in fields):
        review_vendors(market, data, fields, definition, llm, log, ask_group,
                       check_answer=any(g["key"] == "vendors" for g in wanted))
        print("  done: vendor_review + entities")

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

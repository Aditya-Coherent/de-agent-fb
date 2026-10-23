"""Every web search one market's run sends, rebuilt from the code and that market's saved data.

    python vm_fleet/list_queries.py <slug> [--csv out.csv]

Stages in pipeline order, each query with its search mode (fast $1/1000 or standard $5/1000 on Perplexity).
Rebuilt, not logged: the query templates come from the code (field_groups, groups, run_data, entities,
verify_facts, extras, resources, run_cmi_report), the variable parts (vendor names, claims, sources) from the
market's saved files. Retries of a thin search repeat the same queries and are served from the search cache,
so they are not listed.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from AiAgentsFleet.field_groups import FIELD_GROUPS  # noqa: E402
from AiAgentsFleet.cmi_report import entities, verify_facts  # noqa: E402
from AiAgentsFleet.cmi_report.groups import GEOGRAPHY_GROUP, GROUPS  # noqa: E402
from AiAgentsFleet.cmi_report.run_cmi_report import _EVIDENCE_QUERIES  # noqa: E402

PRICE = {"fast": 1.0, "standard": 5.0}


def _market_words(market: str) -> set[str]:
    root = re.sub(r"\s+market\s*$", "", market, flags=re.I).lower()
    return {w for w in re.findall(r"[a-z]{5,}", root)}


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def rebuild(slug: str) -> list[dict]:
    d = ROOT / "AiAgentsFleet" / "output" / slug
    fleet, cmi = load(d / "whitepaper_fields.json"), load(d / "cmi_fields.json")
    extras, res, model = load(d / "report_extras.json"), load(d / "resources.json"), load(d / "cmi_model.json")
    market = fleet["market_name"]
    f = cmi.get("fields") or {}
    rows: list[dict] = []

    def add(stage: str, q: str, mode: str = "fast", note: str = "", shared: bool = False) -> None:
        # shared: the text names no market (a company, source or framework) -> paid once per run (shared cache)
        rows.append({"stage": stage, "query": q, "mode": mode, "note": note, "shared": shared})

    # 1. fleet: market definition + 13 field-group agents
    for q in (f"{market} definition scope", f"{market} market segmentation included excluded"):
        add("1 fleet: market definition", q)
    for g in FIELD_GROUPS:
        for q in g["queries"]:
            add(f"1 fleet: {g['key']}", q.format(market=market))
    # 2. CMI data agents (run_data)
    for q in GEOGRAPHY_GROUP["queries"]:
        add("2 cmi: geography", q.format(market=market))
    if ((f.get("geo_review") or {}).get("regional") or {}).get("reasked"):
        reg = next(g for g in FIELD_GROUPS if g["key"] == "regional")
        for q in reg["queries"]:
            add("2 cmi: regional re-ask (fresh search)", q.format(market=market), note="same text as fleet: cache hit")
    for g in GROUPS:
        for q in g["queries"]:
            add(f"2 cmi: {g['key']}", q.format(market=market))
    for q in GEOGRAPHY_GROUP["queries"][:2]:
        add("2 cmi: geo sanity review", q.format(market=market))
    # 3. vendor entity checks (standard search): HQ, parent, the market, a product term -- per vendor
    ent = f.get("entities") or {}
    products = ent.get("products") or []
    for name in (ent.get("records") or {}):
        for k, q in enumerate(entities.entity_queries(name, market, products)):
            add("3 vendor entity checks", q, "fast" if k == 0 else "standard", name, shared=k == 0)
    # 4. fact checks (standard search): first query per claim (the second only if the first finds nothing)
    product = verify_facts.product_name(market)
    # the claims the current code makes from this market's data (requirement claims carry their regulator's
    # category terms now), in check order and capped as verify() caps them
    for r in verify_facts.claims(f)[:verify_facts.MAX_CLAIMS]:
        if r.get("kind") == "vendor_move" and verify_facts._NON_CLAIM.search(r.get("text", "")):
            continue                                       # states no move: not searched
        qs = verify_facts.queries(r, product)
        add("4 fact checks", qs[0], "standard", r.get("kind", ""))
    # 5. report extras (main 67743b8): vendor tier renames, global regulation frameworks
    raw = extras.get("raw") or extras
    for t in (raw.get("tiers") or {}).get("tiers") or []:
        for p in t.get("players") or []:
            add("5 extras: tier company renames", f"{re.sub(r'\s*\(.*\)', '', p)} rebranded OR renamed OR acquired by",
                "fast", shared=True)
    for r in (raw.get("regulation") or {}).get("frameworks") or []:
        if r.get("framework"):
            add("5 extras: regulation frameworks", f'"{re.sub(r"\s*\([^)]*\)", "", r["framework"]).strip()}"', "fast", shared=True)
    # 6. 9.1 Sources: every named source verified by a search
    for key, items in (res.get("raw") or {}).items():
        if key == "primary_interviews":                    # role descriptions: resources.build never searches them
            continue
        for it in items if isinstance(items, list) else []:
            name = str((it.get("name") or "") if isinstance(it, dict) else it).strip()
            if name:
                add("6 sources section", f'"{re.sub(r"\s*\([^)]*\)", "", name).strip()}"', "fast", key, shared=True)
    # 7. prose evidence, one per chapter
    for ch, qs in _EVIDENCE_QUERIES.items():
        for q in qs:
            add(f"7 prose evidence ({ch})", q.format(market=market))
    return rows


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("slug")
    p.add_argument("--csv")
    a = p.parse_args()
    rows = rebuild(a.slug)
    by_stage: dict[str, list[dict]] = {}
    for r in rows:
        by_stage.setdefault(r["stage"], []).append(r)
    for stage, rs in by_stage.items():
        cost = sum(PRICE[r["mode"]] for r in rs) / 1000
        print(f"\n## {stage}  ({len(rs)} queries, {rs[0]['mode']}, ${cost:.3f})")
        for r in rs:
            print(f"   {r['query']}" + (f"   [{r['note']}]" if r["note"] else ""))
    n = Counter(r["mode"] for r in rows)
    seen, paid = set(), []
    for r in rows:                     # a repeat of an earlier query (same mode) in this market: a cache hit
        k = (r["mode"], " ".join(r["query"].lower().split()))
        r["paid"] = k not in seen
        seen.add(k)
        if r["paid"]:
            paid.append(r)
    cost = lambda rs: sum(PRICE[r["mode"]] for r in rs) / 1000  # noqa: E731
    shared = [r for r in paid if r["shared"]]
    c = lambda rs: f"{sum(r['mode'] == 'fast' for r in rs)} fast + {sum(r['mode'] == 'standard' for r in rs)} standard"  # noqa: E731
    print(f"\nqueries sent: {len(rows)} ({c(rows)})")
    print(f"paid in this market: {len(paid)} distinct ({c(paid)}) = ${cost(paid):.3f}; "
          f"{len(rows) - len(paid)} answered from the cache")
    print(f"  of which market-independent (company / source / framework names, paid once per run): "
          f"{len(shared)} ({c(shared)}) = ${cost(shared):.3f}")
    print(f"steady-state cost per market once those are cached: ${cost(paid) - cost(shared):.3f}")
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["stage", "query", "mode", "note", "shared", "paid"])
            w.writeheader()
            w.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

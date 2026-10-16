"""AiAgentsFleet CLI -- give it a market name, it runs one agent per
whitepaper field group (see field_groups.py), each doing live web search +
source filtering (sources.py) + LLM synthesis, and writes the combined result
to output/<market-slug>/whitepaper_fields.json. `fields_raw` holds each
agent's answer as returned; `fields` is the same unless the optional jitter
(randomizer.py, off by default, env AIFLEET_RANDOMIZE=1) is switched on.

Usage:
    python AiAgentsFleet/run_fleet.py --market "Global Vitamin C Market"
    python AiAgentsFleet/run_fleet.py --market "..." --groups market_size,regional
    python AiAgentsFleet/run_fleet.py --market "..." --workers 4

See README.md in this folder for the full field list and the caveat about
what "Google AI Overview" means here (no literal scrape of Google's AI
Overview widget -- see field_agent.py's module docstring).
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))  # so `AiAgentsFleet` itself is importable as a package

from me_engine.generation.config import GenConfig  # noqa: E402
from me_engine.generation.llm import GenLLM  # noqa: E402

from AiAgentsFleet import randomizer  # noqa: E402
from AiAgentsFleet.field_agent import run_field_group  # noqa: E402
from AiAgentsFleet.field_groups import FIELD_GROUPS, FIELD_GROUP_KEYS  # noqa: E402
from AiAgentsFleet.market_definition import generate_market_definition  # noqa: E402

CACHE_PATH = ROOT / "output" / ".gen_cache.db"
OUTPUT_ROOT = Path(__file__).resolve().parent / "output"


def _slug(market_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", market_name.strip().lower()).strip("-")
    return slug or "market"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="AiAgentsFleet -- fill whitepaper fields for a market")
    p.add_argument("--market", required=True, help='e.g. "Global Vitamin C Market"')
    p.add_argument("--groups", default=None, help=f"Comma-separated subset of: {', '.join(FIELD_GROUP_KEYS)}")
    p.add_argument("--workers", type=int, default=4, help="Parallel field-group agents (default 4)")
    p.add_argument("--out", default=None, help="Output JSON path (default: output/<slug>/whitepaper_fields.json)")
    return p.parse_args()


def _maybe_jitter(raw: dict) -> dict:
    """The group's answer with the optional jitter applied (an unchanged copy
    when the switch is off). `_sources` and errors are never jittered."""
    if "error" in raw:
        return copy.deepcopy(raw)
    body = {k: v for k, v in raw.items() if k != "_sources"}
    out = randomizer.randomize_json(body)
    if "_sources" in raw:
        out["_sources"] = copy.deepcopy(raw["_sources"])
    return out


def run_fleet(market: str, groups: list[dict], workers: int = 4) -> dict:
    cfg = GenConfig.load(ROOT)
    if not cfg.is_online:
        print("ERROR: no LLM API key found in .env (DEEPSEEK_API_KEY / OPENAI_API_KEY / OPENROUTER_API_KEY).")
        sys.exit(2)
    llm = GenLLM(cfg, cache_path=CACHE_PATH)

    log: list[str] = []
    print(f"Generating one shared market definition for '{market}' (scope lock)...")
    market_definition = generate_market_definition(market, llm, cfg, log)
    for line in log:
        print("  " + line)

    results: dict[str, dict] = {}
    raw_results: dict[str, dict] = {}
    print(f"Running {len(groups)} field-group agent(s) for '{market}' (up to {workers} in parallel)...")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(run_field_group, market, g, llm, log, market_definition, randomize=False): g["key"]
            for g in groups
        }
        for fut in as_completed(futures):
            key = futures[fut]
            try:
                raw = fut.result()
                raw_results[key] = raw
                results[key] = _maybe_jitter(raw)
                print(f"  done: {key}")
            except Exception as exc:  # noqa: BLE001
                results[key] = {"error": str(exc)}
                print(f"  FAILED: {key}: {exc}")

    # as_completed finishes in thread order; sort so the same evidence always writes the same file
    order = {g["key"]: i for i, g in enumerate(groups)}
    results = dict(sorted(results.items(), key=lambda kv: order.get(kv[0], 0)))
    raw_results = dict(sorted(raw_results.items(), key=lambda kv: order.get(kv[0], 0)))
    return {
        "market_name": market,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market_definition": market_definition,
        "randomization": ("+/-5-10% applied independently to every numeric field (see randomizer.py)"
                          if randomizer.ENABLED else "none"),
        "source_note": (
            "Field values are synthesized by an LLM from live multi-backend web search results "
            "(ddgs: Google/Bing/DuckDuckGo/Mojeek), in the style of a concise AI-Overview-style "
            "answer -- NOT a literal scrape of Google's AI Overview UI element (no public API for "
            "that exists). See field_agent.py. A single shared market definition (see "
            "`market_definition`) is injected into every field group's prompt to keep scope "
            "consistent, and `report/consistency.py` reconciles/validates every number before the "
            "report is built -- see output/<slug>/validation_report.json."
        ),
        "fields": results,
        "fields_raw": raw_results,
        "log": log,
    }


def main() -> int:
    args = _parse_args()
    if args.groups:
        wanted = {g.strip() for g in args.groups.split(",") if g.strip()}
        unknown = wanted - set(FIELD_GROUP_KEYS)
        if unknown:
            print(f"ERROR: unknown group(s): {sorted(unknown)}. Valid: {FIELD_GROUP_KEYS}")
            return 2
        groups = [g for g in FIELD_GROUPS if g["key"] in wanted]
    else:
        groups = FIELD_GROUPS

    result = run_fleet(args.market, groups, workers=args.workers)

    out_path = Path(args.out) if args.out else OUTPUT_ROOT / _slug(args.market) / "whitepaper_fields.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    check = ((result["fields_raw"].get("market_size") or {}).get("_sources") or {}).get("size_check")
    if check:                    # is the headline backed by its own evidence? (PLAN-02)
        (out_path.parent / "market_size_check.json").write_text(json.dumps(check, indent=2, ensure_ascii=False),
                                                                 encoding="utf-8")

    print("\n--- run log ---")
    for line in result["log"]:
        print("  " + line)
    print(f"\nWrote {out_path}")
    from me_engine.generation.web_evidence import backend as _search_backend, cost_summary as _search_cost
    if _search_backend() == "perplexity":
        print(_search_cost())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

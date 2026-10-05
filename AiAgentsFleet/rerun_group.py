"""Re-runs ONE field group for an existing market (search -> LLM ->
randomize), using the market's saved definition as the scope lock, and
updates that market's whitepaper_fields.json in place (raw value, the
randomized value, and the source domains). Every other group is untouched.

Usage:
    python AiAgentsFleet/rerun_group.py --market-slug <slug> --group willingness_to_pay
Then rebuild the report with report/run_report.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from me_engine.generation.config import GenConfig  # noqa: E402
from me_engine.generation.llm import GenLLM  # noqa: E402

from AiAgentsFleet.field_agent import run_field_group  # noqa: E402
from AiAgentsFleet.field_groups import FIELD_GROUPS  # noqa: E402
from AiAgentsFleet.randomizer import randomize_json  # noqa: E402

OUTPUT_ROOT = Path(__file__).resolve().parent / "output"


def _scope(data: dict) -> dict | None:
    if data.get("market_definition"):
        return data["market_definition"]
    de = data.get("definition_inclusion_exclusion")
    if not de:
        return None
    return {"included": f"{de.get('definition', '')} {de.get('inclusions', '')}".strip(),
            "excluded": de.get("exclusions", ""), "value_chain_level": "as defined",
            "unit_of_measure": "USD million, market value"}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--market-slug", required=True)
    p.add_argument("--group", required=True)
    args = p.parse_args()

    group = next((g for g in FIELD_GROUPS if g["key"] == args.group), None)
    if group is None:
        print(f"ERROR: unknown group {args.group}")
        return 2
    path = OUTPUT_ROOT / args.market_slug / "whitepaper_fields.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    cfg = GenConfig.load(ROOT)
    llm = GenLLM(cfg, cache_path=ROOT / "output" / ".gen_cache.db")

    # run_field_group returns the RANDOMIZED answer; to also keep the raw
    # one, run with randomization disabled and jitter it here.
    import AiAgentsFleet.field_agent as fa
    fa.randomize_json = lambda x: x
    log: list[str] = []
    raw = run_field_group(data["market_name"], group, llm, log, _scope(data))
    if "error" in raw:
        print(f"ERROR: {raw['error']}")
        return 1
    sources = raw.pop("_sources", None)
    jittered = randomize_json(raw)
    if sources is not None:
        raw["_sources"] = sources
        jittered["_sources"] = sources

    data.setdefault("fields_raw", {})[args.group] = raw
    data.setdefault("fields_before_consistency", dict(data.get("fields", {})))[args.group] = jittered
    data["fields"][args.group] = jittered
    data["log"] = [l for l in data.get("log", []) if not l.startswith(f"[{args.group}]")] + \
                  [l.replace("then randomized", "then randomized") for l in log]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n".join(log))
    print(f"Updated {path} [{args.group}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""One agent per field group: gather live web evidence, then have an LLM
synthesize a concise, structured "AI-Overview-style" answer for that group.

IMPORTANT CAVEAT (read this before assuming this literally scrapes Google's
AI Overview widget): there is no public, ToS-compliant API for Google's AI
Overview feature, and scraping its rendered HTML is fragile, JS-dependent,
and against Google's Terms of Service. This module instead reuses the SAME
multi-backend live web search already used throughout this codebase
(`me_engine.generation.web_evidence.gather`, backed by `ddgs`, which queries
Google/Bing/DuckDuckGo/Mojeek) and has an LLM produce a concise, cited-style
synthesis from those results -- functionally the same shape of output an AI
Overview box gives (a short, confident, multi-source-grounded answer), just
without literally scraping that specific Google UI element.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))  # so `AiAgentsFleet` itself is importable as a package

from me_engine.generation.llm import GenLLM  # noqa: E402
from me_engine.generation.web_evidence import gather_with_sources  # noqa: E402

from AiAgentsFleet.market_definition import as_prompt_block  # noqa: E402
from AiAgentsFleet.randomizer import randomize_json  # noqa: E402


def _evidence_block(snippets: list[str]) -> str:
    if not snippets:
        return "(no web evidence found -- use domain knowledge for a reasonable estimate)"
    return "WEB EVIDENCE:\n" + "\n".join(f"- {s}" for s in snippets[:15])


def run_field_group(
    market: str, group: dict, llm: GenLLM, log: list[str] | None = None,
    market_definition: dict | None = None,
) -> dict[str, Any]:
    """Runs ONE field-group agent: search -> LLM synthesis -> randomize.
    Returns the randomized JSON for this group (never raises -- degrades to
    an empty dict with an "error" key on failure, so one bad group never
    aborts the whole fleet run).

    `market_definition` (see `market_definition.py`) is prepended to the
    prompt so every group answers against the SAME scope -- the fix for one
    group describing the named market while another describes its parent
    category. Also tracks source domains (`_sources`) for the confidence
    scoring `report/consistency.py` computes later; added AFTER
    randomization so it's never itself jittered."""
    log = log if log is not None else []
    queries = [q.format(market=market) for q in group["queries"]]
    try:
        sourced = gather_with_sources(queries, per_query=5, cap=15)
    except Exception as exc:  # noqa: BLE001
        sourced = []
        log.append(f"[{group['key']}] web search failed: {exc}")
    snippets = [s["text"] for s in sourced]
    domains = sorted({s["domain"] for s in sourced if s.get("domain")})

    system = group["system"]
    user = (
        f"Market: {market}\n\n{as_prompt_block(market_definition)}{_evidence_block(snippets)}\n\n"
        f"Return ONLY valid JSON matching exactly this shape (fill every field, no nulls unless the "
        f"shape explicitly allows it):\n{group['schema_hint']}"
    )
    try:
        data = llm.complete_json(system, user)
    except Exception as exc:  # noqa: BLE001
        log.append(f"[{group['key']}] LLM synthesis failed: {exc}")
        return {"error": str(exc)}

    randomized = randomize_json(data)
    randomized["_sources"] = {"num_sources": len(sourced), "domains": domains}
    log.append(
        f"[{group['key']}] {group['section']}: synthesized from {len(snippets)} web snippets "
        f"({len(domains)} distinct domains), then randomized every value by +/-5-10%."
    )
    return randomized

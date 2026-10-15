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
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))  # so `AiAgentsFleet` itself is importable as a package

from me_engine.generation.llm import GenLLM  # noqa: E402
from me_engine.generation.web_evidence import gather_with_sources  # noqa: E402

from AiAgentsFleet.market_definition import as_prompt_block  # noqa: E402
from AiAgentsFleet import randomizer  # noqa: E402
from AiAgentsFleet.randomizer import randomize_json  # noqa: E402
from AiAgentsFleet.sources import MIN_KEPT_SNIPPETS, filter_sources, on_topic, sources_block  # noqa: E402


def _evidence_block(snippets: list[str]) -> str:
    if not snippets:
        return "(no web evidence found -- use domain knowledge for a reasonable estimate)"
    return "WEB EVIDENCE:\n" + "\n".join(f"- {s}" for s in snippets[:15])


def run_field_group(
    market: str, group: dict, llm: GenLLM, log: list[str] | None = None,
    market_definition: dict | None = None, randomize: bool = True,
) -> dict[str, Any]:
    """Runs ONE field-group agent: search -> source filter -> LLM synthesis
    (-> optional jitter, off by default; see randomizer.py). Returns the JSON
    for this group (never raises -- degrades to
    an empty dict with an "error" key on failure, so one bad group never
    aborts the whole fleet run).

    `market_definition` (see `market_definition.py`) is prepended to the
    prompt so every group answers against the SAME scope -- the fix for one
    group describing the named market while another describes its parent
    category. Snippets from blocked domains, and snippets that don't mention
    the market, are dropped before the LLM sees them (sources.py); if fewer
    than 3 on-topic snippets remain the search is retried once. Also tracks source domains and quality (`_sources`)
    for the confidence scoring computed later; added AFTER any jitter so
    it's never itself jittered. `randomize=False` returns the LLM's answer
    as is, so a caller can keep the raw answer and jitter it itself.
    For `market_size` only, the snippets shown are kept (`_sources.snippets`)
    and the headline is checked against them (`_sources.size_check`, see
    cmi_report/verify_size.py)."""
    log = log if log is not None else []
    queries = [q.format(market=market) for q in group["queries"]]
    best: tuple[list[dict], list[dict], list[dict]] | None = None
    for attempt in range(2):           # one retry when the search comes back thin, as run_data.search does
        try:
            sourced = gather_with_sources(queries, per_query=5, cap=15)
        except Exception as exc:  # noqa: BLE001
            sourced = []
            log.append(f"[{group['key']}] web search failed: {exc}")
        sourced, dropped = filter_sources(sourced)
        sourced, off = on_topic(sourced, market)
        if best is None or len(sourced) > len(best[0]):
            best = (sourced, dropped, off)
        if len(best[0]) >= MIN_KEPT_SNIPPETS or attempt == 1:
            break
        time.sleep(20)
    sourced, dropped, off = best
    if dropped:
        log.append(f"[{group['key']}] dropped {len(dropped)} snippet(s) from blocked sources: "
                   + ", ".join(sorted({d['domain'] for d in dropped})))
    if off:
        log.append(f"[{group['key']}] dropped {len(off)} off-topic snippet(s) that don't mention the market")
    snippets = [s["text"] for s in sourced]
    src = sources_block(group["key"], sourced, dropped, len(off))
    if len(sourced) < MIN_KEPT_SNIPPETS:
        src["low_evidence"] = True

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
    if group["key"] == "market_size":
        # the headline must cite a quote from these snippets; re-asked once if it doesn't (PLAN-02)
        from AiAgentsFleet.cmi_report.verify_size import check_with_reask
        shown = [{k: s.get(k) for k in ("text", "url", "domain")} for s in sourced[:15]]
        data, src["size_check"] = check_with_reask(data, shown, lambda note: llm.complete_json(system, user + note), log,
                                                   market)
        src["snippets"] = shown
    elif group["key"] == "competitive_landscape":
        # a company share is printed only when a quote in these snippets states it; re-asked once (PLAN-05)
        from AiAgentsFleet.cmi_report.entities import check_shares_with_reask
        shown = [{k: s.get(k) for k in ("text", "url", "domain")} for s in sourced[:15]]
        data, src["share_check"] = check_shares_with_reask(data, shown, lambda note: llm.complete_json(system, user + note),
                                                           log, market)
        src["snippets"] = shown

    jittered = randomize and randomizer.ENABLED
    out = randomize_json(data) if randomize else data
    out["_sources"] = src
    log.append(
        f"[{group['key']}] {group['section']}: synthesized from {len(snippets)} web snippets "
        f"({len(src['domains'])} distinct domains, {src['quality']['trusted']} trusted)"
        + (", then randomized every value by +/-5-10%." if jittered else "; values kept as returned (no jitter).")
        + (" LOW EVIDENCE." if src.get("low_evidence") else "")
    )
    return out

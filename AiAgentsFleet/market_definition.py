"""Generates ONE written market definition before any field-group agent
runs, so every agent searches/answers against the same scope instead of
each one independently (and inconsistently) deciding what "the market"
means. This is the fix for scope bleed -- e.g. a "Super-Intensive Shrimp
Farming Market" agent answering with the whole shrimp farming/processing
industry's numbers instead of just the super-intensive-systems slice.

Called once per fleet run (`run_fleet.py`), BEFORE any field group; its
result is injected into every field group's prompt by `field_agent.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from me_engine.generation.llm import GenLLM  # noqa: E402
from me_engine.generation.web_evidence import gather  # noqa: E402

_SYSTEM = (
    "You are a market-scoping analyst. Before any sizing or segmentation work starts, define EXACTLY "
    "what this market does and does not include, so every later analysis stays inside this boundary. "
    "Be specific: name the value-chain level (e.g. 'farming/production', not the processed end-product "
    "market; 'software platforms', not the hardware they run on) and the unit the market is sized in. "
    "If the market name itself implies a narrow technology/intensity/grade (e.g. 'Super-Intensive', "
    "'Functional', 'Generative AI'), the definition MUST exclude the broader parent category (e.g. "
    "traditional/extensive farming, standard/commodity feed, non-generative AI) explicitly -- this is "
    "the single most important thing to get right."
)
_SCHEMA_HINT = (
    '{"included": "<1-2 sentences: exactly what counts as revenue/value in this market>", '
    '"excluded": "<1-2 sentences: the parent/adjacent categories and activities explicitly OUT of scope>", '
    '"value_chain_level": "<e.g. \'farming/production\', \'software/platform\', \'ingredient/formulation\'>", '
    '"unit_of_measure": "<e.g. \'USD million, market value\'>"}'
)


def generate_market_definition(market: str, llm: GenLLM, cfg: Any, log: list[str] | None = None) -> dict:
    """Returns {"market_name", "included", "excluded", "value_chain_level",
    "unit_of_measure"}. Never raises -- degrades to a generic definition
    built from the market name alone if search/LLM fails, so a fleet run
    never blocks on this step."""
    log = log if log is not None else []
    try:
        snippets = gather([
            f"{market} definition scope",
            f"{market} market segmentation included excluded",
        ], per_query=4, cap=8)
    except Exception as exc:  # noqa: BLE001
        snippets = []
        log.append(f"[market_definition] web search failed: {exc}")

    evidence = "WEB EVIDENCE:\n" + "\n".join(f"- {s}" for s in snippets) if snippets else "(no web evidence found)"
    user = f"Market: {market}\n\n{evidence}\n\nReturn ONLY valid JSON matching exactly this shape:\n{_SCHEMA_HINT}"
    try:
        data = llm.complete_json(_SYSTEM, user)
        definition = {
            "market_name": market,
            "included": str(data.get("included") or f"Revenue/value generated specifically by {market}.").strip(),
            "excluded": str(data.get("excluded") or "Parent and adjacent categories not specific to this market.").strip(),
            "value_chain_level": str(data.get("value_chain_level") or "n/a").strip(),
            "unit_of_measure": str(data.get("unit_of_measure") or "USD million, market value").strip(),
        }
        log.append("[market_definition] generated once, will be injected into every field-group prompt.")
        return definition
    except Exception as exc:  # noqa: BLE001
        log.append(f"[market_definition] LLM synthesis failed, using generic fallback: {exc}")
        return {
            "market_name": market,
            "included": f"Revenue/value generated specifically by {market}, as named.",
            "excluded": "Any broader parent category or adjacent market not specifically named.",
            "value_chain_level": "n/a",
            "unit_of_measure": "USD million, market value",
        }


def as_prompt_block(definition: dict | None) -> str:
    """Renders the definition as a prompt block every field-group agent
    prepends to its own query -- the actual scope-lock mechanism."""
    if not definition:
        return ""
    return (
        f"MARKET DEFINITION (do not deviate from this scope -- reject/ignore any number that actually "
        f"describes a parent or adjacent market):\n"
        f"- Included: {definition.get('included', '')}\n"
        f"- Excluded: {definition.get('excluded', '')}\n"
        f"- Value-chain level: {definition.get('value_chain_level', '')}\n"
        f"- Unit of measure: {definition.get('unit_of_measure', '')}\n\n"
    )

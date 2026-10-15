"""Generates ONE written market definition before any field-group agent
runs, so every agent searches/answers against the same scope instead of
each one independently (and inconsistently) deciding what "the market"
means. This is the fix for scope bleed -- e.g. a "Super-Intensive Shrimp
Farming Market" agent answering with the whole shrimp farming/processing
industry's numbers instead of just the super-intensive-systems slice.

Called once per fleet run (`run_fleet.py`), BEFORE any field group; its
result is injected into every field group's prompt by `field_agent.py`.
`cmi_report/run_data.py` regenerates it (with the client's definition as the
anchor) when the stored one predates PLAN-04.

The definition states what revenue is counted and at which level of the
value chain (`measures`, `value_chain_level`), with non-empty `included` and
`excluded` lists; an answer missing any of them is re-asked once (PLAN-04).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from me_engine.generation.llm import GenLLM  # noqa: E402
from me_engine.generation.web_evidence import gather  # noqa: E402

# The value-chain levels a market can be sized at (PLAN-04).
LEVELS = ("producer", "manufacturer", "vendor/software", "service", "farm-gate output", "retail")
_LEVEL_ALIASES = {"farm-gate": "farm-gate output", "farm gate": "farm-gate output", "farm gate output":
                  "farm-gate output", "software": "vendor/software", "vendor": "vendor/software",
                  "services": "service", "manufacturing": "manufacturer", "production": "producer"}

_SYSTEM = (
    "You are a market-scoping analyst. Before any sizing or segmentation work starts, define EXACTLY "
    "what this market does and does not include, so every later analysis stays inside this boundary. "
    "State WHAT REVENUE IS COUNTED and AT WHICH LEVEL OF THE VALUE CHAIN in one sentence (e.g. 'ex-factory "
    "sales of ...', 'farm-gate value of ...', 'vendor revenue from ...'), and pick exactly one value-chain "
    "level from: " + " | ".join(LEVELS) + ". Everything included must be at that level: a farm-gate "
    "output market does not count sales of the equipment or technology used to produce the output; a "
    "vendor/software market does not count the value of what its buyers produce. "
    "If the market name itself implies a narrow technology/intensity/grade (e.g. 'Super-Intensive', "
    "'Functional', 'Generative AI'), the definition MUST exclude the broader parent category (e.g. "
    "traditional/extensive farming, standard/commodity feed, non-generative AI) explicitly -- this is "
    "the single most important thing to get right. 'included' and 'excluded' are never empty; each item "
    "is a short noun phrase. Name every major end use or application of what is measured in one of the two "
    "lists (e.g. for an ingredient: supplements, food, pharmaceuticals, cosmetics, animal feed -- each one "
    "either counted or not), so no later analysis has to guess whether a use is in scope."
)
_SCHEMA_HINT = (
    '{"measures": "<1 sentence: what revenue is counted, and at which value-chain level>", '
    '"value_chain_level": "<exactly one of: ' + " | ".join(LEVELS) + '>", '
    '"included": ["<short item whose revenue is counted>", ... at least 2], '
    '"excluded": ["<short item NOT counted: a parent/adjacent category or another value-chain level>", '
    '... at least 2], '
    '"unit": "<e.g. USD million, market value>", '
    '"typical_published_size_usd_bn": <number if the EVIDENCE states a published global size for this '
    'exact scope, else null>}'
)


def items(v) -> list[str]:
    """A list answer as clean strings ('a; b' and ['a', ''] both work)."""
    if isinstance(v, str):
        v = v.replace("\n", ";").split(";")
    if not isinstance(v, (list, tuple)):
        return []
    return [str(x).strip(" .") for x in v if str(x).strip(" .")]


def normalise_level(v) -> str | None:
    """One of LEVELS, or None when the answer is not exactly one of them."""
    t = str(v or "").strip().lower()
    t = _LEVEL_ALIASES.get(t, t)
    return t if t in LEVELS else None


def check_definition(d: dict | None) -> list[str]:
    """What is missing from a definition ([] = complete)."""
    d = d or {}
    out = []
    if not str(d.get("measures") or "").strip():
        out.append("'measures' is empty: say what revenue is counted and at which value-chain level")
    if not normalise_level(d.get("value_chain_level")):
        out.append(f"'value_chain_level' is '{d.get('value_chain_level') or ''}', not exactly one of: "
                   + ", ".join(LEVELS))
    for k in ("included", "excluded"):
        if not items(d.get(k)):
            out.append(f"'{k}' is empty: list what is {'counted' if k == 'included' else 'not counted'}")
    return out


def _client_block(client: dict | None) -> str:
    if not client:
        return ""
    return ("CLIENT DEFINITION (verbatim and authoritative: keep every client exclusion in 'excluded', and "
            "read the client's inclusions at the value-chain level you choose -- e.g. technology a client "
            "lists for an output market describes HOW the output is produced, not revenue that is counted):\n"
            + "\n".join(f"- {k}: {client.get(k)}" for k in ("definition", "inclusions", "exclusions")
                        if client.get(k)) + "\n\n")


def _shape(data: dict, market: str) -> dict:
    size = data.get("typical_published_size_usd_bn")
    return {
        "market_name": market,
        "measures": str(data.get("measures") or "").strip(),
        "value_chain_level": normalise_level(data.get("value_chain_level")) or str(data.get("value_chain_level") or ""),
        "included": items(data.get("included")),
        "excluded": items(data.get("excluded")),
        "unit": str(data.get("unit") or data.get("unit_of_measure") or "USD million, market value").strip(),
        "typical_published_size_usd_bn": size if isinstance(size, (int, float)) and not isinstance(size, bool) else None,
    }


def generate_market_definition(market: str, llm: GenLLM, cfg: Any, log: list[str] | None = None, *,
                               client: dict | None = None) -> dict:
    """Returns {"market_name", "measures", "value_chain_level", "included" (list),
    "excluded" (list), "unit", "unit_of_measure", "typical_published_size_usd_bn",
    "check"}. An answer failing check_definition is re-asked once with its
    problems shown; whatever is still missing after that stays in
    check["problems"] (a blocker in m["segment_review"]) -- it is never filled
    in with generic text. `client` is a client-supplied definition the answer
    must keep to. Never raises, so a fleet run never blocks on this step."""
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
    user = (f"Market: {market}\n\n{_client_block(client)}{evidence}\n\n"
            f"Return ONLY valid JSON matching exactly this shape:\n{_SCHEMA_HINT}")

    def ask(note: str = "") -> dict | None:
        try:
            return _shape(llm.complete_json(_SYSTEM, user + note), market)
        except Exception as exc:  # noqa: BLE001
            log.append(f"[market_definition] LLM synthesis failed: {exc}")
            return None

    d = ask()
    check: dict = {"first_problems": check_definition(d) if d else ["no answer from the agent"],
                   "reasked": False, "problems": []}
    if check["first_problems"]:
        log.append("[market_definition] " + "; ".join(check["first_problems"]) + " -- re-asking once")
        check["reasked"] = True
        again = ask("\n\nYOUR PREVIOUS ANSWER failed these checks:\n"
                    + "\n".join(f"- {p}" for p in check["first_problems"])
                    + "\nReturn a corrected answer in exactly the same shape."
                    + (f"\nPREVIOUS ANSWER:\n{json.dumps(d, ensure_ascii=False)}" if d else ""))
        if again is not None:
            d = again
    d = d or _shape({}, market)
    check["problems"] = check_definition(d)
    if check["problems"]:
        log.append("[market_definition] still incomplete after one re-ask: " + "; ".join(check["problems"]))
    else:
        log.append("[market_definition] generated once, will be injected into every field-group prompt.")
    return {**d, "unit_of_measure": d["unit"], "check": check}


def is_current(definition: dict | None) -> bool:
    """A definition generated by this version (it carries its own check)."""
    return bool(definition) and isinstance(definition.get("check"), dict)


def listed(v) -> str:
    """'a; b' for a list, the text itself for an older string definition."""
    return "; ".join(items(v)) if isinstance(v, (list, tuple)) else str(v or "")


def as_prompt_block(definition: dict | None) -> str:
    """Renders the definition as a prompt block every field-group agent
    prepends to its own query -- the actual scope-lock mechanism."""
    if not definition:
        return ""
    return (
        "MARKET DEFINITION (do not deviate from this scope -- reject/ignore any number that actually "
        "describes a parent or adjacent market, or another level of the value chain):\n"
        + (f"- Measures: {definition['measures']}\n" if definition.get("measures") else "")
        + f"- Included: {listed(definition.get('included'))}\n"
        f"- Excluded: {listed(definition.get('excluded'))}\n"
        f"- Value-chain level: {definition.get('value_chain_level', '')}\n"
        f"- Unit of measure: {definition.get('unit_of_measure') or definition.get('unit', '')}\n\n"
    )

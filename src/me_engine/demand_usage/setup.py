"""Phase 1: Market Setup Draft -- the FIRST user approval gate.

Produces one small, structured, EDITABLE object (Market, Scope, Geography,
Base Year, Market Unit, Physical Unit) via one combined, web-grounded LLM
call. The LLM only proposes this draft; nothing downstream (Phase 2 or
Phase 3) may run until a human approves it, possibly after editing any
field -- see `checkpoints.py` for the draft/approved file contract shared by
the CLI and the backend API routes.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from me_engine.generation.config import GenConfig
from me_engine.generation.llm import GenLLM
from me_engine.generation.web_evidence import gather

from . import checkpoints

_DEFAULT_MARKET_UNIT = "USD Mn"
_DEFAULT_PHYSICAL_UNIT = "Units"
_SETUP_FIELDS = ("market_name", "scope", "geography", "base_year", "market_unit", "physical_unit")


def _current_year() -> int:
    # Explicit call, not inlined -- kept easy to monkeypatch in tests.
    return datetime.now().year


def market_setup_draft_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg: GenConfig = state["config"]
    llm: GenLLM = state["llm"]
    market = state["market_name"]
    log = state.setdefault("log", [])

    snippets: list[str] = []
    if cfg.enable_web:
        try:
            snippets = gather([
                f"{market} market definition scope",
                f"{market} regional market share major countries",
                f"{market} market size unit measured in",
            ], per_query=5, cap=15)
        except Exception as exc:  # noqa: BLE001
            log.append(f"[market_setup] could not fetch web evidence: {exc}")

    system = (
        "You are a market-scoping analyst preparing a DRAFT for human review -- every field you "
        "propose will be shown to a person who can edit or reject it before any downstream work "
        "happens, so be concrete and specific, never vague filler.\n\n"
        "Propose: (1) a 2-4 sentence scope statement (what IS and is NOT included in this "
        "market's boundaries); (2) the countries that materially drive demand for this market "
        "(8-14 entries, prefer individual countries, 'Rest of <Region>' only for genuine long "
        "tail); (3) the most recent full year this market's data would realistically be "
        "reported for (base_year); (4) the currency/scale unit the market SIZE should be "
        "reported in (market_unit, e.g. 'USD Mn', 'USD Bn'); (5) the single physical unit this "
        "market's underlying volume is most naturally measured in (physical_unit, e.g. "
        "'Barrels', 'Tonnes', 'Units', 'kWh') -- this anchors the whole bottom-up build, so pick "
        "what industry sources actually use for THIS market, never a generic default."
    )
    user = (
        f"Market: {market}\n\n"
        + ("WEB EVIDENCE:\n" + "\n".join(f"- {s}" for s in snippets) if snippets else "(no external evidence available)")
        + "\n\nReturn JSON: {\"scope\": \"<statement>\", \"geography\": [\"<country>\", ...], "
        '"base_year": <int>, "market_unit": "<string>", "physical_unit": "<string>", '
        '"grounded": <true|false>}'
    )
    try:
        data = llm.complete_json(system, user)
    except Exception as exc:  # noqa: BLE001
        log.append(f"[market_setup] LLM call failed: {exc}")
        data = {}

    scope = str(data.get("scope") or "").strip() or f"The {market}."
    geography = [str(c).strip() for c in (data.get("geography") or []) if str(c).strip()]
    if not geography:
        geography = ["United States", "China", "Germany", "Rest of World"]
        log.append("[market_setup] LLM returned no countries -- using a minimal fallback scope")
    try:
        base_year = int(data.get("base_year"))
    except (TypeError, ValueError):
        base_year = _current_year()
        log.append("[market_setup] LLM returned no usable base_year -- defaulting to the current year")
    market_unit = str(data.get("market_unit") or "").strip() or _DEFAULT_MARKET_UNIT
    physical_unit = str(data.get("physical_unit") or "").strip() or _DEFAULT_PHYSICAL_UNIT
    grounded = bool(data.get("grounded"))

    log.append(
        f"Market setup draft proposed ({'grounded' if grounded else 'ungrounded estimate'}): "
        f"{len(geography)} countries, base year {base_year}, market unit '{market_unit}', "
        f"physical unit '{physical_unit}'. EDITABLE -- nothing downstream runs until approved."
    )
    return {
        "market_name": market,
        "scope": scope,
        "geography": geography,
        "base_year": base_year,
        "market_unit": market_unit,
        "physical_unit": physical_unit,
        "grounded": grounded,
        "setup_snippets": snippets,
        "log": log,
    }


def run_setup_phase(
    market_name: str,
    config: GenConfig,
    cache_path: Path | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Runs the single-node Phase 1 draft and writes the draft checkpoint
    file. Returns the draft dict (same shape written to disk)."""
    llm = GenLLM(config, cache_path=cache_path)
    state: dict[str, Any] = {"market_name": market_name, "config": config, "llm": llm, "log": []}
    draft = market_setup_draft_node(state)
    checkpoints.write_json(checkpoints.setup_draft_path(market_name, root), draft)
    return draft


def approve_setup(edited: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    """Validates and writes the approved setup checkpoint -- the ONLY gate
    before Phase 2 (taxonomy) may run. `edited` is the draft as returned by
    `run_setup_phase`, possibly hand-edited by the user (any field, any
    value) before this call."""
    market_name = str(edited.get("market_name") or "").strip()
    if not market_name:
        raise ValueError("approved setup is missing market_name")
    missing = [f for f in _SETUP_FIELDS if not edited.get(f) and edited.get(f) != 0]
    if missing:
        raise ValueError(f"approved setup is missing required field(s): {missing}")
    geography = [str(c).strip() for c in edited["geography"] if str(c).strip()]
    if not geography:
        raise ValueError("approved setup must have at least one geography entry")
    try:
        base_year = int(edited["base_year"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"approved setup's base_year must be an integer, got {edited['base_year']!r}") from exc

    approved = {
        "market_name": market_name,
        "scope": str(edited["scope"]).strip(),
        "geography": geography,
        "base_year": base_year,
        "market_unit": str(edited["market_unit"]).strip(),
        "physical_unit": str(edited["physical_unit"]).strip(),
        "grounded": bool(edited.get("grounded", False)),
        "approved": True,
    }
    checkpoints.write_json(checkpoints.setup_approved_path(market_name, root), approved)
    return approved


def load_approved_setup(market_name: str, root: Path | None = None) -> dict[str, Any]:
    data = checkpoints.read_json(checkpoints.setup_approved_path(market_name, root))
    if data is None:
        raise FileNotFoundError(
            f"No approved market setup found for '{market_name}' -- run Phase 1 (setup) and approve "
            "it before Phase 2 (taxonomy)."
        )
    return data

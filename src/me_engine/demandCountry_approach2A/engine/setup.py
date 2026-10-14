"""Phase 1: Market Setup Draft -- the FIRST user approval gate.

Produces one small, structured, EDITABLE object (Market, Scope, Geography,
Base Year, Market Unit, Physical Unit) via one combined, web-grounded LLM
call. The LLM only proposes this draft; nothing downstream (Phase 2 or
Phase 3) may run until a human approves it, possibly after editing any
field -- see `checkpoints.py` for the draft/approved file contract shared by
the CLI and the backend API routes.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from me_engine.generation.config import GenConfig
from me_engine.generation.llm import GenLLM

from . import checkpoints
from .web_search import gather

_DEFAULT_MARKET_UNIT = "USD Mn"
_DEFAULT_PHYSICAL_UNIT = "Units"
_SETUP_FIELDS = ("market_name", "scope", "geography", "base_year", "market_unit", "physical_unit")


def _current_year() -> int:
    # Explicit call, not inlined -- kept easy to monkeypatch in tests.
    return datetime.now().year


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def market_setup_draft_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg: GenConfig = state["config"]
    llm: GenLLM = state["llm"]
    market = state["market_name"]
    log = state.setdefault("log", [])
    # Additive audit/observability layer (`me_engine.audit`) -- optional
    # `AuditEmitter`, read via `state.get("emitter")` exactly like every
    # other demand-usage node (`agents.py`, `taxonomy.py`); a no-op
    # everywhere it's absent, never a hard dependency.
    emitter = state.get("emitter")
    if emitter is not None:
        emitter.emit("setup", "stage_start", data={"node": "market_setup_draft_node"})

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
        "market's boundaries). This is a BOTTOM-UP, USAGE-BASED demand methodology: the scope "
        "statement draws the line around what is VALUED as this market's own commodity, never "
        "around what evidence may be USED to derive that value. If this market is a raw/upstream "
        "commodity (e.g. Crude Oil) whose scope excludes downstream/refined products (e.g. "
        "gasoline, diesel, asphalt, petrochemicals) as SEPARATELY VALUED markets, say so explicitly "
        "-- but also state plainly that downstream/refined-product END-USE DEMAND may still be used "
        "as an intermediate signal to derive how much of the raw/upstream commodity that demand "
        "requires (e.g. 'asphalt production volume' is legitimate evidence for how much crude oil "
        "road construction requires, even though asphalt itself is never priced or valued as part "
        "of this market). Downstream-product involvement in a usage bucket must never, by itself, "
        "place that usage outside scope -- only a usage with NO defensible link to this market's own "
        "commodity at all is out of scope; (2) the countries that materially drive demand for this market "
        "(8-14 entries, prefer individual countries, 'Rest of <Region>' only for genuine long "
        "tail); (3) the most recent full year this market's data would realistically be "
        "reported for (base_year); (4) the currency/scale unit the market SIZE should be "
        "reported in (market_unit, e.g. 'USD Mn', 'USD Bn'); (5) the single physical unit this "
        "market's underlying volume is most naturally measured in (physical_unit, e.g. "
        "'Barrels', 'Tonnes', 'kWh', 'gloves (single pieces)') -- this anchors the whole bottom-up "
        "build, so pick what industry sources actually use for THIS market, never a generic "
        "default. For a counted product, NAME the item and say whether it is a single piece or a "
        "pack (e.g. 'gloves (single pieces)', 'smartphones (units shipped)', 'tyres (pieces)') -- "
        "never a bare 'Units', which later steps cannot tell apart from a box or pack."
    )
    user = (
        f"Market: {market}\n\n"
        + ("WEB EVIDENCE:\n" + "\n".join(f"- {s}" for s in snippets) if snippets else "(no external evidence available)")
        + "\n\nReturn JSON: {\"scope\": \"<statement>\", \"geography\": [\"<country>\", ...], "
        '"base_year": <int>, "market_unit": "<string>", "physical_unit": "<string>", '
        '"grounded": <true|false>}'
    )
    llm_error: str | None = None
    try:
        data = llm.complete_json(system, user)
    except Exception as exc:  # noqa: BLE001
        log.append(f"[market_setup] LLM call failed: {exc}")
        data = {}
        llm_error = str(exc)
    if emitter is not None:
        emitter.emit(
            "setup", "llm_call",
            llm={"prompt": f"{system}\n\n{user}", "response": json.dumps(data, ensure_ascii=False) if data else "", "error": llm_error},
            status="failed" if llm_error else "success",
        )

    scope = str(data.get("scope") or "").strip() or f"The {market}."
    geography = [str(c).strip() for c in (data.get("geography") or []) if str(c).strip()]
    if not geography:
        geography = ["United States", "China", "Germany", "Rest of World"]
        log.append("[market_setup] LLM returned no countries -- using a minimal fallback scope")
    # Every approach reports the same base year (2025 by default, set with
    # DEAGENT_BASE_YEAR) so their numbers compare; the LLM's own pick is only
    # logged. A reviewer can still change it before approving.
    base_year = int(os.environ.get("DEAGENT_BASE_YEAR", "2025"))
    try:
        proposed = int(data.get("base_year"))
    except (TypeError, ValueError):
        proposed = None
    if proposed is not None and proposed != base_year:
        log.append(f"[market_setup] LLM proposed base_year {proposed}; using the standard base year {base_year}")
    market_unit = str(data.get("market_unit") or "").strip() or _DEFAULT_MARKET_UNIT
    physical_unit = str(data.get("physical_unit") or "").strip() or _DEFAULT_PHYSICAL_UNIT
    grounded = bool(data.get("grounded"))

    log.append(
        f"Market setup draft proposed ({'grounded' if grounded else 'ungrounded estimate'}): "
        f"{len(geography)} countries, base year {base_year}, market unit '{market_unit}', "
        f"physical unit '{physical_unit}'. EDITABLE -- nothing downstream runs until approved."
    )
    if emitter is not None:
        emitter.emit(
            "setup", "stage_end",
            data={"node": "market_setup_draft_node"},
            output_data={
                "scope": scope, "geography": geography, "base_year": base_year,
                "market_unit": market_unit, "physical_unit": physical_unit, "grounded": grounded,
            },
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

    # Additive audit/observability layer (`me_engine.audit`) -- one
    # `AuditEmitter` per run, injected into the initial state exactly like
    # `graph.run_sizing_phase` does for Phase 3. Never raises: if the audit
    # package can't be constructed for any reason, setup must still proceed
    # exactly as before.
    emitter = None
    try:
        from me_engine.audit.store import AuditEmitter

        emitter = AuditEmitter(run_id=market_name, approach="usage_based", market=market_name, root=root)
    except Exception:  # noqa: BLE001
        emitter = None

    state: dict[str, Any] = {
        "market_name": market_name, "config": config, "llm": llm, "log": [], "emitter": emitter,
    }
    draft = market_setup_draft_node(state)
    checkpoints.write_json(checkpoints.setup_draft_path(market_name, root), draft)
    return draft


def approve_setup(
    edited: dict[str, Any], root: Path | None = None,
    approved_by: str | None = None, emitter: Any | None = None,
) -> dict[str, Any]:
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
        # Additive audit fields -- mirrors `taxonomy.approve_taxonomy`:
        # `approved_by` is optional/backward compatible (None if not
        # supplied); `approved_at` is a real UTC timestamp captured at the
        # moment of this approval call.
        "approved_by": approved_by,
        "approved_at": _now_iso(),
    }
    # `emitter` is accepted for a stable, forward-compatible signature
    # (mirroring `taxonomy.approve_taxonomy`), but there is no real
    # precedent to mirror here: `approve_taxonomy` itself emits nothing on
    # a plain approval -- only on an explicit `scope_override`, a concept
    # that has no equivalent in Phase 1's setup approval. So a plain setup
    # approval never emits an event either, exactly matching that precedent.
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

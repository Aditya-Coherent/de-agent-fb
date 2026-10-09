"""Interpret a free-form market brief into a structured scope using the LLM.

Supports three user intents, auto-detected:
  - "structured"  : user clearly states market + the segments to cover.
  - "detailed"    : user describes the value chain and which entities to INCLUDE / EXCLUDE for
                    market sizing (the analyst process). The LLM extracts sections + exclusions.
  - "standard"    : user gave only a bare market name -> default value-chain taxonomy is used.

The LLM is instructed to extract ONLY what the user stated (no invented requirements), so the
downstream run is scoped to the user's actual intent rather than a hallucinated one.
"""
from __future__ import annotations

import re
from typing import Any

# Strip value-chain boilerplate the LLM sometimes bakes into the market name
# (e.g. "Functional Entities in the U.S. Aging-in-Place Safety Solutions Value Chain"
#  -> "Aging-in-Place Safety Solutions"). Only fires when the wrapper is present, so a
# clean name like "Satcom" is returned unchanged.
_MARKET_WRAPPER = re.compile(
    r"^\s*(?:functional\s+|key\s+)?entit(?:y|ies)\s+(?:in|of|within|across)\s+the\s+", re.I
)
_VALUE_CHAIN_TAIL = re.compile(r"\s+value[\s-]+chain\s*$", re.I)
_GEO_PREFIX = re.compile(
    r"^(?:the\s+)?(?:u\.?\s?s\.?a?|usa|united\s+states|global|worldwide|europe(?:an)?|uk|asia(?:[\s-]pacific)?)\s+",
    re.I,
)


def _clean_market_name(market: str) -> str:
    """Remove 'Functional Entities in the … Value Chain' wrappers (and a leading geo) from a
    market name. No-op for names without that boilerplate (e.g. 'Satcom')."""
    m = (market or "").strip()
    if not m:
        return m
    if not (_MARKET_WRAPPER.search(m) or _VALUE_CHAIN_TAIL.search(m)):
        return m  # clean name — leave it alone
    m2 = _MARKET_WRAPPER.sub("", m)
    m2 = _VALUE_CHAIN_TAIL.sub("", m2)
    m2 = _GEO_PREFIX.sub("", m2).strip(" -–—:,")
    return m2 if len(m2) >= 3 else m


_SYS = (
    "You are a market-research scoping assistant. The user describes a market they want analysed - "
    "either briefly (just a market name) or in detail (value-chain entities, and which to include "
    "or exclude for market sizing). Interpret their intent and return a structured scope. "
    "CRITICAL: extract ONLY what the user actually stated or clearly implied - do NOT invent "
    "segments, exclusions, or requirements they did not express.\n\n"
    "Determine:\n"
    "- market: concise market name.\n"
    "- geography: the geography mentioned, else 'global'.\n"
    "- mode: 'detailed' if the user described value-chain segments and/or include-exclude rules; "
    "'structured' if they gave a market plus an explicit list of sections; 'standard' if they gave "
    "only a bare market name with no segmentation.\n"
    "- sections: EVERY functional entity / value-chain segment the brief describes - present the "
    "FULL value chain as sections (e.g. all of 'Manufacturers & Launch Providers', 'Satellite "
    "Operators', 'Ground Segment & Equipment Manufacturers', 'Network & Service Providers'), NOT "
    "only the ones kept for sizing. Concise headings of 2-6 words each; put examples/detail in "
    "'definition', NOT in the section names. Empty list for 'standard'.\n"
    "- exclude: the segments the user excludes from MARKET SIZING (counted elsewhere or to avoid "
    "double-counting), as short labels matching the section names (e.g. 'Satellite Manufacturers', "
    "'Launch Providers', 'Wholesale-Only Operators'). These STILL appear as sections - the label is "
    "used only to flag companies for the sizing note. Empty if none.\n"
    "- definition: a 2-4 sentence scope statement capturing what is IN and OUT, built only from what "
    "the user said. Empty for 'standard'.\n\n"
    'Return ONLY JSON: {"market":"","geography":"","mode":"","sections":[],"exclude":[],"definition":""}'
)


_GEN_BRIEF_SYS = (
    "You are a senior market-intelligence analyst. For the given MARKET and GEOGRAPHY, draft a "
    "value-chain brief an analyst can review and edit. Use PLAIN TEXT in exactly this structure:\n\n"
    "Market: <market name>\nGeography: <geography>\n\n"
    "One-line market definition.\n\n"
    "FUNCTIONAL ENTITIES IN THE VALUE CHAIN (these are the segments to profile):\n"
    "For each segment (3-6 total, ordered upstream -> downstream), give:\n"
    "  <n>. <Segment name>\n"
    "     - Function: what they do.\n"
    "     - Core entities: the sub-types, with 2-3 REAL example companies.\n"
    "     - Business model: how they make money.\n"
    "     - Market sizing: INCLUDE / INCLUDE PARTIALLY / EXCLUDE, with a one-line reason.\n\n"
    "SCOPING DISCIPLINE (critical — a careless EXCLUDE silently drops real market participants "
    "the analyst wants, especially in pharma, diagnostics, and life-science markets):\n"
    "- INCLUDE every segment that sells a FINISHED, market-specific product or service: instruments, "
    "reagents, kits, assays, master mixes, enzymes, primers, probes, cartridges, consumables, test "
    "strips, modules, software, etc. These branded products ARE the market — the reagent / consumable "
    "segment is frequently the LARGEST revenue pool, so never wave it off as 'just upstream'.\n"
    "- Only mark a segment EXCLUDE when its revenue is genuinely one of: (a) double-counted inside an "
    "included segment's price, (b) a different market (care delivery, financing, logistics), (c) a "
    "pure reseller / distributor of others' products, (d) an end-user / buyer, or (e) a generic "
    "commodity input (bulk chemicals, raw plastic/metal, generic electronics) sold across many "
    "UNRELATED industries. A company is NOT commodity-upstream merely because it makes 'components': "
    "enzymes, primers, probes, and consumables branded and sold FOR this market are IN scope.\n"
    "- If a player both supplies inputs AND sells finished market products, treat it as INCLUDE.\n"
    "- When unsure, INCLUDE the product-making segment; reserve EXCLUDE for clear buyers, resellers, "
    "financiers, and true cross-industry raw-material suppliers.\n\n"
    "WHICH ENTITIES TO INCLUDE IN MARKET SIZING: a short paragraph stating which segments count "
    "toward the market size and which are excluded or consolidated to avoid double counting. Keep the "
    "included set broad enough to capture the instrument, reagent/consumable, and service makers of "
    "this market — do not strand a whole class of genuine vendors in an excluded 'raw materials' bucket.\n\n"
    "Only real, well-known companies. No preamble, no markdown fences — just the brief text."
)


def generate_market_brief(market: str, geo: str, settings: Any, claude: Any | None = None) -> str:
    """Draft an editable value-chain + market-sizing brief for a market using the LLM.
    Returns plain text (empty string if the LLM is unavailable). Best-effort; never raises."""
    market = str(market or "").strip()
    if not market:
        return ""
    if claude is None:
        from vendor_intel.clients.claude import ClaudeClient

        claude = ClaudeClient(settings)
    if not getattr(claude, "available", False):
        return ""
    model = getattr(settings, "market_map_model", None) or "claude-sonnet-4-6"
    user = f"MARKET: {market}\nGEOGRAPHY: {geo or 'global'}"
    try:
        return str(claude.complete(_GEN_BRIEF_SYS, user, model=model, max_tokens=2000) or "").strip()
    except Exception:
        try:
            return str(claude.complete(_GEN_BRIEF_SYS, user,
                                       model="claude-haiku-4-5-20251001", max_tokens=2000) or "").strip()
        except Exception:
            return ""


_GEN_SECTIONS_SYS = (
    "You are a market analyst. For the given MARKET and GEOGRAPHY, list the 3-6 FUNCTIONAL "
    "value-chain segments (participant categories) an analyst would profile, ordered upstream -> "
    "downstream. Use concise segment NAMES suitable as report sections (e.g. 'Upstream: "
    "Manufacturers & Launch Providers', 'Satellite Operators', 'Ground Segment & Equipment "
    "Manufacturers', 'Network & Service Providers'). Names only — no descriptions.\n"
    'Return ONLY JSON: {"sections":["<name>", ...]}'
)

_GEN_SECTION_CARDS_SYS = (
    "You are a senior market-intelligence analyst. For the given MARKET and GEOGRAPHY, draft "
    "3-6 FUNCTIONAL value-chain segments an analyst would profile, ordered upstream -> downstream.\n\n"
    "SCOPING DISCIPLINE (critical):\n"
    "- INCLUDE every segment that sells a FINISHED, market-specific product or service.\n"
    "- Only EXCLUDE clear buyers, pure resellers, financiers, care delivery, or true cross-industry "
    "commodity raw materials. When unsure, INCLUDE.\n\n"
    "For EACH segment return:\n"
    "- name: concise section title (e.g. 'Device-Agnostic Platform Providers')\n"
    "- content: plain text bullets the analyst can edit, covering:\n"
    "  Function: what they do.\n"
    "  Core entities: sub-types with 2-3 REAL example companies.\n"
    "  Business model: how they make money.\n"
    "  Market sizing: INCLUDE / INCLUDE PARTIALLY / EXCLUDE, with a one-line reason.\n\n"
    'Return ONLY JSON: {"sections":[{"name":"...","content":"..."}, ...]}'
)


def generate_market_sections(market: str, geo: str, settings: Any, claude: Any | None = None) -> list[str]:
    """Draft the value-chain segment NAMES for a market (for the user to fill in). Returns a list
    of section names (empty if the LLM is unavailable). Best-effort; never raises."""
    cards = generate_market_section_cards(market, geo, settings, claude=claude)
    return [c["name"] for c in cards if c.get("name")]


def generate_market_section_cards(
    market: str, geo: str, settings: Any, claude: Any | None = None
) -> list[dict[str, str]]:
    """Draft section name + 'what to profile' notes for each value-chain segment.

    Matches the Streamlit wizard language (Function / Core entities / Business model /
    Market sizing INCLUDE|EXCLUDE). Best-effort; never raises.
    """
    market = str(market or "").strip()
    if not market:
        return []
    if claude is None:
        from vendor_intel.clients.claude import ClaudeClient

        claude = ClaudeClient(settings)
    if not getattr(claude, "available", False):
        return []
    model = getattr(settings, "market_map_model", None) or "claude-sonnet-4-6"
    user = f"MARKET: {market}\nGEOGRAPHY: {geo or 'global'}"
    try:
        out = claude.complete_json(_GEN_SECTION_CARDS_SYS, user, model=model, max_tokens=2500)
    except Exception:
        try:
            out = claude.complete_json(
                _GEN_SECTION_CARDS_SYS,
                user,
                model="claude-haiku-4-5-20251001",
                max_tokens=2500,
            )
        except Exception:
            out = {}
    rows = out.get("sections") if isinstance(out, dict) else out
    res: list[dict[str, str]] = []
    seen: set[str] = set()
    for r in rows or []:
        if isinstance(r, dict):
            name = str(r.get("name") or r.get("section") or r.get("label") or "").strip()
            content = str(r.get("content") or r.get("profile") or r.get("notes") or "").strip()
        else:
            name = str(r or "").strip()
            content = ""
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        res.append({"name": name, "content": content})
    # Fallback: names-only prompt if cards came back empty / without notes
    if not res or not any(c.get("content") for c in res):
        try:
            out2 = claude.complete_json(
                _GEN_SECTIONS_SYS, user, model=model, max_tokens=600,
            )
        except Exception:
            out2 = {}
        rows2 = out2.get("sections") if isinstance(out2, dict) else out2
        names_only: list[str] = []
        for r in rows2 or []:
            s = str((r.get("name") if isinstance(r, dict) else r) or "").strip()
            if s and s.lower() not in seen and s not in names_only:
                names_only.append(s)
        if names_only and not res:
            return [{"name": n, "content": ""} for n in names_only[:8]]
    return res[:8]


def interpret_brief(text: str, settings: Any, claude: Any | None = None) -> dict:
    """Return a scope dict. Falls back to a bare 'standard' scope if the LLM is unavailable
    or the input is empty."""
    raw = (text or "").strip()
    if not raw:
        return {"market": "", "geography": "global", "mode": "standard", "sections": [], "exclude": [], "definition": ""}
    if claude is None:
        from vendor_intel.clients.claude import ClaudeClient

        claude = ClaudeClient(settings)
    if not getattr(claude, "available", False):
        # no LLM: treat the whole input as the market name (standard run)
        return {"market": raw.split("\n")[0][:120], "geography": "global", "mode": "standard",
                "sections": [], "exclude": [], "definition": ""}

    model = getattr(settings, "market_map_model", None) or "claude-sonnet-4-6"
    try:
        out = claude.complete_json(_SYS, f"USER BRIEF:\n{raw[:6000]}", model=model, max_tokens=2048)
    except Exception:
        try:
            out = claude.complete_json(_SYS, f"USER BRIEF:\n{raw[:6000]}",
                                       model="claude-haiku-4-5-20251001", max_tokens=2048)
        except Exception:
            out = {}
    if not isinstance(out, dict):
        out = {}
    def _label(s: Any) -> str:
        # the LLM sometimes returns {name, definition} objects instead of plain strings
        if isinstance(s, dict):
            return str(s.get("name") or s.get("section") or s.get("label") or "").strip()
        return str(s).strip()

    market = _clean_market_name(str(out.get("market") or raw.split("\n")[0][:120]).strip())
    geo = str(out.get("geography") or "global").strip() or "global"
    mode = str(out.get("mode") or "standard").strip().lower()
    sections = [_label(s) for s in (out.get("sections") or []) if _label(s)]
    exclude = [_label(s) for s in (out.get("exclude") or []) if _label(s)]
    definition = str(out.get("definition") or "").strip()
    if mode not in ("detailed", "structured", "standard"):
        mode = "detailed" if (sections or exclude) else "standard"
    # In DETAILED mode the brief's "exclude" list is a MARKET-SIZING note (revenue counted
    # elsewhere / avoid double-counting), NOT a company-landscape exclusion. Dropping it so those
    # segments and companies (e.g. Technology / Future Supply Enablers, satellite operators) are
    # still discovered, profiled and classified — not filtered out or penalized.
    if mode == "detailed":
        exclude = []
    return {
        "market": market,
        "geography": geo,
        "mode": mode,
        "sections": sections,
        "exclude": exclude,
        "definition": definition,
    }

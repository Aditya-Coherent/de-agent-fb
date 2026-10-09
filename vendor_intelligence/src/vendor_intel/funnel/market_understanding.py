"""LLM-driven market understanding — definition, value-chain mapping, query + seed generation."""
from __future__ import annotations

import json
import re
from typing import Any

from vendor_intel.config import _project_root
from vendor_intel.discovery.discovery_query_quality import (
    is_listicle_discovery_query,
    sanitize_discovery_query,
)
from vendor_intel.funnel.prompt_builder import _q, geo_search_label, refine_search_topic
from vendor_intel.mock.fixtures import is_mock_run

_LAYER_ID_RE = re.compile(r"^L\d+$", re.I)


def _load_market_understanding_prompt() -> str:
    path = _project_root() / "config" / "prompts" / "market_understanding.txt"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return (
        "Analyze the market query. Return JSON with market_definition, value_chain_layers, "
        "discovery_prompts, seed_companies."
    )


def _slug_sub_sector(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return s[:32] or "general"


def _clean_str_list(raw: Any, *, max_items: int = 20) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item or "").strip()
        if len(text) < 2:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= max_items:
            break
    return out


def _normalize_domain(raw: str) -> str:
    d = (raw or "").strip().lower().removeprefix("www.")
    if d.startswith("http"):
        from vendor_intel.utils.domains import domain_from_url

        d = domain_from_url(d) or d
    return d if "." in d and " " not in d else ""


def _normalize_seed_rows(raw: Any, *, max_items: int = 14) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in raw:
        if not isinstance(row, dict):
            continue
        name = str(row.get("canonical_name") or row.get("name") or "").strip()
        dom = _normalize_domain(str(row.get("primary_domain") or row.get("domain") or ""))
        if not name:
            continue
        key = dom or name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "canonical_name": name,
                "primary_domain": dom,
                "company_function": str(row.get("company_function") or row.get("function") or "").strip(),
                "segment": str(row.get("segment") or "").strip(),
                "source": "market_map_llm",
            }
        )
        if len(out) >= max_items:
            break
    return out


def _normalize_layers(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    layers: list[dict[str, Any]] = []
    for i, layer in enumerate(raw, start=1):
        if not isinstance(layer, dict):
            continue
        lid = str(layer.get("layer_id") or f"L{i}")
        if not _LAYER_ID_RE.match(lid):
            lid = f"L{i}"
        segments_in: list[dict[str, Any]] = []
        for seg in layer.get("segments") or []:
            if not isinstance(seg, dict):
                continue
            sname = str(seg.get("segment_name") or "").strip()
            if not sname:
                continue
            segments_in.append(
                {
                    "segment_name": sname,
                    "sub_segments": _clean_str_list(seg.get("sub_segments"), max_items=8),
                    "participant_types": _clean_str_list(seg.get("participant_types"), max_items=6),
                    "search_intents": _clean_str_list(seg.get("search_intents"), max_items=4),
                }
            )
        if not segments_in:
            continue
        layers.append(
            {
                "layer_id": lid,
                "layer_name": str(layer.get("layer_name") or "").strip(),
                "description": str(layer.get("description") or "").strip(),
                "segments": segments_in,
            }
        )
    return layers[:6]


def _normalize_discovery_prompts(raw: Any, geo: str) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    g = geo_search_label(geo)
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for i, row in enumerate(raw, start=1):
        if not isinstance(row, dict):
            continue
        text = sanitize_discovery_query(str(row.get("text") or "").strip())
        if not text or is_listicle_discovery_query(text):
            continue
        if g and g.lower() not in text.lower() and geo.lower() not in ("global", "worldwide"):
            text = sanitize_discovery_query(f"{text} {g}")
        key = " ".join(text.lower().split())
        if not key or key in seen:
            continue
        seen.add(key)
        segment = str(row.get("segment") or "").strip()
        out.append(
            {
                "id": str(row.get("id") or f"M{i}"),
                "level": "market_map",
                "text": text,
                "sub_sector": str(row.get("sub_sector") or _slug_sub_sector(segment)),
                "layer_id": str(row.get("layer_id") or ""),
                "segment": segment,
            }
        )
    return out


def _unwrap_market_map_raw(raw: Any) -> dict[str, Any]:
    """Unwrap nested LLM shapes (market_map, result, etc.)."""
    if not isinstance(raw, dict):
        return {}
    if raw.get("value_chain_layers") or raw.get("discovery_prompts"):
        return raw
    for key in ("market_map", "market_intel", "result", "data", "response", "output"):
        inner = raw.get(key)
        if isinstance(inner, dict):
            return _unwrap_market_map_raw(inner)
    return raw


def _layers_from_raw(data: dict[str, Any]) -> Any:
    return (
        data.get("value_chain_layers")
        or data.get("value_chain")
        or data.get("layers")
        or data.get("market_layers")
    )


def _prompts_from_raw(data: dict[str, Any]) -> Any:
    return (
        data.get("discovery_prompts")
        or data.get("search_prompts")
        or data.get("queries")
        or data.get("discovery_queries")
    )


def _market_map_models(settings: Any) -> list[str]:
    """Models to try for market map — Sonnet first for large JSON (Anthropic only)."""
    provider = str(getattr(settings, "llm_provider", "") or "").strip().lower()
    if provider == "opencode":
        from vendor_intel.placeholders import llm as ph

        primary = str(getattr(settings, "market_map_model", "") or "").strip()
        if primary and "claude" in primary.lower():
            primary = ""
        return ph.opencode_model_chain(primary or getattr(settings, "opencode_model", None))
    primary = str(getattr(settings, "market_map_model", "") or "").strip()
    compiler = str(getattr(settings, "compiler_model", "") or "").strip()
    fallbacks = [
        primary,
        "claude-sonnet-4-6",
        compiler,
    ]
    out: list[str] = []
    seen: set[str] = set()
    for m in fallbacks:
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out or ["claude-sonnet-4-6"]


_PLAYERS_SYSTEM = """You are a market analyst with broad, current knowledge of real companies.
List EVERY real, established company you are confident actually operates in the given market and
geography, spanning the listed segments — global leaders, regional players, AND notable smaller
firms. This is the list a thorough analyst (or someone using GPT/Google) would compile.

To be COMPREHENSIVE, deliberately recall companies from ALL of these angles (not just the
keyword-friendly names a plain web search returns):
- Members of the market's major TRADE ASSOCIATIONS / industry bodies (recyclers', operators',
  manufacturers' associations, consortiums, certification bodies' member lists).
- Companies named in "top / largest / leading" industry RANKINGS and the key-player lists of
  market-research reports.
- PUBLICLY-LISTED companies in the sector (stock-listed / annual-report filers) and their subsidiaries.
- Companies recently in the NEWS for contracts, plant expansions, acquisitions, or partnerships.
- AWARD winners / recognised innovators in the field.
Brand-recognised MAJORS (e.g. oil supermajors for a waste-oil market, the big satellite operators
for SATCOM) rarely put market keywords on their homepage, so plain search MISSES them — be sure to
include them explicitly.

Return JSON only:
{"companies":[{"canonical_name":"Real Company","primary_domain":"realdomain.com",
"company_function":"manufacturer | technology provider | distributor | supplier | ...",
"segment":"which listed segment it best fits"}]}

RULES:
- Only REAL companies you are genuinely confident exist. NEVER invent names or domains.
- Use the real official domain (bare hostname). If you are unsure of the domain, omit that company.
- Cover EACH listed segment with as many real companies as you actually know.
- Aim for breadth: 80-150 companies when the market has them. Deliberately include the LONG TAIL
  of smaller, REGIONAL, and SPECIALIST firms — name real players from EACH region (Africa, Middle
  East, Asia-Pacific, Latin America, Europe, North America), not only the global majors. The
  regional/niche specialists are exactly the ones plain web search misses, so recall them by name.
- Do NOT include market-research firms, consultancies, news sites, databases or directories —
  only actual market participants (producers, vendors, operators, suppliers)."""


def enumerate_market_players(
    market: str,
    geo: str,
    sections: list[str],
    claude: Any,
    settings: Any,
    *,
    market_definition: str = "",
) -> list[dict[str, str]]:
    """LLM enumeration of known real companies for the market (recall, not web-junk).

    Returns seed dicts [{canonical_name, primary_domain, company_function}] for every
    company the model is confident about, with a real domain. Used to widen the
    REAL-company base without loosening the relevance gate.
    """
    if not getattr(claude, "available", False):
        return []
    seg_lines = "\n".join(f"- {s}" for s in (sections or []) if str(s).strip())
    head = (
        f"Market: {market}\nGeography: {geo or 'global'}\n"
        + (f"Definition: {market_definition}\n" if market_definition else "")
        + (f"Segments to cover:\n{seg_lines}\n" if seg_lines else "")
    )
    # Multiple framings (pure LLM, no web search), deduped by domain. Each framing attacks a
    # different recall blind-spot: the comprehensive sweep, the brand-majors plain search misses,
    # the SMALLER/REGIONAL players a global list overlooks, and an exhaustive per-segment pass.
    framings = [
        head + "List every real company you are confident operates in this market.",
        head
        + (
            "Now list the 30-50 LARGEST and most DOMINANT companies in this market — the "
            "brand-recognised global and regional MAJORS and publicly-listed leaders that any "
            "analyst would name first (oil supermajors, the biggest operators/manufacturers, etc.). "
            "These rarely show market keywords on their homepage, so they are easy to miss — be "
            "exhaustive. Give each company's correct OFFICIAL domain."
        ),
        head
        + (
            "Now list the SMALLER, SPECIALISED, and REGIONAL players that a global top-list "
            "overlooks: niche manufacturers and component suppliers, regional/national operators, "
            "distributors, integrators, and service providers. Cover EVERY world region explicitly "
            "— North America, Latin America, Europe, the Middle East, Africa, and Asia-Pacific — "
            "naming country-level and emerging-market firms, not just US/EU ones. Be exhaustive; aim "
            "for 40+ companies. Give each company's correct OFFICIAL domain."
        ),
    ]
    # (A 4th per-segment framing was dropped for cost — 3 framings is the balanced setting.)
    models = _market_map_models(settings)

    def _enumerate_once(user: str) -> list[dict]:
        for model in models:
            try:
                # 8192 tokens — a 40-80 company list overflows 4096 and truncates to invalid JSON.
                raw = claude.complete_json(_PLAYERS_SYSTEM, user, model=model, max_tokens=8192)
                if isinstance(raw, dict) and isinstance(raw.get("companies"), list) and raw["companies"]:
                    return raw["companies"]
            except Exception:
                continue
        return []

    # The framings are independent: run them CONCURRENTLY (each is a long ~8k-token generation),
    # then merge in framing order so the dedupe priority is unchanged.
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=len(framings)) as ex:
        framing_results = list(ex.map(_enumerate_once, framings))

    out: list[dict[str, str]] = []
    seen: set[str] = set()
    major_doms: set[str] = set()  # domains named in the majors-focused framing (index 1)
    for idx, rows in enumerate(framing_results):
        for r in rows:
            if not isinstance(r, dict):
                continue
            name = str(r.get("canonical_name") or r.get("name") or "").strip()
            dom = str(r.get("primary_domain") or r.get("domain") or "").strip().lower()
            dom = dom.removeprefix("http://").removeprefix("https://").removeprefix("www.").split("/")[0]
            if not name or not dom or "." not in dom:
                continue
            if idx == 1:
                major_doms.add(dom)
            if dom in seen:
                continue
            seen.add(dom)
            out.append(
                {
                    "canonical_name": name,
                    "primary_domain": dom,
                    "company_function": str(r.get("company_function") or "").strip(),
                }
            )
    # Mark the brand-recognised majors so the export gate can protect them from over-strict
    # relevance drops (a dominant operator must never be silently filtered out).
    for o in out:
        o["is_major"] = o["primary_domain"] in major_doms
    return out


def _market_intel_usable(intel: dict[str, Any]) -> bool:
    """True when LLM returned enough structure to skip offline fallback."""
    layers = intel.get("value_chain_layers") or []
    prompts = intel.get("discovery_prompts") or []
    if not layers and not prompts:
        return False
    n_seg = sum(len(l.get("segments") or []) for l in layers if isinstance(l, dict))
    if len(layers) >= 2 or n_seg >= 4:
        return True
    if len(prompts) >= 8:
        return True
    if layers and prompts:
        return True
    return False


def normalize_market_intel(raw: Any, *, query: str, geo: str) -> dict[str, Any]:
    """Coerce LLM/offline market understanding payload."""
    data = _unwrap_market_map_raw(raw)
    layers = _normalize_layers(_layers_from_raw(data))
    prompts = _normalize_discovery_prompts(_prompts_from_raw(data), geo)
    if not prompts and layers:
        prompts = build_prompts_from_market_map(
            {"value_chain_layers": layers, "market": query},
            query,
            geo=geo,
            max_prompts=18,
        )
    seeds = _normalize_seed_rows(data.get("seed_companies"))
    eco = _clean_str_list(data.get("ecosystem_functions"), max_items=14)
    if not eco and layers:
        for layer in layers:
            for seg in layer.get("segments") or []:
                eco.append(str(seg.get("segment_name") or ""))
        eco = _clean_str_list(eco, max_items=14)

    return {
        "market_definition": str(data.get("market_definition") or "").strip(),
        "market_boundary": data.get("market_boundary")
        if isinstance(data.get("market_boundary"), dict)
        else {},
        "value_chain_layers": layers,
        "ecosystem_functions": eco,
        "industry_terms": _clean_str_list(data.get("industry_terms"), max_items=10),
        "include_keywords": _clean_str_list(
            data.get("include_keywords") or data.get("relevance_keywords"), max_items=14
        ),
        "exclude_keywords": _clean_str_list(
            data.get("exclude_keywords") or data.get("negative_keywords"), max_items=14
        ),
        "relevance_keywords": _clean_str_list(data.get("relevance_keywords"), max_items=16),
        "negative_keywords": _clean_str_list(data.get("negative_keywords"), max_items=12),
        "discovery_prompts": prompts,
        "seed_companies": seeds,
        "market_map_source": str(data.get("market_map_source") or "llm"),
    }


def offline_market_intel(query: str, scope: dict[str, Any]) -> dict[str, Any]:
    """Rule-based fallback when LLM market understanding is unavailable."""
    from vendor_intel.funnel.offline_compiler import infer_ecosystem_functions
    from vendor_intel.funnel.query_intent import parse_query_parts

    market, geo = parse_query_parts(query)
    market = str(scope.get("market") or market or query).strip()
    geo = str((scope.get("geographies") or [geo or "global"])[0])
    roles = infer_ecosystem_functions(market, query)
    topic = refine_search_topic(market, geo)
    g = geo_search_label(geo)

    segments = []
    for role in roles:
        segments.append(
            {
                "segment_name": role,
                "sub_segments": [],
                "participant_types": [role],
                "search_intents": [
                    _q(topic, role, g, "official", "site"),
                    _q(topic, role, g, "corporate", "headquarters"),
                ],
            }
        )

    from vendor_intel.pipeline.market_relevance import derive_include_keywords

    include_kw = derive_include_keywords(query, roles)
    intel = normalize_market_intel(
        {
            "market_definition": f"Market map for {topic} in {geo}: participants across {len(roles)} value-chain roles.",
            "include_keywords": include_kw,
            "exclude_keywords": [
                "news",
                "blog",
                "magazine",
                "market report",
                "directory",
                "job board",
                "consultant",
                "pharmaceutical",
                "pharma",
            ],
            "market_boundary": {"in_scope": roles[:6], "out_of_scope": ["news", "directories", "market reports"]},
            "value_chain_layers": [
                {
                    "layer_id": "L1",
                    "layer_name": "Market participants",
                    "description": f"Key roles in {topic}",
                    "segments": segments,
                }
            ],
            "ecosystem_functions": roles,
            "market_map_source": "offline",
        },
        query=query,
        geo=geo,
    )
    return intel


def understand_market(
    query: str,
    scope: dict[str, Any],
    claude: Any,
    settings: Any,
) -> dict[str, Any]:
    """
    Step 1 — LLM analyzes the user query: market definition + value-chain mapping.
    Section 1b — also returns seed_companies from LLM knowledge.
    """
    geo = str((scope.get("geographies") or ["global"])[0])
    if is_mock_run(settings):
        intel = offline_market_intel(query, scope)
        print(
            f"  [market_map] Offline market map — {len(intel.get('value_chain_layers') or [])} layers, "
            f"{len(intel.get('discovery_prompts') or [])} queries",
            flush=True,
        )
        return intel

    if not getattr(claude, "available", False):
        return offline_market_intel(query, scope)

    system = _load_market_understanding_prompt()
    user_base = (
        f"User query:\n{query}\n\n"
        f"Parsed market: {scope.get('market') or 'unknown'}\n"
        f"Geography: {geo}\n"
    )
    retry_note = (
        "\n\nReturn the COMPLETE JSON schema from the system prompt: "
        "3-5 value_chain_layers with 2-4 segments each, "
        "12-18 discovery_prompts, 8-12 seed_companies, include_keywords, exclude_keywords."
    )
    models = _market_map_models(settings)
    last_keys: list[str] = []
    intel: dict[str, Any] = {}

    for attempt, model in enumerate(models, start=1):
        user = user_base if attempt == 1 else user_base + retry_note
        try:
            print(f"  [market_map] LLM attempt {attempt}/{len(models)} model={model}", flush=True)
            raw = claude.complete_json(system, user, model=model, max_tokens=8192)
        except Exception as exc:
            print(f"  [market_map] LLM failed ({exc})", flush=True)
            if attempt == len(models):
                print("  [market_map] All models failed — offline fallback", flush=True)
                return offline_market_intel(query, scope)
            continue

        data = _unwrap_market_map_raw(raw)
        last_keys = list(data.keys())[:16] if isinstance(data, dict) else []
        intel = normalize_market_intel(data, query=query, geo=geo)
        if _market_intel_usable(intel):
            intel["market_map_source"] = "llm"
            break
        n_l = len(intel.get("value_chain_layers") or [])
        n_p = len(intel.get("discovery_prompts") or [])
        print(
            f"  [market_map] Thin on {model} — layers={n_l} prompts={n_p} keys={last_keys}",
            flush=True,
        )
        if attempt < len(models):
            print(f"  [market_map] Retrying with stronger model…", flush=True)

    if not _market_intel_usable(intel):
        print(
            f"  [market_map] Thin LLM response after {len(models)} attempt(s) — offline fallback",
            flush=True,
        )
        return offline_market_intel(query, scope)

    n_layers = len(intel.get("value_chain_layers") or [])
    n_seg = sum(len(l.get("segments") or []) for l in intel.get("value_chain_layers") or [])
    n_prompts = len(intel.get("discovery_prompts") or [])
    n_seeds = len(intel.get("seed_companies") or [])
    print(
        f"  [market_map] Understood market — {n_layers} layers, {n_seg} segments, "
        f"{n_prompts} queries, {n_seeds} LLM seeds",
        flush=True,
    )
    if intel.get("market_definition"):
        print(f"  [market_map] {intel['market_definition'][:140]}", flush=True)
    return intel


def merge_market_understanding(scope: dict[str, Any], intel: dict[str, Any]) -> dict[str, Any]:
    """Merge market intel into run scope (does not overwrite good LLM compiler fields blindly)."""
    out = dict(scope or {})
    if not intel:
        return out

    if intel.get("market_definition"):
        out["market_definition"] = intel["market_definition"]
        out["interpretation_summary"] = intel["market_definition"][:220]

    if intel.get("value_chain_layers"):
        out["value_chain_layers"] = intel["value_chain_layers"]

    boundary = intel.get("market_boundary")
    if isinstance(boundary, dict) and boundary:
        out["market_boundary"] = boundary

    for field, max_items in (
        ("ecosystem_functions", 14),
        ("industry_terms", 10),
        ("include_keywords", 14),
        ("exclude_keywords", 14),
        ("relevance_keywords", 16),
        ("negative_keywords", 12),
    ):
        incoming = intel.get(field)
        if not incoming:
            continue
        existing = _clean_str_list(out.get(field), max_items=max_items)
        merged = _clean_str_list(incoming + existing, max_items=max_items)
        if merged:
            out[field] = merged

    if intel.get("discovery_prompts"):
        out["market_map_prompts"] = intel["discovery_prompts"]

    out["market_map_source"] = intel.get("market_map_source") or "llm"
    return out


def merge_market_map_seeds(scope: dict[str, Any], intel: dict[str, Any]) -> int:
    """Append LLM market-map seeds without duplicating existing seeds."""
    rows = _normalize_seed_rows(intel.get("seed_companies"), max_items=14)
    if not rows:
        return 0

    existing = list(scope.get("seed_companies") or [])
    seen: set[str] = set()
    for row in existing:
        if isinstance(row, dict):
            dom = _normalize_domain(str(row.get("primary_domain") or ""))
            name = str(row.get("canonical_name") or "").strip().lower()
            seen.add(dom or name)
        elif isinstance(row, str) and row.strip():
            seen.add(row.strip().lower())

    added = 0
    for row in rows:
        dom = row.get("primary_domain") or ""
        name = row.get("canonical_name") or ""
        key = dom or name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        existing.append(row)
        added += 1

    scope["seed_companies"] = existing
    if added:
        print(f"  [market_map] Added {added} LLM seed(s) from market understanding", flush=True)
    return added


def format_market_intel_for_compiler(intel: dict[str, Any]) -> str:
    """Compact context block injected into the main compiler LLM call."""
    slim = {
        "market_definition": intel.get("market_definition"),
        "value_chain_layers": intel.get("value_chain_layers"),
        "ecosystem_functions": intel.get("ecosystem_functions"),
        "industry_terms": intel.get("industry_terms"),
        "relevance_keywords": intel.get("relevance_keywords"),
        "negative_keywords": intel.get("negative_keywords"),
        "seed_companies": intel.get("seed_companies"),
    }
    try:
        return json.dumps(slim, ensure_ascii=False)[:6000]
    except Exception:
        return str(slim)[:6000]


def build_prompts_from_market_map(
    scope: dict[str, Any],
    query: str,
    *,
    geo: str | None = None,
    max_prompts: int = 20,
) -> list[dict[str, str]]:
    """
    Section 1a — discovery queries derived from value-chain mapping.
    One or more queries per segment; sub_segments get variant queries.
    """
    geo = geo or str((scope.get("geographies") or ["global"])[0])
    cached = scope.get("market_map_prompts")
    if isinstance(cached, list) and cached:
        prompts = _normalize_discovery_prompts(cached, geo)
        if prompts:
            for i, row in enumerate(prompts[:max_prompts]):
                row["id"] = f"M{i + 1}"
            return prompts[:max_prompts]

    layers = scope.get("value_chain_layers") or []
    if not isinstance(layers, list) or not layers:
        return []

    g = geo_search_label(geo)
    topic = refine_search_topic(str(scope.get("market") or query), geo)
    seen: set[str] = set()
    out: list[dict[str, str]] = []

    def push(
        text: str,
        *,
        layer_id: str,
        segment: str,
        sub_sector: str,
    ) -> None:
        if len(out) >= max_prompts:
            return
        text = sanitize_discovery_query(text)
        if not text or is_listicle_discovery_query(text):
            return
        if g and g.lower() not in text.lower() and geo.lower() not in ("global", "worldwide"):
            text = sanitize_discovery_query(f"{text} {g}")
        key = " ".join(text.lower().split())
        if not key or key in seen:
            return
        seen.add(key)
        out.append(
            {
                "id": f"M{len(out) + 1}",
                "level": "market_map",
                "text": text,
                "sub_sector": sub_sector,
                "layer_id": layer_id,
                "segment": segment,
            }
        )

    for layer in layers:
        if not isinstance(layer, dict):
            continue
        lid = str(layer.get("layer_id") or "")
        for seg in layer.get("segments") or []:
            if not isinstance(seg, dict) or len(out) >= max_prompts:
                break
            sname = str(seg.get("segment_name") or "").strip()
            if not sname:
                continue
            sub_sector = _slug_sub_sector(sname)
            intents = list(seg.get("search_intents") or [])
            for intent in intents[:2]:
                push(str(intent), layer_id=lid, segment=sname, sub_sector=sub_sector)
            for sub in (seg.get("sub_segments") or [])[:2]:
                sub_s = str(sub).strip()
                if not sub_s:
                    continue
                push(
                    _q(sub_s, topic, g, "official", "site"),
                    layer_id=lid,
                    segment=sname,
                    sub_sector=_slug_sub_sector(sub_s),
                )
            if len([r for r in out if r.get("segment") == sname]) == 0:
                push(
                    _q(topic, sname, g, "official", "website"),
                    layer_id=lid,
                    segment=sname,
                    sub_sector=sub_sector,
                )

    return out[:max_prompts]


def build_sector_tree_from_market_map(
    scope: dict[str, Any],
    geo: str,
    *,
    max_prompts: int = 16,
) -> list[dict[str, str]]:
    """Dynamic sector-tree prompts from LLM value-chain layers (Phase 2 supplement)."""
    layers = scope.get("value_chain_layers") or []
    if not isinstance(layers, list) or not layers:
        return []

    g = geo_search_label(geo)
    topic = refine_search_topic(str(scope.get("market") or ""), geo)
    seen: set[str] = set()
    out: list[dict[str, str]] = []

    for layer in layers:
        if not isinstance(layer, dict) or len(out) >= max_prompts:
            break
        lid = str(layer.get("layer_id") or "L")
        for seg in layer.get("segments") or []:
            if len(out) >= max_prompts:
                break
            if not isinstance(seg, dict):
                continue
            sname = str(seg.get("segment_name") or "").strip()
            if not sname:
                continue
            text = sanitize_discovery_query(_q(topic, sname, g, "corporate", "site"))
            if not text or is_listicle_discovery_query(text):
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "id": f"ST_{lid}_{len(out) + 1}",
                    "level": "sector_tree",
                    "text": text,
                    "sub_sector": _slug_sub_sector(sname),
                    "layer_id": lid,
                    "segment": sname,
                }
            )
    return out[:max_prompts]


def market_map_summary(scope: dict[str, Any] | None) -> str:
    if not scope:
        return ""
    layers = scope.get("value_chain_layers") or []
    if not layers:
        return ""
    parts = []
    for layer in layers[:4]:
        if not isinstance(layer, dict):
            continue
        name = layer.get("layer_name") or layer.get("layer_id") or "?"
        segs = [
            str(s.get("segment_name") or "")
            for s in (layer.get("segments") or [])
            if isinstance(s, dict) and s.get("segment_name")
        ]
        if segs:
            parts.append(f"{name}: {', '.join(segs[:4])}")
    return " | ".join(parts)

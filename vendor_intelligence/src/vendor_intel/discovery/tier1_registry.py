"""Tier-1 market anchors — curated seeds + strategic discovery queries."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from vendor_intel.config import _project_root
from vendor_intel.discovery.discovery_query_quality import (
    is_listicle_discovery_query,
    sanitize_discovery_query,
)
from vendor_intel.funnel.prompt_builder import _q, geo_search_label, refine_search_topic


def _blob(*parts: str) -> str:
    return " ".join(p for p in parts if p).lower()


@lru_cache(maxsize=1)
def _load_tier1_config() -> dict[str, Any]:
    path = _project_root() / "config" / "tier1_markets.yaml"
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _keywords_match(blob: str, keywords: list[str]) -> bool:
    for kw in keywords:
        s = str(kw).strip().lower()
        if s and s in blob:
            return True
    return False


def resolve_tier1_market(scope: dict[str, Any], query: str) -> str | None:
    """Return market key from tier1_markets.yaml or None."""
    market = str(scope.get("market") or "")
    terms = scope.get("industry_terms") or []
    term_blob = " ".join(str(t) for t in terms) if isinstance(terms, list) else ""
    blob = _blob(market, query, term_blob, str(scope.get("industry") or ""))
    for key, cfg in (_load_tier1_config().get("markets") or {}).items():
        if not isinstance(cfg, dict):
            continue
        kws = cfg.get("match_keywords") or []
        if _keywords_match(blob, kws):
            return str(key)
    return None


def resolve_tier1_geo(scope: dict[str, Any], geo: str) -> str | None:
    """Return geography key under market config (e.g. europe)."""
    market_key = resolve_tier1_market(scope, str(scope.get("market") or ""))
    if not market_key:
        return None
    cfg = (_load_tier1_config().get("markets") or {}).get(market_key) or {}
    geos = cfg.get("geographies") or {}
    blob = _blob(geo, " ".join(str(g) for g in (scope.get("geographies") or [])))
    for gkey, gcfg in geos.items():
        if not isinstance(gcfg, dict):
            continue
        kws = gcfg.get("match_keywords") or []
        if _keywords_match(blob, kws):
            return str(gkey)
    return None


def tier1_seed_companies(scope: dict[str, Any], query: str) -> list[dict[str, str]]:
    """Curated Tier-1 companies for this market + geography."""
    market_key = resolve_tier1_market(scope, query)
    if not market_key:
        return []
    cfg = (_load_tier1_config().get("markets") or {}).get(market_key) or {}
    geos = cfg.get("geographies") or {}
    geo = str((scope.get("geographies") or ["global"])[0])
    gkey = resolve_tier1_geo(scope, geo)
    if not gkey:
        for gk, gcfg in geos.items():
            if isinstance(gcfg, dict) and gcfg.get("tier1"):
                gkey = gk
                break
    if not gkey:
        return []
    gcfg = geos.get(gkey) or {}
    rows = gcfg.get("tier1") or []
    out: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("canonical_name") or "").strip()
        dom = str(row.get("primary_domain") or "").strip().lower().removeprefix("www.")
        if not name or not dom:
            continue
        out.append(
            {
                "canonical_name": name,
                "primary_domain": dom,
                "company_function": str(row.get("company_function") or "manufacturer"),
                "brand": str(row.get("brand") or name),
                "tier1": True,
            }
        )
    return out


def inject_tier1_into_scope(scope: dict[str, Any], query: str) -> int:
    """Merge Tier-1 seeds into scope.seed_companies; return count added."""
    tier1 = tier1_seed_companies(scope, query)
    if not tier1:
        return 0

    existing = list(scope.get("seed_companies") or scope.get("seeds") or [])
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    added = 0

    def _append(row: dict[str, Any]) -> None:
        nonlocal added
        name = str(row.get("canonical_name") or row.get("name") or "").strip()
        dom = str(row.get("primary_domain") or row.get("domain") or "").strip().lower()
        key = dom.removeprefix("www.") if dom else name.lower()
        if not name or key in seen:
            return
        seen.add(key)
        merged.append(row)
        if row.get("tier1"):
            added += 1

    for row in tier1:
        if isinstance(row, dict):
            _append(row)
    for row in existing:
        if isinstance(row, str):
            name = row.strip()
            if name and name.lower() not in seen:
                seen.add(name.lower())
                merged.append(
                    {"canonical_name": name, "primary_domain": "", "company_function": "manufacturer"}
                )
        elif isinstance(row, dict):
            _append({**row, "tier1": False})
    scope["seed_companies"] = merged
    scope["tier1_market"] = resolve_tier1_market(scope, query)
    if added:
        print(
            f"  [tier1] Injected {added} Tier-1 anchor(s) for "
            f"{scope.get('tier1_market')} ({(scope.get('geographies') or ['global'])[0]})",
            flush=True,
        )
    return added


def _push_prompt(
    out: list[dict[str, str]],
    seen: set[str],
    pid: str,
    text: str,
    *,
    sub_sector: str,
) -> None:
    text = sanitize_discovery_query(text)
    if not text or is_listicle_discovery_query(text):
        return
    key = " ".join(text.lower().split())
    if key in seen:
        return
    seen.add(key)
    out.append({"id": pid, "level": "tier1", "text": text, "sub_sector": sub_sector})


def build_facade_strategic_prompts(
    market: str,
    geo: str,
    *,
    include_contractors: bool = True,
) -> list[dict[str, str]]:
    """Practitioner queries for ACP / rainscreen / façade markets."""
    g = geo_search_label(geo)
    seen: set[str] = set()
    out: list[dict[str, str]] = []

    specs: list[tuple[str, str, str]] = [
        ("T1M1", _q("ACP", "manufacturers", g), "manufacturers"),
        ("T1M2", _q("aluminium composite panel", "manufacturer", g, "official"), "manufacturers"),
        ("T1M3", _q("aluminium rainscreen", "facade", "systems", "companies", g), "manufacturers"),
        ("T1M4", _q("architectural aluminium panel", "manufacturers", g), "manufacturers"),
        ("T1M5", _q("ventilated facade", "system", "suppliers", g), "distributors"),
        ("T1M6", _q("aluminium cladding", "manufacturer", g, "corporate", "site"), "manufacturers"),
        ("T1M7", _q("curtain wall", "manufacturer", g, "official", "website"), "manufacturers"),
        ("T1M8", _q("building envelope", "aluminium", "systems", g), "integrators"),
    ]
    # System houses
    specs.extend(
        [
            ("T1S1", _q("aluminium facade", "systems", "companies", g), "integrators"),
            ("T1S2", _q("facade", "system", "house", g, "official"), "integrators"),
            ("T1S3", _q("rainscreen", "cladding", "supplier", g, "official"), "distributors"),
        ]
    )
    if include_contractors:
        specs.append(
            ("T1C1", _q("facade", "contractors", g, "aluminium"), "contractors"),
        )

    for pid, text, sector in specs:
        _push_prompt(out, seen, pid, text, sub_sector=sector)
    return out


def build_tier1_competitor_prompts(
    scope: dict[str, Any],
    query: str,
    geo: str,
    *,
    max_prompts: int = 12,
) -> list[dict[str, str]]:
    """Competitor queries anchored on Tier-1 seed names only."""
    seeds = tier1_seed_companies(scope, query)
    if not seeds:
        return []
    g = geo_search_label(geo)
    market = str(scope.get("market") or query)
    topic = refine_search_topic(market, geo)
    seen: set[str] = set()
    out: list[dict[str, str]] = []

    for row in seeds:
        if len(out) >= max_prompts:
            break
        name = str(row.get("canonical_name") or "").strip()
        if not name:
            continue
        short = name.split()[0] if name else name
        candidates = [
            (_q(name, "competitors", g), f"TC_{short}_1"),
            (_q("alternatives to", name, g, "facade"), f"TC_{short}_2"),
            (_q(name, "facade", "competitors", g), f"TC_{short}_3"),
        ]
        for text, pid in candidates:
            if len(out) >= max_prompts:
                break
            _push_prompt(out, seen, pid, text, sub_sector="competitors")

    if len(out) < max_prompts:
        _push_prompt(
            out,
            seen,
            "TC_TOP",
            _q(topic, "competitive landscape", g, "companies"),
            sub_sector="competitors",
        )
        _push_prompt(
            out,
            seen,
            "TC_LAND",
            _q("leading", topic, "brands", g),
            sub_sector="competitors",
        )
    return out[:max_prompts]


def build_tier1_discovery_prompts(
    scope: dict[str, Any],
    query: str,
    geo: str,
) -> list[dict[str, str]]:
    """All Tier-1 strategic prompts (facade pack + competitor expansion)."""
    if not resolve_tier1_market(scope, query):
        return []
    market = str(scope.get("market") or query)
    strategic = build_facade_strategic_prompts(market, geo, include_contractors=True)
    competitors = build_tier1_competitor_prompts(scope, query, geo, max_prompts=12)
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for p in strategic + competitors:
        key = " ".join(str(p.get("text") or "").lower().split())
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out

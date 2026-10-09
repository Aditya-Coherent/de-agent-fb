"""Coerce imperfect LLM compiler JSON into a runnable plan — never hard-fail on missing keys."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from vendor_intel.funnel.query_intent import enrich_scope_from_query, parse_query_parts
from vendor_intel.funnel.scope_schema import normalize_run_scope

logger = logging.getLogger(__name__)

# Known majors when LLM seeds are empty (bio-ethylene / petrochemical / generic B2B)
_DEFAULT_ETHYLENE_SEEDS: list[dict[str, str]] = [
    {"canonical_name": "Braskem", "primary_domain": "braskem.com", "company_function": "manufacturer"},
    {"canonical_name": "LyondellBasell", "primary_domain": "lyondellbasell.com", "company_function": "manufacturer"},
    {"canonical_name": "Dow", "primary_domain": "dow.com", "company_function": "manufacturer"},
    {"canonical_name": "SABIC", "primary_domain": "sabic.com", "company_function": "manufacturer"},
    {"canonical_name": "TotalEnergies", "primary_domain": "totalenergies.com", "company_function": "manufacturer"},
    {"canonical_name": "Neste", "primary_domain": "neste.com", "company_function": "supplier"},
    {"canonical_name": "BASF", "primary_domain": "basf.com", "company_function": "manufacturer"},
    {"canonical_name": "Indorama Ventures", "primary_domain": "indorama.com", "company_function": "manufacturer"},
]


def repair_json_text(text: str) -> dict[str, Any]:
    """Parse LLM text; return minimal dict on failure (never raise)."""
    from vendor_intel.placeholders.llm import _extract_json, _repair_json_text, salvage_compiler_payload

    raw = (text or "").strip()
    if not raw:
        return {}

    for attempt in (raw, _repair_json_text(raw)):
        if not attempt:
            continue
        try:
            parsed = json.loads(attempt)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    try:
        parsed = _extract_json(raw)
        if isinstance(parsed, dict):
            return parsed
    except (ValueError, json.JSONDecodeError):
        pass

    salvaged = salvage_compiler_payload(raw)
    return salvaged if isinstance(salvaged, dict) else {}


def _coerce_seed_list(raw: Any) -> list[dict[str, str]]:
    """Accept seed_companies, seeds, or list of strings."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, dict):
            name = str(item.get("canonical_name") or item.get("name") or "").strip()
            dom = str(item.get("primary_domain") or item.get("domain") or "").strip()
            if name:
                out.append(
                    {
                        "canonical_name": name,
                        "primary_domain": dom,
                        "company_function": str(item.get("company_function") or "manufacturer"),
                    }
                )
        elif isinstance(item, str) and item.strip():
            out.append(
                {
                    "canonical_name": item.strip(),
                    "primary_domain": "",
                    "company_function": "manufacturer",
                }
            )
    return out


def _sanitize_chemical_scope_negatives(scope: dict[str, Any], query: str) -> dict[str, Any]:
    """
    LLM sometimes adds 'petrochemical', 'polymer', 'fossil' as negatives for ethylene markets,
    which drops real participants. Keep junk-page negatives only.
    """
    blob = f"{scope.get('market') or ''} {query}".lower()
    if not re.search(
        r"\b(?:ethylene|bio[\s-]?based|biobased|renewable\s+ethylene|green\s+ethylene|"
        r"bioethylene|petrochemical|polymer)\b",
        blob,
    ):
        return scope
    drop = {
        "petrochemical",
        "polymer",
        "plastic",
        "ethylene",
        "chemical",
        "chemicals",
        "fossil",
        "crude oil",
        "natural gas",
        "conventional",
        "non-renewable",
        "coal",
        "fracking",
    }
    raw = scope.get("negative_keywords") or []
    cleaned = [k for k in raw if str(k).strip().lower() not in drop]
    if len(cleaned) < len(raw):
        print(
            f"  [llm] Removed {len(raw) - len(cleaned)} counterproductive negative_keywords "
            f"for chemical/ethylene market",
            flush=True,
        )
    scope["negative_keywords"] = cleaned or [
        "wikipedia",
        "market report",
        "market research",
        "directory",
        "news",
        "blog",
    ]
    return scope


def ensure_seed_companies(scope: dict[str, Any], query: str) -> int:
    """Guarantee at least 4 seed companies so Phase 2 is not search-only."""
    from vendor_intel.discovery.tier1_registry import inject_tier1_into_scope

    inject_tier1_into_scope(scope, query)
    existing = _coerce_seed_list(scope.get("seed_companies") or scope.get("seeds"))
    if len(existing) >= 4:
        scope["seed_companies"] = existing
        return len(existing)

    market = str(scope.get("market") or query)
    from vendor_intel.funnel.offline_compiler import offline_seed_companies

    extra = offline_seed_companies(market, query) or []
    low = f"{market} {query}".lower()
    if not extra and any(
        k in low for k in ("ethylene", "bio-based", "biobased", "renewable", "green ethylene")
    ):
        extra = list(_DEFAULT_ETHYLENE_SEEDS)

    seen: set[str] = set()
    merged: list[dict[str, str]] = []
    for row in existing + extra:
        name = str(row.get("canonical_name") or "").strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        merged.append(row)
        if len(merged) >= 8:
            break

    scope["seed_companies"] = merged
    if merged and len(existing) < 4:
        print(
            f"  [llm] Filled {len(merged)} seed companies (LLM had {len(existing)})",
            flush=True,
        )
    return len(merged)


def coerce_compiler_payload(
    data: Any,
    query: str,
    *,
    raw_text: str | None = None,
) -> dict[str, Any]:
    """
    Normalize any LLM/offline compiler dict: fill scope defaults, repair JSON, ensure seeds.

    Returns dict with keys: scope, funnel_prompts, discovery_prompts, status, _coerced.
    """
    parsed_market, parsed_geo = parse_query_parts(query)

    if isinstance(data, str):
        payload = repair_json_text(data)
    elif isinstance(data, dict):
        payload = dict(data)
        if payload.get("error") and raw_text:
            repaired = repair_json_text(raw_text)
            payload = {**repaired, **{k: v for k, v in payload.items() if k != "error"}}
    else:
        payload = repair_json_text(raw_text or "")

    if raw_text and not payload.get("scope"):
        extra = repair_json_text(raw_text)
        if extra.get("scope"):
            payload.setdefault("scope", extra["scope"])
        for key in ("funnel_prompts", "discovery_prompts", "prompts"):
            if not payload.get(key) and extra.get(key):
                payload[key] = extra[key]

    scope: dict[str, Any] = {}
    if isinstance(payload.get("scope"), dict):
        scope = dict(payload["scope"])

    # market: scope.market | scope.product | top-level | query
    market = (
        str(scope.get("market") or scope.get("product") or payload.get("market") or "").strip()
    )
    if (
        not market
        or market.lower() in ("string", "product category without geography")
        or market.strip().lower() == query.strip().lower()
    ):
        market = parsed_market or query.strip()
    scope["market"] = market

    geo_raw = scope.get("geographies") or scope.get("region") or payload.get("region")
    if isinstance(geo_raw, list) and geo_raw:
        geographies = [str(g).strip() for g in geo_raw if str(g).strip()]
    elif isinstance(geo_raw, str) and geo_raw.strip():
        geographies = [geo_raw.strip()]
    else:
        geographies = [parsed_geo or "global"]
    scope["geographies"] = geographies
    scope["geography"] = geographies[0]

    if not scope.get("ecosystem_functions") and payload.get("entity_types"):
        scope["ecosystem_functions"] = list(payload.get("entity_types") or [])

    seeds = _coerce_seed_list(
        scope.get("seed_companies") or scope.get("seeds") or payload.get("seeds")
    )
    if seeds:
        scope["seed_companies"] = seeds

    scope = enrich_scope_from_query(scope, query)
    scope = normalize_run_scope(scope, query)
    scope["market"] = re.sub(r"\s+market\s*$", "", str(scope.get("market") or ""), flags=re.I).strip()
    ensure_seed_companies(scope, query)

    from vendor_intel.funnel.offline_compiler import (
        _relevance_keywords,
        infer_ecosystem_functions,
        infer_industry_context,
    )

    if not scope.get("ecosystem_functions"):
        scope["ecosystem_functions"] = infer_ecosystem_functions(
            str(scope.get("market") or market), query
        )
    if not scope.get("industry_terms"):
        scope["industry_terms"] = infer_industry_context(
            str(scope.get("market") or market), query
        ).get("industry_terms") or []
    if not scope.get("relevance_keywords"):
        scope["relevance_keywords"] = _relevance_keywords(
            str(scope.get("market") or market),
            scope.get("industry_terms") if isinstance(scope.get("industry_terms"), list) else [],
            str((scope.get("geographies") or ["global"])[0]),
        )
    if not scope.get("negative_keywords"):
        scope["negative_keywords"] = [
            "wikipedia",
            "market report",
            "market research",
            "directory",
            "news",
            "blog",
        ]
    scope = _sanitize_chemical_scope_negatives(scope, query)

    funnel = payload.get("funnel_prompts") if isinstance(payload.get("funnel_prompts"), list) else []
    discovery = payload.get("discovery_prompts") or payload.get("prompts") or []
    if not isinstance(discovery, list):
        discovery = []

    salvaged = bool(payload.get("_salvaged"))
    had_error = bool(payload.get("error") or payload.get("status") == "llm_failed")
    if had_error and scope.get("market"):
        status = "llm_repaired"
    elif salvaged or had_error:
        status = "llm_partial"
    else:
        status = "llm"

    return {
        "scope": scope,
        "funnel_prompts": funnel,
        "discovery_prompts": discovery,
        "gates": payload.get("gates"),
        "listing_rules": payload.get("listing_rules"),
        "ranking": payload.get("ranking"),
        "evidence_policy": payload.get("evidence_policy"),
        "freshness_policy": payload.get("freshness_policy"),
        "status": status,
        "_coerced": True,
        "_raw_preview": (raw_text or "")[:400] if had_error else "",
    }


def compiler_payload_usable(data: dict[str, Any]) -> bool:
    """True when we have enough to run Phase 2 without full offline rebuild."""
    scope = data.get("scope") if isinstance(data.get("scope"), dict) else {}
    market = str(scope.get("market") or "").strip()
    seeds = scope.get("seed_companies") or []
    prompts = data.get("discovery_prompts") or data.get("prompts") or []
    return bool(market) and (len(seeds) >= 1 or len(prompts) >= 3)

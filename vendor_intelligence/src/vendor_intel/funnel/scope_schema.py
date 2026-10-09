"""Normalized run scope from LLM (domain, geo, company type, seeds, keywords)."""
from __future__ import annotations

import re
from typing import Any

from vendor_intel.utils.domains import domain_from_url, normalize_name

_DOMAIN_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$",
    re.I,
)


def _clean_str_list(raw: Any, *, max_items: int = 20) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        s = str(item or "").strip()
        if len(s) < 2:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= max_items:
            break
    return out


def _normalize_domain(raw: str) -> str:
    d = (raw or "").strip().lower()
    if d.startswith("http"):
        d = domain_from_url(d) or d
    d = d.removeprefix("www.")
    if _DOMAIN_RE.match(d):
        return d
    return ""


def normalize_seed_companies(
    raw: Any, *, max_items: int = 450
) -> list[tuple[str, str, str]]:
    """Returns (canonical_name, primary_domain, company_function)."""
    if not isinstance(raw, list):
        return []
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for row in raw:
        fn = ""
        if isinstance(row, str) and row.strip():
            name = normalize_name(row.strip())
            dom = ""
        elif isinstance(row, dict):
            name = normalize_name(
                str(row.get("canonical_name") or row.get("name") or "").strip()
            )
            dom = _normalize_domain(
                str(row.get("primary_domain") or row.get("domain") or "")
            )
            fn = str(row.get("company_function") or row.get("function") or "").strip().lower()
        else:
            continue
        if len(name) < 2:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((name, dom, fn))
        if len(out) >= max_items:
            break
    return out


def normalize_run_scope(scope: dict[str, Any], query: str) -> dict[str, Any]:
    """
    Ensure scope carries LLM-driven intent fields used by discovery / validation / export.
    Does not infer industry from keyword lists — only normalizes what the LLM (or caller) set.
    """
    out = dict(scope or {})

    market = str(out.get("market") or out.get("product_category") or "").strip()
    if market:
        out["market"] = market
        out["product_category"] = market

    geos = out.get("geographies")
    if not isinstance(geos, list) or not geos:
        g = str(out.get("geography") or "").strip()
        geos = [g] if g else ["global"]
    out["geographies"] = [str(g).strip() for g in geos if str(g).strip()]

    geo_primary = out["geographies"][0] if out["geographies"] else "global"
    out["geography"] = geo_primary

    company_type = str(
        out.get("company_type") or out.get("company_types") or "company"
    ).strip()
    if isinstance(out.get("company_types"), list) and out["company_types"]:
        company_type = str(out["company_types"][0]).strip()
    out["company_type"] = company_type

    intent = out.get("run_intent")
    if isinstance(intent, dict):
        if not out.get("company_type") and intent.get("company_type"):
            out["company_type"] = str(intent["company_type"]).strip()
        if not out.get("relevance_keywords") and intent.get("relevance_keywords"):
            out["relevance_keywords"] = intent["relevance_keywords"]
        if not out.get("negative_keywords") and intent.get("negative_keywords"):
            out["negative_keywords"] = intent["negative_keywords"]
        if not out.get("seed_companies") and intent.get("seed_companies"):
            out["seed_companies"] = intent["seed_companies"]

    out["relevance_keywords"] = _clean_str_list(
        out.get("relevance_keywords"), max_items=18
    )
    out["negative_keywords"] = _clean_str_list(
        out.get("negative_keywords"), max_items=15
    )

    out["ecosystem_functions"] = _clean_str_list(
        out.get("ecosystem_functions"), max_items=14
    )
    out["industry_terms"] = _clean_str_list(out.get("industry_terms"), max_items=8)
    out["industry_vertical"] = str(out.get("industry_vertical") or "").strip()

    if out.get("market_definition"):
        out["market_definition"] = str(out["market_definition"]).strip()
    layers = out.get("value_chain_layers")
    if isinstance(layers, list) and layers:
        out["value_chain_layers"] = layers
    if out.get("market_boundary") and isinstance(out.get("market_boundary"), dict):
        out["market_boundary"] = out["market_boundary"]
    if isinstance(out.get("market_map_prompts"), list):
        out["market_map_prompts"] = out["market_map_prompts"]
    if out.get("market_map_source"):
        out["market_map_source"] = str(out["market_map_source"]).strip()
    out["include_keywords"] = _clean_str_list(out.get("include_keywords"), max_items=14)
    out["exclude_keywords"] = _clean_str_list(out.get("exclude_keywords"), max_items=14)

    seeds_raw = out.get("seed_companies") or out.get("known_companies") or []
    seeds = normalize_seed_companies(seeds_raw)
    out["seed_companies"] = [
        {
            "canonical_name": n,
            "primary_domain": d,
            **({"company_function": fn} if fn else {}),
        }
        for n, d, fn in seeds
    ]

    if not out.get("interpretation_summary"):
        out["interpretation_summary"] = (
            f"{company_type} companies in {market} ({geo_primary})"
            if geo_primary != "global"
            else f"{company_type} companies in {market}"
        )

    out["scope_version"] = 2
    return out


def scope_summary(scope: dict[str, Any] | None) -> str:
    if not scope:
        return "unknown"
    market = scope.get("market") or "?"
    geo = (scope.get("geographies") or ["?"])[0]
    ctype = scope.get("company_type") or "?"
    n_seeds = len(scope.get("seed_companies") or [])
    n_fn = len(scope.get("ecosystem_functions") or [])
    n_layers = len(scope.get("value_chain_layers") or [])
    return (
        f"{market} | {geo} | type={ctype} | seeds={n_seeds} | roles={n_fn}"
        + (f" | layers={n_layers}" if n_layers else "")
    )

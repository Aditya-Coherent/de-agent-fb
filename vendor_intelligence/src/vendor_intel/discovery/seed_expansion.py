"""Seed-anchored discovery — find more real vendors via known market leaders."""
from __future__ import annotations

import os
import re
from typing import Any

from vendor_intel.pipeline.geo_limits import is_global_geography

from vendor_intel.discovery.discovery_query_quality import (
    is_listicle_discovery_query,
    sanitize_discovery_query,
)
from vendor_intel.funnel.prompt_builder import _q, geo_search_label, refine_search_topic


def _push(
    out: list[dict[str, str]],
    seen: set[str],
    pid: str,
    text: str,
    *,
    max_prompts: int,
    sub_sector: str = "seed_expansion",
) -> None:
    if len(out) >= max_prompts:
        return
    text = sanitize_discovery_query(text)
    if not text or is_listicle_discovery_query(text):
        return
    key = " ".join(text.lower().split())
    if key in seen:
        return
    seen.add(key)
    out.append({"id": pid, "level": "seed_expansion", "text": text, "sub_sector": sub_sector})


def _seed_rows(scope: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for s in scope.get("seed_companies") or []:
        if not isinstance(s, dict):
            continue
        name = str(s.get("canonical_name") or "").strip()
        if not name or len(name) < 3:
            continue
        rows.append(
            {
                "name": name,
                "function": str(s.get("company_function") or "").strip(),
                "domain": str(s.get("primary_domain") or "").strip(),
            }
        )
    return rows


def _max_seed_expansion_seeds(geo: str) -> int:
    try:
        n = int(os.getenv("DISCOVERY_SEED_EXPANSION_MAX_SEEDS", "0") or "0")
    except ValueError:
        n = 0
    if n > 0:
        return n
    return 8 if is_global_geography(geo) else 6


def build_seed_expansion_prompts(
    scope: dict[str, Any],
    market: str,
    geo: str,
    *,
    max_prompts: int = 18,
) -> list[dict[str, str]]:
    """
    Competitor / alternative searches anchored on Phase 1 seed companies.
    Finds real peers that generic market queries miss in niche landscapes.
    """
    seeds = _seed_rows(scope)
    if not seeds:
        return []

    topic = refine_search_topic(market, geo)
    g = geo_search_label(geo)
    terms = [str(t).strip() for t in (scope.get("industry_terms") or []) if t]
    product = terms[0] if terms else topic
    short_product = " ".join(product.split()[:3])

    seen: set[str] = set()
    out: list[dict[str, str]] = []

    max_seeds = _max_seed_expansion_seeds(geo)
    fast = os.getenv("DISCOVERY_FAST_SEED_EXPANSION", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    queries_per_seed = 2 if fast else 3

    for i, row in enumerate(seeds[:max_seeds]):
        name = row["name"]
        fn = row["function"]
        tag = re.sub(r"[^a-z0-9]", "", name.lower())[:10] or f"s{i}"
        ctx = fn or short_product

        _push(
            out,
            seen,
            f"SE_{tag}_1",
            _q(name, "competitors", g),
            max_prompts=max_prompts,
            sub_sector="competitors",
        )
        _push(
            out,
            seen,
            f"SE_{tag}_2",
            _q("alternatives to", name, ctx, g),
            max_prompts=max_prompts,
            sub_sector="competitors",
        )
        if queries_per_seed >= 3:
            _push(
                out,
                seen,
                f"SE_{tag}_3",
                _q("companies like", name, ctx, g),
                max_prompts=max_prompts,
                sub_sector="competitors",
            )

    for j, term in enumerate(terms[1:5], 1):
        if len(term) < 4:
            continue
        _push(
            out,
            seen,
            f"ST_{j}a",
            _q(term, "manufacturer", g, "official", "site"),
            max_prompts=max_prompts,
            sub_sector="product_terms",
        )
        _push(
            out,
            seen,
            f"ST_{j}b",
            _q(term, "vendor", g, "corporate", "website"),
            max_prompts=max_prompts,
            sub_sector="product_terms",
        )

    _push(
        out,
        seen,
        "SE_LAND",
        _q("leading", topic, "vendors", g),
        max_prompts=max_prompts,
        sub_sector="landscape",
    )
    _push(
        out,
        seen,
        "SE_PEER",
        _q(topic, "peer companies", g, "official"),
        max_prompts=max_prompts,
        sub_sector="landscape",
    )

    return out[:max_prompts]

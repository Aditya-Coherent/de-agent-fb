"""Sector-tree discovery prompts — subtree queries instead of generic repeats."""
from __future__ import annotations

import re

from vendor_intel.discovery.discovery_query_quality import (
    is_listicle_discovery_query,
    sanitize_discovery_query,
)
from vendor_intel.funnel.prompt_builder import _q, geo_search_label, refine_search_topic


def _is_pharma(market: str, terms: list[str]) -> bool:
    # "pharma"/"pharmaceutical" always count; bare "drug" counts UNLESS it's clearly a
    # non-manufacturing context (testing/screening/enforcement/policy/abuse), which is
    # where the old bare-word match used to misfire into unrelated markets.
    blob = f"{market} {' '.join(terms)}".lower()
    return bool(
        re.search(
            r"\b(?:pharma\w*|pharmaceutical\w*|bulk\s+drug|active\s+pharmaceutical\s+ingredient)\b"
            r"|\bdrug\b(?!\s*(?:test|screen|abuse|polic|war|enforcement|awareness|addict))",
            blob,
        )
    )


def build_sector_tree_prompts(
    market: str,
    geo: str,
    *,
    industry_terms: list[str] | None = None,
    max_prompts: int = 16,
    scope: dict | None = None,
) -> list[dict[str, str]]:
    """Pharma/cyber subtree queries — higher unique yield than generic volume repeats."""
    if scope and scope.get("value_chain_layers"):
        from vendor_intel.funnel.market_understanding import build_sector_tree_from_market_map

        dynamic = build_sector_tree_from_market_map(scope, geo, max_prompts=max_prompts)
        if dynamic:
            return dynamic

    g = geo_search_label(geo)
    topic = refine_search_topic(market, geo)
    terms = industry_terms or []
    seen: set[str] = set()
    out: list[dict[str, str]] = []

    def _add(pid: str, text: str, sub_sector: str) -> None:
        if len(out) >= max_prompts:
            return
        text = sanitize_discovery_query(text)
        if not text or is_listicle_discovery_query(text):
            return
        key = text.lower()
        if key in seen:
            return
        seen.add(key)
        out.append({"id": pid, "level": "sector_tree", "text": text, "sub_sector": sub_sector})

    if _is_pharma(market, terms):
        tree: list[tuple[str, str]] = [
            ("ST1", "api_manufacturers", _q("API manufacturers", g, "GMP", "official", "site")),
            ("ST2", "api_manufacturers", _q("bulk drug", "API", g, "manufacturing", "plant")),
            ("ST3", "manufacturers", _q("formulation", "pharmaceutical", g, "official", "website")),
            ("ST4", "cdmo", _q("contract manufacturing", "pharma", g, "CDMO", "GMP")),
            ("ST5", "biotech", _q("biotech pharmaceutical", g, "R&D", "pipeline")),
            ("ST6", "biotech", _q("biosimilar", "manufacturer", g, "corporate", "site")),
            ("ST7", "manufacturers", _q("injectable", "pharmaceutical", g, "manufacturer")),
            ("ST8", "manufacturers", _q("oncology", "pharma", g, "company", "official")),
            ("ST9", "manufacturers", _q("dermatology", "pharma", g, "corporate", "website")),
            ("ST10", "exporters", _q("pharmaceutical exporters", g, "WHO", "GMP")),
            ("ST11", "distributors", _q("authorized pharmaceutical distributor", g, "firm")),
            ("ST12", "manufacturers", _q("generics", "pharmaceutical", "manufacturer", g)),
            ("ST15", "exporters", _q("pharma", "export", "company", g, "corporate")),
            ("ST16", "distributors", _q("hospital", "pharma", "supplier", g, "official")),
        ]
        for pid, sector, text in tree:
            _add(pid, text, sector)
    else:
        for i, sector in enumerate(
            ("manufacturers", "exporters", "distributors", "integrators"), 1
        ):
            _add(f"ST{i}a", _q(topic, sector.replace("_", " "), g, "official", "site"), sector)
            _add(f"ST{i}b", _q(topic, "company", g, "headquarters", "corporate"), sector)

    return out[:max_prompts]

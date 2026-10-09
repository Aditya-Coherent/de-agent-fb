"""Discovery prompts — LLM-first; ecosystem_functions from scope when LLM output is thin."""
from __future__ import annotations

from typing import Any

from vendor_intel.funnel.ecosystem_prompts import build_prompts_from_ecosystem
from vendor_intel.funnel.query_intent import (
    build_generic_discovery_prompts,
    enrich_scope_from_query,
)

_FUNCTION_SIGNALS = [
    "distribut", "wholesal", "import", "export", "retail", "dealer",
    "service provider", "system integrat", "consult", "mssp", "msp",
    "reseller", "var ", "vendor", "supplier", "manufactur", "oem",
    "integrat", "wholesale", "franchise", "trader", "cdmo", "co-pack",
]


def _count_diverse_functions(prompts: list[dict]) -> int:
    seen: set[str] = set()
    for p in prompts:
        text = (p.get("text") or "").lower()
        for sig in _FUNCTION_SIGNALS:
            if sig in text:
                seen.add(sig[:6])
                break
    return len(seen)


def _dedupe_prompts(items: list[dict[str, str]], funnel_texts: set[str]) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for p in items:
        text = (p.get("text") or "").strip()
        key = " ".join(text.lower().split())
        if not text or key in seen or key in funnel_texts:
            continue
        seen.add(key)
        row: dict[str, str] = {
            "id": p.get("id", f"P{len(out) + 1}"),
            "level": p.get("level", "discovery"),
            "text": text,
        }
        for key in ("sub_sector", "layer_id", "segment"):
            if p.get(key):
                row[key] = str(p[key])
        out.append(row)
    return out


def build_discovery_prompts(
    scope: dict[str, Any],
    query: str,
    funnel_prompts: list[dict[str, str]],
    *,
    max_prompts: int = 12,
    llm_prompts: list[dict[str, str]] | None = None,
    market_map_prompts: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    scope = enrich_scope_from_query(scope, query)
    market = str(scope.get("market") or query)
    geo = (scope.get("geographies") or ["global"])[0]
    anchor = (scope.get("anchor_company") or "").strip() or None
    industry_terms = scope.get("industry_terms")
    if not isinstance(industry_terms, list):
        industry_terms = []
    ecosystem = scope.get("ecosystem_functions")
    if not isinstance(ecosystem, list):
        ecosystem = []

    funnel_texts = {
        " ".join((fp.get("text") or "").lower().split()) for fp in funnel_prompts
    }

    llm_only: list[dict[str, str]] = []
    if llm_prompts:
        llm_only = [
            p
            for p in llm_prompts
            if str(p.get("id", "")).upper().startswith("P")
            or p.get("level") == "discovery"
        ]
    deduped_llm = _dedupe_prompts(llm_only or [], funnel_texts)
    deduped_map = _dedupe_prompts(market_map_prompts or [], funnel_texts)

    # Market-map prompts (value-chain driven) take priority over generic LLM filler
    if len(deduped_map) >= 6:
        combined = _dedupe_prompts(
            deduped_map + deduped_llm + [], funnel_texts
        )
        from_ecosystem_early: list[dict[str, str]] = []
        if ecosystem and len(combined) < max_prompts:
            from_ecosystem_early = build_prompts_from_ecosystem(
                market,
                geo,
                ecosystem,
                industry_terms=industry_terms,
                max_prompts=max_prompts,
            )
        generic_early = build_generic_discovery_prompts(
            market,
            geo,
            funnel_prompts,
            max_prompts=max_prompts,
            anchor_company=anchor,
            industry_terms=industry_terms,
        )
        combined = _dedupe_prompts(
            combined + from_ecosystem_early + generic_early, funnel_texts
        )
        for i, row in enumerate(combined[:max_prompts]):
            row["id"] = f"P{i + 1}"
        return combined[:max_prompts]

    # LLM produced enough diverse prompts — use them only (no generic dilution)
    if len(deduped_llm) >= 8 and _count_diverse_functions(deduped_llm) >= 5:
        for i, row in enumerate(deduped_llm[:max_prompts]):
            row["id"] = f"P{i + 1}"
        return deduped_llm[:max_prompts]

    from_ecosystem: list[dict[str, str]] = []
    if ecosystem:
        from_ecosystem = build_prompts_from_ecosystem(
            market,
            geo,
            ecosystem,
            industry_terms=industry_terms,
            max_prompts=max_prompts,
        )

    generic = build_generic_discovery_prompts(
        market,
        geo,
        funnel_prompts,
        max_prompts=max_prompts,
        anchor_company=anchor,
        industry_terms=industry_terms,
    )

    if deduped_llm:
        # Prefer LLM, then ecosystem-derived, then generic filler
        combined = _dedupe_prompts(
            deduped_llm + from_ecosystem + generic, funnel_texts
        )
    elif from_ecosystem:
        combined = _dedupe_prompts(from_ecosystem + generic, funnel_texts)
    else:
        combined = _dedupe_prompts(generic, funnel_texts)

    for i, row in enumerate(combined[:max_prompts]):
        row["id"] = f"P{i + 1}"
    return combined[:max_prompts]

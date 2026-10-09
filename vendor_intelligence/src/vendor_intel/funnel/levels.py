"""Search funnel L0–L3 — LLM-first prompts; generic regex fallback only."""
from __future__ import annotations

from enum import Enum
from typing import Any

from vendor_intel.funnel.discovery_prompts import build_discovery_prompts
from vendor_intel.funnel.query_intent import (
    build_generic_funnel_prompts,
    enrich_scope_from_query,
)
from vendor_intel.models import RunConfig


class FunnelLevel(str, Enum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


def funnel_level_order() -> list[FunnelLevel]:
    return [FunnelLevel.L0, FunnelLevel.L1, FunnelLevel.L2]


def _prompt_quality_ok(text: str) -> bool:
    t = text.strip()
    if len(t) < 8 or len(t) > 100:
        return False
    low = t.lower()
    for word in ("suppliers", "manufacturers", "companies", "producers"):
        if low.count(word) > 1:
            return False
    if " in global" in low:
        return False
    return True


def _valid_prompt_list(items: Any) -> list[dict[str, str]]:
    if not isinstance(items, list):
        return []
    out: list[dict[str, str]] = []
    for p in items:
        if not isinstance(p, dict):
            continue
        text = (p.get("text") or "").strip()
        if not text or not _prompt_quality_ok(text):
            continue
        out.append(
            {
                "id": str(p.get("id", f"P{len(out)}")),
                "level": str(p.get("level", "discovery")),
                "text": text,
            }
        )
    return out


def build_funnel_prompts(
    scope: dict[str, Any],
    query: str,
    *,
    llm_funnel: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    if len(llm_funnel) >= 3:
        return llm_funnel[:3]
    scope = enrich_scope_from_query(scope, query)
    market = str(scope.get("market") or query)
    geo = (scope.get("geographies") or ["global"])[0]
    terms = scope.get("industry_terms")
    if not isinstance(terms, list):
        terms = []
    return build_generic_funnel_prompts(market, geo, industry_terms=terms)


def merge_funnel_into_config(config: RunConfig, query: str) -> RunConfig:
    scope = enrich_scope_from_query(config.scope or {}, query)
    llm_funnel = _valid_prompt_list(config.funnel_prompts)
    llm_discovery = _valid_prompt_list(config.prompts)

    funnel = build_funnel_prompts(scope, query, llm_funnel=llm_funnel)
    discovery = build_discovery_prompts(
        scope,
        query,
        funnel,
        llm_prompts=llm_discovery if llm_discovery else None,
    )

    return config.model_copy(
        update={
            "scope": scope,
            "funnel_prompts": funnel,
            "prompts": discovery[:12],
        }
    )

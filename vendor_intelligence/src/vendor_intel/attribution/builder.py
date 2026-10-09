"""Source attribution: inclusion reason + URLs (R3)."""
from __future__ import annotations

from vendor_intel.models import Entity, RunConfig
from vendor_intel.utils.description import build_company_description


def _gate_summary(entity: Entity) -> list[str]:
    passed = [g for g, ok in entity.gate_pass.items() if ok]
    return passed


def build_inclusion_reason(entity: Entity, config: RunConfig) -> str:
    parts: list[str] = []
    levels = ", ".join(entity.funnel_levels_seen) if entity.funnel_levels_seen else "discovery"
    parts.append(f"Found via funnel levels {levels}")
    if entity.company_type and entity.company_type != "Unknown":
        parts.append(f"classified as {entity.company_type}")
    gates = _gate_summary(entity)
    if gates:
        parts.append(f"passed gates: {', '.join(gates)}")
    parts.append(f"tier {entity.tier}")
    scope = config.scope or {}
    if scope.get("segment_conditions"):
        parts.append("matches query segment")
    return "; ".join(parts) + "."


def build_inclusion_sources(entity: Entity) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    def add(u: str) -> None:
        u = (u or "").strip()
        if not u or u in seen:
            return
        seen.add(u)
        urls.append(u)

    for u in entity.scraped_urls:
        add(u)
    for items in entity.gates.values():
        for it in items:
            add(it.url)
    return urls[:25]


def apply_attribution(entities: list[Entity], config: RunConfig) -> None:
    for e in entities:
        if e.tier not in ("A", "B") or e.excluded_from_company_list:
            continue
        e.inclusion_reason = build_inclusion_reason(e, config)
        e.inclusion_sources = build_inclusion_sources(e)
        if not e.company_description:
            e.company_description = build_company_description(e, [], lead_text=e.scraped_text)

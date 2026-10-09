from __future__ import annotations

from vendor_intel.clients.wikidata import lookup_parent_org
from vendor_intel.config import Settings
from vendor_intel.models import RunConfig, RunState
from vendor_intel.utils.dedupe import build_entities_from_hits
from vendor_intel.utils.domains import domain_from_url

FAMILY_HINTS = {
    "xiaomi": ["Xiaomi", "Redmi", "POCO", "Mi"],
    "bbk": ["Oppo", "Realme", "Vivo", "OnePlus", "iQOO"],
    "lenovo": ["Lenovo", "Motorola"],
}


def _detect_family(name: str) -> str | None:
    low = name.lower()
    for fam, members in FAMILY_HINTS.items():
        if any(m.lower() in low or low in m.lower() for m in members):
            return fam
    return None


async def run_entity_graph(
    state: RunState,
    config: RunConfig,
    settings: Settings,
) -> None:
    del config
    entities = build_entities_from_hits(state.discovery_hits)
    use_mock = settings.mock_mode or settings.use_mock_data

    for e in entities:
        fam = _detect_family(e.canonical_name)
        if fam:
            e.anchor_family = fam
            e.siblings = [m for m in FAMILY_HINTS[fam] if m.lower() != e.canonical_name.lower()]

        parent, wurl = await lookup_parent_org(e.canonical_name)
        if parent:
            e.parent_group = parent
            if wurl and not use_mock:
                e.gates.setdefault("ma", [])
        elif not e.parent_group:
            e.parent_group = e.canonical_name

        if not e.primary_domain:
            doms = [
                domain_from_url(h.source_url)
                for h in state.discovery_hits
                if h.name_raw == e.canonical_name
            ]
            doms = [d for d in doms if d and "example-" not in d]
            e.primary_domain = doms[0] if doms else f"{e.canonical_name.lower().replace(' ', '')}.com"

    state.entities = entities

from __future__ import annotations

from collections import defaultdict

from vendor_intel.clients.claude import ClaudeClient
from vendor_intel.config import Settings
from vendor_intel.models import ClusterDecision, Entity, ListingMode, RunConfig, RunState


def _rule_based_decision(parent: str, cluster: list[Entity], geo: str) -> ClusterDecision | None:
    names = {e.canonical_name for e in cluster}
    if "Motorola" in names and "Lenovo" in names and "india" in geo.lower():
        return ClusterDecision(
            parent_group=parent or "Lenovo",
            listing_mode=ListingMode.PARENT_ONLY,
            brands_in_company_list=["Lenovo"],
            canonical_brand="Lenovo",
            excluded_from_company_list=["Motorola"],
            confidence=0.88,
            rationale="Motorola not material in India vs Lenovo.",
        )
    if names & {"Xiaomi", "Redmi", "POCO", "Mi"}:
        canonical = "Xiaomi" if "Xiaomi" in names else "Redmi"
        return ClusterDecision(
            parent_group=parent or "Xiaomi",
            listing_mode=ListingMode.COLLAPSE_CANONICAL,
            brands_in_company_list=[canonical],
            canonical_brand=canonical,
            excluded_from_company_list=[n for n in names if n != canonical],
            confidence=0.85,
            rationale="Sub-brands as portfolio lines.",
        )
    bbk = names & {"Oppo", "Realme", "Vivo", "OnePlus", "iQOO"}
    if len(bbk) >= 2:
        return ClusterDecision(
            parent_group=parent or "BBK Electronics",
            listing_mode=ListingMode.SPLIT_BRANDS,
            brands_in_company_list=sorted(bbk),
            confidence=0.82,
            rationale="Multiple BBK siblings with independent presence.",
        )
    return None


async def run_brand_classifier(
    state: RunState,
    config: RunConfig,
    claude: ClaudeClient,
    settings: Settings,
) -> None:
    geo = (config.scope.get("geographies") or ["global"])[0]
    clusters: dict[str, list[Entity]] = defaultdict(list)
    for e in state.entities:
        if e.tier in ("A", "B"):
            clusters[e.parent_group or e.anchor_family or e.canonical_name].append(e)

    decisions: list[ClusterDecision] = []
    for parent, cluster in clusters.items():
        if len(cluster) < 2:
            decisions.append(
                ClusterDecision(
                    parent_group=parent,
                    listing_mode=ListingMode.SINGLE_BRAND,
                    brands_in_company_list=[cluster[0].canonical_name],
                    confidence=0.9,
                    rationale="Single brand cluster.",
                )
            )
            continue
        ruled = _rule_based_decision(parent, cluster, geo)
        if ruled:
            decisions.append(ruled)
        else:
            brands = [e.canonical_name for e in cluster if e.tier in ("A", "B")]
            decisions.append(
                ClusterDecision(
                    parent_group=parent,
                    listing_mode=ListingMode.SPLIT_BRANDS,
                    brands_in_company_list=brands,
                    confidence=0.7,
                    rationale="Default split.",
                )
            )

    state.cluster_decisions = decisions
    for dec in decisions:
        for e in state.entities:
            if e.canonical_name in dec.excluded_from_company_list:
                e.excluded_from_company_list = True
                e.suppression_reason = dec.rationale[:80]

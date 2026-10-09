from vendor_intel.config import Settings
from vendor_intel.models import (
    CompanyListItem,
    ListingMode,
    ParentGroupItem,
    PipelineResult,
    RunConfig,
    RunState,
    SuppressedBrand,
)


def apply_listing_and_select(state: RunState, config: RunConfig, settings: Settings) -> PipelineResult:
    scope = config.scope
    min_count = scope.get("min_final_count") or settings.min_final_count
    max_count = scope.get("max_final_count") or settings.max_final_count
    anchor = scope.get("anchor_company")
    exclude_anchor = scope.get("anchor_exclude_from_company_list", False)

    brand_allowed: dict[str, bool] = {}
    for dec in state.cluster_decisions:
        if dec.listing_mode == ListingMode.SPLIT_BRANDS:
            for b in dec.brands_in_company_list:
                brand_allowed[b] = True
        elif dec.listing_mode == ListingMode.COLLAPSE_CANONICAL:
            canon = dec.canonical_brand or (dec.brands_in_company_list[0] if dec.brands_in_company_list else "")
            if canon:
                brand_allowed[canon] = True
            for ex in dec.excluded_from_company_list:
                brand_allowed[ex] = False
        elif dec.listing_mode == ListingMode.PARENT_ONLY:
            canon = dec.canonical_brand or dec.brands_in_company_list[0]
            for b in dec.brands_in_company_list:
                brand_allowed[b] = b == canon
            for ex in dec.excluded_from_company_list:
                brand_allowed[ex] = False
        elif dec.listing_mode == ListingMode.SINGLE_BRAND:
            for b in dec.brands_in_company_list:
                brand_allowed[b] = True

    candidates = []
    for e in state.entities:
        if e.tier not in ("A", "B") or e.excluded_from_company_list:
            continue
        if exclude_anchor and anchor and e.canonical_name.lower() == anchor.lower():
            continue
        if brand_allowed and e.canonical_name in brand_allowed and not brand_allowed[e.canonical_name]:
            continue
        candidates.append(e)

    candidates.sort(key=lambda x: (0 if x.tier == "A" else 1, -x.composite_score))
    seen: set[str] = set()
    unique = []
    for e in candidates:
        k = e.canonical_name.lower()
        if k in seen:
            continue
        seen.add(k)
        unique.append(e)
    selected = unique[:max_count]
    warnings = []
    if len(selected) < min_count:
        warnings.append(f"Only {len(selected)} companies met quality bar (target {min_count}). No padding.")

    company_list = []
    for e in selected:
        urls = []
        for items in e.gates.values():
            for it in items:
                if it.url not in urls:
                    urls.append(it.url)
        mode = "SINGLE_BRAND"
        for dec in state.cluster_decisions:
            if e.canonical_name in dec.brands_in_company_list:
                mode = dec.listing_mode.value
                break
        inclusion_sources = e.inclusion_sources or urls[:10]
        bd = e.score_breakdown or {}
        company_list.append(
            CompanyListItem(
                display_name=e.canonical_name,
                parent_group=e.parent_group or e.canonical_name,
                primary_domain=e.primary_domain,
                listing_mode=mode,
                score=round(e.composite_score, 3),
                discovery_score=float(bd.get("discovery_score") or 0),
                evidence_score=float(bd.get("evidence_score") or 0),
                scrape_score=float(bd.get("scrape_score") or 0),
                verification_score=float(
                    bd.get("verification_score") or e.verification_confidence or 0
                ),
                tier=e.tier,
                distinct_domains=e.distinct_domains,
                evidence_urls=urls[:10],
                short_rationale=e.inclusion_reason or f"Tier {e.tier}",
                company_type=e.company_type,
                inclusion_reason=e.inclusion_reason,
                inclusion_sources=inclusion_sources,
                funnel_levels_seen=e.funnel_levels_seen,
                company_description=e.company_description,
            )
        )

    parent_group_list = [
        ParentGroupItem(
            parent_name=dec.parent_group,
            brands_in_company_list=dec.brands_in_company_list,
            listing_mode=dec.listing_mode.value,
            collapsed_from=list(dec.excluded_from_company_list) if dec.listing_mode == ListingMode.COLLAPSE_CANONICAL else [],
            excluded_subsidiaries=list(dec.excluded_from_company_list) if dec.listing_mode == ListingMode.PARENT_ONLY else [],
        )
        for dec in state.cluster_decisions
    ]

    suppressed = [
        SuppressedBrand(name=e.canonical_name, reason=e.suppression_reason or "excluded")
        for e in state.entities
        if e.suppression_reason or e.excluded_from_company_list
    ]

    return PipelineResult(
        run_id=state.run_id,
        query=state.query,
        interpretation_summary=scope.get("interpretation_summary", ""),
        company_list=company_list,
        parent_group_list=parent_group_list,
        suppressed_brands=suppressed,
        counts={
            "discovery_hits": len(state.discovery_hits),
            "entities_total": len(state.entities),
            "company_list_returned": len(company_list),
            "tier_a": sum(1 for e in selected if e.tier == "A"),
            "tier_b": sum(1 for e in selected if e.tier == "B"),
        },
        audit_manifest={
            "prompts_used": len(config.prompts),
            "widen_loops": state.widen_loops,
            "mock_mode": settings.mock_mode or settings.use_mock_data,
            "validation_gates": ["operational", "geography", "product", "activity", "ma"],
        },
        warnings=warnings,
        mock_mode=settings.mock_mode or settings.use_mock_data,
    )

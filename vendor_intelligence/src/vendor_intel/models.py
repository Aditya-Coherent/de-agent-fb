from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class ListingMode(str, Enum):
    SPLIT_BRANDS = "SPLIT_BRANDS"
    COLLAPSE_CANONICAL = "COLLAPSE_CANONICAL"
    PARENT_ONLY = "PARENT_ONLY"
    SINGLE_BRAND = "SINGLE_BRAND"


class EvidenceItem(BaseModel):
    url: str
    domain: str
    type: str = "other"
    snippet: str = ""
    gate: str = ""


class DiscoveryHit(BaseModel):
    name_raw: str
    source_url: str
    source_domain: str
    prompt_id: str
    backend: str
    snippet: str = ""
    funnel_level: str = ""
    search_theme: str = ""


class Entity(BaseModel):
    entity_id: str = Field(default_factory=lambda: str(uuid4())[:8])
    canonical_name: str
    primary_domain: str = ""
    parent_group: str = ""
    siblings: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    discovery_count: int = 0
    distinct_domains_discovery: int = 0
    anchor_family: str | None = None
    gates: dict[str, list[EvidenceItem]] = Field(default_factory=dict)
    distinct_domains: int = 0
    gate_pass: dict[str, bool] = Field(default_factory=dict)
    tier: str = "C"
    product_fit_score: float = 0.0
    geo_fit_score: float = 0.0
    composite_score: float = 0.0
    corporate_event_date: str | None = None
    parent_company: str | None = None
    checked_on: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    listing_mode: ListingMode | None = None
    excluded_from_company_list: bool = False
    collapse_into: str | None = None
    suppression_reason: str | None = None
    company_type: str = "Unknown"
    company_function: str = ""
    discovered_functions: list[str] = Field(default_factory=list)
    funnel_levels_seen: list[str] = Field(default_factory=list)
    scraped_text: str = ""
    scraped_urls: list[str] = Field(default_factory=list)
    inclusion_reason: str = ""
    inclusion_sources: list[str] = Field(default_factory=list)
    company_description: str = ""
    score_breakdown: dict[str, Any] = Field(default_factory=dict)
    verification_confidence: float = 0.0


class ClusterDecision(BaseModel):
    parent_group: str
    listing_mode: ListingMode
    brands_in_company_list: list[str] = Field(default_factory=list)
    canonical_brand: str | None = None
    excluded_from_company_list: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    rationale: str = ""
    evidence_urls: list[str] = Field(default_factory=list)


class RunConfig(BaseModel):
    scope: dict[str, Any] = Field(default_factory=dict)
    funnel_prompts: list[dict[str, str]] = Field(default_factory=list)
    prompts: list[dict[str, str]] = Field(default_factory=list)
    gates: dict[str, Any] = Field(default_factory=dict)
    listing_rules: dict[str, Any] = Field(default_factory=dict)
    ranking: dict[str, Any] = Field(default_factory=dict)
    evidence_policy: dict[str, Any] = Field(default_factory=dict)
    freshness_policy: dict[str, Any] = Field(default_factory=dict)


class CompanyListItem(BaseModel):
    display_name: str
    parent_group: str
    primary_domain: str = ""
    listing_mode: str
    score: float
    discovery_score: float = 0.0
    evidence_score: float = 0.0
    scrape_score: float = 0.0
    verification_score: float = 0.0
    tier: str
    distinct_domains: int
    evidence_urls: list[str] = Field(default_factory=list)
    short_rationale: str = ""
    company_type: str = "Unknown"
    industry_name: str = ""
    country_presence: str = ""
    inclusion_reason: str = ""
    inclusion_sources: list[str] = Field(default_factory=list)
    company_description: str = ""
    funnel_levels_seen: list[str] = Field(default_factory=list)


class ParentGroupItem(BaseModel):
    parent_name: str
    brands_in_company_list: list[str] = Field(default_factory=list)
    listing_mode: str
    collapsed_from: list[str] = Field(default_factory=list)
    excluded_subsidiaries: list[str] = Field(default_factory=list)


class SuppressedBrand(BaseModel):
    name: str
    reason: str


class PipelineResult(BaseModel):
    run_id: str
    query: str
    interpretation_summary: str = ""
    company_list: list[CompanyListItem] = Field(default_factory=list)
    parent_group_list: list[ParentGroupItem] = Field(default_factory=list)
    suppressed_brands: list[SuppressedBrand] = Field(default_factory=list)
    counts: dict[str, Any] = Field(default_factory=dict)
    audit_manifest: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    mock_mode: bool = False
    csv_paths: dict[str, str] = Field(
        default_factory=dict,
        description="Paths to CSV deliverables: company_list, parent_group_list, suppressed_brands, run_summary",
    )


class RunState(BaseModel):
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    query: str
    config: RunConfig = Field(default_factory=RunConfig)
    discovery_hits: list[DiscoveryHit] = Field(default_factory=list)
    entities: list[Entity] = Field(default_factory=list)
    cluster_decisions: list[ClusterDecision] = Field(default_factory=list)
    widen_loops: int = 0
    query_yield_metrics: list[dict] = Field(default_factory=list)
    query_sector_validated: dict[str, int] = Field(default_factory=dict)

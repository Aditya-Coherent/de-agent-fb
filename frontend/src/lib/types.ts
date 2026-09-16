export interface Config {
  model: string;
  base_url: string;
  enable_web: boolean;
  min_loop_iters: number;
  max_loop_iters: number;
  reward_threshold: number;
  per_region_shares: boolean;
  db_connected: boolean;
  is_online: boolean;
}

export interface RegionalInfo {
  value_share: number;
  cagr: number;
}

export interface Artifact {
  topic: string;
  question: string;
  answer: string;
  drivers: string[];
  current_events: string[];
}

export interface QualityFlag {
  code: string;
  severity: "critical" | "warning" | "info";
  message: string;
}

export interface GenerateSummary {
  run_id: string;
  market: string;
  reward: number;
  iter: number;
  global_anchor_2026: number;
  global_cagr: number;
  regional: Record<string, RegionalInfo>;
  shares: Record<string, Record<string, Record<string, Record<string, number>>>>;
  artifacts: Artifact[];
  has_human_sheet: boolean;
  quality_flags: QualityFlag[];
  // Desk Research Phase 0 gate (segment_definition.py): region_total_market_value
  // (global_anchor_2026 * regional[region].value_share) and the user's
  // approved base-year segment shares applied to it.
  region_totals_usd_mn?: Record<string, number>;
  segment_value_usd_mn?: Record<string, Record<string, Record<string, number>>>;
}

// --------------------------------------------------------------------------- #
// Desk Research -- Phase 0: Base-Year Segment Definition. The mandatory gate
// BEFORE `GET /api/generate` -- no LLM involved; pure user input, validated
// deterministically. See `src/me_engine/generation/segment_definition.py`.
// --------------------------------------------------------------------------- #
export const DESK_RESEARCH_REGIONS = [
  "North America",
  "Europe",
  "Asia Pacific",
  "Latin America",
  "Middle East & Africa",
] as const;

export type DeskResearchRegion = (typeof DESK_RESEARCH_REGIONS)[number];

export interface DeskSegmentDefinitionSegment {
  name: string;
  // One entry per DESK_RESEARCH_REGIONS value, 0-100. Blank/unset entries
  // are represented client-side as `undefined` until the user fills them in.
  region_shares: Partial<Record<DeskResearchRegion, number>>;
}

export interface DeskSegmentDefinitionDimension {
  dimension_name: string;
  segments: DeskSegmentDefinitionSegment[];
}

export interface DeskSegmentDefinition {
  market_name: string;
  base_year: number;
  dimensions: DeskSegmentDefinitionDimension[];
}

export interface DeskSegmentDefinitionPreview {
  regions: string[];
  base_year: number;
  dimensions: DeskSegmentDefinitionDimension[];
}

export interface MEResult {
  gate1_passed: boolean;
  gate3_passed: boolean;
  output_exists: boolean;
  log_tail: string;
}

export interface HistoryEntry {
  run_id?: string;
  timestamp: string;
  market_name: string;
  skeleton_source: string;
  reward: number | null;
  iterations: number | null;
  geographies: number;
  global_anchor_2026: number | null;
  global_cagr: number | null;
  db_evidence_used: boolean;
  web_snippets: number;
  output_path: string;
  me_output_path?: string;
  me_gate1_passed?: boolean;
  me_gate3_passed?: boolean;
}

export interface CompareReport {
  report_text: string;
}

// --------------------------------------------------------------------------- #
// Demand-Side (Usage-Based) three-phase pipeline
// --------------------------------------------------------------------------- #
export interface SetupDraft {
  market_name: string;
  scope: string;
  geography: string[];
  base_year: number;
  market_unit: string;
  physical_unit: string;
  grounded: boolean;
}

export interface EvidenceStrategy {
  driver_name: string;
  metric_required: string;
  physical_unit: string;
  period: string;
  geography: string[];
  primary_source_types: string[];
  preferred_sources: string[];
  search_query_templates: string[];
  fallback_source_types: string[];
  fallback_queries: string[];
  derivation_path: string;
  expected_evidence: string;
  why_this_driver_measures_the_usage: string;
  strategy_source: string;
}

export interface EvidenceStrategyFlag {
  code: string;
  field: string;
  message: string;
}

export interface DemandDriver {
  driver_name: string;
  unit: string;
  final_demand_unit: string;
  consumption_unit?: string;
  intensity_kind?: string;
  conversion_formula?: string;
  required_evidence?: string[];
  rationale?: string;
  library_status?: string;
  // Phase 7 (DEMAND_SIDE_EVIDENCE_PLAN.md) -- explicit, user-editable
  // per-driver evidence strategy, proposed alongside the driver itself.
  // Optional: absence never blocks approval/sizing.
  evidence_strategy?: EvidenceStrategy;
  evidence_strategy_flags?: EvidenceStrategyFlag[];
}

export interface UsageBucket {
  name: string;
  description: string;
  bucket_category: string;
}

// Phase 9 (DEMAND_SIDE_EVIDENCE_PLAN.md) -- per-bucket scope <-> taxonomy
// verdict. Flag only: the node that produces this never removes a bucket.
export interface ScopeValidationFlag {
  classification: "in_scope" | "ambiguous" | "out_of_scope";
  reason: string;
  evidence: string;
  confidence: number;
  source: "llm" | "deterministic";
}

// Aggregate "is the SCOPE ITSELF too narrow for this taxonomy" signal,
// computed deterministically from the per-bucket flags above.
export interface ScopeTaxonomyAggregate {
  flagged_count: number;
  total_buckets: number;
  flagged_ratio: number;
  likely_scope_too_narrow: boolean;
}

export interface TaxonomyDraft {
  market_name: string;
  usage_buckets: UsageBucket[];
  excluded_usage_buckets: Record<string, unknown>[];
  missing_usage_flags: Record<string, unknown>[];
  coverage_pct: number | null;
  overlap_flags: string[];
  drivers: Record<string, DemandDriver>;
  scope_validation_flags?: Record<string, ScopeValidationFlag>;
  scope_taxonomy_aggregate?: ScopeTaxonomyAggregate;
  // {bucket_name: "<justification>"} -- required to retain a bucket flagged
  // out_of_scope through approval; see `approve_taxonomy`.
  scope_overrides?: Record<string, string>;
}

export interface SizingSummary {
  run_id: string;
  market: string;
  total_market_value_usd_mn: number;
  related_demand_value_usd_mn: number;
  validation: Record<string, unknown>;
  quality_flags: QualityFlag[];
}

// --------------------------------------------------------------------------- #
// Demand-Side (Usage-Based) -- India-only variant. Adds one more hierarchy
// level (Major Use Case -> India -> Actual Use Case) and locks geography to
// India; reuses every other shape above unchanged.
// --------------------------------------------------------------------------- #
export interface ActualUseCase extends UsageBucket {
  major_use_case: string;
}

export interface IndiaTaxonomyDraft {
  market_name: string;
  major_use_cases: UsageBucket[];
  actual_use_cases: ActualUseCase[];
  rejected_actual_use_case_candidates?: Record<string, unknown>[];
  excluded_usage_buckets: Record<string, unknown>[];
  missing_usage_flags: Record<string, unknown>[];
  coverage_pct: number | null;
  overlap_flags: string[];
  drivers: Record<string, DemandDriver>;
  scope_validation_flags?: Record<string, ScopeValidationFlag>;
  scope_taxonomy_aggregate?: ScopeTaxonomyAggregate;
}

export interface IndiaSizingSummary {
  run_id: string;
  market: string;
  total_market_value_usd_mn: number;
  related_demand_value_usd_mn: number;
  major_use_case_totals: Record<string, number>;
  india_market_total: number;
  validation: Record<string, unknown>;
  quality_flags: QualityFlag[];
}

// --------------------------------------------------------------------------- #
// Research Audit -- audit trail introspection for the Usage-Based pipeline
// --------------------------------------------------------------------------- #
export type AuditRunStatus = "running" | "completed" | "interrupted" | "unknown";

export interface AuditRunSummary {
  market: string;
  run_id: string;
  status: AuditRunStatus;
  started_at?: string | null;
  last_update?: string | null;
  current_stage?: string | null;
  final_market_size?: number | null;
  has_audit_log: boolean;
}

export interface AuditSummary {
  total_events: number;
  counts_by_event_type: Record<string, number>;
  counts_by_status: Record<string, number>;
  countries_touched: string[];
  buckets_touched: string[];
  not_captured?: boolean;
}

export interface AuditTotalsCaptured {
  total_market_value_usd_mn?: number | null;
  related_demand_value_usd_mn?: number | null;
  quality_flags?: QualityFlag[] | null;
  not_captured?: false;
}

export interface AuditTotalsNotCaptured {
  not_captured: true;
}

export type AuditTotals = AuditTotalsCaptured | AuditTotalsNotCaptured;

export interface AuditRunDetail {
  market: string;
  run_id: string;
  status: AuditRunStatus;
  progress?: Record<string, unknown> | { not_captured: true } | null;
  lock?: Record<string, unknown> | { not_captured: true } | null;
  audit_summary: AuditSummary;
  totals: AuditTotals;
}

export interface AuditLlmCall {
  model?: string | null;
  prompt?: unknown;
  response?: unknown;
  [key: string]: unknown;
}

export interface AuditEvent {
  event_id: string;
  run_id: string;
  approach?: string | null;
  market?: string | null;
  stage?: string | null;
  event_type?: string | null;
  timestamp?: string | null;
  seq?: number | null;
  country?: string | null;
  bucket?: string | null;
  llm?: AuditLlmCall | null;
  query?: string | null;
  source?: unknown;
  raw_evidence?: unknown;
  validation?: unknown;
  derivation?: Record<string, unknown> | null;
  calculation?: unknown;
  input_data?: unknown;
  output_data?: unknown;
  status?: string | null;
  error?: string | null;
  data?: unknown;
}

export interface AuditEventsResponse {
  total: number;
  limit: number;
  offset: number;
  events: AuditEvent[];
}

export type AuditStageStatus = "done" | "in_progress" | "pending";

export interface AuditTimelineStage {
  stage: string;
  status: AuditStageStatus;
}

export interface AuditTimeline {
  market: string;
  run_id: string;
  timeline: AuditTimelineStage[];
}

export interface AuditLineageNode {
  name: string;
  value?: number | string | null;
  unit?: string | null;
  geography?: string | null;
  verification_status?: string | null;
  source?: string | null;
  source_url?: string | null;
  derivation_method?: string | null;
  formula?: string | null;
  inputs?: AuditLineageNode[];
  is_leaf?: boolean;
  evidence_event?: AuditEvent | null;
}

export interface AuditLineageResponse {
  market: string;
  run_id: string;
  output_id: string;
  lineage: AuditLineageNode;
}

// --------------------------------------------------------------------------- #
// Segmentation Demo
// --------------------------------------------------------------------------- #
export type SegmentationViewKey =
  | "product"
  | "source"
  | "flavor"
  | "application"
  | "distribution_channel"
  | "sales_channel";

export type SegmentationViews = Partial<Record<SegmentationViewKey, Record<string, number>>>;

export interface SegmentationMatrixCell {
  product: string;
  application: string;
  value_usd_mn: number;
  price_usd_per_l: number;
  /** Derived as value / price -- null when no usable price was returned. */
  volume_mn_l: number | null;
}

export interface SegmentationProductApplication {
  products: string[];
  applications: string[];
  cells: SegmentationMatrixCell[];
  prices_usd_per_l: Record<string, number>;
  total_volume_mn_l: number;
  blended_price_usd_per_l: number | null;
}

export interface SegmentationCountry {
  name: string;
  total_usd_mn: number;
  views: SegmentationViews;
  product_application?: SegmentationProductApplication;
}

export interface SegmentationRegion {
  name: string;
  total_usd_mn: number;
  views: SegmentationViews;
  countries: SegmentationCountry[];
}

export interface TradeOrigin {
  partner: string;
  value_usd: number;
  weight_kg: number;
  unit_value_usd_per_kg: number;
  unit_value_usd_per_l: number;
}

export interface TradeFlow {
  value_usd: number;
  weight_kg: number;
  unit_value_usd_per_kg: number | null;
  unit_value_usd_per_l: number | null;
}

export interface TradeEvidence {
  available: boolean;
  reason?: string;
  country?: string;
  year?: number;
  hs_code?: string;
  hs_description?: string;
  source?: string;
  source_urls?: { imports: string; exports: string; by_partner: string };
  kg_per_litre?: number;
  imports?: TradeFlow;
  exports?: TradeFlow;
  net_trade_kg?: number;
  origins?: TradeOrigin[];
  coverage_note?: string;
}

export interface CalcStep {
  label: string;
  op: string;
  value: number;
  unit: string;
  provenance: "verified" | "derived" | "assumed";
  source: string | null;
  url: string | null;
  note: string | null;
  formula: string | null;
}

export interface CalcResult {
  country: string;
  product: string;
  application: string;
  chain: CalcStep[];
  per_capita_detail: CalcStep[];
  result: {
    volume_l: number;
    volume_mn_l: number;
    price_usd_per_l: number;
    revenue_usd: number;
    revenue_usd_mn: number;
  };
  provenance_counts: Record<string, number>;
  parameters: Record<string, { value: string | number; unit?: string; note?: string }>;
}

export interface SegmentationDemoCheck {
  check: string;
  expected: number;
  actual: number;
  ok: boolean;
}

export interface SegmentationDemoStep {
  step_id: string;
  kind: "input" | "llm_call" | "aggregate" | "narrative";
  title: string;
  description: string;
  data?: Record<string, unknown>;
  request?: { system: string; user: string };
  response?: unknown;
  computed?: Record<string, unknown>;
  checks?: SegmentationDemoCheck[];
}

export interface SegmentationMarketNarrative {
  headline: string;
  key_shifts: string[];
  growth_drivers: string[];
  regions_to_watch: { region: string; note: string }[];
  outlook_summary: string;
}

export interface SegmentationDemoResult {
  market_name: string;
  base_year: number;
  generated_at?: string;
  view_labels: Partial<Record<SegmentationViewKey, string>>;
  canonical_view?: SegmentationViewKey;
  global_total_usd_mn: number;
  global_views: SegmentationViews;
  regions: SegmentationRegion[];
  narrative?: SegmentationMarketNarrative;
  steps: SegmentationDemoStep[];
  log: string[];
  grounded: boolean;
  saved_path?: string;
}

// --------------------------------------------------------------------------- #
// Apparent consumption model
// --------------------------------------------------------------------------- #
export interface ApparentApplication {
  application: string;
  volume_l: number;
  volume_mn_l: number;
  share_of_product: number;
  price_usd_per_l: number;
  revenue_usd: number;
  revenue_usd_mn: number;
}

export interface ApparentProduct {
  product: string;
  volume_l: number;
  volume_mn_l: number;
  share_of_market: number;
  price_usd_per_l: number;
  revenue_usd: number;
  revenue_usd_mn: number;
  applications: ApparentApplication[];
}

export interface ApparentConsumption {
  market: string;
  country: string;
  year: number;
  identity: {
    production_mn_l: number;
    production_basis: string;
    production_provenance: string;
    imports_mn_l: number;
    exports_mn_l: number;
    trade_provenance: string;
    trade_source: string | null;
    trade_urls: { imports: string; exports: string; by_partner: string } | null;
    apparent_consumption_mn_l: number;
    per_capita_l: number | null;
    formula: string;
  };
  plausibility: {
    verdict: string;
    detail: string;
    band_lo: number;
    band_hi: number;
    per_capita_l: number;
  };
  products: string[];
  applications: string[];
  tree: ApparentProduct[];
  total_revenue_usd_mn: number;
  log: string[];
}

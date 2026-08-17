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
}

export interface UsageBucket {
  name: string;
  description: string;
  bucket_category: string;
}

export interface TaxonomyDraft {
  market_name: string;
  usage_buckets: UsageBucket[];
  excluded_usage_buckets: Record<string, unknown>[];
  missing_usage_flags: Record<string, unknown>[];
  coverage_pct: number | null;
  overlap_flags: string[];
  drivers: Record<string, DemandDriver>;
}

export interface SizingSummary {
  run_id: string;
  market: string;
  total_market_value_usd_mn: number;
  related_demand_value_usd_mn: number;
  validation: Record<string, unknown>;
  quality_flags: QualityFlag[];
}

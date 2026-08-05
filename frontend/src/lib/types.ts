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

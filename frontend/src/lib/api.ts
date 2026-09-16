import type {
  Config,
  HistoryEntry,
  MEResult,
  CompareReport,
  SetupDraft,
  TaxonomyDraft,
  IndiaTaxonomyDraft,
  DeskSegmentDefinition,
  DeskSegmentDefinitionPreview,
  AuditRunSummary,
  AuditRunDetail,
  AuditEventsResponse,
  AuditTimeline,
  AuditLineageResponse,
  SegmentationDemoResult,
  TradeEvidence,
  CalcResult,
  ApparentConsumption,
} from "./types";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000";

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`${path} failed: ${res.status}`);
  return res.json() as Promise<T>;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || `${path} failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const getConfig = () => getJson<Config>("/api/config");
export const getSkeletonFiles = () => getJson<string[]>("/api/skeleton-files");
export const getHistory = (limit = 25) => getJson<HistoryEntry[]>(`/api/history?limit=${limit}`);
export const generateMe = (runId: string) => postJson<MEResult>("/api/generate-me", { run_id: runId });
export const runCompare = (runId: string) => postJson<CompareReport>("/api/compare", { run_id: runId });

export function generateEventUrl(market: string, source: "db" | "reference", skeletonChoice: string) {
  const params = new URLSearchParams({ market, source });
  if (source === "reference") params.set("skeleton_choice", skeletonChoice);
  return `${API_BASE}/api/generate?${params.toString()}`;
}

// --------------------------------------------------------------------------- #
// Desk Research -- Phase 0: Base-Year Segment Definition. Mandatory gate
// before `generateEventUrl`'s `/api/generate` is allowed to run; no LLM call.
// --------------------------------------------------------------------------- #
export const getDeskRegions = () => getJson<{ regions: string[] }>("/api/segment-definition/regions");
export const getDeskDefaultBaseYear = () => getJson<{ base_year: number }>("/api/segment-definition/default-base-year");

export function previewDeskSegmentDefinition(market: string, source: "db" | "reference", skeletonChoice: string) {
  const params = new URLSearchParams({ market, source });
  if (source === "reference") params.set("skeleton_choice", skeletonChoice);
  return getJson<DeskSegmentDefinitionPreview>(`/api/segment-definition/preview?${params.toString()}`);
}

export const submitDeskSegmentDefinition = (definition: DeskSegmentDefinition) =>
  postJson<DeskSegmentDefinition & { approved: true }>("/api/segment-definition/submit", definition);

export const getDeskSegmentDefinition = (market: string) =>
  getJson<DeskSegmentDefinition>(`/api/segment-definition/${encodeURIComponent(market)}`);

export const downloadInputSheetUrl = (runId: string) => `${API_BASE}/api/download/input-sheet/${runId}`;
export const downloadMeWorkbookUrl = (runId: string) => `${API_BASE}/api/download/me-workbook/${runId}`;
export const downloadInputJsonUrl = (runId: string) => `${API_BASE}/api/download/input-json/${runId}`;
export const downloadMeJsonUrl = (runId: string) => `${API_BASE}/api/download/me-json/${runId}`;

// Re-download a past run's file straight from the History tab — that run may
// no longer be in the backend's in-memory cache (e.g. after a server
// restart), so this fetches by the file's on-disk path instead of run_id.
export const downloadByPathUrl = (path: string) => `${API_BASE}/api/download/file?path=${encodeURIComponent(path)}`;

// --------------------------------------------------------------------------- #
// Demand-Side (Usage-Based) three-phase pipeline
// --------------------------------------------------------------------------- #
export async function generateSetupDraft(market: string): Promise<{ run_id: string; draft: SetupDraft }> {
  const res = await fetch(`${API_BASE}/api/demand-usage/setup/generate?${new URLSearchParams({ market })}`);
  if (!res.ok) throw new Error(`setup/generate failed: ${res.status}`);
  return res.json();
}

export const approveSetup = (runId: string, draft: SetupDraft) =>
  postJson<SetupDraft & { approved: boolean }>(`/api/demand-usage/setup/${runId}/approve`, draft);

export async function generateTaxonomyDraft(runId: string): Promise<{ run_id: string; draft: TaxonomyDraft }> {
  const res = await fetch(`${API_BASE}/api/demand-usage/taxonomy/generate?${new URLSearchParams({ run_id: runId })}`);
  if (!res.ok) throw new Error(`taxonomy/generate failed: ${res.status}`);
  return res.json();
}

export const approveTaxonomy = (runId: string, draft: TaxonomyDraft) =>
  postJson<TaxonomyDraft & { approved: boolean }>(`/api/demand-usage/taxonomy/${runId}/approve`, draft);

export function sizeEventUrl(runId: string) {
  return `${API_BASE}/api/demand-usage/size/generate?${new URLSearchParams({ run_id: runId })}`;
}

export const downloadDemandUsageUrl = (runId: string, fmt: "json" | "xlsx") =>
  `${API_BASE}/api/demand-usage/download/${runId}/${fmt}`;

// --------------------------------------------------------------------------- #
// Demand-Side (Usage-Based) -- India-only variant (same shape, new prefix)
// --------------------------------------------------------------------------- #
export async function generateIndiaSetupDraft(market: string): Promise<{ run_id: string; draft: SetupDraft }> {
  const res = await fetch(`${API_BASE}/api/demand-usage-india/setup/generate?${new URLSearchParams({ market })}`);
  if (!res.ok) throw new Error(`setup/generate (India) failed: ${res.status}`);
  return res.json();
}

export const approveIndiaSetup = (runId: string, draft: SetupDraft) =>
  postJson<SetupDraft & { approved: boolean }>(`/api/demand-usage-india/setup/${runId}/approve`, draft);

export async function generateIndiaTaxonomyDraft(runId: string): Promise<{ run_id: string; draft: IndiaTaxonomyDraft }> {
  const res = await fetch(`${API_BASE}/api/demand-usage-india/taxonomy/generate?${new URLSearchParams({ run_id: runId })}`);
  if (!res.ok) throw new Error(`taxonomy/generate (India) failed: ${res.status}`);
  return res.json();
}

export const approveIndiaTaxonomy = (runId: string, draft: IndiaTaxonomyDraft) =>
  postJson<IndiaTaxonomyDraft & { approved: boolean }>(`/api/demand-usage-india/taxonomy/${runId}/approve`, draft);

export function indiaSizeEventUrl(runId: string) {
  return `${API_BASE}/api/demand-usage-india/size/generate?${new URLSearchParams({ run_id: runId })}`;
}

export const downloadDemandIndiaUrl = (runId: string, fmt: "json" | "xlsx") =>
  `${API_BASE}/api/demand-usage-india/download/${runId}/${fmt}`;

// --------------------------------------------------------------------------- #
// Research Audit -- audit trail introspection for the Usage-Based pipeline
// --------------------------------------------------------------------------- #
export const listAuditRuns = (approach = "usage_based") =>
  getJson<AuditRunSummary[]>(`/api/audit/runs?${new URLSearchParams({ approach })}`);

export const getAuditRunDetail = (market: string, runId: string) =>
  getJson<AuditRunDetail>(`/api/audit/runs/${encodeURIComponent(market)}/${encodeURIComponent(runId)}`);

export interface AuditEventFilters {
  stage?: string;
  event_type?: string;
  country?: string;
  bucket?: string;
  status?: string;
  limit?: number;
  offset?: number;
}

export function getAuditEvents(market: string, runId: string, filters: AuditEventFilters = {}) {
  const params = new URLSearchParams();
  if (filters.stage) params.set("stage", filters.stage);
  if (filters.event_type) params.set("event_type", filters.event_type);
  if (filters.country) params.set("country", filters.country);
  if (filters.bucket) params.set("bucket", filters.bucket);
  if (filters.status) params.set("status", filters.status);
  params.set("limit", String(filters.limit ?? 100));
  params.set("offset", String(filters.offset ?? 0));
  return getJson<AuditEventsResponse>(
    `/api/audit/runs/${encodeURIComponent(market)}/${encodeURIComponent(runId)}/events?${params.toString()}`
  );
}

export const getAuditTimeline = (market: string, runId: string) =>
  getJson<AuditTimeline>(`/api/audit/runs/${encodeURIComponent(market)}/${encodeURIComponent(runId)}/timeline`);

export const getAuditLineage = (market: string, runId: string, outputId: string) =>
  getJson<AuditLineageResponse>(
    `/api/audit/runs/${encodeURIComponent(market)}/${encodeURIComponent(runId)}/lineage/${encodeURIComponent(outputId)}`
  );

// --------------------------------------------------------------------------- #
// Segmentation Demo -- standalone, one-shot LLM-fetched illustration of a
// Product/Source/Flavor/Application/Distribution/Sales-Channel x
// Region/Country breakdown. Not a checkpointed pipeline: one GET call.
// --------------------------------------------------------------------------- #
export const generateSegmentationDemo = (market: string) =>
  getJson<SegmentationDemoResult>(`/api/segmentation-demo/generate?${new URLSearchParams({ market })}`);

/** Official UN Comtrade customs statistics -- the source-backed check against
 * the LLM's own price estimates. */
/** Deterministic demand build-up -- returns the full driver chain for one
 * leaf, with no LLM-supplied magnitude anywhere in it. */
export const calculateLeaf = (body: {
  country: string;
  product: string;
  application: string;
  affinity: Record<string, Record<string, number>>;
}) => postJson<CalcResult>("/api/segmentation-demo/calculate", body);

export const getTradeEvidence = (country: string, year = 2023) =>
  getJson<TradeEvidence>(`/api/segmentation-demo/trade-evidence?${new URLSearchParams({ country, year: String(year) })}`);

/** Apparent consumption for any market/country + the Product -> Application
 * tree beneath it. Trade is real customs data; production is an estimate. */
export const getApparentConsumption = (market: string, country: string, year = 2023) =>
  getJson<ApparentConsumption>(
    `/api/segmentation-demo/apparent-consumption?${new URLSearchParams({ market, country, year: String(year) })}`
  );

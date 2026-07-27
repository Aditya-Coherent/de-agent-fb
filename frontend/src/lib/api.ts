import type { Config, HistoryEntry, MEResult, CompareReport } from "./types";

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

export const downloadInputSheetUrl = (runId: string) => `${API_BASE}/api/download/input-sheet/${runId}`;
export const downloadMeWorkbookUrl = (runId: string) => `${API_BASE}/api/download/me-workbook/${runId}`;

// Re-download a past run's file straight from the History tab — that run may
// no longer be in the backend's in-memory cache (e.g. after a server
// restart), so this fetches by the file's on-disk path instead of run_id.
export const downloadByPathUrl = (path: string) => `${API_BASE}/api/download/file?path=${encodeURIComponent(path)}`;

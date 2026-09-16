"use client";

// Shared, dependency-free presentation helpers used across every Research
// Audit panel. Deliberately tiny -- no chart/gauge libraries, matches the
// dark/information-dense aesthetic already established by StatusBadge.tsx
// and page.tsx.

import type { ReactNode } from "react";
import { AlertTriangle } from "lucide-react";
import type { AuditEvent } from "@/lib/types";

export function NotCaptured({ reason }: { reason?: string }) {
  return (
    <span className="text-xs italic text-neutral-600">
      Not captured{reason ? ` — ${reason}` : ""}
    </span>
  );
}

export function Field({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <div className="text-xs font-medium uppercase tracking-wider text-neutral-500">{label}</div>
      <div className="mt-0.5 text-sm text-neutral-200">{value ?? <NotCaptured />}</div>
    </div>
  );
}

export function PrettyBlock({ title, value }: { title: string; value: unknown }) {
  if (value == null) {
    return (
      <div>
        <div className="text-xs font-medium uppercase tracking-wider text-neutral-500">{title}</div>
        <NotCaptured />
      </div>
    );
  }
  return (
    <div className="space-y-1">
      <div className="text-xs font-medium uppercase tracking-wider text-neutral-500">{title}</div>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg border border-white/5 bg-neutral-950 p-2.5 text-xs text-neutral-300">
        {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}

export function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-rose-900/50 bg-rose-950/40 p-3 text-sm text-rose-300">
      <AlertTriangle size={15} /> {message}
    </div>
  );
}

export function GapNote({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-amber-900/40 bg-amber-950/20 p-2.5 text-xs text-amber-300">
      <AlertTriangle size={13} className="mt-0.5 shrink-0" />
      <span>{children}</span>
    </div>
  );
}

export function fmtTime(ts?: string | null) {
  return ts ? ts.slice(0, 19).replace("T", " ") : null;
}

export function statusDotClass(status?: string | null) {
  if (status === "success" || status === "pass" || status === "accepted") return "bg-emerald-400";
  if (status === "rejected" || status === "fail" || status === "failed") return "bg-rose-400";
  return "bg-neutral-500";
}

// -- Provenance / verification-status treatment ---------------------------- //
// Section 6 requirement: assumptions must look OBVIOUSLY different, never
// blended in with observed data -- a bordered warning treatment, not just a
// different text color.
export function provenanceClass(status?: string | null) {
  switch (status) {
    case "verified_observed":
      return "border-emerald-800/60 bg-emerald-950/30 text-emerald-300";
    case "derived":
      return "border-sky-800/60 bg-sky-950/30 text-sky-300";
    case "source_found_unverified":
      return "border-amber-800/60 bg-amber-950/30 text-amber-300";
    case "assumption":
      return "border-2 border-dashed border-rose-600/70 bg-rose-950/40 text-rose-200";
    default:
      return "border-white/10 bg-neutral-900/60 text-neutral-400";
  }
}

export function provenanceLabel(status?: string | null) {
  switch (status) {
    case "verified_observed":
      return "Verified observed";
    case "derived":
      return "Derived";
    case "source_found_unverified":
      return "Source found, unverified";
    case "assumption":
      return "ASSUMPTION — not evidence-backed";
    default:
      return "Not captured";
  }
}

// -- Attempt-narrative helpers ---------------------------------------------- //
// One real audit event can fulfil more than one "narrative beat" from the
// user's spec (section 19): a `source_attempt` with status "success" IS both
// "search result found" and "source selected" in one event -- we never wait
// for a literal event_type that doesn't exist in the real schema.
export interface AttemptStep {
  seq: number;
  event: AuditEvent;
  kind: "query" | "source" | "llm";
  label: string;
}

export function buildAttemptNarrative(
  events: AuditEvent[],
  filter: { bucket?: string | null; country?: string | null },
): AttemptStep[] {
  const steps: AttemptStep[] = [];
  for (const e of events) {
    if (filter.bucket !== undefined && (e.bucket ?? null) !== (filter.bucket ?? null)) continue;
    if (filter.country !== undefined && (e.country ?? null) !== (filter.country ?? null)) continue;
    if (e.event_type === "search_query") {
      const q = (e.query as { search_query?: string } | null)?.search_query ?? "unknown query";
      steps.push({ seq: e.seq ?? 0, event: e, kind: "query", label: `Searched: "${q}"` });
    } else if (e.event_type === "source_attempt") {
      const src = (e.source as { name?: string } | null)?.name ?? "unnamed source";
      const verdict = e.status === "success" ? "found & accepted" : e.status === "rejected" ? "rejected" : e.status ?? "attempted";
      steps.push({ seq: e.seq ?? 0, event: e, kind: "source", label: `${src} — ${verdict}${e.error ? ` (${e.error})` : ""}` });
    } else if (e.event_type === "llm_call") {
      steps.push({ seq: e.seq ?? 0, event: e, kind: "llm", label: "LLM call" });
    }
  }
  return steps.sort((a, b) => a.seq - b.seq);
}

export function groupBy<T>(items: T[], keyFn: (item: T) => string): Record<string, T[]> {
  const out: Record<string, T[]> = {};
  for (const item of items) {
    const k = keyFn(item);
    (out[k] ??= []).push(item);
  }
  return out;
}

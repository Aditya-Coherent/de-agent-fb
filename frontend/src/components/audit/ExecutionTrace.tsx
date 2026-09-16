"use client";

import { useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  Circle,
  Radio,
} from "lucide-react";
import type { AuditEvent } from "@/lib/types";
import {
  NotCaptured,
  PrettyBlock,
  Field,
  GapNote,
  fmtTime,
  statusDotClass,
  buildAttemptNarrative,
  groupBy,
} from "./shared";

// -- The 16-step Execution Trace ------------------------------------------- //
// Each step's `stages`/`eventTypes` are the REAL values that appear in
// `AuditEvent.stage` / `AuditEvent.event_type` today (confirmed against
// `output/demand_usage/global-bottled-water-market/audit_log.jsonl`).
// A step with none of its stages/eventTypes present in this run's events
// renders an honest "not captured for this run" state -- never a fabricated
// example.
interface StepDef {
  key: string;
  label: string;
  stages: string[];
  eventTypes: string[];
  gapNote?: string;
}

const STEPS: StepDef[] = [
  { key: "input", label: "1. Input", stages: ["setup"], eventTypes: [],
    gapNote: "00_setup_draft.json / 00_setup_approved.json (scope, geography, base_year, market_unit, physical_unit) are not exposed via any /api/audit route. Only a stage=\"setup\" event (new instrumentation) can reconstruct this for a completed run — absent for this run." },
  { key: "market_understanding", label: "2. Market Understanding", stages: ["setup"], eventTypes: ["llm_call"],
    gapNote: "Requires a stage=\"setup\" llm_call event. Not present in this run's audit log." },
  { key: "taxonomy", label: "3. Taxonomy", stages: ["taxonomy"], eventTypes: ["taxonomy_generated"],
    gapNote: "Taxonomy generation (Phase 2) is a synchronous request/response endpoint, not a LangGraph node — no taxonomy_generated event exists in this run's log." },
  { key: "taxonomy_approval", label: "4. Taxonomy Approval", stages: ["taxonomy_approval"], eventTypes: ["scope_override"],
    gapNote: "No scope_override event recorded for this run (taxonomy approval happens before the instrumented sizing run and is not itself an audit-emitting step)." },
  { key: "evidence_strategy", label: "5. Evidence Strategy", stages: ["evidence_search"], eventTypes: [],
    gapNote: "The per-driver evidence_strategy object (preferred sources, query templates) lives in the Taxonomy artifact, not in an audit event. The strategy actually EXECUTED is visible as the literal text inside each llm_call prompt below (\"Approved metric to find: ...\") and as the resulting search_query events in step 6 — shown there instead of fabricating a separate strategy event." },
  { key: "evidence_search", label: "6. Evidence Search", stages: ["evidence_search"], eventTypes: ["search_query"] },
  { key: "sources", label: "7. Sources", stages: ["evidence_search"], eventTypes: ["source_attempt"] },
  { key: "content_acquisition", label: "8. Content Acquisition", stages: ["evidence_search"], eventTypes: ["source_attempt"],
    gapNote: "Drawn from source_attempt.raw_evidence / data.found — in this run raw_evidence is null on every attempt seen so far (no successful fetch yet), so this step shows the acquisition data.* fields instead of a raw content excerpt." },
  { key: "llm_extraction", label: "9. LLM Extraction", stages: ["evidence_search", "fact_extraction"], eventTypes: ["llm_call"] },
  { key: "validation", label: "10. Deterministic Fact Validation", stages: [], eventTypes: ["fact_validation"],
    gapNote: "No fact_validation event has been recorded for this run — see the Validation tab for the deterministic check semantics (from fact_validator.py) and why validator-level rejections (geography/year/unit/monetary/metric-family) are distinct from the acquisition-layer rejections (no_source_found, extraction_failed) actually observed in step 7." },
  { key: "classification", label: "11. Observed / Derived / Assumption", stages: [], eventTypes: [],
    gapNote: "No per-fact verification_status is attached to a dedicated event type; the only real signal at this stage is the aggregate progress.evidence_quality histogram (see below) and, for facts with a recorded derivation, the verification_status carried on lineage nodes (Number Lineage tab)." },
  { key: "derivation", label: "12. Derivation", stages: [], eventTypes: ["derivation"] },
  { key: "quantity", label: "13. Quantity", stages: ["demand_quantification"], eventTypes: [] },
  { key: "price", label: "14. Price", stages: ["pricing"], eventTypes: [] },
  { key: "magnitude_validation", label: "15. Magnitude Validation", stages: ["driver_magnitude_validation"], eventTypes: ["magnitude_check"] },
  { key: "triangulation", label: "16. Triangulation / Cross-Source Validation", stages: ["triangulation", "magnitude_cross_validation"], eventTypes: [] },
  { key: "final_result", label: "17. Final Result", stages: ["validation"], eventTypes: [] },
];

function matches(e: AuditEvent, step: StepDef): boolean {
  const stageMatch = step.stages.length > 0 && step.stages.includes(e.stage ?? "");
  const typeMatch = step.eventTypes.length > 0 && step.eventTypes.includes(e.event_type ?? "");
  return stageMatch || typeMatch;
}

function StepIcon({ count }: { count: number }) {
  if (count > 0) return <CheckCircle2 size={16} className="text-emerald-400" />;
  return <Circle size={16} className="text-neutral-600" />;
}

function LlmEventCard({ event }: { event: AuditEvent }) {
  return (
    <div className="space-y-2 rounded-lg border border-indigo-900/40 bg-indigo-950/20 p-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wider text-indigo-300">LLM call — seq {event.seq}</span>
        <span className="text-xs text-neutral-500">{fmtTime(event.timestamp)}</span>
      </div>
      <Field label="Model" value={event.llm?.model} />
      <PrettyBlock title="Prompt (full, real text)" value={event.llm?.prompt} />
      <PrettyBlock title="Response (full, real text)" value={event.llm?.response} />
      {event.llm?.error != null && <PrettyBlock title="LLM error" value={event.llm.error} />}
    </div>
  );
}

function SourceEventCard({ event }: { event: AuditEvent }) {
  const src = (event.source as { name?: string; url?: string | null; type?: string } | null) ?? null;
  return (
    <div
      className={`space-y-2 rounded-lg border p-3 ${
        event.status === "success"
          ? "border-emerald-900/50 bg-emerald-950/20"
          : event.status === "rejected"
            ? "border-rose-900/50 bg-rose-950/20"
            : "border-white/10 bg-neutral-900/40"
      }`}
    >
      <div className="flex items-center justify-between text-xs">
        <span className="flex items-center gap-1.5 font-semibold uppercase tracking-wider text-neutral-400">
          <span className={`h-2 w-2 rounded-full ${statusDotClass(event.status)}`} /> seq {event.seq} — {event.status ?? "attempted"}
        </span>
        <span className="text-neutral-500">{fmtTime(event.timestamp)}</span>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Field label="Source" value={src?.name} />
        <Field label="Type" value={src?.type} />
        <Field label="URL" value={src?.url ? <a href={src.url} target="_blank" rel="noreferrer" className="text-indigo-400 hover:text-indigo-300">{src.url}</a> : null} />
      </div>
      <Field label="Query executed" value={(event.query as { search_query?: string } | null)?.search_query} />
      {event.error && <Field label="Rejection reason (exact)" value={<span className="font-mono text-rose-300">{event.error}</span>} />}
      <PrettyBlock title="Acquisition data" value={event.data} />
    </div>
  );
}

function AttemptTimeline({ events, bucket, country }: { events: AuditEvent[]; bucket: string | null; country: string | null }) {
  const steps = useMemo(
    () => buildAttemptNarrative(events, { bucket, country }),
    [events, bucket, country],
  );
  if (steps.length === 0) return <NotCaptured reason="no matching events for this bucket/country" />;
  return (
    <div className="space-y-2">
      {steps.map((s, i) => (
        <div key={`${s.event.event_id}-${i}`} className="flex gap-3">
          <div className="flex w-6 shrink-0 flex-col items-center pt-1">
            <span className="text-[10px] text-neutral-600">#{i + 1}</span>
            {i < steps.length - 1 && <div className="mt-1 h-full w-px bg-white/10" />}
          </div>
          <div className="flex-1 pb-2">
            {s.kind === "llm" ? (
              <LlmEventCard event={s.event} />
            ) : s.kind === "source" ? (
              <SourceEventCard event={s.event} />
            ) : (
              <div className="rounded-lg border border-white/10 bg-neutral-900/40 p-2.5 text-sm text-neutral-300">
                {s.label}
                <span className="ml-2 text-xs text-neutral-500">seq {s.event.seq} · {fmtTime(s.event.timestamp)}</span>
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function BucketCountryDrilldown({ events, step }: { events: AuditEvent[]; step: StepDef }) {
  const matched = useMemo(() => events.filter((e) => matches(e, step)), [events, step]);
  const byBucket = useMemo(() => groupBy(matched, (e) => e.bucket ?? "(no bucket)"), [matched]);
  const buckets = Object.keys(byBucket);
  const [bucket, setBucket] = useState<string | null>(buckets[0] ?? null);

  const countriesForBucket = useMemo(() => {
    if (!bucket) return [];
    const set = new Set<string>();
    for (const e of byBucket[bucket] ?? []) if (e.country) set.add(e.country);
    return Array.from(set).sort();
  }, [byBucket, bucket]);
  const [country, setCountry] = useState<string | null>(null);

  if (buckets.length === 0) {
    return step.gapNote ? <GapNote>{step.gapNote}</GapNote> : <NotCaptured reason="no events recorded for this step in this run" />;
  }

  const bucketKey = bucket === "(no bucket)" ? null : bucket;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        {buckets.map((b) => (
          <button
            key={b}
            onClick={() => { setBucket(b); setCountry(null); }}
            className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
              bucket === b ? "border-indigo-500/50 bg-indigo-500/10 text-indigo-200" : "border-white/10 text-neutral-400 hover:border-white/20"
            }`}
          >
            {b} ({byBucket[b].length})
          </button>
        ))}
      </div>
      {countriesForBucket.length > 0 && (
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => setCountry(null)}
            className={`rounded-md border px-2 py-0.5 text-xs transition-colors ${
              country === null ? "border-sky-500/50 bg-sky-500/10 text-sky-200" : "border-white/10 text-neutral-500 hover:border-white/20"
            }`}
          >
            All countries
          </button>
          {countriesForBucket.map((c) => (
            <button
              key={c}
              onClick={() => setCountry(c)}
              className={`rounded-md border px-2 py-0.5 text-xs transition-colors ${
                country === c ? "border-sky-500/50 bg-sky-500/10 text-sky-200" : "border-white/10 text-neutral-500 hover:border-white/20"
              }`}
            >
              {c}
            </button>
          ))}
        </div>
      )}
      <AttemptTimeline events={matched} bucket={bucketKey} country={country} />
    </div>
  );
}

export default function ExecutionTrace({
  events,
  progress,
}: {
  events: AuditEvent[];
  progress: Record<string, unknown> | { not_captured: true } | null | undefined;
}) {
  const [openStep, setOpenStep] = useState<string | null>("evidence_search");

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const step of STEPS) c[step.key] = events.filter((e) => matches(e, step)).length;
    return c;
  }, [events]);

  // Live Run: derive "current operation" from the most recent few events by
  // seq, not just the coarse progress.phase string (spec section: Live Run).
  const recent = useMemo(() => [...events].sort((a, b) => (b.seq ?? 0) - (a.seq ?? 0)).slice(0, 5), [events]);
  const latest = recent[0];
  const phase = progress && !("not_captured" in progress) ? String((progress as Record<string, unknown>).phase ?? "") : null;

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-sky-900/40 bg-sky-950/10 p-4">
        <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-sky-300">
          <Radio size={13} className="animate-pulse" /> Live operation (derived from the last 5 audit events)
        </div>
        {latest ? (
          <div className="mt-2 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
            <Field label="Stage" value={latest.stage} />
            <Field label="Bucket" value={latest.bucket} />
            <Field label="Country" value={latest.country} />
            <Field label="Most recent event" value={`${latest.event_type} (seq ${latest.seq})`} />
          </div>
        ) : (
          <NotCaptured reason="no events recorded yet" />
        )}
        <div className="mt-2 text-xs text-neutral-500">
          progress.phase (coarse, for comparison): <span className="font-mono text-neutral-400">{phase ?? "not captured"}</span>
        </div>
      </div>

      <div className="space-y-2">
        {STEPS.map((step) => {
          const isOpen = openStep === step.key;
          const count = counts[step.key] ?? 0;
          return (
            <div key={step.key} className="overflow-hidden rounded-xl border border-white/5 bg-neutral-900/40">
              <button
                onClick={() => setOpenStep(isOpen ? null : step.key)}
                className="flex w-full items-center justify-between gap-2 p-3 text-left hover:bg-neutral-800/40"
              >
                <span className="flex items-center gap-2">
                  {isOpen ? <ChevronDown size={14} className="text-neutral-500" /> : <ChevronRight size={14} className="text-neutral-500" />}
                  <StepIcon count={count} />
                  <span className="text-sm font-medium text-neutral-200">{step.label}</span>
                </span>
                <span className="text-xs text-neutral-500">{count > 0 ? `${count} event${count === 1 ? "" : "s"}` : "not captured"}</span>
              </button>
              {isOpen && (
                <div className="border-t border-white/5 p-3">
                  <BucketCountryDrilldown events={events} step={step} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

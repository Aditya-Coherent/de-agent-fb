"use client";

import { useMemo } from "react";
import type { AuditEvent } from "@/lib/types";
import { GapNote, PrettyBlock, Field, fmtTime } from "./shared";

// Taxonomy proposal -> reasoning -> validation flags -> overrides ->
// approval. Phase 2 (taxonomy generation/approval) runs as a synchronous
// request/response endpoint BEFORE the instrumented sizing graph starts, so
// today's real audit_log.jsonl for every run we inspected carries ZERO
// stage="taxonomy"/"taxonomy_approval" events and no "taxonomy_generated"/
// "scope_override" event_type. This panel renders that honestly rather than
// fabricating a taxonomy view — but still surfaces any such events if a
// FUTURE run instruments them (forward-compatible, never assumes their
// absence is permanent).
export default function TaxonomyExplorer({ events }: { events: AuditEvent[] }) {
  const taxonomyEvents = useMemo(
    () => events.filter((e) => e.stage === "taxonomy" || e.stage === "taxonomy_approval" || e.event_type === "taxonomy_generated" || e.event_type === "scope_override"),
    [events],
  );

  if (taxonomyEvents.length === 0) {
    return (
      <div className="space-y-3 rounded-xl border border-white/5 bg-neutral-900/40 p-4">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-500">Taxonomy explorer</h3>
        <GapNote>
          Not captured by this run's audit log. Taxonomy generation and approval (Phase 2 —
          <code className="mx-1 rounded bg-black/30 px-1">/api/demand-usage/taxonomy/generate</code>
          and
          <code className="mx-1 rounded bg-black/30 px-1">/api/demand-usage/taxonomy/{"{run_id}"}/approve</code>)
          are plain request/response endpoints that run before the instrumented sizing graph starts — no
          stage="taxonomy" or stage="taxonomy_approval" audit event, and no "taxonomy_generated" /
          "scope_override" event_type, exists in this run's audit_log.jsonl. The per-bucket proposal, its
          reasoning, scope_validation_flags, and any scope_overrides are only available for a run that is
          CURRENTLY in the taxonomy wizard step (via the in-memory TaxonomyDraft returned by that endpoint),
          not for a completed/older run read back through /api/audit/*. This is a genuine backend-exposure
          gap, not a frontend limitation — flagged rather than worked around.
        </GapNote>
      </div>
    );
  }

  return (
    <div className="space-y-3 rounded-xl border border-white/5 bg-neutral-900/40 p-4">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-500">Taxonomy explorer</h3>
      <div className="space-y-2">
        {taxonomyEvents.map((e) => (
          <div key={e.event_id} className="space-y-2 rounded-lg border border-white/10 bg-neutral-950/60 p-3">
            <div className="flex items-center justify-between text-xs text-neutral-500">
              <span>{e.event_type} · seq {e.seq}</span>
              <span>{fmtTime(e.timestamp)}</span>
            </div>
            <Field label="Bucket" value={e.bucket} />
            {e.event_type === "taxonomy_generated" && <PrettyBlock title="Taxonomy data (real, as captured — no invented reasoning field)" value={e.data} />}
            {e.event_type === "scope_override" && <PrettyBlock title="Scope override" value={e.data} />}
            {e.llm && (
              <>
                <PrettyBlock title="LLM prompt (full, real text — the actual reasoning source when no separate rationale field exists)" value={e.llm.prompt} />
                <PrettyBlock title="LLM response (full, real text)" value={e.llm.response} />
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

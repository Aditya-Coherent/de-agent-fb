"use client";

import { useState } from "react";
import { Loader2, AlertTriangle, Search } from "lucide-react";
import type { AuditLineageNode, AuditEvent } from "@/lib/types";
import { getAuditLineage } from "@/lib/api";
import { NotCaptured, provenanceClass, provenanceLabel, fmtTime, statusDotClass } from "./shared";

const inputCls =
  "w-full rounded-lg border border-white/10 bg-neutral-950 p-2.5 text-sm text-neutral-100 outline-none placeholder:text-neutral-600 focus:border-indigo-500/50";
const btnPrimary =
  "flex items-center gap-2 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-600 px-4 py-2.5 text-sm font-medium text-white shadow-lg shadow-indigo-500/20 transition-all hover:shadow-indigo-500/30 disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none";

function EvidenceEventInline({ event }: { event: AuditEvent | null | undefined }) {
  if (!event) {
    return <p className="text-xs text-neutral-600">No matching evidence event found — this fact's source cannot be traced to a specific audit event.</p>;
  }
  return (
    <details className="rounded-lg border border-white/5 bg-neutral-950 p-2.5">
      <summary className="cursor-pointer text-xs text-neutral-500">Evidence event ({event.event_type ?? "unknown"})</summary>
      <pre className="mt-2 max-h-64 overflow-auto text-xs text-neutral-500">{JSON.stringify(event, null, 2)}</pre>
    </details>
  );
}

// Section 6 requirement: show REJECTED sibling attempts (same bucket/
// geography) ALONGSIDE the accepted lineage leaf — a rejected source_attempt
// carries no output_id of its own, so this is matched by bucket+country
// only (best-effort, same discipline as the backend's own
// `_find_source_event`); absence of siblings is common and shown honestly.
function RejectedSiblings({ events, bucket, country }: { events: AuditEvent[]; bucket?: string | null; country?: string | null }) {
  const siblings = events.filter(
    (e) =>
      e.event_type === "source_attempt" &&
      e.status === "rejected" &&
      (bucket == null || e.bucket === bucket) &&
      (country == null || e.country === country || e.country == null),
  );
  if (siblings.length === 0) return null;
  return (
    <div className="space-y-1.5 rounded-lg border border-rose-900/30 bg-rose-950/10 p-2.5">
      <div className="text-xs font-medium uppercase tracking-wider text-rose-400">
        Rejected sibling attempts for this same requirement ({siblings.length})
      </div>
      {siblings.map((s) => (
        <div key={s.event_id} className="flex items-center gap-2 text-xs text-neutral-400">
          <span className={`h-1.5 w-1.5 rounded-full ${statusDotClass(s.status)}`} />
          <span>{(s.source as { name?: string } | null)?.name ?? "unnamed source"}</span>
          <span className="text-rose-400">{s.error ?? "rejected"}</span>
          <span className="text-neutral-600">seq {s.seq} · {fmtTime(s.timestamp)}</span>
        </div>
      ))}
    </div>
  );
}

function LineageNodeView({ node, events, depth = 0 }: { node: AuditLineageNode; events: AuditEvent[]; depth?: number }) {
  const isAssumption = node.verification_status === "assumption";
  return (
    <div
      className={`space-y-2 rounded-lg p-3 ${depth > 0 ? "ml-4" : ""} ${
        isAssumption ? "border-2 border-dashed border-rose-600/70 bg-rose-950/20" : "border border-white/5 bg-neutral-900/40"
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-neutral-100">{node.name}</span>
        <span className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium ${provenanceClass(node.verification_status)}`}>
          {provenanceLabel(node.verification_status)}
        </span>
        {isAssumption && (
          <span className="text-xs font-semibold text-rose-300">⚠ Not evidence-backed — treat as a modeling assumption, not observed fact</span>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div>
          <div className="text-xs font-medium uppercase tracking-wider text-neutral-500">Value</div>
          <div className="text-sm text-neutral-200">{node.value != null ? String(node.value) : <NotCaptured />}</div>
        </div>
        <div>
          <div className="text-xs font-medium uppercase tracking-wider text-neutral-500">Unit</div>
          <div className="text-sm text-neutral-200">{node.unit ?? <NotCaptured />}</div>
        </div>
        <div>
          <div className="text-xs font-medium uppercase tracking-wider text-neutral-500">Geography</div>
          <div className="text-sm text-neutral-200">{node.geography ?? <NotCaptured />}</div>
        </div>
        <div>
          <div className="text-xs font-medium uppercase tracking-wider text-neutral-500">Source</div>
          <div className="truncate text-sm text-neutral-200">
            {node.source_url ? (
              <a href={node.source_url} target="_blank" rel="noreferrer" className="text-indigo-400 hover:text-indigo-300">
                {node.source ?? node.source_url}
              </a>
            ) : (
              node.source ?? <NotCaptured />
            )}
          </div>
        </div>
      </div>

      {(node.derivation_method || node.formula) && (
        <div className="space-y-1 rounded-lg border border-white/5 bg-neutral-950/60 p-2.5">
          <div className="text-xs font-medium uppercase tracking-wider text-neutral-500">Derivation</div>
          {node.derivation_method && <div className="text-sm text-neutral-300">Method: {node.derivation_method}</div>}
          {node.formula && <div className="font-mono text-xs text-neutral-400">{node.formula}</div>}
        </div>
      )}

      {node.is_leaf ? (
        <>
          <EvidenceEventInline event={node.evidence_event} />
          <RejectedSiblings events={events} bucket={node.evidence_event?.bucket} country={node.geography} />
        </>
      ) : (
        node.inputs && node.inputs.length > 0 && (
          <div className="space-y-2">
            <div className="text-xs font-medium uppercase tracking-wider text-neutral-500">Inputs</div>
            {node.inputs.map((input, i) => (
              <LineageNodeView key={`${input.name}-${i}`} node={input} events={events} depth={depth + 1} />
            ))}
          </div>
        )
      )}
    </div>
  );
}

export default function NumberLineage({ market, runId, events }: { market: string; runId: string; events: AuditEvent[] }) {
  const [outputId, setOutputId] = useState("");
  const [lineage, setLineage] = useState<AuditLineageNode | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);

  const handleLookup = () => {
    if (!outputId.trim()) return;
    setLoading(true);
    setError(null);
    setNotFound(false);
    setLineage(null);
    getAuditLineage(market, runId, outputId.trim())
      .then((res) => setLineage(res.lineage))
      .catch((e) => {
        const msg = e instanceof Error ? e.message : "Failed to load lineage";
        if (msg.includes("404")) setNotFound(true);
        else setError(msg);
      })
      .finally(() => setLoading(false));
  };

  const derivationOutputIds = Array.from(
    new Set(
      events
        .filter((e) => e.event_type === "derivation")
        .map((e) => (e.derivation as { output_id?: string } | null)?.output_id)
        .filter((id): id is string => !!id),
    ),
  );

  return (
    <div className="space-y-3 rounded-xl border border-white/5 bg-neutral-900/40 p-4 backdrop-blur-sm">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-500">Number lineage</h3>
      <p className="text-xs text-neutral-600">
        Trace a market-sizing output back to its source evidence, including rejected sibling attempts for the
        same requirement. Output IDs come from <span className="font-mono">event.derivation.output_id</span> on
        a derivation event.
      </p>

      {derivationOutputIds.length === 0 ? (
        <div className="rounded-lg border border-amber-900/40 bg-amber-950/20 p-2.5 text-xs text-amber-300">
          No derivation events recorded in this run yet — there is no known output_id to look up. Enter one
          manually if you have it from another source, or check back once the pipeline has produced at least
          one derived quantity.
        </div>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {derivationOutputIds.map((id) => (
            <button
              key={id}
              onClick={() => setOutputId(id)}
              className="rounded-md border border-white/10 bg-neutral-950/60 px-2 py-0.5 text-xs text-neutral-400 hover:border-white/20"
            >
              {id}
            </button>
          ))}
        </div>
      )}

      <div className="flex gap-2">
        <input
          className={inputCls}
          placeholder="output_id"
          value={outputId}
          onChange={(e) => setOutputId(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleLookup()}
        />
        <button onClick={handleLookup} disabled={loading || !outputId.trim()} className={btnPrimary}>
          {loading ? <Loader2 size={16} className="animate-spin" /> : <Search size={16} />}
          Trace
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-rose-900/50 bg-rose-950/40 p-3 text-sm text-rose-300">
          <AlertTriangle size={15} /> {error}
        </div>
      )}

      {notFound && (
        <p className="text-sm text-neutral-500">
          No lineage available for this output_id — no derivation event was recorded for it.
        </p>
      )}

      {lineage && <LineageNodeView node={lineage} events={events} />}
    </div>
  );
}

"use client";

import { useEffect, useState, Fragment } from "react";
import { Loader2, ChevronDown, ChevronRight } from "lucide-react";
import type { AuditEvent } from "@/lib/types";
import { getAuditEvents } from "@/lib/api";
import { NotCaptured, Field, PrettyBlock, ErrorBanner, fmtTime } from "./shared";

const inputCls =
  "w-full rounded-lg border border-white/10 bg-neutral-950 p-2 text-xs text-neutral-100 outline-none placeholder:text-neutral-600 focus:border-indigo-500/50";

// The full, unfiltered, paginated raw event feed — every field of every
// event, verbatim. This is the "trust but verify" escape hatch behind every
// friendlier panel (Execution Trace, Evidence, Taxonomy, Validation,
// Number Lineage): nothing rendered elsewhere should ever be un-traceable
// back to a row in this table.
function EventDetail({ event }: { event: AuditEvent }) {
  return (
    <div className="space-y-3 border-t border-white/5 bg-neutral-950/60 p-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Field label="Stage" value={event.stage} />
        <Field label="Event type" value={event.event_type} />
        <Field label="Country" value={event.country} />
        <Field label="Bucket" value={event.bucket} />
        <Field label="Status" value={event.status} />
        <Field label="Timestamp" value={event.timestamp} />
      </div>

      {event.error && <ErrorBanner message={event.error} />}

      {event.event_type === "llm_call" && (
        <div className="space-y-2 rounded-lg border border-indigo-900/40 bg-indigo-950/20 p-3">
          <div className="text-xs font-semibold uppercase tracking-wider text-indigo-300">LLM Call</div>
          <Field label="Model" value={event.llm?.model} />
          <PrettyBlock title="Prompt" value={event.llm?.prompt} />
          <PrettyBlock title="Response" value={event.llm?.response} />
        </div>
      )}

      {event.event_type === "source_attempt" && (
        <div className="space-y-2 rounded-lg border border-white/5 bg-neutral-900/40 p-3">
          <Field label="Query" value={typeof event.query === "object" ? JSON.stringify(event.query) : event.query} />
          <PrettyBlock title="Source" value={event.source} />
          <PrettyBlock title="Raw evidence" value={event.raw_evidence} />
        </div>
      )}

      {event.event_type === "fact_validation" && <PrettyBlock title="Validation" value={event.validation} />}
      {event.event_type === "derivation" && <PrettyBlock title="Derivation" value={event.derivation} />}
      {event.event_type === "magnitude_check" && <PrettyBlock title="Calculation" value={event.calculation} />}

      {event.input_data != null && <PrettyBlock title="Input data" value={event.input_data} />}
      {event.output_data != null && <PrettyBlock title="Output data" value={event.output_data} />}
      {event.data != null && <PrettyBlock title="Data" value={event.data} />}

      <details className="rounded-lg border border-white/5 bg-neutral-950 p-3">
        <summary className="cursor-pointer text-xs text-neutral-500">Raw event JSON (full auditability)</summary>
        <pre className="mt-2 max-h-96 overflow-auto text-xs text-neutral-500">{JSON.stringify(event, null, 2)}</pre>
      </details>
    </div>
  );
}

export default function RawEvents({
  market,
  runId,
  initialStage,
}: {
  market: string;
  runId: string;
  initialStage?: string | null;
}) {
  const [stage, setStage] = useState(initialStage ?? "");
  const [eventType, setEventType] = useState("");
  const [country, setCountry] = useState("");
  const [bucket, setBucket] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const limit = 25;

  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setStage(initialStage ?? "");
    setOffset(0);
  }, [initialStage]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    setError(null);
    getAuditEvents(market, runId, {
      stage: stage || undefined,
      event_type: eventType || undefined,
      country: country || undefined,
      bucket: bucket || undefined,
      status: status || undefined,
      limit,
      offset,
    })
      .then((res) => {
        setEvents(res.events);
        setTotal(res.total);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load events"))
      .finally(() => setLoading(false));
  }, [market, runId, stage, eventType, country, bucket, status, offset]);

  return (
    <div className="space-y-3 rounded-xl border border-white/5 bg-neutral-900/40 p-4 backdrop-blur-sm">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-500">Raw events</h3>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        <input className={inputCls} placeholder="Stage" value={stage} onChange={(e) => { setStage(e.target.value); setOffset(0); }} />
        <input className={inputCls} placeholder="Event type" value={eventType} onChange={(e) => { setEventType(e.target.value); setOffset(0); }} />
        <input className={inputCls} placeholder="Country" value={country} onChange={(e) => { setCountry(e.target.value); setOffset(0); }} />
        <input className={inputCls} placeholder="Bucket" value={bucket} onChange={(e) => { setBucket(e.target.value); setOffset(0); }} />
        <input className={inputCls} placeholder="Status" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }} />
      </div>

      {error && <ErrorBanner message={error} />}

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-neutral-500">
          <Loader2 size={14} className="animate-spin" /> Loading events…
        </div>
      ) : events.length === 0 ? (
        <p className="text-sm text-neutral-500">No events match the current filters.</p>
      ) : (
        <div className="overflow-hidden rounded-lg border border-white/5">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-neutral-600">
                <th className="p-2 font-medium" />
                <th className="p-2 font-medium">Seq</th>
                <th className="p-2 font-medium">Stage</th>
                <th className="p-2 font-medium">Event type</th>
                <th className="p-2 font-medium">Country</th>
                <th className="p-2 font-medium">Bucket</th>
                <th className="p-2 font-medium">Status</th>
                <th className="p-2 font-medium">Timestamp</th>
              </tr>
            </thead>
            <tbody>
              {events.map((ev) => {
                const isOpen = expanded === ev.event_id;
                return (
                  <Fragment key={ev.event_id}>
                    <tr
                      onClick={() => setExpanded(isOpen ? null : ev.event_id)}
                      className="cursor-pointer border-t border-white/5 hover:bg-neutral-800/40"
                    >
                      <td className="p-2 text-neutral-500">{isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</td>
                      <td className="p-2 text-neutral-500">{ev.seq ?? <NotCaptured />}</td>
                      <td className="p-2 text-neutral-300">{ev.stage ?? <NotCaptured />}</td>
                      <td className="p-2 text-neutral-300">{ev.event_type ?? <NotCaptured />}</td>
                      <td className="p-2 text-neutral-400">{ev.country ?? <NotCaptured />}</td>
                      <td className="p-2 text-neutral-400">{ev.bucket ?? <NotCaptured />}</td>
                      <td className="p-2 text-neutral-400">{ev.status ?? <NotCaptured />}</td>
                      <td className="p-2 whitespace-nowrap text-neutral-500">{fmtTime(ev.timestamp) ?? <NotCaptured />}</td>
                    </tr>
                    {isOpen && (
                      <tr>
                        <td colSpan={8} className="p-0">
                          <EventDetail event={ev} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex items-center justify-between text-xs text-neutral-500">
        <span>{total > 0 ? `${offset + 1}–${Math.min(offset + limit, total)} of ${total}` : "0 results"}</span>
        <div className="flex gap-2">
          <button
            onClick={() => setOffset((o) => Math.max(0, o - limit))}
            disabled={offset === 0}
            className="rounded-md border border-white/10 bg-neutral-900/60 px-2 py-1 disabled:opacity-40"
          >
            Prev
          </button>
          <button
            onClick={() => setOffset((o) => (o + limit < total ? o + limit : o))}
            disabled={offset + limit >= total}
            className="rounded-md border border-white/10 bg-neutral-900/60 px-2 py-1 disabled:opacity-40"
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}

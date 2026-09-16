"use client";

import { Fragment, useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { AuditEvent } from "@/lib/types";
import { NotCaptured, groupBy, buildAttemptNarrative, fmtTime, statusDotClass } from "./shared";

// Evidence, grouped by bucket -> country -- a scan-friendly complement to
// Execution Trace's step-by-step drilldown: one row per (bucket, country)
// cell showing how many queries were issued and how many sources were tried/
// rejected/accepted, expandable into the same chronological attempt
// narrative ("attempt 1 rejected, attempt 2 rejected, ...").
export default function Evidence({ events }: { events: AuditEvent[] }) {
  const evidenceEvents = useMemo(
    () => events.filter((e) => e.event_type === "search_query" || e.event_type === "source_attempt"),
    [events],
  );

  const cells = useMemo(() => {
    const byBucket = groupBy(evidenceEvents, (e) => e.bucket ?? "(no bucket)");
    const rows: { bucket: string; country: string; queries: number; attempts: number; success: number; rejected: number }[] = [];
    for (const [bucket, evs] of Object.entries(byBucket)) {
      const byCountry = groupBy(evs, (e) => e.country ?? "(no country)");
      for (const [country, cEvs] of Object.entries(byCountry)) {
        rows.push({
          bucket,
          country,
          queries: cEvs.filter((e) => e.event_type === "search_query").length,
          attempts: cEvs.filter((e) => e.event_type === "source_attempt").length,
          success: cEvs.filter((e) => e.event_type === "source_attempt" && e.status === "success").length,
          rejected: cEvs.filter((e) => e.event_type === "source_attempt" && e.status === "rejected").length,
        });
      }
    }
    return rows;
  }, [evidenceEvents]);

  const [openKey, setOpenKey] = useState<string | null>(null);

  if (cells.length === 0) {
    return (
      <div className="rounded-xl border border-white/5 bg-neutral-900/40 p-4">
        <NotCaptured reason="no search_query / source_attempt events recorded for this run" />
      </div>
    );
  }

  return (
    <div className="space-y-3 rounded-xl border border-white/5 bg-neutral-900/40 p-4">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-500">
        Evidence acquisition, by bucket &amp; country
      </h3>
      <div className="overflow-hidden rounded-lg border border-white/5">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wider text-neutral-600">
              <th className="p-2" />
              <th className="p-2 font-medium">Bucket</th>
              <th className="p-2 font-medium">Country</th>
              <th className="p-2 font-medium">Queries</th>
              <th className="p-2 font-medium">Source attempts</th>
              <th className="p-2 font-medium">Accepted</th>
              <th className="p-2 font-medium">Rejected</th>
            </tr>
          </thead>
          <tbody>
            {cells.map((row) => {
              const key = `${row.bucket}::${row.country}`;
              const isOpen = openKey === key;
              return (
                <Fragment key={key}>
                  <tr
                    onClick={() => setOpenKey(isOpen ? null : key)}
                    className="cursor-pointer border-t border-white/5 hover:bg-neutral-800/40"
                  >
                    <td className="p-2 text-neutral-500">{isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</td>
                    <td className="p-2 text-neutral-200">{row.bucket}</td>
                    <td className="p-2 text-neutral-300">{row.country}</td>
                    <td className="p-2 text-neutral-400">{row.queries}</td>
                    <td className="p-2 text-neutral-400">{row.attempts}</td>
                    <td className="p-2 text-emerald-400">{row.success}</td>
                    <td className="p-2 text-rose-400">{row.rejected}</td>
                  </tr>
                  {isOpen && (
                    <tr>
                      <td colSpan={7} className="border-t border-white/5 bg-neutral-950/60 p-3">
                        <CellNarrative
                          events={evidenceEvents}
                          bucket={row.bucket === "(no bucket)" ? null : row.bucket}
                          country={row.country === "(no country)" ? null : row.country}
                        />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function CellNarrative({ events, bucket, country }: { events: AuditEvent[]; bucket: string | null; country: string | null }) {
  const steps = useMemo(() => buildAttemptNarrative(events, { bucket, country }), [events, bucket, country]);
  if (steps.length === 0) return <NotCaptured />;
  return (
    <ol className="space-y-1.5 text-sm">
      {steps.map((s, i) => (
        <li key={`${s.event.event_id}-${i}`} className="flex items-start gap-2">
          <span className="mt-1 text-xs text-neutral-600">{i + 1}.</span>
          {s.kind === "source" && <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${statusDotClass(s.event.status)}`} />}
          <span className="text-neutral-300">
            {s.label}
            <span className="ml-2 text-xs text-neutral-600">seq {s.event.seq} · {fmtTime(s.event.timestamp)}</span>
          </span>
        </li>
      ))}
    </ol>
  );
}

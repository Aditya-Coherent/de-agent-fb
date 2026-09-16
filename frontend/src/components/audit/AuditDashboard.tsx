"use client";

import { useEffect, useState } from "react";
import { ShieldCheck, Loader2 } from "lucide-react";
import type { AuditRunDetail } from "@/lib/types";
import { getAuditRunDetail } from "@/lib/api";
import RunPicker from "./RunPicker";
import RunOverview from "./RunOverview";
import ExecutionTrace from "./ExecutionTrace";
import TaxonomyExplorer from "./TaxonomyExplorer";
import Evidence from "./Evidence";
import NumberLineage from "./NumberLineage";
import ValidationPanel from "./ValidationPanel";
import Comparison from "./Comparison";
import RawEvents from "./RawEvents";
import { GapNote, ErrorBanner } from "./shared";
import { useAuditEvents } from "./useAuditEvents";

type Section = "overview" | "trace" | "taxonomy" | "evidence" | "lineage" | "validation" | "comparison" | "raw";

// Execution Trace is the DEFAULT screen per the spec's information
// architecture — a step-by-step research audit explorer, not a status/
// totals monitoring dashboard.
const sections: { key: Section; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "trace", label: "Execution Trace" },
  { key: "taxonomy", label: "Taxonomy" },
  { key: "evidence", label: "Evidence" },
  { key: "lineage", label: "Number Lineage" },
  { key: "validation", label: "Validation" },
  { key: "comparison", label: "Comparison" },
  { key: "raw", label: "Raw Events" },
];

type Methodology = "usage_based" | "demand_side";

const methodologies: { key: Methodology; label: string }[] = [
  { key: "demand_side", label: "Demand Side (Desk Research)" },
  { key: "usage_based", label: "Usage-Based / Demand-Usage" },
];

export default function AuditDashboard() {
  const [methodology, setMethodology] = useState<Methodology>("usage_based");
  const [selected, setSelected] = useState<{ market: string; runId: string } | null>(null);
  const [section, setSection] = useState<Section>("trace");

  const [detail, setDetail] = useState<AuditRunDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  const { events, loading: eventsLoading, error: eventsError } = useAuditEvents(
    selected?.market ?? "",
    selected?.runId ?? "",
  );

  useEffect(() => {
    if (!selected) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setDetail(null);
      return;
    }
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDetailError(null);
    getAuditRunDetail(selected.market, selected.runId)
      .then(setDetail)
      .catch((e) => setDetailError(e instanceof Error ? e.message : "Failed to load run detail"));
  }, [selected]);

  const handleSelect = (market: string, runId: string) => {
    setSelected({ market, runId });
    setSection("trace");
  };

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <div className="flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-sky-500 to-indigo-600 shadow-lg shadow-sky-500/20">
            <ShieldCheck size={18} className="text-white" />
          </div>
          <h1 className="bg-gradient-to-r from-neutral-50 to-neutral-400 bg-clip-text text-2xl font-bold text-transparent">
            Research Audit
          </h1>
          <span className="text-sm font-medium text-neutral-500">Step-by-step research audit explorer</span>
        </div>
        <p className="max-w-2xl text-sm leading-relaxed text-neutral-500">
          Every rendered value here traces to a real API response field from a real, on-disk audit event —
          nothing is fabricated. Steps or fields the pipeline hasn't captured for a given run are shown as
          "Not captured" rather than invented.
        </p>
      </header>

      <div className="flex gap-2">
        {methodologies.map((m) => (
          <button
            key={m.key}
            onClick={() => {
              setMethodology(m.key);
              setSelected(null);
            }}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold uppercase tracking-wider transition-colors ${
              methodology === m.key ? "bg-indigo-500/20 text-indigo-300" : "text-neutral-500 hover:text-neutral-300"
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>

      {methodology === "demand_side" ? (
        <GapNote>
          Not yet instrumented — this audit framework currently only captures the Usage-Based pipeline. The
          Demand Side (Desk Research) pipeline (`/api/generate`) has no `me_engine.audit` emitter wired in, so
          there is no run list, no event log, and no lineage to show here.
        </GapNote>
      ) : (
        <>
          <RunPicker onSelect={handleSelect} selected={selected} />

          {!selected && <p className="text-sm text-neutral-500">Select a run above to open its execution trace.</p>}

          {selected && (
            <div className="space-y-4">
              <div className="flex flex-wrap gap-2">
                {sections.map((s) => (
                  <button
                    key={s.key}
                    onClick={() => setSection(s.key)}
                    className={`rounded-lg px-3 py-1.5 text-xs font-semibold uppercase tracking-wider transition-colors ${
                      section === s.key ? "bg-sky-500/20 text-sky-300" : "text-neutral-500 hover:text-neutral-300"
                    }`}
                  >
                    {s.label}
                  </button>
                ))}
              </div>

              {detailError && <ErrorBanner message={detailError} />}
              {eventsError && <ErrorBanner message={eventsError} />}

              {eventsLoading && events.length === 0 ? (
                <div className="flex items-center gap-2 rounded-xl border border-white/5 bg-neutral-900/40 p-4 text-sm text-neutral-500">
                  <Loader2 size={14} className="animate-spin" /> Loading audit events…
                </div>
              ) : (
                <>
                  {section === "overview" && <RunOverview market={selected.market} runId={selected.runId} />}
                  {section === "trace" && <ExecutionTrace events={events} progress={detail?.progress} />}
                  {section === "taxonomy" && <TaxonomyExplorer events={events} />}
                  {section === "evidence" && <Evidence events={events} />}
                  {section === "lineage" && <NumberLineage market={selected.market} runId={selected.runId} events={events} />}
                  {section === "validation" && <ValidationPanel events={events} />}
                  {section === "comparison" && <Comparison />}
                  {section === "raw" && <RawEvents market={selected.market} runId={selected.runId} />}
                </>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

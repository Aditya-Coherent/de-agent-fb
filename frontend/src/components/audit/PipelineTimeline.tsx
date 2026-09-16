"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, Circle, Loader2, AlertTriangle } from "lucide-react";
import type { AuditTimelineStage, AuditStageStatus } from "@/lib/types";
import { getAuditTimeline } from "@/lib/api";

const STAGE_LABELS: Record<string, string> = {
  setup: "Setup",
  taxonomy: "Taxonomy",
  taxonomy_approval: "Taxonomy Approval",
  demand_quantification: "Demand Quantification",
  pricing: "Pricing",
  calculate: "Calculate",
  driver_magnitude_validation: "Driver Magnitude Validation",
  triangulation: "Triangulation",
  magnitude_cross_validation: "Magnitude Cross-Validation",
  validation: "Validation",
};

function StageIcon({ status }: { status: AuditStageStatus }) {
  if (status === "done") return <CheckCircle2 size={16} className="text-emerald-400" />;
  if (status === "in_progress") return <span className="flex h-4 w-4 items-center justify-center"><span className="h-2.5 w-2.5 rounded-full bg-sky-400 animate-pulse" /></span>;
  return <Circle size={16} className="text-neutral-600" />;
}

export default function PipelineTimeline({
  market,
  runId,
  onSelectStage,
  activeStage,
}: {
  market: string;
  runId: string;
  onSelectStage?: (stage: string) => void;
  activeStage?: string | null;
}) {
  const [stages, setStages] = useState<AuditTimelineStage[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    setError(null);
    setStages(null);
    getAuditTimeline(market, runId)
      .then((res) => setStages(res.timeline))
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load timeline"))
      .finally(() => setLoading(false));
  }, [market, runId]);

  if (loading) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-white/5 bg-neutral-900/40 p-4 text-sm text-neutral-500">
        <Loader2 size={14} className="animate-spin" /> Loading timeline…
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-rose-900/50 bg-rose-950/40 p-3 text-sm text-rose-300">
        <AlertTriangle size={15} /> {error}
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-white/5 bg-neutral-900/40 p-4 backdrop-blur-sm">
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-neutral-500">Pipeline stages</h3>
      <div className="flex flex-wrap gap-2">
        {(stages ?? []).map((s, i) => (
          <button
            key={s.stage}
            onClick={() => onSelectStage?.(s.stage)}
            className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors ${
              activeStage === s.stage
                ? "border-indigo-500/50 bg-indigo-500/10 text-indigo-200"
                : "border-white/10 bg-neutral-950/60 text-neutral-300 hover:border-white/20"
            }`}
          >
            <StageIcon status={s.status} />
            <span>{STAGE_LABELS[s.stage] ?? s.stage}</span>
            {i < (stages?.length ?? 0) - 1 && <span className="ml-1 text-neutral-700">→</span>}
          </button>
        ))}
      </div>
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import { Loader2, AlertTriangle } from "lucide-react";
import type { AuditRunDetail, QualityFlag } from "@/lib/types";
import { getAuditRunDetail } from "@/lib/api";
import PipelineTimeline from "./PipelineTimeline";

function NotCaptured() {
  return <span className="text-xs text-neutral-600">Not captured</span>;
}

function Tile({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-white/5 bg-neutral-900/60 p-4 backdrop-blur-sm">
      <div className="text-xs font-medium uppercase tracking-wider text-neutral-500">{label}</div>
      <div className="mt-1.5 text-lg font-semibold text-neutral-100">{value}</div>
    </div>
  );
}

function Chips({ items }: { items: string[] }) {
  if (!items || items.length === 0) return <NotCaptured />;
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((it) => (
        <span key={it} className="rounded-md border border-white/10 bg-neutral-950/60 px-2 py-0.5 text-xs text-neutral-300">
          {it}
        </span>
      ))}
    </div>
  );
}

function CountBar({ title, counts }: { title: string; counts: Record<string, number> }) {
  const entries = Object.entries(counts || {});
  return (
    <div className="space-y-1.5">
      <div className="text-xs font-medium uppercase tracking-wider text-neutral-500">{title}</div>
      {entries.length === 0 ? (
        <NotCaptured />
      ) : (
        <div className="space-y-1">
          {entries.map(([k, v]) => (
            <div key={k} className="flex items-center justify-between text-sm">
              <span className="text-neutral-400">{k}</span>
              <span className="font-mono text-neutral-200">{v}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function RunOverview({ market, runId }: { market: string; runId: string }) {
  const [detail, setDetail] = useState<AuditRunDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    setError(null);
    setDetail(null);
    getAuditRunDetail(market, runId)
      .then(setDetail)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load run detail"))
      .finally(() => setLoading(false));
  }, [market, runId]);

  if (loading) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-white/5 bg-neutral-900/40 p-4 text-sm text-neutral-500">
        <Loader2 size={14} className="animate-spin" /> Loading run overview…
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

  if (!detail) return null;

  const totalsNotCaptured = !detail.totals || detail.totals.not_captured === true;
  const qualityFlags: QualityFlag[] = !totalsNotCaptured ? (detail.totals as { quality_flags?: QualityFlag[] | null }).quality_flags ?? [] : [];
  const finalSize = !totalsNotCaptured ? (detail.totals as { total_market_value_usd_mn?: number | null }).total_market_value_usd_mn : null;
  const relatedDemand = !totalsNotCaptured ? (detail.totals as { related_demand_value_usd_mn?: number | null }).related_demand_value_usd_mn : null;

  return (
    <div className="space-y-4">
      <PipelineTimeline market={market} runId={runId} />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Tile label="Market" value={detail.market} />
        <Tile label="Status" value={detail.status} />
        <Tile label="Current stage" value={detail.progress && !("not_captured" in detail.progress) ? String((detail.progress as Record<string, unknown>).current_stage ?? "—") : <NotCaptured />} />
        <Tile label="Run ID" value={<span className="font-mono text-xs">{detail.run_id}</span>} />
      </div>

      <div className="rounded-xl border border-white/5 bg-neutral-900/40 p-4 backdrop-blur-sm">
        <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-neutral-500">Totals</h3>
        {totalsNotCaptured ? (
          <p className="text-sm text-neutral-500">Not captured by the pipeline.</p>
        ) : (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-2">
              <Tile
                label="Total market size (US$ Mn)"
                value={finalSize != null ? finalSize.toLocaleString(undefined, { maximumFractionDigits: 1 }) : <NotCaptured />}
              />
              <Tile
                label="Related demand (US$ Mn)"
                value={relatedDemand != null ? relatedDemand.toLocaleString(undefined, { maximumFractionDigits: 1 }) : <NotCaptured />}
              />
            </div>
            <div className="space-y-1.5">
              <div className="text-xs font-medium uppercase tracking-wider text-neutral-500">Quality flags</div>
              {qualityFlags.length === 0 ? (
                <NotCaptured />
              ) : (
                <div className="space-y-2">
                  {qualityFlags.map((flag, i) => (
                    <div
                      key={i}
                      className={`flex items-start gap-2 rounded-lg border p-2.5 text-sm ${
                        flag.severity === "critical"
                          ? "border-rose-900/50 bg-rose-950/40 text-rose-300"
                          : flag.severity === "warning"
                            ? "border-amber-900/50 bg-amber-950/40 text-amber-300"
                            : "border-sky-900/50 bg-sky-950/40 text-sky-300"
                      }`}
                    >
                      <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                      <span>{flag.message}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      <div className="rounded-xl border border-white/5 bg-neutral-900/40 p-4 backdrop-blur-sm">
        <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-neutral-500">Audit summary</h3>
        {detail.audit_summary?.not_captured ? (
          <p className="text-sm text-neutral-500">Not captured.</p>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="space-y-1">
              <div className="text-xs font-medium uppercase tracking-wider text-neutral-500">Total events</div>
              <div className="text-lg font-semibold text-neutral-100">{detail.audit_summary?.total_events ?? 0}</div>
            </div>
            <div />
            <CountBar title="By event type" counts={detail.audit_summary?.counts_by_event_type ?? {}} />
            <CountBar title="By status" counts={detail.audit_summary?.counts_by_status ?? {}} />
            <div className="space-y-1.5">
              <div className="text-xs font-medium uppercase tracking-wider text-neutral-500">Countries touched</div>
              <Chips items={detail.audit_summary?.countries_touched ?? []} />
            </div>
            <div className="space-y-1.5">
              <div className="text-xs font-medium uppercase tracking-wider text-neutral-500">Buckets touched</div>
              <Chips items={detail.audit_summary?.buckets_touched ?? []} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import { RefreshCw, Loader2, AlertTriangle } from "lucide-react";
import type { AuditRunSummary, AuditRunStatus } from "@/lib/types";
import { listAuditRuns } from "@/lib/api";

const statusStyles: Record<AuditRunStatus, string> = {
  completed: "border-emerald-900/60 bg-emerald-950/40 text-emerald-300",
  running: "border-sky-900/60 bg-sky-950/40 text-sky-300 animate-pulse",
  interrupted: "border-rose-900/60 bg-rose-950/40 text-rose-300",
  unknown: "border-white/10 bg-neutral-900/60 text-neutral-400",
};

function StatusPill({ status }: { status: AuditRunStatus }) {
  return (
    <span className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium ${statusStyles[status] ?? statusStyles.unknown}`}>
      {status}
    </span>
  );
}

export default function RunPicker({
  onSelect,
  selected,
}: {
  onSelect: (market: string, runId: string) => void;
  selected?: { market: string; runId: string } | null;
}) {
  const [runs, setRuns] = useState<AuditRunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    setError(null);
    listAuditRuns("usage_based")
      .then(setRuns)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load audit runs"))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, []);

  return (
    <div className="space-y-3 rounded-xl border border-white/5 bg-neutral-900/40 p-4 backdrop-blur-sm">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-500">Instrumented runs (usage-based)</h3>
        <button
          onClick={load}
          disabled={loading}
          className="flex items-center gap-1.5 rounded-md border border-white/10 bg-neutral-900/60 px-2 py-1 text-xs text-neutral-300 hover:border-white/20 hover:bg-neutral-800 disabled:opacity-40"
        >
          {loading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
          Refresh
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-rose-900/50 bg-rose-950/40 p-3 text-sm text-rose-300">
          <AlertTriangle size={15} /> {error}
        </div>
      )}

      {!loading && !error && runs.length === 0 && (
        <p className="text-sm text-neutral-500">
          No instrumented runs found yet — audit data is only captured for runs executed after this feature was added.
        </p>
      )}

      {runs.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-neutral-600">
                <th className="pb-2 pr-3 font-medium">Market</th>
                <th className="pb-2 pr-3 font-medium">Run ID</th>
                <th className="pb-2 pr-3 font-medium">Status</th>
                <th className="pb-2 pr-3 font-medium">Stage</th>
                <th className="pb-2 pr-3 font-medium">Final size (US$ Mn)</th>
                <th className="pb-2 pr-3 font-medium">Last update</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => {
                const isSelected = selected?.market === r.market && selected?.runId === r.run_id;
                return (
                  <tr
                    key={`${r.market}::${r.run_id}`}
                    onClick={() => onSelect(r.market, r.run_id)}
                    className={`cursor-pointer border-t border-white/5 transition-colors hover:bg-neutral-800/40 ${
                      isSelected ? "bg-indigo-500/10" : ""
                    }`}
                  >
                    <td className="py-1.5 pr-3 text-neutral-300">{r.market}</td>
                    <td className="py-1.5 pr-3 font-mono text-xs text-neutral-400">{r.run_id}</td>
                    <td className="py-1.5 pr-3">
                      <StatusPill status={r.status} />
                    </td>
                    <td className="py-1.5 pr-3 text-neutral-400">
                      {r.current_stage ?? <span className="text-neutral-600">Not captured</span>}
                    </td>
                    <td className="py-1.5 pr-3 text-neutral-400">
                      {r.final_market_size != null ? r.final_market_size.toLocaleString(undefined, { maximumFractionDigits: 1 }) : (
                        <span className="text-neutral-600">Not captured</span>
                      )}
                    </td>
                    <td className="py-1.5 pr-3 whitespace-nowrap text-neutral-500">
                      {r.last_update ? r.last_update.slice(0, 19).replace("T", " ") : (
                        <span className="text-neutral-600">Not captured</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

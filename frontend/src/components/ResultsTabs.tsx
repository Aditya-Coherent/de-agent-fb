"use client";

import { useState } from "react";
import {
  Globe2,
  PieChart,
  MessageSquareQuote,
  GitCompareArrows,
  History as HistoryIcon,
  Download,
  type LucideIcon,
} from "lucide-react";
import type { GenerateSummary, HistoryEntry, CompareReport } from "@/lib/types";
import { runCompare, downloadByPathUrl } from "@/lib/api";

const TABS: { name: string; icon: LucideIcon }[] = [
  { name: "Regional", icon: Globe2 },
  { name: "Segmentation shares", icon: PieChart },
  { name: "Artifacts", icon: MessageSquareQuote },
  { name: "Baseline compare", icon: GitCompareArrows },
  { name: "History", icon: HistoryIcon },
];
type Tab = (typeof TABS)[number]["name"];

function pct(v: number) {
  return `${(v * 100).toFixed(1)}%`;
}

export default function ResultsTabs({
  summary,
  history,
}: {
  summary: GenerateSummary;
  history: HistoryEntry[];
}) {
  const [tab, setTab] = useState<Tab>("Regional");
  const [compareReport, setCompareReport] = useState<CompareReport | null>(null);
  const [comparing, setComparing] = useState(false);
  const [compareError, setCompareError] = useState<string | null>(null);

  const runComparison = async () => {
    setComparing(true);
    setCompareError(null);
    try {
      const rep = await runCompare(summary.run_id);
      setCompareReport(rep);
    } catch (e) {
      setCompareError(e instanceof Error ? e.message : "Compare failed");
    } finally {
      setComparing(false);
    }
  };

  return (
    <div className="overflow-hidden rounded-xl border border-white/5 bg-neutral-900/60 backdrop-blur-sm">
      <div className="flex gap-1 overflow-x-auto border-b border-white/5 px-2 pt-2">
        {TABS.map(({ name, icon: Icon }) => (
          <button
            key={name}
            onClick={() => setTab(name)}
            className={`flex items-center gap-1.5 whitespace-nowrap rounded-t-lg px-3.5 py-2.5 text-sm font-medium transition-colors ${
              tab === name
                ? "bg-neutral-950 text-neutral-100"
                : "text-neutral-500 hover:text-neutral-300"
            }`}
          >
            <Icon size={14} />
            {name}
          </button>
        ))}
      </div>

      <div className="p-5">
        {tab === "Regional" && (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-neutral-500">
                <th className="pb-3 font-medium">Region</th>
                <th className="pb-3 font-medium">Value share</th>
                <th className="pb-3 font-medium">CAGR</th>
                <th className="pb-3 font-medium">Value (US$ Mn)</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(summary.regional).map(([region, info]) => (
                <tr key={region} className="border-t border-white/5 hover:bg-white/[0.02]">
                  <td className="py-2 font-medium text-neutral-200">{region}</td>
                  <td className="py-2 text-neutral-400">{pct(info.value_share)}</td>
                  <td className="py-2 text-neutral-400">{pct(info.cagr)}</td>
                  <td className="py-2 tabular-nums text-neutral-400">
                    {(summary.global_anchor_2026 * info.value_share).toLocaleString(undefined, {
                      maximumFractionDigits: 1,
                    })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {tab === "Segmentation shares" && (
          <div className="space-y-3">
            {Object.entries(summary.shares).map(([dim, dimMap]) => {
              const glob = dimMap["__global__"] || {};
              return (
                <details key={dim} className="group rounded-lg border border-white/5 bg-white/[0.02] p-3.5" open>
                  <summary className="cursor-pointer text-sm font-medium text-neutral-200 marker:content-none">
                    {dim}
                  </summary>
                  <div className="mt-3 space-y-3">
                    {Object.entries(glob).map(([parent, vec]) => (
                      <div key={parent}>
                        <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-indigo-400/70">
                          {parent === "__root__" ? "whole dimension" : parent}
                        </div>
                        <table className="w-full text-sm">
                          <tbody>
                            {Object.entries(vec).map(([seg, share]) => (
                              <tr key={seg} className="border-t border-white/5">
                                <td className="py-1 text-neutral-300">{seg}</td>
                                <td className="py-1 text-right tabular-nums text-neutral-400">{pct(share)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ))}
                  </div>
                </details>
              );
            })}
          </div>
        )}

        {tab === "Artifacts" && (
          <div className="space-y-4">
            {summary.artifacts.length === 0 && (
              <p className="text-neutral-500">No artifacts recorded for this run.</p>
            )}
            {summary.artifacts.map((a, i) => (
              <div key={i} className="border-b border-white/5 pb-4 last:border-0">
                <div className="font-medium text-neutral-200">
                  {a.topic} — {a.question}
                </div>
                <p className="mt-1.5 text-sm leading-relaxed text-neutral-400">{a.answer}</p>
                {a.current_events?.length > 0 && (
                  <p className="mt-1.5 text-xs text-neutral-500">
                    Current events: {a.current_events.join(", ")}
                  </p>
                )}
              </div>
            ))}
          </div>
        )}

        {tab === "Baseline compare" && (
          <div>
            {!summary.has_human_sheet ? (
              <p className="text-neutral-500">
                No matching human input sheet found for this market — nothing to compare against.
              </p>
            ) : (
              <div>
                <button
                  onClick={runComparison}
                  disabled={comparing}
                  className="rounded-lg bg-indigo-500 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-400 disabled:opacity-50"
                >
                  {comparing ? "Comparing…" : "Run baseline comparison"}
                </button>
                {compareError && <p className="mt-2 text-sm text-rose-400">{compareError}</p>}
                {compareReport && (
                  <pre className="mt-4 max-h-96 overflow-auto rounded-lg bg-neutral-950 p-4 text-xs text-neutral-300">
                    {compareReport.report_text}
                  </pre>
                )}
              </div>
            )}
          </div>
        )}

        {tab === "History" && (
          <div>
            {history.length === 0 ? (
              <p className="text-neutral-500">No generation runs logged yet.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs uppercase tracking-wider text-neutral-500">
                      <th className="pb-3 pr-3 font-medium">Time (UTC)</th>
                      <th className="pb-3 pr-3 font-medium">Market</th>
                      <th className="pb-3 pr-3 font-medium">Source</th>
                      <th className="pb-3 pr-3 font-medium">Reward</th>
                      <th className="pb-3 pr-3 font-medium">Iters</th>
                      <th className="pb-3 pr-3 font-medium">Geos</th>
                      <th className="pb-3 pr-3 font-medium">DB evidence</th>
                      <th className="pb-3 pr-3 font-medium">Files</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.map((h, i) => (
                      <tr key={i} className="border-t border-white/5 hover:bg-white/[0.02]">
                        <td className="py-2 pr-3 whitespace-nowrap text-neutral-400">{h.timestamp.slice(0, 19).replace("T", " ")}</td>
                        <td className="py-2 pr-3 font-medium text-neutral-200">{h.market_name}</td>
                        <td className="py-2 pr-3 text-neutral-400">{h.skeleton_source}</td>
                        <td className="py-2 pr-3 tabular-nums text-neutral-400">{h.reward ?? "—"}</td>
                        <td className="py-2 pr-3 text-neutral-400">{h.iterations ?? "—"}</td>
                        <td className="py-2 pr-3 text-neutral-400">{h.geographies}</td>
                        <td className="py-2 pr-3 text-neutral-400">{h.db_evidence_used ? "yes" : "no"}</td>
                        <td className="py-2 pr-3">
                          <div className="flex items-center gap-2.5">
                            {h.output_path && (
                              <a
                                href={downloadByPathUrl(h.output_path)}
                                title="Download Input Sheet"
                                className="flex items-center gap-1 text-indigo-400 hover:text-indigo-300"
                              >
                                <Download size={13} /> Input
                              </a>
                            )}
                            {h.me_output_path && (
                              <a
                                href={downloadByPathUrl(h.me_output_path)}
                                title="Download ME Workbook"
                                className="flex items-center gap-1 text-teal-400 hover:text-teal-300"
                              >
                                <Download size={13} /> ME
                              </a>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

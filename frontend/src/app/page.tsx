"use client";

import { useEffect, useState } from "react";
import {
  Rocket,
  Download,
  Factory,
  Sparkles,
  Database,
  FileSpreadsheet,
  AlertTriangle,
  Loader2,
} from "lucide-react";
import type { Config, GenerateSummary, HistoryEntry, MEResult } from "@/lib/types";
import {
  getConfig,
  getSkeletonFiles,
  getHistory,
  generateEventUrl,
  generateMe,
  downloadInputSheetUrl,
  downloadMeWorkbookUrl,
  downloadInputJsonUrl,
  downloadMeJsonUrl,
  downloadByPathUrl,
} from "@/lib/api";
import LiveLog from "@/components/LiveLog";
import MetricCard from "@/components/MetricCard";
import ResultsTabs from "@/components/ResultsTabs";
import StatusBadge from "@/components/StatusBadge";
import DemandUsageWizard from "@/components/DemandUsageWizard";
import DemandIndiaWizard from "@/components/DemandIndiaWizard";
import DeskSegmentDefinitionPanel from "@/components/DeskSegmentDefinitionPanel";
import SegmentationDemo from "@/components/SegmentationDemo";

export default function Home() {
  const [methodology, setMethodology] = useState<"desk-research" | "demand-usage" | "demand-india" | "segmentation-demo">("desk-research");
  const [config, setConfig] = useState<Config | null>(null);
  const [skeletonFiles, setSkeletonFiles] = useState<string[]>([]);
  const [history, setHistory] = useState<HistoryEntry[]>([]);

  const [source, setSource] = useState<"db" | "reference">("db");
  const [skeletonChoice, setSkeletonChoice] = useState("");
  const [market, setMarket] = useState("Global Retail Self Scanning Solutions Market");

  const [generating, setGenerating] = useState(false);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [summary, setSummary] = useState<GenerateSummary | null>(null);
  const [genError, setGenError] = useState<string | null>(null);

  const [meGenerating, setMeGenerating] = useState(false);
  const [meResult, setMeResult] = useState<MEResult | null>(null);

  // Desk Research Phase 0 gate -- required before "Generate Input Sheet" may run.
  const [segDefApproved, setSegDefApproved] = useState(false);

  useEffect(() => {
    getConfig().then(setConfig).catch(() => {});
    getSkeletonFiles()
      .then((files) => {
        setSkeletonFiles(files);
        if (files.length) setSkeletonChoice(files.find((f) => f.includes("Self Scanning")) || files[0]);
      })
      .catch(() => {});
    getHistory().then(setHistory).catch(() => {});
  }, []);

  const refreshHistory = () => getHistory().then(setHistory).catch(() => {});

  const handleGenerate = () => {
    setGenerating(true);
    setGenError(null);
    setLogLines([]);
    setSummary(null);
    setMeResult(null);

    const url = generateEventUrl(market, source, skeletonChoice);
    const es = new EventSource(url);

    es.addEventListener("log", (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      setLogLines((prev) => [...prev, data.line]);
    });
    es.addEventListener("done", (e: MessageEvent) => {
      const data = JSON.parse(e.data) as GenerateSummary;
      setSummary(data);
      setGenerating(false);
      es.close();
      refreshHistory();
    });
    es.addEventListener("error", (e: MessageEvent) => {
      try {
        const data = JSON.parse((e as MessageEvent).data);
        setGenError(data.message || "Generation failed");
      } catch {
        setGenError("Connection to the generation server was lost.");
      }
      setGenerating(false);
      es.close();
    });
  };

  const handleGenerateMe = async () => {
    if (!summary) return;
    setMeGenerating(true);
    try {
      const res = await generateMe(summary.run_id);
      setMeResult(res);
    } catch (e) {
      setGenError(e instanceof Error ? e.message : "ME workbook generation failed");
    } finally {
      setMeGenerating(false);
    }
  };

  const meOk = meResult?.gate1_passed && meResult?.gate3_passed && meResult?.output_exists;

  return (
    <div className="relative min-h-screen overflow-x-hidden bg-neutral-950 text-neutral-100">
      {/* Ambient background accent */}
      <div className="pointer-events-none fixed inset-0 overflow-hidden">
        <div className="absolute -top-40 left-1/4 h-96 w-96 rounded-full bg-indigo-600/20 blur-[120px]" />
        <div className="absolute top-1/3 -right-20 h-96 w-96 rounded-full bg-violet-600/10 blur-[120px]" />
      </div>

      <div className="relative mx-auto flex max-w-6xl gap-6 px-6 py-8">
        {/* Sidebar */}
        <aside className="w-72 shrink-0 space-y-4">
          <h2 className="px-1 text-xs font-semibold uppercase tracking-wider text-neutral-500">
            Configuration
          </h2>

          <div className="space-y-2.5 rounded-xl border border-white/5 bg-neutral-900/60 p-4 text-sm backdrop-blur-sm">
            <label className="flex items-center gap-1.5 text-xs font-medium text-neutral-400">
              <Database size={13} /> Segmentation source
            </label>
            <select
              className="w-full rounded-lg border border-white/10 bg-neutral-950 p-2 text-sm text-neutral-200 outline-none focus:border-indigo-500/50"
              value={source}
              onChange={(e) => setSource(e.target.value as "db" | "reference")}
            >
              <option value="db">Neon DB (any market)</option>
              <option value="reference">Reference sheet (labels only)</option>
            </select>
            {source === "reference" && (
              <select
                className="w-full rounded-lg border border-white/10 bg-neutral-950 p-2 text-sm text-neutral-200 outline-none focus:border-indigo-500/50"
                value={skeletonChoice}
                onChange={(e) => setSkeletonChoice(e.target.value)}
              >
                {skeletonFiles.map((f) => (
                  <option key={f} value={f}>
                    {f}
                  </option>
                ))}
              </select>
            )}
          </div>

          {config && (
            <div className="space-y-2 rounded-xl border border-white/5 bg-neutral-900/60 p-4 text-xs text-neutral-400 backdrop-blur-sm">
              <div className="font-mono text-sm text-neutral-200">{config.model}</div>
              <div className="truncate font-mono text-neutral-600">{config.base_url}</div>
              <div className="mt-3 space-y-1.5 border-t border-white/5 pt-3">
                <div className="flex justify-between">
                  <span>Web evidence</span>
                  <span className="font-medium text-neutral-300">{config.enable_web ? "On" : "Off"}</span>
                </div>
                <div className="flex justify-between">
                  <span>Refine loop</span>
                  <span className="font-medium text-neutral-300">
                    {config.min_loop_iters}–{config.max_loop_iters} iters
                  </span>
                </div>
                <div className="flex justify-between">
                  <span>Reward target</span>
                  <span className="font-medium text-neutral-300">≥ {config.reward_threshold}</span>
                </div>
                <div className="flex justify-between">
                  <span>Per-region shares</span>
                  <span className="font-medium text-neutral-300">{config.per_region_shares ? "On" : "Off"}</span>
                </div>
                <div className="flex justify-between">
                  <span>Database</span>
                  <span className={`font-medium ${config.db_connected ? "text-emerald-400" : "text-neutral-500"}`}>
                    {config.db_connected ? "Connected" : "Not set"}
                  </span>
                </div>
              </div>
              {!config.is_online && (
                <div className="mt-2 flex items-center gap-1.5 rounded-lg bg-rose-950/50 p-2 text-rose-300">
                  <AlertTriangle size={13} /> No LLM API key found in .env
                </div>
              )}
            </div>
          )}
        </aside>

        {/* Main */}
        <main className="flex-1 space-y-6">
          <div className="flex gap-2">
            <button
              onClick={() => setMethodology("desk-research")}
              className={`rounded-lg px-3 py-1.5 text-xs font-semibold uppercase tracking-wider transition-colors ${
                methodology === "desk-research" ? "bg-indigo-500/20 text-indigo-300" : "text-neutral-500 hover:text-neutral-300"
              }`}
            >
              Desk Research
            </button>
            <button
              onClick={() => setMethodology("demand-usage")}
              className={`rounded-lg px-3 py-1.5 text-xs font-semibold uppercase tracking-wider transition-colors ${
                methodology === "demand-usage" ? "bg-emerald-500/20 text-emerald-300" : "text-neutral-500 hover:text-neutral-300"
              }`}
            >
              Demand-Side (Usage-Based)
            </button>
            <button
              onClick={() => setMethodology("demand-india")}
              className={`rounded-lg px-3 py-1.5 text-xs font-semibold uppercase tracking-wider transition-colors ${
                methodology === "demand-india" ? "bg-orange-500/20 text-orange-300" : "text-neutral-500 hover:text-neutral-300"
              }`}
            >
              Demand-Side (India Only)
            </button>
            <button
              onClick={() => setMethodology("segmentation-demo")}
              className={`rounded-lg px-3 py-1.5 text-xs font-semibold uppercase tracking-wider transition-colors ${
                methodology === "segmentation-demo" ? "bg-fuchsia-500/20 text-fuchsia-300" : "text-neutral-500 hover:text-neutral-300"
              }`}
            >
              Segmentation Demo
            </button>
          </div>

          {methodology === "demand-usage" && <DemandUsageWizard />}

          {methodology === "demand-india" && <DemandIndiaWizard />}

          {methodology === "segmentation-demo" && <SegmentationDemo />}

          {methodology === "desk-research" && (
          <>
          <header className="space-y-2">
            <div className="flex items-center gap-2.5">
              <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 to-violet-600 shadow-lg shadow-indigo-500/20">
                <Sparkles size={18} className="text-white" />
              </div>
              <h1 className="bg-gradient-to-r from-neutral-50 to-neutral-400 bg-clip-text text-2xl font-bold text-transparent">
                DeAgent
              </h1>
              <span className="text-sm font-medium text-neutral-500">Synthetic Input-Sheet Generator</span>
            </div>
            <p className="max-w-2xl text-sm leading-relaxed text-neutral-500">
              A LangGraph multi-agent fleet builds a market Input Sheet from the report database and live
              web evidence, with an evidence-refinement reward loop and per-number justifications. No
              numbers are copied from any human sheet.
            </p>
          </header>

          <div className="space-y-2.5 rounded-xl border border-white/5 bg-neutral-900/40 p-4 backdrop-blur-sm">
            <label className="text-xs font-medium uppercase tracking-wider text-neutral-500">Market name</label>
            <input
              className="w-full rounded-lg border border-white/10 bg-neutral-950 p-2.5 text-neutral-100 outline-none placeholder:text-neutral-600 focus:border-indigo-500/50"
              value={market}
              onChange={(e) => setMarket(e.target.value)}
              placeholder="e.g. Global Process Analyzer Market"
            />
            {source === "db" && (
              <p className="text-xs text-neutral-600">
                Try <span className="text-neutral-400">Global Process Analyzer Market</span>,{" "}
                <span className="text-neutral-400">Global Retail Self Scanning Solutions Market</span>, or any
                market with segmentation in the report database.
              </p>
            )}
          </div>

          <DeskSegmentDefinitionPanel
            market={market}
            source={source}
            skeletonChoice={skeletonChoice}
            approved={segDefApproved}
            onApprovedChange={setSegDefApproved}
          />

          <div className="space-y-2.5 rounded-xl border border-white/5 bg-neutral-900/40 p-4 backdrop-blur-sm">
            <button
              onClick={handleGenerate}
              disabled={generating || !config?.is_online || !market.trim() || !segDefApproved}
              className="flex items-center gap-2 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-600 px-4 py-2.5 font-medium text-white shadow-lg shadow-indigo-500/20 transition-all hover:shadow-indigo-500/30 disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none"
            >
              {generating ? <Loader2 size={16} className="animate-spin" /> : <Rocket size={16} />}
              {generating ? "Generating…" : "Generate Input Sheet"}
            </button>
            {!segDefApproved && (
              <p className="text-xs text-neutral-600">Approve the segment definition above first.</p>
            )}
          </div>

          {genError && (
            <div className="flex items-center gap-2 rounded-lg border border-rose-900/50 bg-rose-950/40 p-3 text-sm text-rose-300">
              <AlertTriangle size={15} /> {genError}
            </div>
          )}

          <LiveLog lines={logLines} active={generating} />

          {summary && summary.quality_flags?.length > 0 && (
            <div className="space-y-2">
              {summary.quality_flags.map((flag, i) => (
                <div
                  key={i}
                  className={`flex items-start gap-2 rounded-lg border p-3 text-sm ${
                    flag.severity === "critical"
                      ? "border-rose-900/50 bg-rose-950/40 text-rose-300"
                      : flag.severity === "warning"
                        ? "border-amber-900/50 bg-amber-950/40 text-amber-300"
                        : "border-sky-900/50 bg-sky-950/40 text-sky-300"
                  }`}
                >
                  <AlertTriangle size={15} className="mt-0.5 shrink-0" />
                  <span>{flag.message}</span>
                </div>
              ))}
            </div>
          )}

          {summary && (
            <>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <MetricCard
                  label="Global 2026 (US$ Mn)"
                  value={summary.global_anchor_2025.toLocaleString(undefined, { maximumFractionDigits: 1 })}
                  accent="indigo"
                />
                <MetricCard label="Global CAGR" value={`${(summary.global_cagr * 100).toFixed(1)}%`} accent="violet" />
                <MetricCard label="Reward" value={summary.reward.toFixed(2)} accent="teal" />
                <MetricCard label="Loop iterations" value={String(summary.iter)} accent="amber" />
              </div>

              <div className="flex flex-wrap gap-3">
                <a
                  href={downloadInputSheetUrl(summary.run_id)}
                  className="flex items-center gap-2 rounded-lg border border-white/10 bg-neutral-900/60 px-4 py-2.5 text-sm font-medium text-neutral-200 backdrop-blur-sm transition-colors hover:border-white/20 hover:bg-neutral-800"
                >
                  <Download size={15} /> Download Input Sheet
                </a>
                <a
                  href={downloadInputJsonUrl(summary.run_id)}
                  className="flex items-center gap-2 rounded-lg border border-white/10 bg-neutral-900/60 px-4 py-2.5 text-sm font-medium text-neutral-200 backdrop-blur-sm transition-colors hover:border-white/20 hover:bg-neutral-800"
                >
                  <Download size={15} /> Input Sheet (JSON)
                </a>
                <button
                  onClick={handleGenerateMe}
                  disabled={meGenerating}
                  className="flex items-center gap-2 rounded-lg border border-white/10 bg-neutral-900/60 px-4 py-2.5 text-sm font-medium text-neutral-200 backdrop-blur-sm transition-colors hover:border-white/20 hover:bg-neutral-800 disabled:opacity-50"
                >
                  {meGenerating ? <Loader2 size={15} className="animate-spin" /> : <Factory size={15} />}
                  {meGenerating ? "Assembling…" : "Generate ME Workbook"}
                </button>
              </div>

              {meResult && (
                <div className="space-y-3">
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <StatusBadge ok={meResult.gate1_passed} label="Gate 1 — input validation" />
                    <StatusBadge ok={meResult.gate3_passed} label="Gate 3 — output validation" />
                  </div>
                  {meResult.output_exists && (
                    <div className="flex flex-wrap gap-3">
                      <a
                        href={downloadMeWorkbookUrl(summary.run_id)}
                        className="inline-flex items-center gap-2 rounded-lg border border-white/10 bg-neutral-900/60 px-4 py-2.5 text-sm font-medium text-neutral-200 backdrop-blur-sm transition-colors hover:border-white/20 hover:bg-neutral-800"
                      >
                        <Download size={15} /> Download ME Workbook
                      </a>
                      <a
                        href={downloadMeJsonUrl(summary.run_id)}
                        className="inline-flex items-center gap-2 rounded-lg border border-white/10 bg-neutral-900/60 px-4 py-2.5 text-sm font-medium text-neutral-200 backdrop-blur-sm transition-colors hover:border-white/20 hover:bg-neutral-800"
                      >
                        <Download size={15} /> ME Workbook (JSON)
                      </a>
                    </div>
                  )}
                  <details className="rounded-lg border border-white/5 bg-neutral-950 p-3">
                    <summary className="cursor-pointer text-sm text-neutral-500">ME pipeline log</summary>
                    <pre className="mt-2 max-h-72 overflow-auto text-xs text-neutral-500">{meResult.log_tail}</pre>
                  </details>
                  {!meOk && (
                    <p className="text-xs text-neutral-600">
                      ME workbook produced — see gate status above for details.
                    </p>
                  )}
                </div>
              )}

              <ResultsTabs summary={summary} history={history} />
            </>
          )}

          {!summary && history.length > 0 && (
            <div className="rounded-xl border border-white/5 bg-neutral-900/40 p-4 backdrop-blur-sm">
              <h3 className="mb-3 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-neutral-500">
                <FileSpreadsheet size={13} /> Recent generation runs
              </h3>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs uppercase tracking-wider text-neutral-600">
                      <th className="pb-2 pr-3 font-medium">Time (UTC)</th>
                      <th className="pb-2 pr-3 font-medium">Market</th>
                      <th className="pb-2 pr-3 font-medium">Reward</th>
                      <th className="pb-2 pr-3 font-medium">Files</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.slice(0, 8).map((h, i) => (
                      <tr key={i} className="border-t border-white/5">
                        <td className="py-1.5 pr-3 whitespace-nowrap text-neutral-500">{h.timestamp.slice(0, 19).replace("T", " ")}</td>
                        <td className="py-1.5 pr-3 text-neutral-300">{h.market_name}</td>
                        <td className="py-1.5 pr-3 text-neutral-500">{h.reward ?? "—"}</td>
                        <td className="py-1.5 pr-3">
                          <div className="flex items-center gap-2.5">
                            {h.output_path && (
                              <a href={downloadByPathUrl(h.output_path)} title="Download Input Sheet"
                                className="flex items-center gap-1 text-indigo-400 hover:text-indigo-300">
                                <Download size={13} />
                              </a>
                            )}
                            {h.me_output_path && (
                              <a href={downloadByPathUrl(h.me_output_path)} title="Download ME Workbook"
                                className="flex items-center gap-1 text-teal-400 hover:text-teal-300">
                                <Download size={13} />
                              </a>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          </>
          )}
        </main>
      </div>
    </div>
  );
}

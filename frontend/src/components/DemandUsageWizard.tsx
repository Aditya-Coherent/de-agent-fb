"use client";

import { useState } from "react";
import { Sparkles, ArrowRight, Loader2, AlertTriangle, Download, Plus, Trash2 } from "lucide-react";
import type { SetupDraft, TaxonomyDraft, UsageBucket, SizingSummary, DemandDriver } from "@/lib/types";
import {
  generateSetupDraft,
  approveSetup,
  generateTaxonomyDraft,
  approveTaxonomy,
  sizeEventUrl,
  downloadDemandUsageUrl,
} from "@/lib/api";
import LiveLog from "@/components/LiveLog";
import MetricCard from "@/components/MetricCard";

type Step = "setup" | "taxonomy" | "size";

const inputCls =
  "w-full rounded-lg border border-white/10 bg-neutral-950 p-2.5 text-sm text-neutral-100 outline-none placeholder:text-neutral-600 focus:border-indigo-500/50";
const labelCls = "text-xs font-medium uppercase tracking-wider text-neutral-500";
const cardCls = "space-y-3 rounded-xl border border-white/5 bg-neutral-900/40 p-4 backdrop-blur-sm";
const btnPrimary =
  "flex items-center gap-2 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-600 px-4 py-2.5 text-sm font-medium text-white shadow-lg shadow-indigo-500/20 transition-all hover:shadow-indigo-500/30 disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none";
const btnSecondary =
  "flex items-center gap-2 rounded-lg border border-white/10 bg-neutral-900/60 px-4 py-2.5 text-sm font-medium text-neutral-200 backdrop-blur-sm transition-colors hover:border-white/20 hover:bg-neutral-800 disabled:opacity-40 disabled:cursor-not-allowed";

export default function DemandUsageWizard() {
  const [market, setMarket] = useState("Crude Oil");
  const [runId, setRunId] = useState<string | null>(null);
  const [step, setStep] = useState<Step>("setup");
  const [error, setError] = useState<string | null>(null);

  // Phase 1
  const [setupLoading, setSetupLoading] = useState(false);
  const [setupDraft, setSetupDraft] = useState<SetupDraft | null>(null);
  const [setupApproved, setSetupApproved] = useState(false);

  // Phase 2
  const [taxonomyLoading, setTaxonomyLoading] = useState(false);
  const [taxonomyDraft, setTaxonomyDraft] = useState<TaxonomyDraft | null>(null);
  const [taxonomyApproved, setTaxonomyApproved] = useState(false);

  // Phase 3
  const [sizing, setSizing] = useState(false);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [summary, setSummary] = useState<SizingSummary | null>(null);

  const handleGenerateSetup = async () => {
    setError(null);
    setSetupLoading(true);
    try {
      const { run_id, draft } = await generateSetupDraft(market);
      setRunId(run_id);
      setSetupDraft(draft);
      setSetupApproved(false);
      setStep("setup");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Setup draft generation failed");
    } finally {
      setSetupLoading(false);
    }
  };

  const handleApproveSetup = async () => {
    if (!runId || !setupDraft) return;
    setError(null);
    try {
      await approveSetup(runId, setupDraft);
      setSetupApproved(true);
      setStep("taxonomy");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Setup approval failed");
    }
  };

  const handleGenerateTaxonomy = async () => {
    if (!runId) return;
    setError(null);
    setTaxonomyLoading(true);
    try {
      const { draft } = await generateTaxonomyDraft(runId);
      setTaxonomyDraft(draft);
      setTaxonomyApproved(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Taxonomy draft generation failed");
    } finally {
      setTaxonomyLoading(false);
    }
  };

  const handleApproveTaxonomy = async () => {
    if (!runId || !taxonomyDraft) return;
    setError(null);
    try {
      await approveTaxonomy(runId, taxonomyDraft);
      setTaxonomyApproved(true);
      setStep("size");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Taxonomy approval failed");
    }
  };

  const handleRunSizing = () => {
    if (!runId) return;
    setError(null);
    setSizing(true);
    setLogLines([]);
    setSummary(null);

    const es = new EventSource(sizeEventUrl(runId));
    es.addEventListener("log", (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      setLogLines((prev) => [...prev, data.line]);
    });
    es.addEventListener("done", (e: MessageEvent) => {
      setSummary(JSON.parse(e.data) as SizingSummary);
      setSizing(false);
      es.close();
    });
    es.addEventListener("error", (e: MessageEvent) => {
      try {
        setError(JSON.parse((e as MessageEvent).data).message || "Sizing failed");
      } catch {
        setError("Connection to the sizing server was lost.");
      }
      setSizing(false);
      es.close();
    });
  };

  const updateBucket = (index: number, patch: Partial<UsageBucket>) => {
    if (!taxonomyDraft) return;
    const buckets = [...taxonomyDraft.usage_buckets];
    buckets[index] = { ...buckets[index], ...patch };
    setTaxonomyDraft({ ...taxonomyDraft, usage_buckets: buckets });
  };

  const updateDriver = (bucketName: string, patch: Partial<DemandDriver>) => {
    if (!taxonomyDraft) return;
    setTaxonomyDraft({
      ...taxonomyDraft,
      drivers: { ...taxonomyDraft.drivers, [bucketName]: { ...taxonomyDraft.drivers[bucketName], ...patch } },
    });
  };

  const removeBucket = (index: number) => {
    if (!taxonomyDraft) return;
    const buckets = [...taxonomyDraft.usage_buckets];
    buckets.splice(index, 1);
    setTaxonomyDraft({ ...taxonomyDraft, usage_buckets: buckets });
  };

  const addBucket = () => {
    if (!taxonomyDraft) return;
    const name = `New Usage ${taxonomyDraft.usage_buckets.length + 1}`;
    setTaxonomyDraft({
      ...taxonomyDraft,
      usage_buckets: [...taxonomyDraft.usage_buckets, { name, description: "", bucket_category: "physical_consumption" }],
      drivers: {
        ...taxonomyDraft.drivers,
        [name]: { driver_name: "", unit: "units", final_demand_unit: "units" },
      },
    });
  };

  const steps: { key: Step; label: string }[] = [
    { key: "setup", label: "1. Market Setup" },
    { key: "taxonomy", label: "2. Taxonomy + Drivers" },
    { key: "size", label: "3. Sizing" },
  ];

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <div className="flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-emerald-500 to-teal-600 shadow-lg shadow-emerald-500/20">
            <Sparkles size={18} className="text-white" />
          </div>
          <h1 className="bg-gradient-to-r from-neutral-50 to-neutral-400 bg-clip-text text-2xl font-bold text-transparent">
            Demand-Side (Usage-Based)
          </h1>
        </div>
        <p className="max-w-2xl text-sm leading-relaxed text-neutral-500">
          A three-phase, human-approved pipeline: propose and approve the Market Setup, propose and approve the
          Usage/Application Taxonomy and Demand Drivers, then run the deterministic, evidence-first sizing
          build-up. Nothing downstream re-derives what you've already approved.
        </p>
      </header>

      <div className="flex gap-2 text-xs">
        {steps.map((s) => (
          <div
            key={s.key}
            className={`rounded-full border px-3 py-1.5 font-medium ${
              step === s.key
                ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                : "border-white/10 text-neutral-500"
            }`}
          >
            {s.label}
          </div>
        ))}
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-rose-900/50 bg-rose-950/40 p-3 text-sm text-rose-300">
          <AlertTriangle size={15} /> {error}
        </div>
      )}

      {/* Step 1: Market Setup */}
      <div className={cardCls}>
        <label className={labelCls}>Market name</label>
        <input className={inputCls} value={market} onChange={(e) => setMarket(e.target.value)} placeholder="e.g. Crude Oil" />
        <button onClick={handleGenerateSetup} disabled={setupLoading || !market.trim()} className={btnPrimary}>
          {setupLoading ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
          {setupLoading ? "Drafting…" : "Generate Market Setup Draft"}
        </button>

        {setupDraft && (
          <div className="space-y-3 border-t border-white/5 pt-3">
            <p className="text-xs text-neutral-500">
              Every field below is editable. Nothing downstream runs until you approve this setup.
            </p>
            <div>
              <label className={labelCls}>Scope</label>
              <textarea
                className={`${inputCls} min-h-20`}
                value={setupDraft.scope}
                onChange={(e) => setSetupDraft({ ...setupDraft, scope: e.target.value })}
              />
            </div>
            <div>
              <label className={labelCls}>Geography (comma-separated)</label>
              <input
                className={inputCls}
                value={setupDraft.geography.join(", ")}
                onChange={(e) =>
                  setSetupDraft({ ...setupDraft, geography: e.target.value.split(",").map((c) => c.trim()).filter(Boolean) })
                }
              />
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className={labelCls}>Base year</label>
                <input
                  type="number"
                  className={inputCls}
                  value={setupDraft.base_year}
                  onChange={(e) => setSetupDraft({ ...setupDraft, base_year: Number(e.target.value) })}
                />
              </div>
              <div>
                <label className={labelCls}>Market unit</label>
                <input
                  className={inputCls}
                  value={setupDraft.market_unit}
                  onChange={(e) => setSetupDraft({ ...setupDraft, market_unit: e.target.value })}
                />
              </div>
              <div>
                <label className={labelCls}>Physical unit</label>
                <input
                  className={inputCls}
                  value={setupDraft.physical_unit}
                  onChange={(e) => setSetupDraft({ ...setupDraft, physical_unit: e.target.value })}
                />
              </div>
            </div>
            <button onClick={handleApproveSetup} disabled={setupApproved} className={btnPrimary}>
              <ArrowRight size={16} /> {setupApproved ? "Setup approved" : "Approve & Continue"}
            </button>
          </div>
        )}
      </div>

      {/* Step 2: Taxonomy + Drivers */}
      {setupApproved && (
        <div className={cardCls}>
          <button onClick={handleGenerateTaxonomy} disabled={taxonomyLoading} className={btnPrimary}>
            {taxonomyLoading ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
            {taxonomyLoading ? "Drafting…" : "Generate Taxonomy + Driver Draft"}
          </button>

          {taxonomyDraft && (
            <div className="space-y-3 border-t border-white/5 pt-3">
              <p className="text-xs text-neutral-500">
                Add, remove, rename usage buckets; edit each driver's name/unit. Coverage estimate:{" "}
                {taxonomyDraft.coverage_pct != null ? `${taxonomyDraft.coverage_pct.toFixed(1)}%` : "n/a"} (pipeline
                estimate, not a verified measurement).
              </p>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs uppercase tracking-wider text-neutral-600">
                      <th className="pb-2 pr-2 font-medium">Usage</th>
                      <th className="pb-2 pr-2 font-medium">Description</th>
                      <th className="pb-2 pr-2 font-medium">Category</th>
                      <th className="pb-2 pr-2 font-medium">Driver</th>
                      <th className="pb-2 pr-2 font-medium">Physical unit</th>
                      <th className="pb-2 font-medium" />
                    </tr>
                  </thead>
                  <tbody>
                    {taxonomyDraft.usage_buckets.map((b, i) => {
                      const driver = taxonomyDraft.drivers[b.name] || { driver_name: "", unit: "", final_demand_unit: "" };
                      return (
                        <tr key={i} className="border-t border-white/5 align-top">
                          <td className="py-1.5 pr-2">
                            <input className={inputCls} value={b.name} onChange={(e) => updateBucket(i, { name: e.target.value })} />
                          </td>
                          <td className="py-1.5 pr-2">
                            <input
                              className={inputCls}
                              value={b.description}
                              onChange={(e) => updateBucket(i, { description: e.target.value })}
                            />
                          </td>
                          <td className="py-1.5 pr-2">
                            <select
                              className={inputCls}
                              value={b.bucket_category}
                              onChange={(e) => updateBucket(i, { bucket_category: e.target.value })}
                            >
                              <option value="physical_consumption">physical_consumption</option>
                              <option value="inventory_reserve">inventory_reserve</option>
                              <option value="financial_activity">financial_activity</option>
                              <option value="other_irrelevant">other_irrelevant</option>
                            </select>
                          </td>
                          <td className="py-1.5 pr-2">
                            <input
                              className={inputCls}
                              value={driver.driver_name}
                              onChange={(e) => updateDriver(b.name, { driver_name: e.target.value })}
                            />
                          </td>
                          <td className="py-1.5 pr-2">
                            <input
                              className={inputCls}
                              value={driver.final_demand_unit}
                              onChange={(e) => updateDriver(b.name, { final_demand_unit: e.target.value, unit: e.target.value })}
                            />
                          </td>
                          <td className="py-1.5">
                            <button onClick={() => removeBucket(i)} className="text-rose-400 hover:text-rose-300">
                              <Trash2 size={15} />
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <button onClick={addBucket} className={btnSecondary}>
                <Plus size={15} /> Add usage bucket
              </button>
              <div>
                <button onClick={handleApproveTaxonomy} disabled={taxonomyApproved} className={btnPrimary}>
                  <ArrowRight size={16} /> {taxonomyApproved ? "Taxonomy approved" : "Approve & Continue"}
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Step 3: Sizing */}
      {taxonomyApproved && (
        <div className={cardCls}>
          <button onClick={handleRunSizing} disabled={sizing} className={btnPrimary}>
            {sizing ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
            {sizing ? "Running evidence-first sizing…" : "Run Sizing"}
          </button>

          <LiveLog lines={logLines} active={sizing} />

          {summary && (
            <div className="space-y-4 border-t border-white/5 pt-3">
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                <MetricCard
                  label="Included Demand (US$ Mn)"
                  value={summary.total_market_value_usd_mn.toLocaleString(undefined, { maximumFractionDigits: 1 })}
                  accent="indigo"
                />
                <MetricCard
                  label="Related Demand (US$ Mn)"
                  value={summary.related_demand_value_usd_mn.toLocaleString(undefined, { maximumFractionDigits: 1 })}
                  accent="teal"
                />
                <MetricCard
                  label="Coverage %"
                  value={
                    typeof summary.validation?.coverage_pct === "number"
                      ? `${(summary.validation.coverage_pct as number).toFixed(1)}%`
                      : "n/a"
                  }
                  accent="amber"
                />
              </div>

              {summary.quality_flags?.length > 0 && (
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

              <div className="flex flex-wrap gap-3">
                <a href={downloadDemandUsageUrl(summary.run_id, "xlsx")} className={btnSecondary}>
                  <Download size={15} /> Download Build-Up Workbook (.xlsx)
                </a>
                <a href={downloadDemandUsageUrl(summary.run_id, "json")} className={btnSecondary}>
                  <Download size={15} /> Download Build-Up (JSON)
                </a>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

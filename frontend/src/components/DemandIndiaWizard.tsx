"use client";

import { useState } from "react";
import { Sparkles, ArrowRight, Loader2, AlertTriangle, Download, Plus, Trash2, ChevronDown, ChevronRight } from "lucide-react";
import type { SetupDraft, IndiaTaxonomyDraft, ActualUseCase, IndiaSizingSummary, DemandDriver } from "@/lib/types";
import {
  generateIndiaSetupDraft,
  approveIndiaSetup,
  generateIndiaTaxonomyDraft,
  approveIndiaTaxonomy,
  indiaSizeEventUrl,
  downloadDemandIndiaUrl,
} from "@/lib/api";
import LiveLog from "@/components/LiveLog";
import MetricCard from "@/components/MetricCard";

type Step = "setup" | "taxonomy" | "size";

const inputCls =
  "w-full rounded-lg border border-white/10 bg-neutral-950 p-2.5 text-sm text-neutral-100 outline-none placeholder:text-neutral-600 focus:border-indigo-500/50";
const labelCls = "text-xs font-medium uppercase tracking-wider text-neutral-500";
const cardCls = "space-y-3 rounded-xl border border-white/5 bg-neutral-900/40 p-4 backdrop-blur-sm";
const btnPrimary =
  "flex items-center gap-2 rounded-lg bg-gradient-to-r from-orange-500 to-emerald-600 px-4 py-2.5 text-sm font-medium text-white shadow-lg shadow-orange-500/20 transition-all hover:shadow-orange-500/30 disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none";
const btnSecondary =
  "flex items-center gap-2 rounded-lg border border-white/10 bg-neutral-900/60 px-4 py-2.5 text-sm font-medium text-neutral-200 backdrop-blur-sm transition-colors hover:border-white/20 hover:bg-neutral-800 disabled:opacity-40 disabled:cursor-not-allowed";

export default function DemandIndiaWizard() {
  const [market, setMarket] = useState("Vinegar");
  const [runId, setRunId] = useState<string | null>(null);
  const [step, setStep] = useState<Step>("setup");
  const [error, setError] = useState<string | null>(null);

  // Phase 1 -- India-locked, no geography input.
  const [setupLoading, setSetupLoading] = useState(false);
  const [setupDraft, setSetupDraft] = useState<SetupDraft | null>(null);
  const [setupApproved, setSetupApproved] = useState(false);

  // Phase 2 -- Major Use Case + India Actual Use Case + Driver, one draft.
  const [taxonomyLoading, setTaxonomyLoading] = useState(false);
  const [taxonomyDraft, setTaxonomyDraft] = useState<IndiaTaxonomyDraft | null>(null);
  const [taxonomyApproved, setTaxonomyApproved] = useState(false);
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});

  // Phase 3
  const [sizing, setSizing] = useState(false);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [summary, setSummary] = useState<IndiaSizingSummary | null>(null);

  const handleGenerateSetup = async () => {
    setError(null);
    setSetupLoading(true);
    try {
      const { run_id, draft } = await generateIndiaSetupDraft(market);
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
      await approveIndiaSetup(runId, { ...setupDraft, geography: ["India"] });
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
      const { draft } = await generateIndiaTaxonomyDraft(runId);
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
      await approveIndiaTaxonomy(runId, taxonomyDraft);
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

    const es = new EventSource(indiaSizeEventUrl(runId));
    es.addEventListener("log", (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      setLogLines((prev) => [...prev, data.line]);
    });
    es.addEventListener("done", (e: MessageEvent) => {
      setSummary(JSON.parse(e.data) as IndiaSizingSummary);
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

  const updateActualUseCase = (index: number, patch: Partial<ActualUseCase>) => {
    if (!taxonomyDraft) return;
    const actual_use_cases = [...taxonomyDraft.actual_use_cases];
    actual_use_cases[index] = { ...actual_use_cases[index], ...patch };
    setTaxonomyDraft({ ...taxonomyDraft, actual_use_cases });
  };

  const updateDriver = (bucketName: string, patch: Partial<DemandDriver>) => {
    if (!taxonomyDraft) return;
    setTaxonomyDraft({
      ...taxonomyDraft,
      drivers: { ...taxonomyDraft.drivers, [bucketName]: { ...taxonomyDraft.drivers[bucketName], ...patch } },
    });
  };

  const removeActualUseCase = (index: number) => {
    if (!taxonomyDraft) return;
    const actual_use_cases = [...taxonomyDraft.actual_use_cases];
    actual_use_cases.splice(index, 1);
    setTaxonomyDraft({ ...taxonomyDraft, actual_use_cases });
  };

  const addActualUseCase = (majorUseCase: string) => {
    if (!taxonomyDraft) return;
    const name = `New Actual Use Case ${taxonomyDraft.actual_use_cases.length + 1}`;
    setTaxonomyDraft({
      ...taxonomyDraft,
      actual_use_cases: [
        ...taxonomyDraft.actual_use_cases,
        { name, description: "", bucket_category: "physical_consumption", major_use_case: majorUseCase },
      ],
      drivers: {
        ...taxonomyDraft.drivers,
        [name]: { driver_name: "", unit: "units", final_demand_unit: "units" },
      },
    });
  };

  const toggleGroup = (majorUseCase: string) =>
    setCollapsedGroups((prev) => ({ ...prev, [majorUseCase]: !prev[majorUseCase] }));

  const steps: { key: Step; label: string }[] = [
    { key: "setup", label: "1. Market Setup (India)" },
    { key: "taxonomy", label: "2. Major Use Case + Actual Use Case + Drivers" },
    { key: "size", label: "3. Sizing" },
  ];

  // Group actual use cases by their major_use_case tag, preserving the
  // major_use_cases list's own order (never re-sorting/re-deriving groups).
  const groups: { major: string; rows: { auc: ActualUseCase; index: number }[] }[] = [];
  if (taxonomyDraft) {
    const byMajor = new Map<string, { auc: ActualUseCase; index: number }[]>();
    taxonomyDraft.actual_use_cases.forEach((auc, index) => {
      const key = auc.major_use_case || "(unspecified)";
      if (!byMajor.has(key)) byMajor.set(key, []);
      byMajor.get(key)!.push({ auc, index });
    });
    const orderedMajors = taxonomyDraft.major_use_cases.map((m) => m.name);
    for (const key of byMajor.keys()) {
      if (!orderedMajors.includes(key)) orderedMajors.push(key);
    }
    for (const major of orderedMajors) {
      if (byMajor.has(major)) groups.push({ major, rows: byMajor.get(major)! });
    }
  }

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <div className="flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-orange-500 to-emerald-600 shadow-lg shadow-orange-500/20">
            <Sparkles size={18} className="text-white" />
          </div>
          <h1 className="bg-gradient-to-r from-neutral-50 to-neutral-400 bg-clip-text text-2xl font-bold text-transparent">
            Demand-Side (Usage-Based) -- India Only
          </h1>
        </div>
        <p className="max-w-2xl text-sm leading-relaxed text-neutral-500">
          Same three-phase, human-approved pipeline, with one more hierarchy level: Major Use Case -&gt;
          India -&gt; Actual Use Case. Geography is permanently locked to India -- no regional split, no
          other countries. Major Use Case + India Actual Use Case + Demand Driver are proposed and
          approved together, then Actual Use Case totals roll up to Major Use Case totals, which roll up
          to the India Market Total.
        </p>
      </header>

      <div className="flex gap-2 text-xs">
        {steps.map((s) => (
          <div
            key={s.key}
            className={`rounded-full border px-3 py-1.5 font-medium ${
              step === s.key
                ? "border-orange-500/40 bg-orange-500/10 text-orange-300"
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

      {/* Step 1: Market Setup (India-locked) */}
      <div className={cardCls}>
        <label className={labelCls}>Market name</label>
        <input className={inputCls} value={market} onChange={(e) => setMarket(e.target.value)} placeholder="e.g. Vinegar" />
        <button onClick={handleGenerateSetup} disabled={setupLoading || !market.trim()} className={btnPrimary}>
          {setupLoading ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
          {setupLoading ? "Drafting…" : "Generate Market Setup Draft"}
        </button>

        {setupDraft && (
          <div className="space-y-3 border-t border-white/5 pt-3">
            <p className="text-xs text-neutral-500">
              Every field below is editable -- except geography, which is permanently locked to India for
              this methodology. Nothing downstream runs until you approve this setup.
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
              <label className={labelCls}>Geography</label>
              <input className={`${inputCls} opacity-60`} value="India" disabled readOnly />
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

      {/* Step 2: Major Use Case + Actual Use Case + Drivers */}
      {setupApproved && (
        <div className={cardCls}>
          <button onClick={handleGenerateTaxonomy} disabled={taxonomyLoading} className={btnPrimary}>
            {taxonomyLoading ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
            {taxonomyLoading ? "Drafting…" : "Generate Taxonomy + Driver Draft"}
          </button>

          {taxonomyDraft && (
            <div className="space-y-3 border-t border-white/5 pt-3">
              <p className="text-xs text-neutral-500">
                Actual Use Cases are grouped under their Major Use Case. Add/remove/rename Actual Use
                Cases, edit each driver&apos;s name/unit. Coverage estimate:{" "}
                {taxonomyDraft.coverage_pct != null ? `${taxonomyDraft.coverage_pct.toFixed(1)}%` : "n/a"} (pipeline
                estimate, not a verified measurement).
              </p>
              {taxonomyDraft.scope_taxonomy_aggregate?.likely_scope_too_narrow && (
                <div className="flex items-start gap-2 rounded-lg border border-amber-900/50 bg-amber-950/30 p-3 text-xs text-amber-300">
                  <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                  <span>
                    {taxonomyDraft.scope_taxonomy_aggregate.flagged_count}/
                    {taxonomyDraft.scope_taxonomy_aggregate.total_buckets} Major Use Cases (
                    {(taxonomyDraft.scope_taxonomy_aggregate.flagged_ratio * 100).toFixed(0)}%) were flagged
                    ambiguous/out-of-scope vs. the approved Market Setup scope.
                  </span>
                </div>
              )}

              {groups.map((group) => {
                const collapsed = !!collapsedGroups[group.major];
                return (
                  <div key={group.major} className="rounded-lg border border-white/5">
                    <button
                      onClick={() => toggleGroup(group.major)}
                      className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left"
                    >
                      <span className="flex items-center gap-1.5 text-sm font-medium text-neutral-200">
                        {collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
                        {group.major}
                      </span>
                      <span className="text-xs text-neutral-500">{group.rows.length} Actual Use Case(s)</span>
                    </button>
                    {!collapsed && (
                      <div className="overflow-x-auto border-t border-white/5 p-2">
                        <table className="w-full text-sm">
                          <thead>
                            <tr className="text-left text-xs uppercase tracking-wider text-neutral-600">
                              <th className="pb-2 pr-2 font-medium">Actual Use Case</th>
                              <th className="pb-2 pr-2 font-medium">Description</th>
                              <th className="pb-2 pr-2 font-medium">Category</th>
                              <th className="pb-2 pr-2 font-medium">Driver</th>
                              <th className="pb-2 pr-2 font-medium">Physical unit</th>
                              <th className="pb-2 font-medium" />
                            </tr>
                          </thead>
                          <tbody>
                            {group.rows.map(({ auc, index }) => {
                              const driver = taxonomyDraft.drivers[auc.name] || { driver_name: "", unit: "", final_demand_unit: "" };
                              return (
                                <tr key={index} className="border-t border-white/5 align-top">
                                  <td className="py-1.5 pr-2">
                                    <input
                                      className={inputCls}
                                      value={auc.name}
                                      onChange={(e) => updateActualUseCase(index, { name: e.target.value })}
                                    />
                                  </td>
                                  <td className="py-1.5 pr-2">
                                    <input
                                      className={inputCls}
                                      value={auc.description}
                                      onChange={(e) => updateActualUseCase(index, { description: e.target.value })}
                                    />
                                  </td>
                                  <td className="py-1.5 pr-2">
                                    <select
                                      className={inputCls}
                                      value={auc.bucket_category}
                                      onChange={(e) => updateActualUseCase(index, { bucket_category: e.target.value })}
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
                                      onChange={(e) => updateDriver(auc.name, { driver_name: e.target.value })}
                                    />
                                  </td>
                                  <td className="py-1.5 pr-2">
                                    <input
                                      className={inputCls}
                                      value={driver.final_demand_unit}
                                      onChange={(e) => updateDriver(auc.name, { final_demand_unit: e.target.value, unit: e.target.value })}
                                    />
                                  </td>
                                  <td className="py-1.5">
                                    <button onClick={() => removeActualUseCase(index)} className="text-rose-400 hover:text-rose-300">
                                      <Trash2 size={15} />
                                    </button>
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                        <button onClick={() => addActualUseCase(group.major)} className={`${btnSecondary} mt-2 px-2 py-1 text-xs`}>
                          <Plus size={13} /> Add Actual Use Case under &ldquo;{group.major}&rdquo;
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}

              <div>
                <button onClick={handleApproveTaxonomy} disabled={taxonomyApproved} className={btnPrimary}>
                  <ArrowRight size={16} /> {taxonomyApproved ? "Taxonomy approved" : "Approve & Continue"}
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Step 3: Sizing -- nested rollup */}
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
                  label="India Market Total (US$ Mn)"
                  value={summary.india_market_total.toLocaleString(undefined, { maximumFractionDigits: 1 })}
                  accent="indigo"
                />
                <MetricCard
                  label="Included Demand (US$ Mn)"
                  value={summary.total_market_value_usd_mn.toLocaleString(undefined, { maximumFractionDigits: 1 })}
                  accent="teal"
                />
                <MetricCard
                  label="Related Demand (US$ Mn)"
                  value={summary.related_demand_value_usd_mn.toLocaleString(undefined, { maximumFractionDigits: 1 })}
                  accent="amber"
                />
              </div>

              <div>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-neutral-500">
                  Major Use Case Rollup
                </h3>
                <div className="overflow-x-auto rounded-lg border border-white/5">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-xs uppercase tracking-wider text-neutral-600">
                        <th className="p-2 font-medium">Major Use Case</th>
                        <th className="p-2 font-medium">Total (US$ Mn)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(summary.major_use_case_totals || {}).map(([major, total]) => (
                        <tr key={major} className="border-t border-white/5">
                          <td className="p-2 text-neutral-300">{major}</td>
                          <td className="p-2 text-neutral-400">{total.toLocaleString(undefined, { maximumFractionDigits: 1 })}</td>
                        </tr>
                      ))}
                      <tr className="border-t border-white/10 font-medium">
                        <td className="p-2 text-neutral-100">India Market Total</td>
                        <td className="p-2 text-neutral-100">
                          {summary.india_market_total.toLocaleString(undefined, { maximumFractionDigits: 1 })}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
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
                <a href={downloadDemandIndiaUrl(summary.run_id, "xlsx")} className={btnSecondary}>
                  <Download size={15} /> Download Build-Up Workbook (.xlsx)
                </a>
                <a href={downloadDemandIndiaUrl(summary.run_id, "json")} className={btnSecondary}>
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

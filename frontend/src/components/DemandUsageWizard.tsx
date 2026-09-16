"use client";

import { Fragment, useState } from "react";
import { Sparkles, ArrowRight, Loader2, AlertTriangle, Download, Plus, Trash2 } from "lucide-react";
import type { SetupDraft, TaxonomyDraft, UsageBucket, SizingSummary, DemandDriver, EvidenceStrategy } from "@/lib/types";
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
  const [expandedStrategy, setExpandedStrategy] = useState<Record<string, boolean>>({});

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

  // Phase 7 (DEMAND_SIDE_EVIDENCE_PLAN.md) -- the strategy is user-editable
  // before approval, same "edit before approve" pattern as the driver fields
  // above. Editing here does NOT re-run validation client-side; the
  // deterministic checks re-run server-side in `approve_taxonomy` and the
  // refreshed flags come back with the approved checkpoint.
  const updateStrategy = (bucketName: string, patch: Partial<EvidenceStrategy>) => {
    if (!taxonomyDraft) return;
    const driver = taxonomyDraft.drivers[bucketName] || { driver_name: "", unit: "", final_demand_unit: "" };
    const base: EvidenceStrategy =
      driver.evidence_strategy || {
        driver_name: driver.driver_name || "",
        metric_required: "", physical_unit: driver.final_demand_unit || "", period: "",
        geography: [], primary_source_types: [], preferred_sources: [], search_query_templates: [],
        fallback_source_types: [], fallback_queries: [], derivation_path: "", expected_evidence: "",
        why_this_driver_measures_the_usage: "", strategy_source: "user_edited",
      };
    updateDriver(bucketName, { evidence_strategy: { ...base, ...patch } });
  };

  const toggleStrategy = (bucketName: string) =>
    setExpandedStrategy((prev) => ({ ...prev, [bucketName]: !prev[bucketName] }));

  const removeBucket = (index: number) => {
    if (!taxonomyDraft) return;
    const buckets = [...taxonomyDraft.usage_buckets];
    buckets.splice(index, 1);
    setTaxonomyDraft({ ...taxonomyDraft, usage_buckets: buckets });
  };

  // Phase 9 (DEMAND_SIDE_EVIDENCE_PLAN.md) -- retaining a bucket flagged
  // out_of_scope requires a non-empty justification, enforced again
  // server-side in `approve_taxonomy` (this is a UX convenience, not the
  // actual gate).
  const setScopeOverride = (bucketName: string, justification: string) => {
    if (!taxonomyDraft) return;
    const scope_overrides = { ...(taxonomyDraft.scope_overrides || {}) };
    if (justification.trim()) {
      scope_overrides[bucketName] = justification;
    } else {
      delete scope_overrides[bucketName];
    }
    setTaxonomyDraft({ ...taxonomyDraft, scope_overrides });
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
              {taxonomyDraft.scope_taxonomy_aggregate?.likely_scope_too_narrow && (
                <div className="flex items-start gap-2 rounded-lg border border-amber-900/50 bg-amber-950/30 p-3 text-xs text-amber-300">
                  <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                  <span>
                    {taxonomyDraft.scope_taxonomy_aggregate.flagged_count}/
                    {taxonomyDraft.scope_taxonomy_aggregate.total_buckets} usage buckets (
                    {(taxonomyDraft.scope_taxonomy_aggregate.flagged_ratio * 100).toFixed(0)}%) were flagged
                    ambiguous/out-of-scope vs. the approved Market Setup scope -- this usually means the SCOPE
                    STATEMENT itself is too narrow for this taxonomy. Consider going back and revising the scope
                    rather than editing every bucket below.
                  </span>
                </div>
              )}
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs uppercase tracking-wider text-neutral-600">
                      <th className="pb-2 pr-2 font-medium">Usage</th>
                      <th className="pb-2 pr-2 font-medium">Description</th>
                      <th className="pb-2 pr-2 font-medium">Category</th>
                      <th className="pb-2 pr-2 font-medium">Scope</th>
                      <th className="pb-2 pr-2 font-medium">Driver</th>
                      <th className="pb-2 pr-2 font-medium">Physical unit</th>
                      <th className="pb-2 pr-2 font-medium">Evidence strategy</th>
                      <th className="pb-2 font-medium" />
                    </tr>
                  </thead>
                  <tbody>
                    {taxonomyDraft.usage_buckets.map((b, i) => {
                      const driver = taxonomyDraft.drivers[b.name] || { driver_name: "", unit: "", final_demand_unit: "" };
                      const strategy = driver.evidence_strategy;
                      const flags = driver.evidence_strategy_flags || [];
                      const isOpen = !!expandedStrategy[b.name];
                      const scopeFlag = taxonomyDraft.scope_validation_flags?.[b.name];
                      const scopeCls = scopeFlag?.classification || "in_scope";
                      const scopeBadgeCls =
                        scopeCls === "out_of_scope"
                          ? "bg-rose-500/20 text-rose-300"
                          : scopeCls === "ambiguous"
                            ? "bg-amber-500/20 text-amber-300"
                            : "bg-emerald-500/10 text-emerald-400";
                      return (
                        <Fragment key={i}>
                        <tr className="border-t border-white/5 align-top">
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
                            <span
                              title={scopeFlag ? `${scopeFlag.reason}${scopeFlag.evidence ? ` (evidence: ${scopeFlag.evidence})` : ""}` : "not flagged"}
                              className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${scopeBadgeCls}`}
                            >
                              {scopeCls}
                            </span>
                            {scopeCls === "out_of_scope" && (
                              <input
                                className={`${inputCls} mt-1`}
                                placeholder="Justify retaining this out-of-scope usage…"
                                value={taxonomyDraft.scope_overrides?.[b.name] || ""}
                                onChange={(e) => setScopeOverride(b.name, e.target.value)}
                              />
                            )}
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
                          <td className="py-1.5 pr-2">
                            <button onClick={() => toggleStrategy(b.name)} className={btnSecondary + " px-2 py-1 text-xs"}>
                              {isOpen ? "Hide" : strategy ? "Edit" : "Add"} strategy
                              {flags.length > 0 && (
                                <span className="ml-1 rounded-full bg-amber-500/20 px-1.5 text-amber-300">
                                  {flags.length}
                                </span>
                              )}
                            </button>
                          </td>
                          <td className="py-1.5">
                            <button onClick={() => removeBucket(i)} className="text-rose-400 hover:text-rose-300">
                              <Trash2 size={15} />
                            </button>
                          </td>
                        </tr>
                        {isOpen && (
                          <tr className="border-t border-white/5 bg-neutral-950/40">
                            <td colSpan={8} className="space-y-2 p-3">
                              <p className="text-xs text-neutral-500">
                                Explicit Evidence Strategy (Phase 7) -- what factual quantity to look for and how to
                                search for it. This is a search plan, never a market-size estimate; the actual number
                                still comes from the evidence pipeline.
                                {flags.length > 0 && (
                                  <span className="ml-1 text-amber-400">
                                    {flags.length} validation flag(s): {flags.map((f) => f.code).join(", ")}
                                  </span>
                                )}
                              </p>
                              <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
                                <label className="space-y-1">
                                  <span className={labelCls}>Metric required</span>
                                  <input
                                    className={inputCls}
                                    value={strategy?.metric_required || ""}
                                    onChange={(e) => updateStrategy(b.name, { metric_required: e.target.value })}
                                  />
                                </label>
                                <label className="space-y-1">
                                  <span className={labelCls}>Physical unit</span>
                                  <input
                                    className={inputCls}
                                    value={strategy?.physical_unit || ""}
                                    onChange={(e) => updateStrategy(b.name, { physical_unit: e.target.value })}
                                  />
                                </label>
                                <label className="space-y-1">
                                  <span className={labelCls}>Period</span>
                                  <input
                                    className={inputCls}
                                    placeholder="day | month | quarter | year"
                                    value={strategy?.period || ""}
                                    onChange={(e) => updateStrategy(b.name, { period: e.target.value })}
                                  />
                                </label>
                                <label className="space-y-1">
                                  <span className={labelCls}>Preferred sources (comma-separated)</span>
                                  <input
                                    className={inputCls}
                                    value={(strategy?.preferred_sources || []).join(", ")}
                                    onChange={(e) =>
                                      updateStrategy(b.name, {
                                        preferred_sources: e.target.value.split(",").map((s) => s.trim()).filter(Boolean),
                                      })
                                    }
                                  />
                                </label>
                                <label className="space-y-1">
                                  <span className={labelCls}>Primary source types (comma-separated)</span>
                                  <input
                                    className={inputCls}
                                    value={(strategy?.primary_source_types || []).join(", ")}
                                    onChange={(e) =>
                                      updateStrategy(b.name, {
                                        primary_source_types: e.target.value.split(",").map((s) => s.trim()).filter(Boolean),
                                      })
                                    }
                                  />
                                </label>
                                <label className="space-y-1">
                                  <span className={labelCls}>Search query templates (one per line, use {"{country}"})</span>
                                  <textarea
                                    className={inputCls}
                                    rows={2}
                                    value={(strategy?.search_query_templates || []).join("\n")}
                                    onChange={(e) =>
                                      updateStrategy(b.name, {
                                        search_query_templates: e.target.value.split("\n").map((s) => s.trim()).filter(Boolean),
                                      })
                                    }
                                  />
                                </label>
                                <label className="space-y-1">
                                  <span className={labelCls}>Fallback source types (comma-separated)</span>
                                  <input
                                    className={inputCls}
                                    value={(strategy?.fallback_source_types || []).join(", ")}
                                    onChange={(e) =>
                                      updateStrategy(b.name, {
                                        fallback_source_types: e.target.value.split(",").map((s) => s.trim()).filter(Boolean),
                                      })
                                    }
                                  />
                                </label>
                                <label className="space-y-1">
                                  <span className={labelCls}>Fallback queries (one per line)</span>
                                  <textarea
                                    className={inputCls}
                                    rows={2}
                                    value={(strategy?.fallback_queries || []).join("\n")}
                                    onChange={(e) =>
                                      updateStrategy(b.name, {
                                        fallback_queries: e.target.value.split("\n").map((s) => s.trim()).filter(Boolean),
                                      })
                                    }
                                  />
                                </label>
                                <label className="space-y-1">
                                  <span className={labelCls}>Derivation path</span>
                                  <input
                                    className={inputCls}
                                    value={strategy?.derivation_path || ""}
                                    onChange={(e) => updateStrategy(b.name, { derivation_path: e.target.value })}
                                  />
                                </label>
                                <label className="space-y-1 md:col-span-2">
                                  <span className={labelCls}>Expected evidence</span>
                                  <input
                                    className={inputCls}
                                    value={strategy?.expected_evidence || ""}
                                    onChange={(e) => updateStrategy(b.name, { expected_evidence: e.target.value })}
                                  />
                                </label>
                                <label className="space-y-1 md:col-span-3">
                                  <span className={labelCls}>Why this driver measures the usage</span>
                                  <input
                                    className={inputCls}
                                    value={strategy?.why_this_driver_measures_the_usage || ""}
                                    onChange={(e) =>
                                      updateStrategy(b.name, { why_this_driver_measures_the_usage: e.target.value })
                                    }
                                  />
                                </label>
                              </div>
                            </td>
                          </tr>
                        )}
                        </Fragment>
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

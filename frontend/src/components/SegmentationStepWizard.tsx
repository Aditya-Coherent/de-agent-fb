"use client";

import { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Type,
  Sparkles,
  CheckCircle2,
  XCircle,
  Globe2,
  Download,
  FolderOpen,
  TrendingUp,
  Calculator,
  ShieldCheck,
  Cpu,
  Sigma,
} from "lucide-react";
import type {
  SegmentationCountry,
  SegmentationDemoResult,
  SegmentationDemoStep,
  SegmentationRegion,
  SegmentationViews,
} from "@/lib/types";
import MetricCard from "@/components/MetricCard";
import FormulaMechanism from "@/components/FormulaMechanism";
import ProductApplicationMatrix from "@/components/ProductApplicationMatrix";
import SegmentationTree from "@/components/SegmentationTree";
import TradeEvidencePanel from "@/components/TradeEvidencePanel";
import CalculationChain from "@/components/CalculationChain";
import ApparentConsumptionPanel from "@/components/ApparentConsumptionPanel";

const fmt = (n: number) => `$${n.toLocaleString(undefined, { maximumFractionDigits: 1 })}M`;

function ShareBar({ value, of, accent = "violet" }: { value: number; of: number; accent?: string }) {
  const pct = of > 0 ? Math.min(100, (value / of) * 100) : 0;
  const accentMap: Record<string, string> = {
    violet: "bg-violet-500/60",
    fuchsia: "bg-fuchsia-500/60",
  };
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/5">
      <div className={`h-full rounded-full ${accentMap[accent] ?? accentMap.violet}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

function ViewBreakdown({ label, categories, total }: { label: string; categories: Record<string, number>; total: number }) {
  const entries = Object.entries(categories).sort((a, b) => b[1] - a[1]);
  return (
    <div className="space-y-1.5 rounded-md border border-white/5 bg-neutral-900/50 p-2.5">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-neutral-500">{label}</div>
      {entries.map(([cat, val]) => (
        <div key={cat} className="space-y-0.5">
          <div className="flex items-center justify-between text-[11px]">
            <span className="truncate text-neutral-400">{cat}</span>
            <span className="tabular-nums text-neutral-300">{fmt(val)}</span>
          </div>
          <ShareBar value={val} of={total} accent="violet" />
        </div>
      ))}
    </div>
  );
}

const VIEW_LABEL_FALLBACK: Record<string, string> = {
  product: "Product",
  source: "Source",
  flavor: "Flavor",
  application: "Application",
  distribution_channel: "Distribution Channel",
  sales_channel: "Sales Channel",
};

function ViewsGrid({ views, total }: { views: SegmentationViews; total: number }) {
  return (
    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
      {Object.entries(views).map(([key, cats]) =>
        cats ? <ViewBreakdown key={key} label={VIEW_LABEL_FALLBACK[key] ?? key} categories={cats} total={total} /> : null
      )}
    </div>
  );
}

function CollapsibleNode({ title, total, parentTotal, children }: { title: string; total: number; parentTotal: number; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg border border-white/5 bg-neutral-950/60">
      <button type="button" onClick={() => setOpen((v) => !v)} className="flex w-full items-center gap-3 p-3 text-left">
        {open ? <ChevronDown size={15} className="shrink-0 text-neutral-500" /> : <ChevronRight size={15} className="shrink-0 text-neutral-500" />}
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <span className="truncate text-sm font-medium text-neutral-200">{title}</span>
            <span className="shrink-0 text-sm font-semibold tabular-nums text-neutral-100">{fmt(total)}</span>
          </div>
          <div className="mt-1.5">
            <ShareBar value={total} of={parentTotal} accent="fuchsia" />
          </div>
        </div>
      </button>
      {open && <div className="space-y-3 border-t border-white/5 p-3 pl-9">{children}</div>}
    </div>
  );
}

function CountryPanel({ country, parentTotal }: { country: SegmentationCountry; parentTotal: number }) {
  return (
    <CollapsibleNode title={country.name} total={country.total_usd_mn} parentTotal={parentTotal}>
      <TradeEvidencePanel countryName={country.name} pa={country.product_application} />
      {country.product_application && (
        <>
          <SegmentationTree
            pa={country.product_application}
            countryTotal={country.total_usd_mn}
            countryName={country.name}
          />
          <details>
            <summary className="cursor-pointer text-[10px] uppercase tracking-wider text-neutral-600">
              Matrix view
            </summary>
            <div className="mt-2">
              <ProductApplicationMatrix pa={country.product_application} countryTotal={country.total_usd_mn} />
            </div>
          </details>
        </>
      )}
      <ViewsGrid views={country.views} total={country.total_usd_mn} />
    </CollapsibleNode>
  );
}

function RegionPanel({ region, parentTotal }: { region: SegmentationRegion; parentTotal: number }) {
  return (
    <CollapsibleNode title={region.name} total={region.total_usd_mn} parentTotal={parentTotal}>
      <ViewsGrid views={region.views} total={region.total_usd_mn} />
      <div className="space-y-2">
        {region.countries.map((c) => (
          <CountryPanel key={c.name} country={c} parentTotal={region.total_usd_mn} />
        ))}
      </div>
    </CollapsibleNode>
  );
}

export function NarrativeCard({
  narrative,
}: {
  narrative: {
    headline: string;
    key_shifts: string[];
    growth_drivers: string[];
    regions_to_watch: { region: string; note: string }[];
    outlook_summary: string;
  };
}) {
  return (
    <div className="space-y-3 rounded-xl border border-amber-500/20 bg-gradient-to-br from-amber-500/5 to-transparent p-4">
      <div className="flex items-center gap-2">
        <TrendingUp size={16} className="text-amber-300" />
        <span className="text-xs font-semibold uppercase tracking-wider text-amber-300">
          AI Market Intelligence — Tracing Shifts &amp; Movements
        </span>
      </div>
      {narrative.headline && <p className="text-sm font-semibold text-neutral-100">{narrative.headline}</p>}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {narrative.key_shifts.length > 0 && (
          <div className="space-y-1.5">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-neutral-500">Key Shifts</div>
            <ul className="space-y-1 text-xs text-neutral-300">
              {narrative.key_shifts.map((s, i) => (
                <li key={i} className="flex gap-1.5">
                  <span className="text-amber-400">›</span> {s}
                </li>
              ))}
            </ul>
          </div>
        )}
        {narrative.growth_drivers.length > 0 && (
          <div className="space-y-1.5">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-neutral-500">Growth Drivers</div>
            <ul className="space-y-1 text-xs text-neutral-300">
              {narrative.growth_drivers.map((s, i) => (
                <li key={i} className="flex gap-1.5">
                  <span className="text-amber-400">›</span> {s}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {narrative.regions_to_watch.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-neutral-500">Regions to Watch</div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {narrative.regions_to_watch.map((r, i) => (
              <div key={i} className="rounded-md border border-white/5 bg-neutral-900/50 p-2 text-xs">
                <div className="font-medium text-neutral-200">{r.region}</div>
                <div className="mt-0.5 text-neutral-500">{r.note}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {narrative.outlook_summary && (
        <p className="border-t border-white/5 pt-2 text-xs italic text-neutral-400">{narrative.outlook_summary}</p>
      )}
    </div>
  );
}

const KIND_ICON: Record<SegmentationDemoStep["kind"], typeof Type> = {
  input: Type,
  llm_call: Sparkles,
  aggregate: CheckCircle2,
  narrative: TrendingUp,
};

const KIND_ACCENT: Record<SegmentationDemoStep["kind"], string> = {
  input: "text-neutral-400",
  llm_call: "text-fuchsia-300",
  aggregate: "text-emerald-300",
  narrative: "text-amber-300",
};

function StepDetailBody({ step }: { step: SegmentationDemoStep }) {
  const [showPrompt, setShowPrompt] = useState(false);
  const [showRaw, setShowRaw] = useState(false);

  if (step.kind === "input") {
    return (
      <div className="rounded-lg border border-white/5 bg-neutral-900/50 p-3 text-sm text-neutral-300">
        {Object.entries(step.data ?? {}).map(([k, v]) => (
          <div key={k} className="flex justify-between border-b border-white/5 py-1.5 last:border-0">
            <span className="text-neutral-500">{k}</span>
            <span className="font-medium text-neutral-200">{String(v)}</span>
          </div>
        ))}
      </div>
    );
  }

  if (step.kind === "aggregate") {
    return (
      <div className="space-y-2">
        {(step.checks ?? []).map((c, i) => (
          <div key={i} className="flex items-center justify-between gap-2 rounded-lg border border-white/5 bg-neutral-900/50 p-2.5 text-xs">
            <div className="flex items-center gap-2">
              {c.ok ? <CheckCircle2 size={14} className="shrink-0 text-emerald-400" /> : <XCircle size={14} className="shrink-0 text-rose-400" />}
              <span className="text-neutral-300">{c.check}</span>
            </div>
            <span className="tabular-nums text-neutral-500">
              {fmt(c.actual)} / {fmt(c.expected)}
            </span>
          </div>
        ))}
      </div>
    );
  }

  // llm_call / narrative -- both make a real LLM call, so both show the
  // exact prompt/raw-response panels; narrative additionally renders its
  // parsed qualitative content instead of the numeric `computed` blocks.
  const narrative = step.kind === "narrative" ? (step.computed?.narrative as {
    headline: string;
    key_shifts: string[];
    growth_drivers: string[];
    regions_to_watch: { region: string; note: string }[];
    outlook_summary: string;
  } | undefined) : undefined;

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-white/5 bg-neutral-900/40">
        <button
          type="button"
          onClick={() => setShowPrompt((v) => !v)}
          className="flex w-full items-center gap-2 p-2.5 text-left text-xs font-semibold uppercase tracking-wider text-neutral-500"
        >
          {showPrompt ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          Exact prompt sent to the LLM & Web
        </button>
        {showPrompt && step.request && (
          <div className="space-y-2 border-t border-white/5 p-3">
            <div>
              <div className="mb-1 text-[10px] uppercase tracking-wider text-neutral-600">System</div>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-neutral-950 p-2 text-[11px] text-neutral-400">
                {step.request.system}
              </pre>
            </div>
            <div>
              <div className="mb-1 text-[10px] uppercase tracking-wider text-neutral-600">User</div>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-neutral-950 p-2 text-[11px] text-neutral-400">
                {step.request.user}
              </pre>
            </div>
          </div>
        )}
      </div>

      <div className="rounded-lg border border-white/5 bg-neutral-900/40">
        <button
          type="button"
          onClick={() => setShowRaw((v) => !v)}
          className="flex w-full items-center gap-2 p-2.5 text-left text-xs font-semibold uppercase tracking-wider text-neutral-500"
        >
          {showRaw ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          Raw LLM & Web response
        </button>
        {showRaw && (
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap border-t border-white/5 p-3 text-[11px] text-neutral-400">
            {JSON.stringify(step.response, null, 2)}
          </pre>
        )}
      </div>

      {narrative && <NarrativeCard narrative={narrative} />}

      {step.computed && step.kind !== "narrative" && (
        <div className="space-y-2">
          <div className="text-xs font-semibold uppercase tracking-wider text-neutral-500">
            Computed deterministically from the response above
          </div>
          {typeof step.computed.global_total_usd_mn === "number" && (
            <MetricCard label="Global Total" value={fmt(step.computed.global_total_usd_mn as number)} icon={Globe2} accent="indigo" />
          )}
          {typeof step.computed.region_total_usd_mn === "number" && (
            <MetricCard label="Region Total" value={fmt(step.computed.region_total_usd_mn as number)} icon={Globe2} accent="indigo" />
          )}
          {!!step.computed.region_weights_pct && (
            <div className="rounded-lg border border-white/5 bg-neutral-900/50 p-2.5 text-xs">
              <div className="mb-1.5 text-[10px] uppercase tracking-wider text-neutral-600">Region weights (normalized to 100%)</div>
              {Object.entries(step.computed.region_weights_pct as Record<string, number>).map(([k, v]) => (
                <div key={k} className="flex justify-between py-0.5 text-neutral-400">
                  <span>{k}</span>
                  <span className="tabular-nums text-neutral-300">{v.toFixed(1)}%</span>
                </div>
              ))}
            </div>
          )}
          {!!step.computed.country_weights_pct && (
            <div className="rounded-lg border border-white/5 bg-neutral-900/50 p-2.5 text-xs">
              <div className="mb-1.5 text-[10px] uppercase tracking-wider text-neutral-600">Country weights (normalized to 100%)</div>
              {Object.entries(step.computed.country_weights_pct as Record<string, number>).map(([k, v]) => (
                <div key={k} className="flex justify-between py-0.5 text-neutral-400">
                  <span>{k}</span>
                  <span className="tabular-nums text-neutral-300">{v.toFixed(1)}%</span>
                </div>
              ))}
            </div>
          )}
          {!!step.computed.global_views && (
            <ViewsGrid
              views={step.computed.global_views as SegmentationViews}
              total={(step.computed.global_total_usd_mn as number) ?? 0}
            />
          )}
          {!!step.computed.region_views && (
            <ViewsGrid
              views={step.computed.region_views as SegmentationViews}
              total={(step.computed.region_total_usd_mn as number) ?? 0}
            />
          )}
        </div>
      )}
    </div>
  );
}

/** Clickable, per-step walkthrough of exactly what this demo did: the market
 * name input, each real LLM call made (exact prompt + raw response), the
 * deterministic rollup/validation, and the final rendered tree -- built
 * entirely from the `steps` array the backend already returns and persists
 * to `output/segmentation_demo/<slug>.json`. */
export default function SegmentationStepWizard({ result }: { result: SegmentationDemoResult }) {
  const FORMULA_STEP_ID = "formula_mechanism";
  const CALC_STEP_ID = "calculation_chain";
  const APPARENT_STEP_ID = "apparent_consumption";
  const EVIDENCE_STEP_ID = "evidence_check";
  const FINAL_STEP_ID = "final_result";
  const [activeId, setActiveId] = useState(result.steps[0]?.step_id ?? FINAL_STEP_ID);

  const steps = result.steps;
  const active = steps.find((s) => s.step_id === activeId);
  const isFormula = activeId === FORMULA_STEP_ID;
  const isCalc = activeId === CALC_STEP_ID;
  const isApparent = activeId === APPARENT_STEP_ID;
  const isEvidence = activeId === EVIDENCE_STEP_ID;
  const isFinal = activeId === FINAL_STEP_ID;

  // Every country that has a matrix -- the Evidence Check step verifies each
  // one against official customs data independently.
  const allCountries = result.regions.flatMap((r) => r.countries);

  const handleDownload = () => {
    const blob = new Blob([JSON.stringify(result, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${result.market_name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-segmentation-demo.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex flex-col gap-4 lg:flex-row">
      {/* STEP RAIL */}
      <div className="shrink-0 space-y-1.5 lg:w-64">
        {steps.map((s, i) => {
          const Icon = KIND_ICON[s.kind];
          const isActive = s.step_id === activeId;
          return (
            <button
              key={s.step_id}
              type="button"
              onClick={() => setActiveId(s.step_id)}
              className={`flex w-full items-start gap-2 rounded-lg border px-3 py-2 text-left text-xs transition-colors ${
                isActive ? "border-fuchsia-500/40 bg-fuchsia-500/10" : "border-white/5 bg-neutral-900/30 hover:border-white/10"
              }`}
            >
              <span className={`mt-0.5 shrink-0 ${KIND_ACCENT[s.kind]}`}>
                <Icon size={13} />
              </span>
              <span className="min-w-0">
                <span className="block font-medium text-neutral-200">
                  {i + 1}. {s.title}
                </span>
              </span>
            </button>
          );
        })}
        <button
          type="button"
          onClick={() => setActiveId(FORMULA_STEP_ID)}
          className={`flex w-full items-start gap-2 rounded-lg border px-3 py-2 text-left text-xs transition-colors ${
            isFormula ? "border-indigo-500/40 bg-indigo-500/10" : "border-white/5 bg-neutral-900/30 hover:border-white/10"
          }`}
        >
          <span className="mt-0.5 shrink-0 text-indigo-300">
            <Calculator size={13} />
          </span>
          <span className="block font-medium text-neutral-200">{steps.length + 1}. Formula Mechanism</span>
        </button>
        <button
          type="button"
          onClick={() => setActiveId(CALC_STEP_ID)}
          className={`flex w-full items-start gap-2 rounded-lg border px-3 py-2 text-left text-xs transition-colors ${
            isCalc ? "border-sky-500/40 bg-sky-500/10" : "border-white/5 bg-neutral-900/30 hover:border-white/10"
          }`}
        >
          <span className="mt-0.5 shrink-0 text-sky-300">
            <Cpu size={13} />
          </span>
          <span className="block font-medium text-neutral-200">{steps.length + 2}. Demand Build-Up (no LLM)</span>
        </button>
        <button
          type="button"
          onClick={() => setActiveId(APPARENT_STEP_ID)}
          className={`flex w-full items-start gap-2 rounded-lg border px-3 py-2 text-left text-xs transition-colors ${
            isApparent ? "border-cyan-500/40 bg-cyan-500/10" : "border-white/5 bg-neutral-900/30 hover:border-white/10"
          }`}
        >
          <span className="mt-0.5 shrink-0 text-cyan-300">
            <Sigma size={13} />
          </span>
          <span className="block font-medium text-neutral-200">{steps.length + 3}. Apparent Consumption</span>
        </button>
        <button
          type="button"
          onClick={() => setActiveId(EVIDENCE_STEP_ID)}
          className={`flex w-full items-start gap-2 rounded-lg border px-3 py-2 text-left text-xs transition-colors ${
            isEvidence ? "border-emerald-500/40 bg-emerald-500/10" : "border-white/5 bg-neutral-900/30 hover:border-white/10"
          }`}
        >
          <span className="mt-0.5 shrink-0 text-emerald-300">
            <ShieldCheck size={13} />
          </span>
          <span className="block font-medium text-neutral-200">{steps.length + 4}. Evidence Check</span>
        </button>
        <button
          type="button"
          onClick={() => setActiveId(FINAL_STEP_ID)}
          className={`flex w-full items-start gap-2 rounded-lg border px-3 py-2 text-left text-xs transition-colors ${
            isFinal ? "border-indigo-500/40 bg-indigo-500/10" : "border-white/5 bg-neutral-900/30 hover:border-white/10"
          }`}
        >
          <span className="mt-0.5 shrink-0 text-indigo-300">
            <Globe2 size={13} />
          </span>
          <span className="block font-medium text-neutral-200">{steps.length + 5}. Final Result Tree</span>
        </button>

        <button
          type="button"
          onClick={handleDownload}
          className="mt-2 flex w-full items-center justify-center gap-2 rounded-lg border border-white/10 bg-neutral-900/60 px-3 py-2 text-xs font-medium text-neutral-300 transition-colors hover:border-white/20 hover:bg-neutral-800"
        >
          <Download size={13} /> Download full JSON
        </button>
        <div className="rounded-lg border border-white/5 bg-neutral-950/60 px-2.5 py-2 text-[10px] leading-relaxed text-neutral-600">
          A standalone, offline walkthrough of every step below is generated at{" "}
          <span className="break-all text-neutral-500">output/segmentation_demo/{result.market_name.toLowerCase()}_Walkthrough.html</span>
        </div>
        {result.saved_path && (
          <div className="flex items-start gap-1.5 rounded-lg border border-white/5 bg-neutral-950/60 px-2.5 py-2 text-[10px] text-neutral-600">
            <FolderOpen size={12} className="mt-0.5 shrink-0" />
            <span className="break-all">Also saved server-side at {result.saved_path}</span>
          </div>
        )}
      </div>

      {/* DETAIL PANEL */}
      <div className="min-h-[320px] flex-1 rounded-xl border border-white/10 bg-neutral-900/40 p-4">
        {isFormula && <FormulaMechanism result={result} />}

        {isCalc && <CalculationChain result={result} />}

        {isApparent && <ApparentConsumptionPanel />}

        {isEvidence && (
          <div className="space-y-3">
            <div>
              <h3 className="text-base font-bold text-neutral-100">Evidence Check</h3>
              <p className="mt-1 text-xs text-neutral-500">
                Verify the AI&apos;s price estimates against official UN Comtrade customs statistics — real
                declared transaction values, with a live source URL for each country.
              </p>
            </div>
            <div className="space-y-2">
              {allCountries.map((c) => (
                <TradeEvidencePanel key={c.name} countryName={c.name} pa={c.product_application} />
              ))}
            </div>
          </div>
        )}

        {!isFormula && !isCalc && !isApparent && !isEvidence && !isFinal && active && (
          <div className="space-y-3">
            <div>
              <h3 className="text-base font-bold text-neutral-100">{active.title}</h3>
              <p className="mt-1 text-xs text-neutral-500">{active.description}</p>
            </div>
            <StepDetailBody step={active} />
          </div>
        )}

        {isFinal && (
          <div className="space-y-3">
            <div>
              <h3 className="text-base font-bold text-neutral-100">Final Result Tree</h3>
              <p className="mt-1 text-xs text-neutral-500">
                The fully rolled-up tree rendered from the state after every step above.
              </p>
            </div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              <MetricCard label={`${result.market_name} — Global Total`} value={fmt(result.global_total_usd_mn)} icon={Globe2} accent="indigo" />
            </div>
            <ViewsGrid views={result.global_views} total={result.global_total_usd_mn} />
            <div className="space-y-2">
              {result.regions.map((r) => (
                <RegionPanel key={r.name} region={r} parentTotal={result.global_total_usd_mn} />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

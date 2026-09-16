"use client";

import { useEffect, useState } from "react";
import { Loader2, AlertTriangle, ExternalLink, Cpu, ChevronDown, ChevronRight } from "lucide-react";
import type { SegmentationDemoResult, CalcResult, CalcStep } from "@/lib/types";
import { calculateLeaf } from "@/lib/api";

const PROV = {
  verified: { label: "Verified", cls: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30" },
  derived: { label: "Derived", cls: "bg-sky-500/15 text-sky-300 border-sky-500/30" },
  assumed: { label: "Assumed", cls: "bg-amber-500/15 text-amber-300 border-amber-500/30" },
} as const;

function num(v: number, unit: string) {
  if (unit === "US$") return `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
  if (unit === "US$/L") return `$${v.toFixed(2)}`;
  if (unit.endsWith("%")) return unit;
  if (Math.abs(v) >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return v.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

function StepRow({ step, isResult }: { step: CalcStep; isResult: boolean }) {
  const [open, setOpen] = useState(false);
  const p = PROV[step.provenance];
  const hasDetail = !!(step.formula || step.note || step.url);

  return (
    <div
      className={`rounded-md border ${
        isResult ? "border-emerald-500/40 bg-emerald-500/10" : "border-white/5 bg-neutral-900/40"
      }`}
    >
      <button
        type="button"
        onClick={() => hasDetail && setOpen((v) => !v)}
        className={`flex w-full items-center gap-2 px-2.5 py-1.5 text-left ${hasDetail ? "" : "cursor-default"}`}
      >
        <span className="w-4 shrink-0 text-center font-mono text-xs text-neutral-600">{step.op}</span>
        <span className={`min-w-0 flex-1 truncate text-[11.5px] ${isResult ? "font-bold text-emerald-200" : "text-neutral-300"}`}>
          {step.label}
        </span>
        <span className={`shrink-0 text-right text-[11.5px] tabular-nums ${isResult ? "font-bold text-emerald-200" : "text-neutral-200"}`}>
          {num(step.value, step.unit)}
        </span>
        <span className="w-24 shrink-0 truncate text-right text-[10px] text-neutral-600">{step.unit}</span>
        <span className={`shrink-0 rounded border px-1.5 py-0.5 text-[9px] font-bold uppercase ${p.cls}`}>
          {p.label}
        </span>
        {hasDetail ? (
          open ? <ChevronDown size={12} className="shrink-0 text-neutral-600" />
               : <ChevronRight size={12} className="shrink-0 text-neutral-600" />
        ) : (
          <span className="w-3 shrink-0" />
        )}
      </button>
      {open && hasDetail && (
        <div className="space-y-1 border-t border-white/5 px-2.5 py-2 pl-8 text-[10.5px]">
          {step.formula && <div className="font-mono text-neutral-400">{step.formula}</div>}
          {step.note && <div className="text-neutral-500">{step.note}</div>}
          {step.source && <div className="text-neutral-500">Source: {step.source}</div>}
          {step.url && (
            <a href={step.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-sky-400 hover:text-sky-300">
              <ExternalLink size={9} /> Open source record
            </a>
          )}
        </div>
      )}
    </div>
  );
}

/** The demand build-up, shown as a visible chain. Every magnitude here is
 * COMPUTED from drivers -- no LLM is asked for a market size at any point.
 * The only judgement input is the application affinity, which is tagged
 * ASSUMED so it can't be mistaken for evidence. */
export default function CalculationChain({ result }: { result: SegmentationDemoResult }) {
  const affinityStep = result.steps.find((s) => s.step_id === "llm_affinity");
  const affinity = (affinityStep?.computed?.affinity ?? {}) as Record<string, Record<string, number>>;

  const countries = result.regions.flatMap((r) => r.countries.map((c) => c.name));
  const products = Object.keys(affinity);
  const applications = products.length ? Object.keys(affinity[products[0]]) : [];

  const [country, setCountry] = useState(countries.includes("India") ? "India" : countries[0] ?? "India");
  const [product, setProduct] = useState(products[0] ?? "Balsamic Vinegar");
  const [application, setApplication] = useState(applications[0] ?? "Food and Beverages");
  const [data, setData] = useState<CalcResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!products.length) return;
    let cancelled = false;

    // All state transitions happen inside the async closure (after the first
    // await tick) rather than synchronously in the effect body, so this never
    // triggers a cascading re-render.
    const run = async () => {
      await Promise.resolve();
      if (cancelled) return;
      setLoading(true);
      setError(null);
      try {
        const d = await calculateLeaf({ country, product, application, affinity });
        if (!cancelled) setData(d);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Calculation failed");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void run();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [country, product, application]);

  const sel =
    "rounded-md border border-white/10 bg-neutral-950 px-2 py-1 text-[11px] text-neutral-200 outline-none focus:border-sky-500/50";

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Cpu size={15} className="text-sky-300" />
        <span className="text-[11px] font-semibold uppercase tracking-wider text-sky-300">
          Demand build-up — how the number is calculated
        </span>
      </div>
      <p className="text-xs text-neutral-500">
        No LLM is asked for a market size here. Each figure below is computed from the one above it. Pick any
        combination and watch the whole chain recompute.
      </p>

      <div className="flex flex-wrap gap-2">
        <select className={sel} value={country} onChange={(e) => setCountry(e.target.value)}>
          {countries.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        <select className={sel} value={product} onChange={(e) => setProduct(e.target.value)}>
          {products.map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
        <select className={sel} value={application} onChange={(e) => setApplication(e.target.value)}>
          {applications.map((a) => (
            <option key={a} value={a}>{a}</option>
          ))}
        </select>
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-xs text-neutral-500">
          <Loader2 size={13} className="animate-spin" /> Computing…
        </div>
      )}
      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-rose-900/50 bg-rose-950/40 p-2.5 text-xs text-rose-300">
          <AlertTriangle size={13} /> {error}
        </div>
      )}

      {data && !loading && (
        <>
          <div className="flex flex-wrap gap-2">
            {(["verified", "derived", "assumed"] as const).map((k) => (
              <span key={k} className={`rounded border px-2 py-0.5 text-[10px] font-semibold ${PROV[k].cls}`}>
                {data.provenance_counts[k] ?? 0} {PROV[k].label}
              </span>
            ))}
          </div>

          <div className="space-y-1">
            {data.chain.map((s, i) => (
              <StepRow key={i} step={s} isResult={s.label === "REVENUE"} />
            ))}
          </div>

          <div className="grid grid-cols-3 gap-2">
            {[
              ["Volume", `${data.result.volume_mn_l.toLocaleString(undefined, { maximumFractionDigits: 2 })}M L`],
              ["Price", `$${data.result.price_usd_per_l.toFixed(2)}/L`],
              ["Revenue", `$${data.result.revenue_usd_mn.toLocaleString(undefined, { maximumFractionDigits: 2 })}M`],
            ].map(([l, v]) => (
              <div key={l} className="rounded-md border border-white/5 bg-neutral-900/60 p-2">
                <div className="text-[9.5px] uppercase tracking-wider text-neutral-500">{l}</div>
                <div className="text-sm font-semibold tabular-nums text-neutral-100">{v}</div>
              </div>
            ))}
          </div>

          <details className="rounded-md border border-white/5 bg-neutral-900/40">
            <summary className="cursor-pointer px-2.5 py-1.5 text-[10.5px] uppercase tracking-wider text-neutral-500">
              Per-capita consumption sub-model
            </summary>
            <div className="space-y-1 border-t border-white/5 p-2">
              {data.per_capita_detail.map((s, i) => (
                <StepRow key={i} step={s} isResult={false} />
              ))}
            </div>
          </details>

          <details className="rounded-md border border-amber-500/20 bg-amber-500/5">
            <summary className="cursor-pointer px-2.5 py-1.5 text-[10.5px] uppercase tracking-wider text-amber-300">
              Every assumption in this model ({Object.keys(data.parameters).length})
            </summary>
            <div className="space-y-1.5 border-t border-white/5 p-2.5">
              {Object.entries(data.parameters).map(([k, p]) => (
                <div key={k} className="text-[10.5px]">
                  <div className="flex justify-between gap-2">
                    <span className="text-neutral-300">{k.replace(/_/g, " ")}</span>
                    <span className="tabular-nums font-semibold text-amber-200">
                      {String(p.value)} {p.unit ?? ""}
                    </span>
                  </div>
                  <div className="text-neutral-500">{p.note}</div>
                </div>
              ))}
            </div>
          </details>
        </>
      )}
    </div>
  );
}

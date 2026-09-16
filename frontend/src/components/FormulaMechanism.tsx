"use client";

import { Calculator } from "lucide-react";
import type { SegmentationDemoResult, SegmentationDemoStep } from "@/lib/types";

const fmt = (n: number) => `$${n.toLocaleString(undefined, { maximumFractionDigits: 1 })}M`;

const VIEW_LABEL_FALLBACK: Record<string, string> = {
  product: "Product",
  source: "Source",
  flavor: "Flavor",
  application: "Application",
  distribution_channel: "Distribution Channel",
  sales_channel: "Sales Channel",
};

function FormulaLine({ label, formula }: { label: string; formula: string }) {
  return (
    <div className="rounded-md border border-white/5 bg-neutral-950/60 p-2.5">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-neutral-500">{label}</div>
      <div className="mt-1 font-mono text-xs text-neutral-300">{formula}</div>
    </div>
  );
}

/** Picks one concrete (region, country) pair from the real result to
 * substitute into the formulas below -- prefers "India" if present (since
 * it's the market this demo was built around), otherwise the first
 * region's first country. */
function pickExampleCountry(result: SegmentationDemoResult) {
  for (const region of result.regions) {
    const india = region.countries.find((c) => c.name === "India");
    if (india) return { region, country: india };
  }
  const region = result.regions[0];
  return region ? { region, country: region.countries[0] } : null;
}

/** No LLM call, no backend round-trip -- this is purely a client-side
 * explainer built from numbers already present in `result` (including the
 * RAW, pre-rescale LLM values pulled back out of that region's `llm_call`
 * step), so a stakeholder can see exactly which formula produced every
 * number in the demo. */
export default function FormulaMechanism({ result }: { result: SegmentationDemoResult }) {
  const canonicalKey = result.canonical_view ?? "application";
  const canonicalLabel = VIEW_LABEL_FALLBACK[canonicalKey] ?? canonicalKey;
  const example = pickExampleCountry(result);

  const regionStep: SegmentationDemoStep | undefined = example
    ? result.steps.find((s) => s.step_id === `llm_region_${example.region.name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`)
    : undefined;
  const rawCountryViews = (regionStep?.response as { country_views?: Record<string, Record<string, Record<string, number>>> } | undefined)
    ?.country_views?.[example?.country.name ?? ""];

  const rawCanonical = rawCountryViews?.[canonicalKey] ?? {};
  const rawCanonicalEntries = Object.entries(rawCanonical);
  const canonicalSum = rawCanonicalEntries.reduce((acc, [, v]) => acc + (Number(v) || 0), 0);

  // A second view, to demonstrate the rescale step -- "product" unless that
  // happens to be the canonical view (never true today, but future-proof).
  const rescaleKey = canonicalKey === "product" ? "source" : "product";
  const rawRescaleView = rawCountryViews?.[rescaleKey] ?? {};
  const rawRescaleEntries = Object.entries(rawRescaleView);
  const rawRescaleSum = rawRescaleEntries.reduce((acc, [, v]) => acc + (Number(v) || 0), 0);
  const scaledRescaleView = example?.country.views[rescaleKey] ?? {};

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Calculator size={16} className="text-indigo-300" />
        <span className="text-xs font-semibold uppercase tracking-wider text-indigo-300">Formula Mechanism</span>
      </div>
      <p className="text-xs text-neutral-500">
        Every dollar figure in this demo comes from exactly four deterministic formulas, applied to the LLM &amp;
        Web&apos;s raw per-category estimates. No percentage or parent total is ever asked of the LLM &amp; Web --
        only leaf-level dollar values, summed upward.
      </p>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <FormulaLine
          label={`1. Country Total (from ${canonicalLabel})`}
          formula={`Country Total = Σ (${canonicalLabel} category values, as given by the LLM & Web)`}
        />
        <FormulaLine
          label="2. Every other view, rescaled"
          formula="Rescaled value = (raw LLM & Web value ÷ raw view sum) × Country Total"
        />
        <FormulaLine label="3. Region Total" formula="Region Total = Σ (Country Totals within that region)" />
        <FormulaLine label="4. Global Total" formula="Global Total = Σ (Region Totals)" />
      </div>

      {example && (
        <div className="space-y-3 rounded-xl border border-indigo-500/20 bg-indigo-500/5 p-3">
          <div className="text-[11px] font-semibold uppercase tracking-wider text-indigo-300">
            Worked example — {example.country.name} ({example.region.name})
          </div>

          <div className="space-y-1.5">
            <div className="text-[10px] uppercase tracking-wider text-neutral-500">
              Step 1 — {example.country.name}&rsquo;s {canonicalLabel} values, as the LLM &amp; Web gave them
            </div>
            <div className="rounded-md border border-white/5 bg-neutral-950/60 p-2 font-mono text-[11px] text-neutral-400">
              {rawCanonicalEntries.map(([cat, v], i) => (
                <span key={cat}>
                  {i > 0 && " + "}
                  {cat} ({fmt(Number(v))})
                </span>
              ))}
              {" = "}
              <span className="font-semibold text-neutral-200">{fmt(canonicalSum)}</span>
            </div>
            <div className="text-[10px] text-neutral-600">
              → {example.country.name}&rsquo;s Country Total = {fmt(example.country.total_usd_mn)}
            </div>
          </div>

          {rawRescaleEntries.length > 0 && (
            <div className="space-y-1.5">
              <div className="text-[10px] uppercase tracking-wider text-neutral-500">
                Step 2 — rescaling the &ldquo;{VIEW_LABEL_FALLBACK[rescaleKey] ?? rescaleKey}&rdquo; view to agree with
                that same total
              </div>
              <div className="space-y-1 rounded-md border border-white/5 bg-neutral-950/60 p-2 font-mono text-[11px] text-neutral-400">
                {rawRescaleEntries.slice(0, 3).map(([cat, raw]) => {
                  const rawVal = Number(raw) || 0;
                  const scaled = scaledRescaleView[cat] ?? 0;
                  return (
                    <div key={cat}>
                      {cat}: ({fmt(rawVal)} ÷ {fmt(rawRescaleSum)}) × {fmt(example.country.total_usd_mn)} ={" "}
                      <span className="font-semibold text-neutral-200">{fmt(scaled)}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          <div className="space-y-1.5">
            <div className="text-[10px] uppercase tracking-wider text-neutral-500">
              Step 3 — {example.region.name}&rsquo;s Region Total
            </div>
            <div className="rounded-md border border-white/5 bg-neutral-950/60 p-2 font-mono text-[11px] text-neutral-400">
              {example.region.countries.map((c, i) => (
                <span key={c.name}>
                  {i > 0 && " + "}
                  {c.name} ({fmt(c.total_usd_mn)})
                </span>
              ))}
              {" = "}
              <span className="font-semibold text-neutral-200">{fmt(example.region.total_usd_mn)}</span>
            </div>
          </div>

          <div className="space-y-1.5">
            <div className="text-[10px] uppercase tracking-wider text-neutral-500">Step 4 — Global Total</div>
            <div className="rounded-md border border-white/5 bg-neutral-950/60 p-2 font-mono text-[11px] text-neutral-400">
              {result.regions.map((r, i) => (
                <span key={r.name}>
                  {i > 0 && " + "}
                  {r.name} ({fmt(r.total_usd_mn)})
                </span>
              ))}
              {" = "}
              <span className="font-semibold text-neutral-200">{fmt(result.global_total_usd_mn)}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

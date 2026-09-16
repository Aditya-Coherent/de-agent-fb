"use client";

import { useState } from "react";
import { Grid3x3 } from "lucide-react";
import type { SegmentationProductApplication } from "@/lib/types";

type Metric = "value" | "volume" | "price";

const METRICS: { key: Metric; label: string; unit: string }[] = [
  { key: "value", label: "Value", unit: "$M" },
  { key: "volume", label: "Volume", unit: "M litres" },
  { key: "price", label: "Unit price", unit: "$/L" },
];

const short = (s: string) => s.replace(" Vinegar", "").replace(" Industry", "").replace("Food and Beverages", "Food & Bev");

/** Product x Application joint matrix for one country -- the cell-level view
 * that the six independent segmentation lenses cannot give on their own
 * (e.g. "Balsamic Vinegar used in Food & Beverages"). Values come from the
 * backend's IPF/RAS balancing, so every row sums to that product's total and
 * every column sums to that application's total, exactly. */
export default function ProductApplicationMatrix({
  pa,
  countryTotal,
}: {
  pa: SegmentationProductApplication;
  countryTotal: number;
}) {
  const [metric, setMetric] = useState<Metric>("value");

  const cell = (product: string, application: string) =>
    pa.cells.find((c) => c.product === product && c.application === application);

  const fmt = (v: number | null | undefined) => {
    if (v === null || v === undefined) return "—";
    if (metric === "value") return `$${v.toLocaleString(undefined, { maximumFractionDigits: 1 })}M`;
    if (metric === "volume") return `${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
    return `$${v.toFixed(2)}`;
  };

  const cellValue = (product: string, application: string) => {
    const c = cell(product, application);
    if (!c) return null;
    if (metric === "value") return c.value_usd_mn;
    if (metric === "volume") return c.volume_mn_l;
    return c.price_usd_per_l;
  };

  const rowTotal = (product: string) => {
    const cs = pa.cells.filter((c) => c.product === product);
    if (metric === "price") return pa.prices_usd_per_l[product] ?? null;
    if (metric === "volume") return cs.reduce((a, c) => a + (c.volume_mn_l ?? 0), 0);
    return cs.reduce((a, c) => a + c.value_usd_mn, 0);
  };

  const colTotal = (application: string) => {
    const cs = pa.cells.filter((c) => c.application === application);
    const val = cs.reduce((a, c) => a + c.value_usd_mn, 0);
    const vol = cs.reduce((a, c) => a + (c.volume_mn_l ?? 0), 0);
    if (metric === "value") return val;
    if (metric === "volume") return vol;
    return vol > 0 ? val / vol : null; // blended realised price for that application
  };

  const grandTotal = metric === "value" ? countryTotal : metric === "volume" ? pa.total_volume_mn_l : pa.blended_price_usd_per_l;

  // Colour intensity by share of the country's total value, so the eye lands
  // on the economically meaningful cells regardless of which metric is shown.
  const intensity = (product: string, application: string) => {
    const c = cell(product, application);
    if (!c || countryTotal <= 0) return 0;
    return Math.min(1, (c.value_usd_mn / countryTotal) * 6);
  };

  return (
    <div className="space-y-2 rounded-lg border border-sky-500/20 bg-sky-500/5 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Grid3x3 size={14} className="text-sky-300" />
          <span className="text-[11px] font-semibold uppercase tracking-wider text-sky-300">
            Product × Application
          </span>
        </div>
        <div className="flex overflow-hidden rounded-md border border-white/10 text-[10px] font-semibold">
          {METRICS.map((m) => (
            <button
              key={m.key}
              type="button"
              onClick={() => setMetric(m.key)}
              className={`px-2 py-1 transition-colors ${
                metric === m.key ? "bg-sky-500/20 text-sky-200" : "text-neutral-500 hover:text-neutral-300"
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[11px]">
          <thead>
            <tr className="text-neutral-500">
              <th className="sticky left-0 bg-neutral-950/80 px-2 py-1 text-left font-medium">Product</th>
              {pa.applications.map((a) => (
                <th key={a} className="px-2 py-1 text-right font-medium">
                  {short(a)}
                </th>
              ))}
              <th className="px-2 py-1 text-right font-semibold text-neutral-400">Total</th>
            </tr>
          </thead>
          <tbody>
            {pa.products.map((p) => (
              <tr key={p} className="border-t border-white/5">
                <td className="sticky left-0 bg-neutral-950/80 px-2 py-1 text-neutral-300">{short(p)}</td>
                {pa.applications.map((a) => (
                  <td
                    key={a}
                    className="px-2 py-1 text-right tabular-nums text-neutral-300"
                    style={{ backgroundColor: `rgba(56, 189, 248, ${intensity(p, a) * 0.22})` }}
                  >
                    {fmt(cellValue(p, a))}
                  </td>
                ))}
                <td className="px-2 py-1 text-right font-semibold tabular-nums text-neutral-200">
                  {fmt(rowTotal(p))}
                </td>
              </tr>
            ))}
            <tr className="border-t border-white/10 text-neutral-200">
              <td className="sticky left-0 bg-neutral-950/80 px-2 py-1 font-semibold">Total</td>
              {pa.applications.map((a) => (
                <td key={a} className="px-2 py-1 text-right font-semibold tabular-nums">
                  {fmt(colTotal(a))}
                </td>
              ))}
              <td className="px-2 py-1 text-right font-bold tabular-nums text-neutral-100">{fmt(grandTotal)}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <p className="text-[10px] leading-relaxed text-neutral-500">
        {metric === "value" &&
          "IPF/RAS-balanced: every row sums to that product's total and every column to that application's total — exactly, adding no new dollars."}
        {metric === "volume" && "Litres are derived (value ÷ price per litre), never estimated directly."}
        {metric === "price" &&
          "Row = that product's price per litre (an input). Column total = blended realised price for that application, derived from its own product mix."}
      </p>
    </div>
  );
}

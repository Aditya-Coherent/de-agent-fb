"use client";

import { useMemo, useState } from "react";
import type { GenerateSummary } from "@/lib/types";

function fmtValue(v: number) {
  return v.toLocaleString(undefined, { maximumFractionDigits: 1 });
}
function fmtPct(v: number) {
  return `${(v * 100).toFixed(1)}%`;
}

export default function PivotExplorer({ summary }: { summary: GenerateSummary }) {
  const dims = Object.keys(summary.shares);
  const [dim, setDim] = useState(dims[0] ?? "");
  const [mode, setMode] = useState<"value" | "share">("value");

  const regions = Object.keys(summary.regional);
  const dimMap = summary.shares[dim] || {};
  const globalRoot = dimMap["__global__"]?.["__root__"] || {};
  const segments = Object.keys(globalRoot);

  const grid = useMemo(() => {
    return regions.map((region) => {
      const regionInfo = summary.regional[region];
      const regionValue = summary.global_anchor_2026 * regionInfo.value_share;
      const regionRoot = dimMap[region]?.["__root__"] || globalRoot;
      const cells = segments.map((seg) => {
        const share = regionRoot[seg] ?? globalRoot[seg] ?? 0;
        return { seg, share, value: regionValue * share };
      });
      return { region, regionValue, cells };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dim, regions.join(","), segments.join(",")]);

  const columnTotals = segments.map((seg, i) =>
    grid.reduce((sum, row) => sum + row.cells[i].value, 0)
  );
  const grandTotal = columnTotals.reduce((a, b) => a + b, 0);

  if (dims.length === 0) {
    return <p className="text-neutral-500">No segmentation dimensions available for this run.</p>;
  }

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <label className="text-xs uppercase tracking-wider text-neutral-500">Break down by</label>
        <select
          value={dim}
          onChange={(e) => setDim(e.target.value)}
          className="rounded-lg border border-white/10 bg-neutral-950 px-3 py-1.5 text-sm text-neutral-200 outline-none focus:border-indigo-400"
        >
          {dims.map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>

        <div className="ml-auto flex overflow-hidden rounded-lg border border-white/10">
          <button
            onClick={() => setMode("value")}
            className={`px-3 py-1.5 text-xs font-medium transition-colors ${
              mode === "value" ? "bg-indigo-500 text-white" : "text-neutral-400 hover:text-neutral-200"
            }`}
          >
            US$ Mn
          </button>
          <button
            onClick={() => setMode("share")}
            className={`px-3 py-1.5 text-xs font-medium transition-colors ${
              mode === "share" ? "bg-indigo-500 text-white" : "text-neutral-400 hover:text-neutral-200"
            }`}
          >
            % of region
          </button>
        </div>
      </div>

      <div className="overflow-x-auto rounded-lg border border-white/5">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wider text-neutral-500">
              <th className="sticky left-0 bg-neutral-900/95 py-2.5 pl-3 pr-3 font-medium">Region</th>
              {segments.map((seg) => (
                <th key={seg} className="whitespace-nowrap py-2.5 pr-4 text-right font-medium">
                  {seg}
                </th>
              ))}
              <th className="whitespace-nowrap py-2.5 pr-3 text-right font-medium text-neutral-300">Total</th>
            </tr>
          </thead>
          <tbody>
            {grid.map((row) => (
              <tr key={row.region} className="border-t border-white/5 hover:bg-white/[0.02]">
                <td className="sticky left-0 bg-neutral-900/95 py-2 pl-3 pr-3 font-medium text-neutral-200">
                  {row.region}
                </td>
                {row.cells.map((c) => (
                  <td key={c.seg} className="py-2 pr-4 text-right tabular-nums text-neutral-400">
                    {mode === "value" ? fmtValue(c.value) : fmtPct(c.share)}
                  </td>
                ))}
                <td className="py-2 pr-3 text-right tabular-nums font-medium text-neutral-300">
                  {fmtValue(row.regionValue)}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t border-white/10 bg-white/[0.02]">
              <td className="sticky left-0 bg-neutral-900/95 py-2.5 pl-3 pr-3 font-semibold text-neutral-200">
                Total
              </td>
              {columnTotals.map((v, i) => (
                <td key={segments[i]} className="py-2.5 pr-4 text-right tabular-nums font-semibold text-neutral-200">
                  {mode === "value" ? fmtValue(v) : fmtPct(grandTotal > 0 ? v / grandTotal : 0)}
                </td>
              ))}
              <td className="py-2.5 pr-3 text-right tabular-nums font-semibold text-neutral-100">
                {fmtValue(grandTotal)}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>

      <p className="mt-3 text-xs text-neutral-500">
        Switch the dimension above to instantly re-cut the same market across a different segmentation —
        Region stays fixed as rows, columns change to match.
      </p>
    </div>
  );
}

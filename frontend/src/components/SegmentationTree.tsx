"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, ArrowLeftRight } from "lucide-react";
import type { SegmentationProductApplication } from "@/lib/types";

type Direction = "product-application" | "application-product";

const money = (v: number) => `$${v.toLocaleString(undefined, { maximumFractionDigits: 1 })}M`;
const litres = (v: number | null) =>
  v === null ? "—" : `${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}M L`;
const perL = (v: number | null) => (v === null ? "—" : `$${v.toFixed(2)}/L`);

/** One row of the drill-down. Renders its own metrics, a share-of-parent bar,
 * and (when it has children) an expander. */
function TreeRow({
  label,
  revenue,
  volume,
  price,
  parentRevenue,
  depth,
  expandable,
  open,
  onToggle,
}: {
  label: string;
  revenue: number;
  volume: number | null;
  price: number | null;
  parentRevenue: number;
  depth: number;
  expandable: boolean;
  open?: boolean;
  onToggle?: () => void;
}) {
  const pct = parentRevenue > 0 ? (revenue / parentRevenue) * 100 : 0;
  const accent = depth === 0 ? "bg-sky-500/60" : "bg-violet-500/50";

  const body = (
    <>
      <div className="flex min-w-0 flex-1 items-center gap-2">
        {expandable ? (
          open ? (
            <ChevronDown size={13} className="shrink-0 text-neutral-500" />
          ) : (
            <ChevronRight size={13} className="shrink-0 text-neutral-500" />
          )
        ) : (
          <span className="w-[13px] shrink-0" />
        )}
        <span className={`truncate ${depth === 0 ? "font-medium text-neutral-200" : "text-neutral-400"}`}>
          {label}
        </span>
      </div>
      <div className="flex shrink-0 items-center gap-3 tabular-nums">
        <span className={`w-20 text-right ${depth === 0 ? "font-semibold text-neutral-100" : "text-neutral-300"}`}>
          {money(revenue)}
        </span>
        <span className="w-24 text-right text-neutral-400">{litres(volume)}</span>
        <span className="w-20 text-right text-neutral-500">{perL(price)}</span>
        <span className="w-12 text-right text-neutral-600">{pct.toFixed(1)}%</span>
      </div>
    </>
  );

  return (
    <div>
      {expandable ? (
        <button
          type="button"
          onClick={onToggle}
          className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[11px] transition-colors hover:bg-white/5"
          style={{ paddingLeft: `${8 + depth * 18}px` }}
        >
          {body}
        </button>
      ) : (
        <div
          className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-[11px]"
          style={{ paddingLeft: `${8 + depth * 18}px` }}
        >
          {body}
        </div>
      )}
      <div className="ml-2 h-[3px] overflow-hidden rounded-full bg-white/5" style={{ marginLeft: `${8 + depth * 18}px` }}>
        <div className={`h-full rounded-full ${accent}`} style={{ width: `${Math.min(100, pct)}%` }} />
      </div>
    </div>
  );
}

/** Nested drill-down over the IPF-balanced joint matrix. Because IPF
 * reproduces BOTH marginals exactly, the same underlying numbers can be
 * walked Product -> Application or Application -> Product, and each parent
 * always equals the exact sum of its children either way. Every node carries
 * Revenue (USD Mn), Volume (Mn litres) and unit price ($/L). */
export default function SegmentationTree({
  pa,
  countryTotal,
  countryName,
}: {
  pa: SegmentationProductApplication;
  countryTotal: number;
  countryName: string;
}) {
  const [direction, setDirection] = useState<Direction>("product-application");
  const [openNodes, setOpenNodes] = useState<Record<string, boolean>>({});

  const parents = direction === "product-application" ? pa.products : pa.applications;
  const childKey = direction === "product-application" ? "application" : "product";
  const parentKey = direction === "product-application" ? "product" : "application";

  const childrenOf = (parent: string) =>
    pa.cells
      .filter((c) => (parentKey === "product" ? c.product : c.application) === parent)
      .map((c) => ({
        label: childKey === "application" ? c.application : c.product,
        revenue: c.value_usd_mn,
        volume: c.volume_mn_l,
        price: c.price_usd_per_l > 0 ? c.price_usd_per_l : null,
      }))
      .sort((a, b) => b.revenue - a.revenue);

  const parentRows = parents
    .map((parent) => {
      const kids = childrenOf(parent);
      const revenue = kids.reduce((a, k) => a + k.revenue, 0);
      const volume = kids.every((k) => k.volume === null) ? null : kids.reduce((a, k) => a + (k.volume ?? 0), 0);
      // Product parent -> its own single price. Application parent -> blended
      // realised price, derived from whatever product mix serves it.
      const price = volume && volume > 0 ? revenue / volume : null;
      return { parent, kids, revenue, volume, price };
    })
    .sort((a, b) => b.revenue - a.revenue);

  const totalVolume = pa.total_volume_mn_l;

  return (
    <div className="space-y-2 rounded-lg border border-sky-500/20 bg-sky-500/5 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-sky-300">
          {direction === "product-application" ? "Product → Application" : "Application → Product"}
        </span>
        <button
          type="button"
          onClick={() =>
            setDirection((d) => (d === "product-application" ? "application-product" : "product-application"))
          }
          className="flex items-center gap-1.5 rounded-md border border-white/10 px-2 py-1 text-[10px] font-semibold text-neutral-400 transition-colors hover:border-white/20 hover:text-neutral-200"
        >
          <ArrowLeftRight size={11} /> Flip direction
        </button>
      </div>

      {/* column headers */}
      <div className="flex items-center gap-2 border-b border-white/5 px-2 pb-1 text-[10px] uppercase tracking-wider text-neutral-600">
        <span className="flex-1">{direction === "product-application" ? "Product / Application" : "Application / Product"}</span>
        <span className="w-20 text-right">Revenue</span>
        <span className="w-24 text-right">Volume</span>
        <span className="w-20 text-right">Price</span>
        <span className="w-12 text-right">Share</span>
      </div>

      {/* root */}
      <TreeRow
        label={`${countryName} — total`}
        revenue={countryTotal}
        volume={totalVolume}
        price={pa.blended_price_usd_per_l}
        parentRevenue={countryTotal}
        depth={0}
        expandable={false}
      />

      <div className="space-y-0.5">
        {parentRows.map(({ parent, kids, revenue, volume, price }) => {
          const isOpen = openNodes[parent] ?? false;
          return (
            <div key={parent}>
              <TreeRow
                label={parent}
                revenue={revenue}
                volume={volume}
                price={price}
                parentRevenue={countryTotal}
                depth={1}
                expandable
                open={isOpen}
                onToggle={() => setOpenNodes((s) => ({ ...s, [parent]: !isOpen }))}
              />
              {isOpen &&
                kids.map((k) => (
                  <TreeRow
                    key={k.label}
                    label={k.label}
                    revenue={k.revenue}
                    volume={k.volume}
                    price={k.price}
                    parentRevenue={revenue}
                    depth={2}
                    expandable={false}
                  />
                ))}
            </div>
          );
        })}
      </div>

      <p className="text-[10px] leading-relaxed text-neutral-500">
        Each parent equals the exact sum of its children in both directions (IPF/RAS-balanced). Revenue is
        LLM &amp; Web-estimated and deterministically reconciled; Volume is derived as Revenue ÷ Price per litre.
      </p>
    </div>
  );
}

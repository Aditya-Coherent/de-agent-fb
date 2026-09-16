"use client";

import { useState } from "react";
import {
  Loader2, AlertTriangle, ChevronDown, ChevronRight, ExternalLink,
  CheckCircle2, Sigma, Globe2,
} from "lucide-react";
import type { ApparentConsumption } from "@/lib/types";
import { getApparentConsumption } from "@/lib/api";

const COUNTRIES = [
  "Italy", "Germany", "Spain", "U.S.", "India", "China", "Japan", "South Korea",
  "Australia", "Canada", "Mexico", "U.K.", "France", "Brazil", "Argentina",
  "South Africa", "Turkey",
];

const mnL = (v: number) => `${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}M L`;
const mnUsd = (v: number) => `$${v.toLocaleString(undefined, { maximumFractionDigits: 1 })}M`;

function Badge({ kind }: { kind: string }) {
  const map: Record<string, string> = {
    verified: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
    estimated: "bg-amber-500/15 text-amber-300 border-amber-500/30",
    unavailable: "bg-neutral-600/20 text-neutral-400 border-neutral-600/40",
  };
  return (
    <span className={`rounded border px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider ${map[kind] ?? map.unavailable}`}>
      {kind}
    </span>
  );
}

/** Apparent consumption for any market/country, then the Product ->
 * Application drill-down beneath it. Trade is real customs data; production
 * is an estimate and is labelled as one everywhere it appears. */
export default function ApparentConsumptionPanel() {
  const [market, setMarket] = useState("Vinegar");
  const [country, setCountry] = useState("Italy");
  const [data, setData] = useState<ApparentConsumption | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});

  const run = async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await getApparentConsumption(market, country));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to build the model");
    } finally {
      setLoading(false);
    }
  };

  const inp = "rounded-md border border-white/10 bg-neutral-950 px-2.5 py-1.5 text-sm text-neutral-100 outline-none focus:border-cyan-500/50";
  const id = data?.identity;
  const pl = data?.plausibility;

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-base font-bold text-neutral-100">Apparent Consumption Model</h3>
        <p className="mt-1 text-xs text-neutral-500">
          Market size built from the supply-side identity, then split Product → Application. Trade is official
          customs data; production is the one figure customs cannot see.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-white/5 bg-neutral-900/40 p-3">
        <input className={`${inp} min-w-[160px] flex-1`} value={market} onChange={(e) => setMarket(e.target.value)} placeholder="Market" />
        <select className={inp} value={country} onChange={(e) => setCountry(e.target.value)}>
          {COUNTRIES.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <button
          onClick={run}
          disabled={loading || !market.trim()}
          className="flex items-center gap-2 rounded-lg bg-gradient-to-r from-cyan-500 to-blue-600 px-4 py-2 text-sm font-medium text-white shadow-lg shadow-cyan-500/20 transition-all hover:shadow-cyan-500/30 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {loading ? <Loader2 size={15} className="animate-spin" /> : <Sigma size={15} />}
          {loading ? "Building…" : "Build model"}
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-rose-900/50 bg-rose-950/40 p-3 text-sm text-rose-300">
          <AlertTriangle size={15} /> {error}
        </div>
      )}

      {data && id && (
        <>
          {/* THE IDENTITY */}
          <div className="rounded-xl border border-cyan-500/20 bg-cyan-500/5 p-4">
            <div className="mb-3 flex items-center gap-2">
              <Globe2 size={15} className="text-cyan-300" />
              <span className="font-mono text-xs font-semibold text-cyan-200">{id.formula}</span>
            </div>
            <table className="w-full text-sm">
              <tbody>
                <tr className="border-b border-white/5">
                  <td className="py-1.5 w-6 font-mono text-neutral-600"> </td>
                  <td className="py-1.5 text-neutral-300">Domestic production</td>
                  <td className="py-1.5 text-right font-semibold tabular-nums text-neutral-100">{mnL(id.production_mn_l)}</td>
                  <td className="py-1.5 pl-3 text-right"><Badge kind={id.production_provenance} /></td>
                </tr>
                <tr className="border-b border-white/5">
                  <td className="py-1.5 font-mono text-neutral-500">+</td>
                  <td className="py-1.5 text-neutral-300">Imports</td>
                  <td className="py-1.5 text-right font-semibold tabular-nums text-neutral-100">{mnL(id.imports_mn_l)}</td>
                  <td className="py-1.5 pl-3 text-right"><Badge kind={id.trade_provenance} /></td>
                </tr>
                <tr className="border-b border-white/10">
                  <td className="py-1.5 font-mono text-neutral-500">−</td>
                  <td className="py-1.5 text-neutral-300">Exports</td>
                  <td className="py-1.5 text-right font-semibold tabular-nums text-neutral-100">{mnL(id.exports_mn_l)}</td>
                  <td className="py-1.5 pl-3 text-right"><Badge kind={id.trade_provenance} /></td>
                </tr>
                <tr>
                  <td className="py-2 font-mono text-cyan-300">=</td>
                  <td className="py-2 font-bold text-cyan-200">Apparent consumption</td>
                  <td className="py-2 text-right text-base font-bold tabular-nums text-cyan-200">{mnL(id.apparent_consumption_mn_l)}</td>
                  <td className="py-2 pl-3 text-right text-[10px] text-neutral-500">{id.per_capita_l} L/person</td>
                </tr>
              </tbody>
            </table>

            {id.production_basis && (
              <div className="mt-2 text-[10px] leading-relaxed text-neutral-500">
                <span className="font-semibold text-neutral-400">Production basis:</span> {id.production_basis}
              </div>
            )}
            {id.trade_urls?.imports && (
              <div className="mt-2 flex flex-wrap gap-3">
                <a href={id.trade_urls.imports} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[10px] text-sky-400 hover:text-sky-300">
                  <ExternalLink size={9} /> Raw import record
                </a>
                <a href={id.trade_urls.exports} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[10px] text-sky-400 hover:text-sky-300">
                  <ExternalLink size={9} /> Raw export record
                </a>
                <span className="text-[10px] text-neutral-600">{id.trade_source}</span>
              </div>
            )}
          </div>

          {/* SELF-CHECK */}
          {pl && (
            <div
              className={`flex items-start gap-2 rounded-lg border p-3 text-xs ${
                pl.verdict === "plausible"
                  ? "border-emerald-900/50 bg-emerald-950/30 text-emerald-300"
                  : "border-amber-900/50 bg-amber-950/30 text-amber-300"
              }`}
            >
              {pl.verdict === "plausible" ? <CheckCircle2 size={14} className="mt-0.5 shrink-0" /> : <AlertTriangle size={14} className="mt-0.5 shrink-0" />}
              <span>
                <span className="font-semibold uppercase tracking-wider">
                  Self-check: {pl.verdict.replace("_", " ")}
                </span>
                <br />
                {pl.detail}
              </span>
            </div>
          )}

          {/* PRODUCT -> APPLICATION TREE */}
          <div>
            <div className="mb-2 flex items-center justify-between">
              <span className="text-[11px] font-semibold uppercase tracking-wider text-neutral-500">
                {data.country} → Product → Application
              </span>
              <span className="text-[11px] text-neutral-500">
                Total: <span className="font-semibold text-neutral-200">{mnUsd(data.total_revenue_usd_mn)}</span>
              </span>
            </div>
            <div className="space-y-1">
              {data.tree.map((p) => {
                const isOpen = open[p.product] ?? false;
                return (
                  <div key={p.product} className="rounded-lg border border-white/5 bg-neutral-950/60">
                    <button
                      type="button"
                      onClick={() => setOpen((s) => ({ ...s, [p.product]: !isOpen }))}
                      className="flex w-full items-center gap-2 p-2.5 text-left"
                    >
                      {isOpen ? <ChevronDown size={14} className="shrink-0 text-neutral-500" /> : <ChevronRight size={14} className="shrink-0 text-neutral-500" />}
                      <span className="min-w-0 flex-1 truncate text-xs font-medium text-neutral-200">{p.product}</span>
                      <span className="w-24 shrink-0 text-right text-xs tabular-nums text-neutral-300">{mnL(p.volume_mn_l)}</span>
                      <span className="w-20 shrink-0 text-right text-xs tabular-nums text-neutral-500">${p.price_usd_per_l.toFixed(2)}/L</span>
                      <span className="w-24 shrink-0 text-right text-xs font-semibold tabular-nums text-neutral-100">{mnUsd(p.revenue_usd_mn)}</span>
                    </button>
                    {isOpen && (
                      <div className="border-t border-white/5 py-1">
                        {p.applications.map((a) => (
                          <div key={a.application} className="flex items-center gap-2 px-2.5 py-1.5 pl-9 text-[11px]">
                            <span className="min-w-0 flex-1 truncate text-neutral-400">{a.application}</span>
                            <span className="w-24 shrink-0 text-right tabular-nums text-neutral-400">{mnL(a.volume_mn_l)}</span>
                            <span className="w-20 shrink-0 text-right tabular-nums text-neutral-600">${a.price_usd_per_l.toFixed(2)}/L</span>
                            <span className="w-24 shrink-0 text-right tabular-nums text-neutral-200">{mnUsd(a.revenue_usd_mn)}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
            <div className="mt-2 text-[10px] leading-relaxed text-neutral-600">
              Every litre is the apparent-consumption volume multiplied by a share; every dollar is litres ×
              price. Shares and production are estimates, labelled above. Imports and exports are measured
              customs records.
            </div>
          </div>

          {data.log.length > 0 && (
            <details className="rounded-lg border border-white/5 bg-neutral-950 p-3">
              <summary className="cursor-pointer text-[10px] uppercase tracking-wider text-neutral-500">Model log</summary>
              <pre className="mt-2 text-[10px] text-neutral-500">{data.log.join("\n")}</pre>
            </details>
          )}
        </>
      )}
    </div>
  );
}

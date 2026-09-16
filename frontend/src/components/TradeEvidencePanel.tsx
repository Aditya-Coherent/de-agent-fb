"use client";

import { useState } from "react";
import { ShieldCheck, ExternalLink, Loader2, AlertTriangle, Landmark } from "lucide-react";
import type { SegmentationProductApplication, TradeEvidence } from "@/lib/types";
import { getTradeEvidence } from "@/lib/api";

const usd = (v: number) => `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
const kg = (v: number) => `${v.toLocaleString(undefined, { maximumFractionDigits: 0 })} kg`;

function Badge({ kind }: { kind: "verified" | "modelled" }) {
  const verified = kind === "verified";
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider ${
        verified ? "bg-emerald-500/15 text-emerald-300" : "bg-amber-500/15 text-amber-300"
      }`}
    >
      {verified ? "Verified" : "Modelled"}
    </span>
  );
}

/** Side-by-side of the LLM's own price estimates against official customs
 * statistics (UN Comtrade). The point of this panel is provenance: every
 * number on the right has a live source URL a client can open themselves. */
export default function TradeEvidencePanel({
  countryName,
  pa,
}: {
  countryName: string;
  pa?: SegmentationProductApplication;
}) {
  const [loading, setLoading] = useState(false);
  const [ev, setEv] = useState<TradeEvidence | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setEv(await getTradeEvidence(countryName));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not reach the trade-evidence service");
    } finally {
      setLoading(false);
    }
  };

  const aiPrices = pa?.prices_usd_per_l ?? {};
  const aiBlended = pa?.blended_price_usd_per_l ?? null;

  return (
    <div className="space-y-2 rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <ShieldCheck size={14} className="text-emerald-300" />
          <span className="text-[11px] font-semibold uppercase tracking-wider text-emerald-300">
            Evidence check — official customs data
          </span>
        </div>
        {!ev && (
          <button
            type="button"
            onClick={load}
            disabled={loading}
            className="flex items-center gap-1.5 rounded-md border border-emerald-400/40 bg-emerald-500/10 px-2.5 py-1 text-[10px] font-semibold text-emerald-200 transition-colors hover:bg-emerald-500/20 disabled:opacity-50"
          >
            {loading ? <Loader2 size={11} className="animate-spin" /> : <Landmark size={11} />}
            {loading ? "Querying UN Comtrade…" : "Verify against UN Comtrade"}
          </button>
        )}
      </div>

      {error && (
        <div className="flex items-center gap-1.5 text-[11px] text-rose-300">
          <AlertTriangle size={12} /> {error}
        </div>
      )}

      {ev && !ev.available && (
        <div className="flex items-start gap-1.5 text-[11px] text-amber-300">
          <AlertTriangle size={12} className="mt-0.5 shrink-0" />
          <span>No customs evidence retrieved for {countryName}: {ev.reason}</span>
        </div>
      )}

      {ev?.available && (
        <div className="space-y-2.5">
          <div className="text-[10px] leading-relaxed text-neutral-400">
            <span className="font-semibold text-neutral-300">{ev.source}</span> · HS {ev.hs_code} —{" "}
            {ev.hs_description} · {ev.year}
          </div>

          {/* hard facts */}
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            {[
              { label: "Imports", flow: ev.imports },
              { label: "Exports", flow: ev.exports },
            ].map(({ label, flow }) =>
              flow ? (
                <div key={label} className="rounded-md border border-white/5 bg-neutral-900/60 p-2">
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] uppercase tracking-wider text-neutral-500">{label}</span>
                    <Badge kind="verified" />
                  </div>
                  <div className="mt-1 text-sm font-semibold tabular-nums text-neutral-100">
                    ${flow.unit_value_usd_per_l?.toFixed(2)}/L
                  </div>
                  <div className="text-[10px] text-neutral-500">
                    {usd(flow.value_usd)} ÷ {kg(flow.weight_kg)}
                  </div>
                </div>
              ) : null
            )}
            <div className="rounded-md border border-white/5 bg-neutral-900/60 p-2">
              <div className="flex items-center justify-between">
                <span className="text-[10px] uppercase tracking-wider text-neutral-500">AI blended est.</span>
                <Badge kind="modelled" />
              </div>
              <div className="mt-1 text-sm font-semibold tabular-nums text-neutral-100">
                {aiBlended !== null ? `$${aiBlended.toFixed(2)}/L` : "—"}
              </div>
              <div className="text-[10px] text-neutral-500">whole market incl. domestic</div>
            </div>
          </div>

          {/* verified prices by origin */}
          <div>
            <div className="mb-1 text-[10px] uppercase tracking-wider text-neutral-500">
              Verified transaction prices by country of origin
            </div>
            <div className="overflow-x-auto rounded-md border border-white/5">
              <table className="w-full text-[11px]">
                <thead>
                  <tr className="border-b border-white/5 text-left uppercase tracking-wider text-neutral-600">
                    <th className="px-2 py-1 font-medium">Origin</th>
                    <th className="px-2 py-1 text-right font-medium">Value</th>
                    <th className="px-2 py-1 text-right font-medium">Quantity</th>
                    <th className="px-2 py-1 text-right font-medium">Verified price</th>
                  </tr>
                </thead>
                <tbody>
                  {(ev.origins ?? []).slice(0, 6).map((o) => (
                    <tr key={o.partner} className="border-t border-white/5">
                      <td className="px-2 py-1 text-neutral-300">{o.partner}</td>
                      <td className="px-2 py-1 text-right tabular-nums text-neutral-400">{usd(o.value_usd)}</td>
                      <td className="px-2 py-1 text-right tabular-nums text-neutral-500">{kg(o.weight_kg)}</td>
                      <td className="px-2 py-1 text-right font-semibold tabular-nums text-emerald-300">
                        ${o.unit_value_usd_per_l.toFixed(2)}/L
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* AI vs verified */}
          {Object.keys(aiPrices).length > 0 && (
            <div>
              <div className="mb-1 text-[10px] uppercase tracking-wider text-neutral-500">
                AI price estimate vs. verified import range
              </div>
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(aiPrices).map(([product, price]) => {
                  const lo = Math.min(...(ev.origins ?? []).map((o) => o.unit_value_usd_per_l));
                  const hi = Math.max(...(ev.origins ?? []).map((o) => o.unit_value_usd_per_l));
                  const inRange = price >= lo && price <= hi;
                  return (
                    <span
                      key={product}
                      className={`rounded border px-1.5 py-0.5 text-[10px] ${
                        inRange
                          ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200"
                          : "border-neutral-600/40 bg-neutral-800/60 text-neutral-400"
                      }`}
                      title={inRange ? "Within the verified import price range" : "Outside the verified import range"}
                    >
                      {product.replace(" Vinegar", "")} ${price.toFixed(2)}
                      {inRange ? " ✓" : ""}
                    </span>
                  );
                })}
              </div>
            </div>
          )}

          <p className="text-[10px] leading-relaxed text-neutral-500">{ev.coverage_note}</p>

          <div className="flex flex-wrap gap-3">
            {ev.source_urls?.imports && (
              <a
                href={ev.source_urls.imports}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-[10px] text-sky-400 hover:text-sky-300"
              >
                <ExternalLink size={10} /> Open the raw import record
              </a>
            )}
            {ev.source_urls?.by_partner && (
              <a
                href={ev.source_urls.by_partner}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-[10px] text-sky-400 hover:text-sky-300"
              >
                <ExternalLink size={10} /> Open the by-origin record
              </a>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

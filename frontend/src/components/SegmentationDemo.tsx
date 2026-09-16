"use client";

import { useState } from "react";
import { Loader2, AlertTriangle, Sparkles, Info } from "lucide-react";
import type { SegmentationDemoResult } from "@/lib/types";
import { generateSegmentationDemo } from "@/lib/api";
import SegmentationStepWizard, { NarrativeCard } from "@/components/SegmentationStepWizard";

/** Standalone demo artifact: for a given market, an LLM estimates (1) six
 * fixed segmentation views (Product/Source/Flavor/Application/Distribution
 * Channel/Sales Channel) as % shares, and (2) region/country revenue
 * weights -- every USD Mn total is then computed by the backend
 * (`me_engine.segmentation_demo`), never trusted from the LLM's own
 * arithmetic. Every step (including the exact prompt/response of each real
 * LLM call) is walkable via `SegmentationStepWizard`, and the full run is
 * persisted server-side to `output/segmentation_demo/<slug>.json`. */
export default function SegmentationDemo() {
  const [market, setMarket] = useState("Vinegar");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SegmentationDemoResult | null>(null);

  const handleGenerate = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await generateSegmentationDemo(market);
      setResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Segmentation demo generation failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-5">
      <div className="space-y-1">
        <h2 className="text-lg font-bold text-neutral-100">Segmentation Demo</h2>
        <p className="text-sm text-neutral-500">
          Product × Source × Flavor × Application × Distribution Channel × Sales Channel, each independently
          summing to the same total, rolled up by Region → Country → Global. Click through each step below to
          see exactly what was asked of the LLM & Web and what it returned.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-white/5 bg-neutral-900/40 p-3 backdrop-blur-sm">
        <input
          className="min-w-[220px] flex-1 rounded-lg border border-white/10 bg-neutral-950 p-2 text-sm text-neutral-100 outline-none placeholder:text-neutral-600 focus:border-fuchsia-500/50"
          value={market}
          onChange={(e) => setMarket(e.target.value)}
          placeholder="e.g. Vinegar"
        />
        <button
          onClick={handleGenerate}
          disabled={loading || !market.trim()}
          className="flex items-center gap-2 rounded-lg bg-gradient-to-r from-fuchsia-500 to-violet-600 px-4 py-2 text-sm font-medium text-white shadow-lg shadow-fuchsia-500/20 transition-all hover:shadow-fuchsia-500/30 disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none"
        >
          {loading ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
          {loading ? "Fetching from LLM & Web…" : "Generate"}
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-rose-900/50 bg-rose-950/40 p-3 text-sm text-rose-300">
          <AlertTriangle size={15} /> {error}
        </div>
      )}

      {result && (
        <>
          <div className="flex items-start gap-2 rounded-lg border border-white/5 bg-neutral-900/40 p-3 text-xs text-neutral-500">
            <Info size={15} className="mt-0.5 shrink-0" />
            <span>
              {result.grounded
                ? `LLM & Web-estimated, base year ${result.base_year}, generated ${result.generated_at ?? ""} -- category taxonomy is fixed (this report's own segment list); every USD figure is computed deterministically from the LLM & Web's percentage estimates, never taken as a direct LLM & Web output.`
                : "The LLM & Web did not return a usable total for this market -- figures default to $0. Try again or check the API key."}
            </span>
          </div>

          {result.narrative && <NarrativeCard narrative={result.narrative} />}

          <SegmentationStepWizard result={result} />

          {result.log.length > 0 && (
            <details className="rounded-lg border border-white/5 bg-neutral-950 p-3">
              <summary className="cursor-pointer text-xs text-neutral-500">Generation log</summary>
              <pre className="mt-2 max-h-48 overflow-auto text-xs text-neutral-500">{result.log.join("\n")}</pre>
            </details>
          )}
        </>
      )}
    </div>
  );
}

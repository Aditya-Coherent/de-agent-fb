"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, ArrowRight, Database, FlaskConical, Loader2, Plus, Trash2 } from "lucide-react";
import type { DeskResearchRegion, DeskSegmentDefinitionDimension } from "@/lib/types";
import { DESK_RESEARCH_REGIONS } from "@/lib/types";
import { previewDeskSegmentDefinition, submitDeskSegmentDefinition } from "@/lib/api";

const inputCls =
  "w-full rounded-lg border border-white/10 bg-neutral-950 p-2.5 text-sm text-neutral-100 outline-none placeholder:text-neutral-600 focus:border-indigo-500/50";
const labelCls = "text-xs font-medium uppercase tracking-wider text-neutral-500";
const cardCls = "space-y-3 rounded-xl border border-white/5 bg-neutral-900/40 p-4 backdrop-blur-sm";
const btnPrimary =
  "flex items-center gap-2 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-600 px-4 py-2.5 text-sm font-medium text-white shadow-lg shadow-indigo-500/20 transition-all hover:shadow-indigo-500/30 disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none";
const btnSecondary =
  "flex items-center gap-2 rounded-lg border border-white/10 bg-neutral-900/60 px-4 py-2.5 text-sm font-medium text-neutral-200 backdrop-blur-sm transition-colors hover:border-white/20 hover:bg-neutral-800 disabled:opacity-40 disabled:cursor-not-allowed";

const RECONCILE_TOLERANCE_PCT = 1;

function newSegment(index: number): DeskSegmentDefinitionDimension["segments"][number] {
  return { name: `Segment ${index + 1}`, region_shares: {} };
}

function newDimension(index: number): DeskSegmentDefinitionDimension {
  return { dimension_name: `By Dimension ${index + 1}`, segments: [newSegment(0)] };
}

// Testing/trial convenience only -- NOT tied to any market, never sent
// anywhere unless the user hits "Approve & Continue" themselves like any
// other manually-entered definition. Saves retyping a full, reconciling
// fixture during manual testing.
const SAMPLE_DIMENSIONS: DeskSegmentDefinitionDimension[] = [
  {
    dimension_name: "By Installation",
    segments: [
      {
        name: "Flooring",
        region_shares: {
          "North America": 72.7, "Europe": 76.1, "Asia Pacific": 74.8,
          "Latin America": 78.2, "Middle East & Africa": 78.0,
        },
      },
      {
        name: "Wall Cladding",
        region_shares: {
          "North America": 27.3, "Europe": 23.9, "Asia Pacific": 25.2,
          "Latin America": 21.8, "Middle East & Africa": 22.0,
        },
      },
    ],
  },
  {
    dimension_name: "By Material Type",
    segments: [
      {
        name: "Polyethylene",
        region_shares: {
          "North America": 59.8, "Europe": 62.0, "Asia Pacific": 62.0,
          "Latin America": 61.3, "Middle East & Africa": 63.1,
        },
      },
      {
        name: "Polypropylene",
        region_shares: {
          "North America": 22.9, "Europe": 21.2, "Asia Pacific": 21.2,
          "Latin America": 22.0, "Middle East & Africa": 21.1,
        },
      },
      {
        name: "Nylon",
        region_shares: {
          "North America": 10.9, "Europe": 11.6, "Asia Pacific": 11.6,
          "Latin America": 10.0, "Middle East & Africa": 11.6,
        },
      },
      {
        name: "Others (Polyamides, etc.)",
        region_shares: {
          "North America": 6.4, "Europe": 5.2, "Asia Pacific": 5.2,
          "Latin America": 6.7, "Middle East & Africa": 4.2,
        },
      },
    ],
  },
];

function regionTotals(dim: DeskSegmentDefinitionDimension): Record<DeskResearchRegion, { sum: number; complete: boolean }> {
  const out = {} as Record<DeskResearchRegion, { sum: number; complete: boolean }>;
  for (const region of DESK_RESEARCH_REGIONS) {
    let sum = 0;
    let complete = true;
    for (const seg of dim.segments) {
      const v = seg.region_shares[region];
      if (v === undefined) complete = false;
      else sum += v;
    }
    out[region] = { sum, complete };
  }
  return out;
}

function dimensionErrors(dim: DeskSegmentDefinitionDimension): string[] {
  const errors: string[] = [];
  if (!dim.dimension_name.trim()) errors.push("Dimension name is required.");
  if (dim.segments.length === 0) errors.push(`"${dim.dimension_name}": at least one segment is required.`);
  for (const seg of dim.segments) {
    if (!seg.name.trim()) errors.push(`"${dim.dimension_name}": every segment needs a name.`);
  }
  const totals = regionTotals(dim);
  for (const region of DESK_RESEARCH_REGIONS) {
    const { sum, complete } = totals[region];
    if (!complete) {
      errors.push(`"${dim.dimension_name}" / ${region}: some segments are missing a % share.`);
    } else if (Math.abs(sum - 100) > RECONCILE_TOLERANCE_PCT) {
      errors.push(`"${dim.dimension_name}" / ${region}: shares sum to ${sum.toFixed(1)}%, expected ~100%.`);
    }
  }
  return errors;
}

interface Props {
  market: string;
  source: "db" | "reference";
  skeletonChoice: string;
  approved: boolean;
  onApprovedChange: (approved: boolean) => void;
}

export default function DeskSegmentDefinitionPanel({ market, source, skeletonChoice, approved, onApprovedChange }: Props) {
  const [baseYear, setBaseYear] = useState(() => new Date().getFullYear() - 1);
  const [dimensions, setDimensions] = useState<DeskSegmentDefinitionDimension[]>([newDimension(0)]);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadedFor, setLoadedFor] = useState<string | null>(null);

  // A previously-approved definition doesn't carry over silently once the
  // user picks a different market/source -- the gate must be re-satisfied
  // for whatever `GET /api/generate` is about to actually run against.
  useEffect(() => {
    if (approved) onApprovedChange(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market, source, skeletonChoice]);

  const allErrors = dimensions.flatMap(dimensionErrors);

  const handleLoad = async () => {
    if (!market.trim()) return;
    setError(null);
    setLoading(true);
    try {
      const preview = await previewDeskSegmentDefinition(market, source, skeletonChoice);
      setBaseYear(preview.base_year);
      setDimensions(
        preview.dimensions.length
          ? preview.dimensions.map((d) => ({
              dimension_name: d.dimension_name,
              segments: d.segments.length ? d.segments : [newSegment(0)],
            }))
          : [newDimension(0)]
      );
      setLoadedFor(market);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load segments for this market");
    } finally {
      setLoading(false);
    }
  };

  const handleFillSample = () => {
    setError(null);
    // Deep-copy so subsequent edits never mutate the shared fixture constant.
    setDimensions(SAMPLE_DIMENSIONS.map((d) => ({
      dimension_name: d.dimension_name,
      segments: d.segments.map((s) => ({ name: s.name, region_shares: { ...s.region_shares } })),
    })));
    setLoadedFor(null);
  };

  const updateDimension = (index: number, patch: Partial<DeskSegmentDefinitionDimension>) => {
    const next = [...dimensions];
    next[index] = { ...next[index], ...patch };
    setDimensions(next);
  };
  const addDimension = () => setDimensions([...dimensions, newDimension(dimensions.length)]);
  const removeDimension = (index: number) => setDimensions(dimensions.filter((_, i) => i !== index));

  const addSegment = (dimIndex: number) => {
    const dim = dimensions[dimIndex];
    updateDimension(dimIndex, { segments: [...dim.segments, newSegment(dim.segments.length)] });
  };
  const removeSegment = (dimIndex: number, segIndex: number) => {
    const dim = dimensions[dimIndex];
    updateDimension(dimIndex, { segments: dim.segments.filter((_, i) => i !== segIndex) });
  };
  const updateSegment = (
    dimIndex: number, segIndex: number, patch: Partial<DeskSegmentDefinitionDimension["segments"][number]>
  ) => {
    const dim = dimensions[dimIndex];
    const segs = [...dim.segments];
    segs[segIndex] = { ...segs[segIndex], ...patch };
    updateDimension(dimIndex, { segments: segs });
  };
  const updateShare = (dimIndex: number, segIndex: number, region: DeskResearchRegion, value: string) => {
    const dim = dimensions[dimIndex];
    const seg = dim.segments[segIndex];
    const region_shares = { ...seg.region_shares };
    if (value.trim() === "") delete region_shares[region];
    else region_shares[region] = Number(value);
    updateSegment(dimIndex, segIndex, { region_shares });
  };

  const handleSubmit = async () => {
    setError(null);
    if (!market.trim()) {
      setError("Market name is required.");
      return;
    }
    if (allErrors.length > 0) {
      setError(allErrors[0]);
      return;
    }
    setSubmitting(true);
    try {
      await submitDeskSegmentDefinition({ market_name: market, base_year: baseYear, dimensions });
      onApprovedChange(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Segment definition submission failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className={cardCls}>
      <p className="text-xs text-neutral-500">
        <strong className="text-neutral-300">Define Segments &amp; Base-Year Regional Shares</strong> -- required
        before Desk Research can run. These base-year numbers are yours: the AI analyst team will not re-derive or
        override them for any dimension defined here; it only sizes the market and finds supporting evidence within
        this structure.
      </p>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <div className="flex flex-wrap gap-2">
            <button onClick={handleLoad} disabled={loading || !market.trim() || approved} className={btnSecondary}>
              {loading ? <Loader2 size={15} className="animate-spin" /> : <Database size={15} />}
              {loading ? "Loading…" : "Load segments from database"}
            </button>
            <button
              onClick={handleFillSample}
              disabled={approved}
              title="Fills the form with a fixed sample fixture, unrelated to the typed market -- testing convenience only."
              className={btnSecondary + " border-dashed opacity-70 hover:opacity-100"}
            >
              <FlaskConical size={15} />
              Fill with sample data <span className="text-neutral-500">(for testing)</span>
            </button>
          </div>
          {loadedFor && <p className="mt-1 text-xs text-neutral-600">Pre-filled from '{loadedFor}'. Fully editable below.</p>}
        </div>
        <div>
          <label className={labelCls}>Base year</label>
          <input
            type="number"
            className={inputCls}
            value={baseYear}
            onChange={(e) => setBaseYear(Number(e.target.value))}
            disabled={approved}
          />
          <p className="mt-1 text-xs text-neutral-600">
            Defaults to {new Date().getFullYear() - 1} (last full year of actual data) -- editable.
          </p>
        </div>
      </div>

      <div className="space-y-4">
        {dimensions.map((dim, di) => {
          const totals = regionTotals(dim);
          return (
            <div key={di} className="space-y-2 rounded-lg border border-white/10 bg-neutral-950/60 p-3">
              <div className="flex items-center gap-2">
                <input
                  className={`${inputCls} max-w-xs font-semibold`}
                  value={dim.dimension_name}
                  onChange={(e) => updateDimension(di, { dimension_name: e.target.value })}
                  disabled={approved}
                  placeholder='e.g. "By Installation"'
                />
                {!approved && (
                  <button onClick={() => removeDimension(di)} className="text-rose-400 hover:text-rose-300">
                    <Trash2 size={15} />
                  </button>
                )}
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs uppercase tracking-wider text-neutral-600">
                      <th className="pb-1 pr-2 font-medium">Segment</th>
                      {DESK_RESEARCH_REGIONS.map((r) => (
                        <th key={r} className="pb-1 pr-2 font-medium">{r}</th>
                      ))}
                      <th className="pb-1 font-medium" />
                    </tr>
                  </thead>
                  <tbody>
                    {dim.segments.map((seg, si) => (
                      <tr key={si} className="border-t border-white/5">
                        <td className="py-1 pr-2">
                          <input
                            className={inputCls}
                            value={seg.name}
                            onChange={(e) => updateSegment(di, si, { name: e.target.value })}
                            disabled={approved}
                          />
                        </td>
                        {DESK_RESEARCH_REGIONS.map((region) => (
                          <td key={region} className="py-1 pr-2">
                            <input
                              type="number"
                              min={0}
                              max={100}
                              className={`${inputCls} w-24`}
                              placeholder="%"
                              value={seg.region_shares[region] ?? ""}
                              onChange={(e) => updateShare(di, si, region, e.target.value)}
                              disabled={approved}
                            />
                          </td>
                        ))}
                        <td className="py-1">
                          {!approved && dim.segments.length > 1 && (
                            <button onClick={() => removeSegment(di, si)} className="text-rose-400 hover:text-rose-300">
                              <Trash2 size={14} />
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                    <tr className="border-t border-white/10 text-xs">
                      <td className="py-1 pr-2 font-medium text-neutral-500">Total</td>
                      {DESK_RESEARCH_REGIONS.map((region) => {
                        const { sum, complete } = totals[region];
                        const ok = complete && Math.abs(sum - 100) <= RECONCILE_TOLERANCE_PCT;
                        return (
                          <td key={region} className={`py-1 pr-2 font-medium ${ok ? "text-emerald-400" : "text-rose-400"}`}>
                            {complete ? `${sum.toFixed(1)}%` : "incomplete"}
                          </td>
                        );
                      })}
                      <td />
                    </tr>
                  </tbody>
                </table>
              </div>
              {!approved && (
                <button onClick={() => addSegment(di)} className={btnSecondary + " px-2 py-1 text-xs"}>
                  <Plus size={13} /> Add segment
                </button>
              )}
            </div>
          );
        })}
      </div>

      {!approved && (
        <button onClick={addDimension} className={btnSecondary}>
          <Plus size={15} /> Add dimension
        </button>
      )}

      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-rose-900/50 bg-rose-950/40 p-3 text-sm text-rose-300">
          <AlertTriangle size={15} /> {error}
        </div>
      )}

      {allErrors.length > 0 && !approved && (
        <div className="space-y-1 rounded-lg border border-amber-900/50 bg-amber-950/30 p-3 text-xs text-amber-300">
          {allErrors.map((e, i) => (
            <div key={i} className="flex items-start gap-2">
              <AlertTriangle size={13} className="mt-0.5 shrink-0" /> {e}
            </div>
          ))}
        </div>
      )}

      <button
        onClick={handleSubmit}
        disabled={submitting || approved || !market.trim() || allErrors.length > 0}
        className={btnPrimary}
      >
        {submitting ? <Loader2 size={16} className="animate-spin" /> : <ArrowRight size={16} />}
        {approved ? "Segments approved" : submitting ? "Submitting…" : "Approve & Continue"}
      </button>
    </div>
  );
}

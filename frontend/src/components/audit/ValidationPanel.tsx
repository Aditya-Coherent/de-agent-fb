"use client";

import { useMemo } from "react";
import { CheckCircle2, XCircle, MinusCircle } from "lucide-react";
import type { AuditEvent } from "@/lib/types";
import { GapNote, PrettyBlock, Field, fmtTime } from "./shared";

// `fact_validator.validate_candidate()` SHORT-CIRCUITS in this exact order —
// read directly from src/me_engine/demand_usage/fact_validator.py:
//   1. currency/monetary-value-for-physical-driver
//   2. geography
//   3. year
//   4. unit
//   5. metric-family
// The FIRST failing check's reason code is returned; every check after it
// was NEVER RUN. This panel renders that honestly: earlier checks as
// implicit PASS, the failing check as FAIL, later checks as "not evaluated".
const CHECK_ORDER: { matches: (code: string) => boolean; label: string }[] = [
  { matches: (c) => c === "wrong_metric_monetary_value_for_physical_driver", label: "1. Currency / monetary-value-for-physical-driver" },
  { matches: (c) => c === "geography_mismatch", label: "2. Geography" },
  { matches: (c) => c === "year_mismatch", label: "3. Year" },
  { matches: (c) => c === "unit_mismatch", label: "4. Unit" },
  { matches: (c) => c.startsWith("wrong_metric_family"), label: "5. Metric family" },
];

function CheckRow({ label, state }: { label: string; state: "pass" | "fail" | "not_evaluated" }) {
  return (
    <div className="flex items-center gap-2 text-sm">
      {state === "pass" && <CheckCircle2 size={15} className="text-emerald-400" />}
      {state === "fail" && <XCircle size={15} className="text-rose-400" />}
      {state === "not_evaluated" && <MinusCircle size={15} className="text-neutral-600" />}
      <span className={state === "not_evaluated" ? "text-neutral-500" : "text-neutral-200"}>{label}</span>
      <span className="text-xs text-neutral-500">
        {state === "pass" ? "implicit pass (validator did not fire)" : state === "fail" ? "FAIL — this is the returned reason code" : "not evaluated — validator stops at first failure"}
      </span>
    </div>
  );
}

function ValidationEventCard({ event }: { event: AuditEvent }) {
  const rejectionCode = event.status !== "success" && event.status !== "pass" ? (event.error ?? "") : null;
  return (
    <div className="space-y-2 rounded-lg border border-white/10 bg-neutral-950/60 p-3">
      <div className="flex items-center justify-between text-xs text-neutral-500">
        <span>seq {event.seq} · {event.bucket ?? "—"} / {event.country ?? "—"}</span>
        <span>{fmtTime(event.timestamp)}</span>
      </div>
      {rejectionCode == null ? (
        <div className="text-sm text-emerald-300">All 5 checks passed (status: {event.status ?? "accepted"}).</div>
      ) : (
        <div className="space-y-1.5">
          {CHECK_ORDER.reduce<{ failed: boolean; nodes: React.ReactNode[] }>(
            (acc, check, i) => {
              const isFailure = check.matches(rejectionCode);
              const state: "pass" | "fail" | "not_evaluated" = acc.failed ? "not_evaluated" : isFailure ? "fail" : "pass";
              acc.nodes.push(<CheckRow key={i} label={check.label} state={state} />);
              if (isFailure) acc.failed = true;
              return acc;
            },
            { failed: false, nodes: [] },
          ).nodes}
          <Field label="Exact reason code" value={<span className="font-mono text-rose-300">{rejectionCode}</span>} />
        </div>
      )}
      <PrettyBlock title="Full validation payload" value={event.validation} />
    </div>
  );
}

export default function ValidationPanel({ events }: { events: AuditEvent[] }) {
  const factValidationEvents = useMemo(() => events.filter((e) => e.event_type === "fact_validation"), [events]);

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-white/5 bg-neutral-900/40 p-4">
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-neutral-500">
          Deterministic validation semantics (fact_validator.validate_candidate)
        </h3>
        <p className="text-sm text-neutral-400">
          The validator checks, in this exact order, and stops at the first failure: (1) currency/monetary-value
          for a physical driver, (2) geography, (3) year, (4) unit, (5) metric family. A "rejected" status means
          only the checks up to and including the failing one actually ran — later checks are never evaluated and
          must not be shown as passed.
        </p>
      </div>

      {factValidationEvents.length === 0 ? (
        <GapNote>
          No fact_validation event has been recorded in this run's audit log. The rejections actually observed
          in this run's source_attempt events (e.g. "no_source_found", "extraction_failed") are from the
          ACQUISITION layer, not from validate_candidate — they happen before a candidate ever reaches the
          deterministic validator, so no per-check breakdown exists for them. This is rendered here as a known
          gap rather than a fabricated validation result.
        </GapNote>
      ) : (
        <div className="space-y-3">
          {factValidationEvents.map((e) => (
            <ValidationEventCard key={e.event_id} event={e} />
          ))}
        </div>
      )}
    </div>
  );
}

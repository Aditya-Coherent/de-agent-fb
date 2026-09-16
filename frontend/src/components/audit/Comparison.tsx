"use client";

import { GapNote } from "./shared";

// Comparison mode has no Desk Research audit data at all today (the audit
// layer only instruments the Usage-Based pipeline — see AuditDashboard's
// methodology selector). Render the honest unavailable state rather than
// fabricating a comparison table.
export default function Comparison() {
  return (
    <div className="rounded-xl border border-white/5 bg-neutral-900/40 p-4">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-neutral-500">Comparison</h3>
      <GapNote>
        Not available until both methodologies are instrumented. The Desk Research pipeline has no audit
        trail today — only the Usage-Based (Demand-Side) pipeline emits audit events via
        me_engine.audit.store. Comparing a Usage-Based run against a Desk Research run would require
        fabricating Desk Research audit data, which this explorer will not do.
      </GapNote>
    </div>
  );
}

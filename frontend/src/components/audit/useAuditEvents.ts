"use client";

import { useEffect, useState } from "react";
import type { AuditEvent } from "@/lib/types";
import { getAuditEvents } from "@/lib/api";

// Fetches the FULL event list for a run (paged, oldest-first — the order the
// backend already appends them in) so every panel that needs to build a
// chronological narrative (Execution Trace, Evidence, Number Lineage,
// Validation) shares one fetch instead of five separate ones with five
// separate loading states.
const PAGE = 500;

export function useAuditEvents(market: string, runId: string) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      setEvents([]);
      try {
        let offset = 0;
        let all: AuditEvent[] = [];
        for (;;) {
          const res = await getAuditEvents(market, runId, { limit: PAGE, offset });
          all = all.concat(res.events);
          offset += PAGE;
          if (offset >= res.total || res.events.length === 0) break;
        }
        if (!cancelled) setEvents(all);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load audit events");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [market, runId]);

  return { events, loading, error };
}

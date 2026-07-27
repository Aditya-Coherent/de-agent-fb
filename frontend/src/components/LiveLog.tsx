"use client";

import { useEffect, useRef } from "react";
import { Terminal } from "lucide-react";

export default function LiveLog({ lines, active }: { lines: string[]; active: boolean }) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [lines.length]);

  if (lines.length === 0 && !active) return null;

  return (
    <div className="overflow-hidden rounded-xl border border-white/5 bg-neutral-950 shadow-inner">
      <div className="flex items-center gap-2 border-b border-white/5 bg-neutral-900/50 px-4 py-2.5 text-xs font-medium text-neutral-400">
        <Terminal size={14} />
        <span>{active ? "Running the agent fleet" : "Run log"}</span>
        {active && (
          <span className="relative ml-1 flex h-1.5 w-1.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-500" />
          </span>
        )}
      </div>
      <div className="max-h-72 space-y-1 overflow-y-auto p-4 font-mono text-[13px] leading-relaxed text-neutral-300">
        {lines.map((line, i) => (
          <div key={i} className="flex gap-2">
            <span className="select-none text-indigo-500/60">›</span>
            <span>{line}</span>
          </div>
        ))}
        <div ref={endRef} />
      </div>
    </div>
  );
}

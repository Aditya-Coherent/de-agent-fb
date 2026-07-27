import type { LucideIcon } from "lucide-react";

export default function MetricCard({
  label,
  value,
  icon: Icon,
  accent = "indigo",
}: {
  label: string;
  value: string;
  icon?: LucideIcon;
  accent?: "indigo" | "violet" | "teal" | "amber";
}) {
  const accentMap: Record<string, string> = {
    indigo: "from-indigo-500/20 to-transparent text-indigo-300",
    violet: "from-violet-500/20 to-transparent text-violet-300",
    teal: "from-teal-500/20 to-transparent text-teal-300",
    amber: "from-amber-500/20 to-transparent text-amber-300",
  };

  return (
    <div className="group relative overflow-hidden rounded-xl border border-white/5 bg-neutral-900/60 p-4 backdrop-blur-sm transition-colors hover:border-white/10">
      <div className={`pointer-events-none absolute inset-0 bg-gradient-to-br ${accentMap[accent]} opacity-0 transition-opacity group-hover:opacity-100`} />
      <div className="relative flex items-start justify-between">
        <div>
          <div className="text-xs font-medium uppercase tracking-wider text-neutral-500">{label}</div>
          <div className="mt-1.5 text-2xl font-semibold tabular-nums text-neutral-50">{value}</div>
        </div>
        {Icon && <Icon size={18} className={`mt-0.5 ${accentMap[accent].split(" ").pop()}`} />}
      </div>
    </div>
  );
}

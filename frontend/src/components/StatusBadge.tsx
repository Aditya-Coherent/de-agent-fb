import { CheckCircle2, XCircle } from "lucide-react";

export default function StatusBadge({ ok, label }: { ok: boolean; label: string }) {
  return (
    <div
      className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium ${
        ok
          ? "border-emerald-900/60 bg-emerald-950/40 text-emerald-300"
          : "border-rose-900/60 bg-rose-950/40 text-rose-300"
      }`}
    >
      {ok ? <CheckCircle2 size={16} /> : <XCircle size={16} />}
      {label}
    </div>
  );
}

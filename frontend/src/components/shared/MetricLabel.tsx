import Hint from "@/components/shared/Hint";

/** KPI / total tile label with optional glossary tooltip. */
export default function MetricLabel({ label, hint }: { label: string; hint?: string }) {
  return (
    <div className="flex items-center gap-1 text-xs font-medium uppercase tracking-wider text-slate-500">
      <span>{label}</span>
      {hint ? <Hint text={hint} /> : null}
    </div>
  );
}

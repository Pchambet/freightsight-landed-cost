import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";

/**
 * How a unit landed cost moved between the last two arrivals. A cost going up is bad news, so the
 * colours are the reverse of a revenue chart; the arrow and the sign say the direction on their own.
 * `value` is the API's signed one-decimal string ("8.6", "-2.1").
 */
export default function ChangeBadge({ value, locale }: { value: string | null | undefined; locale: string }) {
  if (value === null || value === undefined) return <span className="text-slate-500">—</span>;
  const n = Number(value);
  if (!Number.isFinite(n)) return <span className="text-slate-500">—</span>;
  const flat = Math.abs(n) < 0.05;
  const Icon = flat ? Minus : n > 0 ? ArrowUpRight : ArrowDownRight;
  const tone = flat ? "text-slate-600 bg-slate-100" : n > 0 ? "text-red-800 bg-red-50" : "text-emerald-800 bg-emerald-50";
  const text = `${n > 0 ? "+" : n < 0 ? "−" : ""}${new Intl.NumberFormat(locale === "fr" ? "fr-FR" : "en-GB", { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(Math.abs(n))} %`;
  return (
    <span className={`inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-xs font-medium tabular-nums whitespace-nowrap ${tone}`}>
      <Icon className="w-3 h-3" aria-hidden />
      {text}
    </span>
  );
}

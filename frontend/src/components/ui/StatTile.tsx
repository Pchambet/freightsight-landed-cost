import Link from "next/link";
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import Sparkline from "@/components/charts/Sparkline";
import MetricLabel from "@/components/shared/MetricLabel";

export type Delta = {
  /** Already signed and formatted: "+2,1 %", "−0,04". */
  text: string;
  direction: "up" | "down" | "flat";
  /** Whether this direction is good news. `null`: neither (a volume, not a performance). */
  good: boolean | null;
  /** "vs 6 mois précédents" */
  versus: string;
};

/**
 * One headline number: label, value, how it moved against a named period, and its recent shape.
 * The arrow and the sign say the direction; the colour only adds whether that is good news, so
 * the tile reads the same in greyscale.
 */
export default function StatTile({
  label,
  hint,
  value,
  sub,
  delta,
  spark,
  href,
  tone = "default",
}: {
  label: string;
  hint?: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  delta?: Delta | null;
  spark?: number[];
  href?: string;
  tone?: "default" | "warning" | "danger";
}) {
  const Arrow = delta?.direction === "up" ? ArrowUpRight : delta?.direction === "down" ? ArrowDownRight : Minus;
  const deltaTone = !delta || delta.good === null || delta.direction === "flat" ? "text-slate-600 bg-slate-100" : delta.good ? "text-emerald-800 bg-emerald-50" : "text-red-800 bg-red-50";
  const valueTone = tone === "danger" ? "text-red-700" : tone === "warning" ? "text-amber-700" : "text-slate-900";
  const body = (
    <>
      <div className="relative z-10 w-fit"><MetricLabel label={label} hint={hint} /></div>
      <div className="mt-2 flex items-end justify-between gap-3">
        <div className={`text-[1.7rem] leading-none font-semibold tracking-tight figure-proportional ${valueTone}`}>{value}</div>
        {spark && spark.length > 1 ? <Sparkline values={spark} className="h-8 w-20 shrink-0" /> : null}
      </div>
      <div className="mt-2 min-h-5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-500">
        {delta ? (
          <>
            <span className={`inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 font-medium tabular-nums ${deltaTone}`}>
              <Arrow className="w-3 h-3" aria-hidden />
              {delta.text}
            </span>
            <span>{delta.versus}</span>
          </>
        ) : null}
        {sub ? <span>{sub}</span> : null}
      </div>
    </>
  );
  // The label carries a help button, and a button may not live inside a link: the tile stays a box
  // and the link is laid over it, under the button.
  return (
    <div className={`relative bg-white rounded-xl border border-slate-200 shadow-card p-4 ${href ? "transition-colors hover:border-slate-300" : ""}`}>
      {href ? <Link href={href} aria-label={label} className="absolute inset-0 rounded-xl focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500" /> : null}
      {body}
    </div>
  );
}

"use client";

import Link from "next/link";
import { useState } from "react";
import { niceRange } from "@/components/charts/scale";
import { money } from "@/lib/format";

export type TrendPoint = {
  key: string;
  /** Under the point: "04/2026". */
  label: string;
  /** Tooltip heading: "MSCU4821990 · 12/04/2026". */
  title: string;
  fob: number;
  landed: number;
  /** Part of this landed cost is still an estimate: the dot is drawn hollow. */
  estimated?: boolean;
  note?: string;
  href?: string;
};

/**
 * What one unit cost at purchase and what it cost once landed, arrival after arrival. The landed
 * line is the subject (accent hue, 2 px); the purchase price is context (grey); the wash between
 * them is the approach cost per unit — the distance the whole product exists to explain.
 *
 * The lines live in a stretched SVG (non-scaling stroke), everything with a shape or a letter —
 * dots, labels, the crosshair — is a box positioned in percentages over it, so nothing is measured
 * and the first server render is already exact. One point per arrival, evenly spaced: arrivals are
 * events, and two containers landing the same week must not collapse into one dot.
 */
export default function UnitCostTrend({
  points,
  currency,
  locale,
  labels,
  height = 220,
}: {
  points: TrendPoint[];
  currency: string;
  locale: string;
  labels: { landed: string; fob: string; approach: string; estimated: string };
  height?: number;
}) {
  const [active, setActive] = useState<number | null>(null);
  if (points.length === 0) return null;
  const all = points.flatMap((p) => [p.fob, p.landed]).filter((v) => Number.isFinite(v));
  const { min, max, ticks } = niceRange(Math.min(...all), Math.max(...all));
  const n = points.length;
  const x = (i: number) => ((i + 0.5) / n) * 100;
  const y = (v: number) => 100 - ((v - min) / (max - min)) * 100;
  const path = (get: (p: TrendPoint) => number) => points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(2)},${y(get(p)).toFixed(2)}`).join(" ");
  const band = `${path((p) => p.landed)} ${[...points].reverse().map((p, k) => `L${x(n - 1 - k).toFixed(2)},${y(p.fob).toFixed(2)}`).join(" ")} Z`;
  const lastP = points[n - 1];
  const anyEstimated = points.some((p) => p.estimated);
  // The two end labels share a gutter: keep them a line apart even when the last fees are thin.
  const landedTop = y(lastP.landed);
  const fobTop = Math.max(y(lastP.fob), landedTop + (18 / height) * 100);

  return (
    <div>
      <ul className="mb-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
        <li className="inline-flex items-center gap-1.5"><span className="w-3 h-0.5 rounded-full bg-viz-freight" aria-hidden />{labels.landed}</li>
        <li className="inline-flex items-center gap-1.5"><span className="w-3 h-0.5 rounded-full bg-slate-400" aria-hidden />{labels.fob}</li>
        {anyEstimated ? (
          <li className="inline-flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-white ring-2 ring-viz-freight ring-inset" aria-hidden />{labels.estimated}</li>
        ) : null}
      </ul>

      <div className="flex gap-3">
        <div className="relative shrink-0 w-14 text-[11px] text-viz-muted tabular-nums" style={{ height }} aria-hidden>
          {ticks.map((v) => (
            <span key={v} className="absolute right-0 leading-none -translate-y-1/2" style={{ top: `${y(v)}%` }}>
              {money(v, currency, locale)}
            </span>
          ))}
        </div>

        <div className="relative flex-1 min-w-0" onPointerLeave={() => setActive(null)}>
          <div className="relative" style={{ height }}>
            {ticks.map((v, i) => (
              <div key={v} className={`absolute inset-x-0 h-px ${i === 0 ? "bg-viz-axis" : "bg-viz-grid"}`} style={{ top: `${y(v)}%` }} aria-hidden />
            ))}

            <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="absolute inset-0 w-full h-full overflow-visible" aria-hidden>
              {n > 1 ? <path d={band} className="fill-viz-freight/10" /> : null}
              <path d={path((p) => p.fob)} fill="none" className="stroke-slate-400" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
              <path d={path((p) => p.landed)} fill="none" className="stroke-viz-freight" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
            </svg>

            {active !== null ? <div className="absolute top-0 bottom-0 w-px bg-slate-300" style={{ left: `${x(active)}%` }} aria-hidden /> : null}

            {points.map((p, i) => (
              <span key={`f-${p.key}`} className="absolute w-2 h-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-slate-400 ring-2 ring-white" style={{ left: `${x(i)}%`, top: `${y(p.fob)}%` }} aria-hidden />
            ))}
            {points.map((p, i) => (
              <span
                key={`l-${p.key}`}
                className={`absolute -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-white ${active === i ? "w-3 h-3" : "w-2.5 h-2.5"} ${p.estimated ? "bg-white border-2 border-viz-freight" : "bg-viz-freight"}`}
                style={{ left: `${x(i)}%`, top: `${y(p.landed)}%` }}
                aria-hidden
              />
            ))}

            {/* One hit zone per arrival, as wide as its share of the plot: the pointer only has to be nearest. */}
            <div className="absolute inset-0 flex">
              {points.map((p, i) => {
                const shared = {
                  tabIndex: 0,
                  onPointerEnter: () => setActive(i),
                  onFocus: () => setActive(i),
                  onBlur: () => setActive(null),
                  "aria-label": `${p.title} — ${labels.landed} ${money(p.landed, currency, locale)}, ${labels.fob} ${money(p.fob, currency, locale)}`,
                  className: "flex-1 min-w-0 outline-none rounded-sm focus-visible:ring-2 focus-visible:ring-blue-500",
                };
                return p.href ? <Link key={p.key} href={p.href} {...shared} /> : <div key={p.key} role="img" {...shared} />;
              })}
            </div>

            {active !== null ? (
              <div
                role="status"
                className={`pointer-events-none absolute z-10 top-2 w-60 rounded-lg border border-slate-200 bg-white shadow-pop p-3 text-xs animate-rise ${x(active) > 55 ? "-translate-x-full -ml-3" : "ml-3"}`}
                style={{ left: `${x(active)}%` }}
              >
                <div className="font-medium text-slate-900">{points[active].title}</div>
                <ul className="mt-2 space-y-1">
                  <li className="flex items-center gap-2">
                    <span className="w-2.5 h-0.5 rounded-full bg-viz-freight" aria-hidden />
                    <span className="text-slate-500 flex-1">{labels.landed}</span>
                    <span className="font-semibold text-slate-900 tabular-nums">{money(points[active].landed, currency, locale)}</span>
                  </li>
                  <li className="flex items-center gap-2">
                    <span className="w-2.5 h-0.5 rounded-full bg-slate-400" aria-hidden />
                    <span className="text-slate-500 flex-1">{labels.fob}</span>
                    <span className="font-medium text-slate-900 tabular-nums">{money(points[active].fob, currency, locale)}</span>
                  </li>
                </ul>
                <div className="mt-2 pt-2 border-t border-slate-100 flex justify-between">
                  <span className="text-slate-500">{labels.approach}</span>
                  <span className="font-medium text-slate-900 tabular-nums">+{money(points[active].landed - points[active].fob, currency, locale)}</span>
                </div>
                {points[active].note ? <div className="mt-1 text-slate-500">{points[active].note}</div> : null}
              </div>
            ) : null}
          </div>

          <div className="mt-2 flex" aria-hidden>
            {points.map((p, i) => (
              <div key={p.key} className={`flex-1 min-w-0 text-center text-[11px] truncate ${active === i ? "text-slate-900 font-medium" : "text-viz-muted"}`}>
                {p.label}
              </div>
            ))}
          </div>
        </div>

        {/* End labels: the value of the latest arrival, read without hovering. */}
        <div className="relative shrink-0 w-20 text-xs tabular-nums hidden sm:block" style={{ height }} aria-hidden>
          <span className="absolute left-0 -translate-y-1/2 font-semibold text-slate-900 whitespace-nowrap" style={{ top: `${landedTop}%` }}>
            {money(lastP.landed, currency, locale)}
          </span>
          <span className="absolute left-0 -translate-y-1/2 text-slate-500 whitespace-nowrap" style={{ top: `${fobTop}%` }}>
            {money(lastP.fob, currency, locale)}
          </span>
        </div>
      </div>
    </div>
  );
}

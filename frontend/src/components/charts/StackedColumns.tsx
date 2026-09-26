"use client";

import Link from "next/link";
import { useState } from "react";
import { COST_FAMILIES, FAMILY_BG, familyTotal, type CostFamily, type FamilyAmounts } from "@/components/charts/families";
import { compactMoney, niceScale } from "@/components/charts/scale";
import { money } from "@/lib/format";

export type StackedColumn = {
  key: string;
  /** Under the column: "avr.", "2026-04"… */
  label: string;
  /** In the tooltip's heading: "avril 2026". */
  title: string;
  families: FamilyAmounts;
  /** Shown under the total in the tooltip: "3 conteneurs · coefficient 1,27". */
  note?: string;
  href?: string;
};

/**
 * Fees per period, stacked by cost family. Built from boxes rather than an SVG: a column is a flex
 * item and a segment a percentage height, so the chart is exact at any width on the server's first
 * render — nothing to measure, nothing that jumps once the script arrives.
 *
 * Reading rules (dataviz skill): one zero-based axis, thin columns, a 2 px surface gap between
 * segments instead of a stroke, the data end rounded and the baseline square, hairline solid grid,
 * text in ink and never in a series colour. The total is written on the tallest and the latest
 * column only; every other value is one hover (or one Tab) away, and the legend plus the table
 * underneath carry them without any pointer at all.
 */
export default function StackedColumns({
  columns,
  currency,
  locale,
  familyLabels,
  totalLabel,
  height = 220,
}: {
  columns: StackedColumn[];
  currency: string;
  locale: string;
  familyLabels: Record<CostFamily, string>;
  totalLabel: string;
  height?: number;
}) {
  const [active, setActive] = useState<number | null>(null);
  const totals = columns.map((c) => familyTotal(c.families));
  const { max, ticks } = niceScale(Math.max(...totals, 0));
  const tallest = totals.indexOf(Math.max(...totals));
  const latest = columns.length - 1;
  const present = COST_FAMILIES.filter((f) => columns.some((c) => c.families[f] > 0));

  return (
    <div>
      <div className="flex gap-3">
        {/* y axis: its labels sit on the gridlines, so the column shares the plot's height */}
        <div className="relative shrink-0 w-12 text-[11px] text-viz-muted tabular-nums" style={{ height }} aria-hidden>
          {ticks.map((v) => (
            <span key={v} className="absolute right-0 leading-none translate-y-1/2" style={{ bottom: `${(v / max) * 100}%` }}>
              {compactMoney(v, currency, locale)}
            </span>
          ))}
        </div>

        <div className="relative flex-1 min-w-0" onPointerLeave={() => setActive(null)}>
          <div className="relative" style={{ height }}>
            {ticks.map((v) => (
              <div key={v} className={`absolute inset-x-0 h-px ${v === 0 ? "bg-viz-axis" : "bg-viz-grid"}`} style={{ bottom: `${(v / max) * 100}%` }} aria-hidden />
            ))}
            <div className="absolute inset-0 flex items-stretch">
              {columns.map((c, i) => {
                const stack = present.filter((f) => c.families[f] > 0);
                const shared = {
                  tabIndex: 0,
                  onPointerEnter: () => setActive(i),
                  onFocus: () => setActive(i),
                  onBlur: () => setActive(null),
                  "aria-label": `${c.title} — ${totalLabel} ${money(totals[i], currency, locale)}`,
                  className: `group relative flex-1 min-w-0 flex flex-col justify-end items-center outline-none rounded-sm focus-visible:ring-2 focus-visible:ring-blue-500 ${active === i ? "bg-slate-100/70" : ""}`,
                };
                const body = (
                  <>
                    {(i === tallest || i === latest) && totals[i] > 0 ? (
                      <span className="mb-1 text-[11px] font-medium text-slate-700 tabular-nums whitespace-nowrap" aria-hidden>
                        {compactMoney(totals[i], currency, locale)}
                      </span>
                    ) : null}
                    <div className="w-full max-w-6 flex flex-col-reverse" style={{ height: `${(totals[i] / max) * 100}%` }}>
                      {stack.map((f, k) => (
                        <div
                          key={f}
                          className={`${FAMILY_BG[f]} ${k === stack.length - 1 ? "rounded-t-[4px]" : ""} ${k > 0 ? "border-b-2 border-white" : ""} ${active !== null && active !== i ? "opacity-60" : ""} transition-opacity`}
                          style={{ height: `${(c.families[f] / totals[i]) * 100}%` }}
                        />
                      ))}
                    </div>
                  </>
                );
                return c.href ? (
                  <Link key={c.key} href={c.href} {...shared}>
                    {body}
                  </Link>
                ) : (
                  <div key={c.key} role="img" {...shared}>
                    {body}
                  </div>
                );
              })}
            </div>

            {active !== null && columns[active] ? (
              <div
                role="status"
                className={`pointer-events-none absolute z-10 bottom-full mb-2 w-60 rounded-lg border border-slate-200 bg-white shadow-pop p-3 text-xs animate-rise ${
                  active < columns.length / 3 ? "left-0" : active > (columns.length * 2) / 3 - 1 ? "right-0" : "-translate-x-1/2"
                }`}
                style={active < columns.length / 3 || active > (columns.length * 2) / 3 - 1 ? undefined : { left: `${((active + 0.5) / columns.length) * 100}%` }}
              >
                <div className="font-medium text-slate-900">{columns[active].title}</div>
                <ul className="mt-2 space-y-1">
                  {present
                    .filter((f) => columns[active].families[f] > 0)
                    .map((f) => (
                      <li key={f} className="flex items-center gap-2">
                        <span className={`w-2.5 h-0.5 rounded-full ${FAMILY_BG[f]}`} aria-hidden />
                        <span className="text-slate-500 flex-1 truncate">{familyLabels[f]}</span>
                        <span className="font-medium text-slate-900 tabular-nums">{money(columns[active].families[f], currency, locale)}</span>
                      </li>
                    ))}
                </ul>
                <div className="mt-2 pt-2 border-t border-slate-100 flex justify-between">
                  <span className="text-slate-500">{totalLabel}</span>
                  <span className="font-semibold text-slate-900 tabular-nums">{money(totals[active], currency, locale)}</span>
                </div>
                {columns[active].note ? <div className="mt-1 text-slate-500">{columns[active].note}</div> : null}
              </div>
            ) : null}
          </div>

          <div className="mt-2 flex" aria-hidden>
            {columns.map((c, i) => (
              <div key={c.key} className={`flex-1 min-w-0 text-center text-[11px] truncate ${active === i ? "text-slate-900 font-medium" : "text-viz-muted"}`}>
                {c.label}
              </div>
            ))}
          </div>
        </div>
      </div>

      <ul className="mt-4 flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-slate-600">
        {present.map((f) => (
          <li key={f} className="inline-flex items-center gap-1.5">
            <span className={`w-2.5 h-2.5 rounded-[3px] ${FAMILY_BG[f]}`} aria-hidden />
            {familyLabels[f]}
          </li>
        ))}
      </ul>
    </div>
  );
}

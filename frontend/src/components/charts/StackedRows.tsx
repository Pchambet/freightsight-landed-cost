"use client";

import { useState } from "react";
import { COST_FAMILIES, FAMILY_BG, familyTotal, type CostFamily, type FamilyAmounts } from "@/components/charts/families";
import { money, pct } from "@/lib/format";

export type StackedRow = { key: string; label: string; fob: number; families: FamilyAmounts; display: string };

/**
 * Who costs what to bring in, and why: one row per supplier or route, its charges stacked by family
 * and measured against ITS OWN purchases (a bar is charges ÷ FOB), so a small supplier with heavy
 * freight stands out instead of hiding behind a large one. Rows share one scale and are sorted
 * heaviest first; the figure at the end of a row is the landed cost ratio the bar explains.
 */
export default function StackedRows({
  rows,
  currency,
  locale,
  familyLabels,
  ofFobLabel,
}: {
  rows: StackedRow[];
  currency: string;
  locale: string;
  familyLabels: Record<CostFamily, string>;
  ofFobLabel: string;
}) {
  const [active, setActive] = useState<string | null>(null);
  const usable = rows.filter((r) => r.fob > 0).sort((a, b) => familyTotal(b.families) / b.fob - familyTotal(a.families) / a.fob);
  if (usable.length === 0) return null;
  const max = Math.max(...usable.map((r) => familyTotal(r.families) / r.fob), 0.0001);
  const present = COST_FAMILIES.filter((f) => usable.some((r) => r.families[f] > 0));

  return (
    <div>
      <ul className="space-y-3">
        {usable.map((r) => {
          const stack = present.filter((f) => r.families[f] > 0);
          const share = familyTotal(r.families) / r.fob;
          const open = active === r.key;
          return (
            <li key={r.key}>
              <button
                type="button"
                aria-expanded={open}
                onClick={() => setActive(open ? null : r.key)}
                onPointerEnter={() => setActive(r.key)}
                onPointerLeave={() => setActive(null)}
                className="w-full text-left rounded-md outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
              >
                <span className="flex items-baseline justify-between gap-3 text-sm">
                  <span className="min-w-0 truncate text-slate-800">{r.label}</span>
                  <span className="shrink-0 tabular-nums">
                    <span className="text-xs text-slate-500 mr-2">{pct(share * 100, locale)} {ofFobLabel}</span>
                    <span className="font-medium text-slate-900">{r.display}</span>
                  </span>
                </span>
                <span className="mt-1.5 flex h-2 gap-0.5" style={{ width: `${Math.max((share / max) * 100, 2)}%` }} aria-hidden>
                  {stack.map((f, i) => (
                    <span key={f} className={`${FAMILY_BG[f]} ${i === stack.length - 1 ? "rounded-r-[4px]" : ""} ${active !== null && !open ? "opacity-50" : ""} transition-opacity`} style={{ width: `${(r.families[f] / familyTotal(r.families)) * 100}%`, minWidth: 2 }} />
                  ))}
                </span>
              </button>
              {open ? (
                <ul className="mt-2 grid gap-x-6 gap-y-0.5 sm:grid-cols-2 text-xs text-slate-600 animate-rise">
                  {stack.map((f) => (
                    <li key={f} className="flex items-center gap-2">
                      <span className={`w-2 h-2 rounded-[2px] shrink-0 ${FAMILY_BG[f]}`} aria-hidden />
                      <span className="flex-1 truncate">{familyLabels[f]}</span>
                      <span className="tabular-nums text-slate-500">{pct((r.families[f] / r.fob) * 100, locale)}</span>
                      <span className="tabular-nums font-medium text-slate-900">{money(r.families[f], currency, locale)}</span>
                    </li>
                  ))}
                </ul>
              ) : null}
            </li>
          );
        })}
      </ul>
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

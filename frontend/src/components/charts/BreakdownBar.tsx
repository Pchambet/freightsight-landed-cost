"use client";

import { useState } from "react";
import { COST_FAMILIES, FAMILY_BG, familyTotal, type CostFamily, type FamilyAmounts } from "@/components/charts/families";
import { money, pct } from "@/lib/format";

/**
 * From purchase price to landed cost in one bar: the FOB as a neutral base, then one segment per
 * cost family, in the families' fixed order. The legend underneath is the table twin — every amount
 * and its weight against the FOB is written there, so the bar is never the only way to a value.
 * Hovering (or focusing) a legend row and hovering its segment light each other up.
 */
export default function BreakdownBar({
  fob,
  families,
  currency,
  locale,
  fobLabel,
  familyLabels,
  ofFobLabel,
  dense = false,
}: {
  fob: number;
  families: FamilyAmounts;
  currency: string;
  locale: string;
  fobLabel: string;
  familyLabels: Record<CostFamily, string>;
  /** "du FOB" — appended to each family's percentage. */
  ofFobLabel: string;
  dense?: boolean;
}) {
  const [active, setActive] = useState<CostFamily | "fob" | null>(null);
  const fees = familyTotal(families);
  const whole = fob + fees;
  const present = COST_FAMILIES.filter((f) => families[f] > 0);
  if (whole <= 0) return null;
  const dim = (k: CostFamily | "fob") => (active !== null && active !== k ? "opacity-40" : "");
  const last = present[present.length - 1];

  return (
    <div>
      <div className="flex h-3 w-full gap-0.5" role="img" aria-label={`${fobLabel} ${money(fob, currency, locale)} · ${present.map((f) => `${familyLabels[f]} ${money(families[f], currency, locale)}`).join(" · ")}`}>
        {fob > 0 ? (
          <div
            className={`bg-viz-fob transition-opacity ${present.length === 0 ? "rounded-r-[4px]" : ""} ${dim("fob")}`}
            style={{ width: `${(fob / whole) * 100}%` }}
            onPointerEnter={() => setActive("fob")}
            onPointerLeave={() => setActive(null)}
          />
        ) : null}
        {present.map((f) => (
          <div
            key={f}
            className={`${FAMILY_BG[f]} transition-opacity ${f === last ? "rounded-r-[4px]" : ""} ${dim(f)}`}
            style={{ width: `${(families[f] / whole) * 100}%`, minWidth: 3 }}
            onPointerEnter={() => setActive(f)}
            onPointerLeave={() => setActive(null)}
          />
        ))}
      </div>

      <ul className={`mt-3 grid gap-x-6 gap-y-1 text-sm ${dense ? "" : "sm:grid-cols-2"}`}>
        <li
          className={`flex items-center gap-2 rounded px-1 -mx-1 transition-colors ${active === "fob" ? "bg-slate-100" : ""}`}
          onPointerEnter={() => setActive("fob")}
          onPointerLeave={() => setActive(null)}
        >
          <span className="w-2.5 h-2.5 rounded-[3px] bg-viz-fob shrink-0" aria-hidden />
          <span className="text-slate-600 flex-1 truncate">{fobLabel}</span>
          <span className="font-medium text-slate-900 tabular-nums">{money(fob, currency, locale)}</span>
        </li>
        {present.map((f) => (
          <li
            key={f}
            className={`flex items-center gap-2 rounded px-1 -mx-1 transition-colors ${active === f ? "bg-slate-100" : ""}`}
            onPointerEnter={() => setActive(f)}
            onPointerLeave={() => setActive(null)}
          >
            <span className={`w-2.5 h-2.5 rounded-[3px] shrink-0 ${FAMILY_BG[f]}`} aria-hidden />
            <span className="text-slate-600 flex-1 truncate">{familyLabels[f]}</span>
            {fob > 0 ? (
              <span className="text-xs text-slate-500 tabular-nums whitespace-nowrap">
                {pct((families[f] / fob) * 100, locale)} {ofFobLabel}
              </span>
            ) : null}
            <span className="font-medium text-slate-900 tabular-nums">{money(families[f], currency, locale)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

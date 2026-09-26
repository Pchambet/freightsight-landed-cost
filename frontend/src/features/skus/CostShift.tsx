import { COST_FAMILIES, foldFamilies, type CostFamily } from "@/components/charts/families";
import { money } from "@/lib/format";

type Arrival = { unit_fob: string; unit_by_cost_type: Record<string, string> };

/**
 * Why the unit cost moved between two arrivals: the purchase price and each family of charges, as
 * the euros per unit they added or took away. Bars grow from a centre line — right and warm for a
 * rise, left and cool for a fall — and every row carries its signed amount, so the colour only
 * repeats what the sign already says.
 */
export default function CostShift({
  previous,
  last,
  currency,
  locale,
  fobLabel,
  familyLabels,
  totalLabel,
}: {
  previous: Arrival;
  last: Arrival;
  currency: string;
  locale: string;
  fobLabel: string;
  familyLabels: Record<CostFamily, string>;
  totalLabel: string;
}) {
  const before = foldFamilies(previous.unit_by_cost_type);
  const after = foldFamilies(last.unit_by_cost_type);
  const rows = [
    { key: "fob", label: fobLabel, delta: Number(last.unit_fob) - Number(previous.unit_fob) },
    ...COST_FAMILIES.map((f) => ({ key: f, label: familyLabels[f], delta: after[f] - before[f] })),
  ].filter((r) => Math.abs(r.delta) >= 0.005);
  const total = rows.reduce((s, r) => s + r.delta, 0);
  const max = Math.max(...rows.map((r) => Math.abs(r.delta)), 0.01);
  const signed = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${money(Math.abs(v), currency, locale)}`;

  if (rows.length === 0) return null;
  return (
    <div>
      <ul className="space-y-2">
        {rows
          .sort((x, y) => Math.abs(y.delta) - Math.abs(x.delta))
          .map((r) => (
            <li key={r.key} className="grid grid-cols-[minmax(0,11rem)_1fr_5.5rem] items-center gap-3 text-sm">
              <span className="truncate text-slate-700">{r.label}</span>
              <span className="relative h-2" aria-hidden>
                <span className="absolute inset-y-[-4px] left-1/2 w-px bg-viz-axis" />
                <span
                  className={`absolute inset-y-0 ${r.delta > 0 ? "left-1/2 rounded-r-[4px] bg-viz-rise" : "right-1/2 rounded-l-[4px] bg-viz-fall"}`}
                  style={{ width: `${(Math.abs(r.delta) / max) * 50}%` }}
                />
              </span>
              <span className="text-right font-medium text-slate-900 tabular-nums">{signed(r.delta)}</span>
            </li>
          ))}
      </ul>
      <div className="mt-3 pt-3 border-t border-slate-100 flex justify-between text-sm">
        <span className="text-slate-600">{totalLabel}</span>
        <span className="font-semibold text-slate-900 tabular-nums">{signed(total)}</span>
      </div>
    </div>
  );
}

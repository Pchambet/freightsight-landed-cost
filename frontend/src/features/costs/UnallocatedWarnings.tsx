import { getLocale, getTranslations } from "next-intl/server";
import { AlertTriangle } from "lucide-react";
import type { LandedCostReport } from "@/lib/api/client";
import { money } from "@/lib/format";

/**
 * The costs the engine could not place, one notice per reason and per set of lines rather than one per
 * cost: six invoices held by the same order with no rate are one thing to fix, and six identical
 * paragraphs buried it. Each notice lists the costs it holds, with their amounts; their total is the
 * "Non ventilé" tile's.
 *
 * The amount of a cost entered on a shipment or an order is the whole invoice, part of which may
 * belong to other containers: said once, and only when it is the case.
 */
export default async function UnallocatedWarnings({ report }: { report: LandedCostReport }) {
  if (report.warnings.length === 0) return null;
  const [t, errorCode, costType, locale] = await Promise.all([getTranslations("unallocated"), getTranslations("errors"), getTranslations("domain.costType"), getLocale()]);
  const cur = report.base_currency;
  const costs = new Map(report.costs.map((c) => [c.id, c]));
  const groups = new Map<string, { code: string; message: string; labels: string[]; items: typeof report.warnings }>();
  for (const w of report.warnings) {
    const labels = w.load_labels ?? [];
    const key = `${w.code}|${labels.join(",")}`;
    const group = groups.get(key) ?? { code: w.code, message: w.message, labels, items: [] };
    group.items.push(w);
    groups.set(key, group);
  }

  return (
    <div className="rounded-md border border-amber-200 bg-amber-50 text-amber-800 text-sm px-4 py-3 space-y-3 mb-6">
      {[...groups.values()].map((g) => {
        const shared = g.items.some((w) => { const c = costs.get(w.cost_id); return c !== undefined && c.scope !== "CONTAINER" && c.scope !== "PO_LINE"; });
        const listed = g.items.map((w) => {
          const c = costs.get(w.cost_id);
          const name = c && costType.has(c.cost_type) ? costType(c.cost_type) : (c?.cost_type ?? "");
          return `${name} ${money(w.amount_base, cur, locale)}`.trim();
        });
        return (
          <div key={`${g.code}|${g.labels.join(",")}`} className="flex gap-2">
            <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" aria-hidden />
            <div>
              <p>
                {errorCode.has(g.code) ? errorCode(g.code) : g.message}
                {g.labels.length ? <span className="text-amber-700/80"> {g.labels.join(", ")}</span> : null}
              </p>
              <p className="mt-1 font-medium">{t("held", { count: g.items.length, costs: listed.join(", ") })}</p>
              {shared ? <p className="mt-0.5 text-xs text-amber-700/90">{t("whole")}</p> : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}

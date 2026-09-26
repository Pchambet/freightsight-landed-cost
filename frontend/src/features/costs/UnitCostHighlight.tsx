import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";
import { Card } from "@/components/layout/AppShell";
import { skuHref } from "@/features/skus/href";
import type { LandedCostReport } from "@/lib/api/client";
import { qty, unitCost } from "@/lib/format";

/** The numbers a prospect must see: FOB unit cost vs landed unit cost per SKU. */
export default async function UnitCostHighlight({ report }: { report: LandedCostReport }) {
  const [t, locale] = await Promise.all([getTranslations("unitCostHighlight"), getLocale()]);
  if (report.lines.length === 0) return null;

  return (
    <section id="couts-unitaires" className="scroll-mt-6">
    <Card
      title={t("title")}
      right={<span className="text-xs text-slate-500">{t("subtitle")}</span>}
      className="ring-2 ring-emerald-200/80"
    >
      <ul className="divide-y divide-slate-100">
        {report.lines.map((ln) => {
          const fobUnit = Number(ln.quantity) > 0 ? Number(ln.fob) / Number(ln.quantity) : null;
          return (
            <li key={ln.load_id} className="px-5 py-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
              <div className="min-w-0">
                <div className="font-medium text-slate-900">
                  {ln.po_number} <span className="text-slate-500 font-normal">#{ln.line_no}</span>
                </div>
                <div className="text-sm text-slate-600 truncate">
                  {ln.sku ? <Link href={skuHref(ln.sku)} className="font-mono text-blue-700 hover:underline" title={t("skuHistory")}>{ln.sku}</Link> : (ln.description ?? "—")}
                </div>
                <div className="text-xs text-slate-500 mt-0.5">{t("quantity", { qty: qty(ln.quantity, locale) })}</div>
              </div>
              <div className="flex items-center gap-4 shrink-0">
                <div className="text-right">
                  <div className="text-xs uppercase tracking-wider text-slate-500">{t("fobUnit")}</div>
                  <div className="text-lg font-semibold tabular-nums text-slate-700">
                    {fobUnit != null ? unitCost(fobUnit, report.base_currency, locale) : "—"}
                  </div>
                </div>
                <div className="text-slate-300 text-xl" aria-hidden>→</div>
                <div className="text-right">
                  <div className="text-xs uppercase tracking-wider text-emerald-700">{t("landedUnit")}</div>
                  <div className="text-2xl font-bold tabular-nums text-emerald-800">
                    {unitCost(ln.unit_landed_cost, report.base_currency, locale)}
                  </div>
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </Card>
    </section>
  );
}

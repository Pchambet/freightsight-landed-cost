import { getLocale, getTranslations } from "next-intl/server";
import BreakdownBar from "@/components/charts/BreakdownBar";
import { COST_FAMILIES, familyTotal, foldFamilies, type CostFamily } from "@/components/charts/families";
import Panel from "@/components/ui/Panel";
import type { LandedCostReport } from "@/lib/api/client";
import { money } from "@/lib/format";

/**
 * The file in one picture: what was paid to the supplier, what each family of charges added, and
 * the ratio between the two — the "coefficient d'approche" an importer quotes from memory and has
 * never seen computed from real invoices. Shown on a container and on an order, from the same report.
 */
export default async function CostBreakdown({ report }: { report: LandedCostReport }) {
  const [t, tf, locale] = await Promise.all([getTranslations("costBreakdown"), getTranslations("costFamily"), getLocale()]);
  const fob = Number(report.totals.fob);
  const families = foldFamilies(report.by_cost_type);
  if (!(fob > 0) || familyTotal(families) <= 0) return null;
  const landed = Number(report.totals.landed);
  const coef = new Intl.NumberFormat(locale === "fr" ? "fr-FR" : "en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(landed / fob);
  const familyLabels = Object.fromEntries(COST_FAMILIES.map((f) => [f, tf(f)])) as Record<CostFamily, string>;

  return (
    <Panel
      title={t("title")}
      subtitle={t("subtitle", { landed: money(report.totals.landed, report.base_currency, locale) })}
      right={
        <div className="text-right">
          <div className="text-[11px] uppercase tracking-wider text-slate-500">{t("coef")}</div>
          <div className="text-xl font-semibold text-slate-900 figure-proportional leading-tight">{coef}</div>
        </div>
      }
      className="print:break-inside-avoid"
    >
      <BreakdownBar fob={fob} families={families} currency={report.base_currency} locale={locale} fobLabel={t("fob")} familyLabels={familyLabels} ofFobLabel={t("ofFob")} />
    </Panel>
  );
}

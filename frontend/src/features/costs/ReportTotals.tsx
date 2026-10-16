import { getLocale, getTranslations } from "next-intl/server";
import MetricLabel from "@/components/shared/MetricLabel";
import { FobMissingLine } from "@/features/report/FobMissing";
import { Money } from "@/components/shared/Money";
import type { LandedCostReport } from "@/lib/api/client";
import { money } from "@/lib/format";

type TotalKey = "fob" | "allocated" | "vat" | "landed" | "unallocated";

/**
 * The tiles at the top of a container or an order. "Coût rendu" is the one a reader quotes, so it says
 * what it rests on: with no charge allocated it would only repeat the FOB — shown as a dash, not as a
 * landed cost — and while estimates stand in for invoices it says how much of it is still estimated.
 */
export default async function ReportTotals<K extends TotalKey>({ report, keys, labels }: { report: LandedCostReport; keys: readonly K[]; labels: Record<K, string> }) {
  const [t, tg, locale] = await Promise.all([getTranslations("reportTotals"), getTranslations("glossary"), getLocale()]);
  const cur = report.base_currency;
  const totals = report.totals;
  const estimated = totals.estimated ?? "0.00";
  const hasEstimates = Number(estimated) > 0;
  const nothingAllocated = report.lines.length > 0 && report.lines.every((ln) => ln.without_charge);

  return (
    <div className={`grid grid-cols-2 gap-3 mb-6 ${keys.length === 5 ? "sm:grid-cols-5" : "sm:grid-cols-4"}`}>
      {keys.map((k) => {
        const landed = k === "landed";
        return (
          <div key={k} className="bg-white rounded-lg border border-slate-200 px-4 py-3">
            <MetricLabel label={labels[k]} hint={tg(k as TotalKey)} />
            <div className={`text-lg font-semibold tabular-nums mt-1 flex flex-wrap items-baseline gap-x-1.5 ${k === "unallocated" && totals.unallocated !== "0.00" ? "text-amber-700" : ""}`}>
              {landed && nothingAllocated ? <span className="text-slate-500">—</span> : <Money amount={totals[k]} currency={cur} />}
              {landed && hasEstimates ? <span className="rounded bg-violet-50 px-1 py-0.5 text-[11px] font-medium text-violet-800">{t("estimated")}</span> : null}
            </div>
            {landed && nothingAllocated ? <div className="text-[11px] text-slate-500 mt-0.5">{t("landedNone")}</div> : null}
            {(k === "fob" || landed) && totals[k] === null ? <FobMissingLine /> : null}
            {landed && !nothingAllocated && hasEstimates ? <div className="text-[11px] text-violet-800 mt-0.5">{t("landedEstimated", { amount: money(estimated, cur, locale) })}</div> : null}
          </div>
        );
      })}
    </div>
  );
}

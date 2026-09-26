"use client";

import { useTransition } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Loader2, Wand2 } from "lucide-react";
import { toast } from "sonner";
import { btnSecondary } from "@/components/layout/AppShell";
import { Money } from "@/components/shared/Money";
import { applyEstimates } from "@/features/actions";
import type { LandedCostReport } from "@/lib/api/client";

/**
 * Estimated vs invoiced, told so that the figures side by side can be added up.
 *
 * They could not be, and were all three correct: "estimated" and "invoiced" split what is allocated
 * today, while the variance only ever concerned the estimates an invoice has replaced — three
 * different populations under one strip, which read as arithmetic that does not work. So the split
 * of the allocation and the variance are now two blocks that each state their own basis, and what
 * was invoiced with nothing to compare it to is said rather than hidden inside the variance.
 */
export default function EstimatePanel({ report, currency, containerId }: { report: LandedCostReport; currency: string; containerId?: string }) {
  const t = useTranslations("estimates");
  const typeLabel = useTranslations("domain.costType");
  const [pending, start] = useTransition();
  const router = useRouter();
  const detail = Object.entries(report.by_cost_type_detail ?? {});
  const expected = detail.filter(([, v]) => Number(v.estimated) > 0 || Number(v.actual) > 0);
  const invoiced = expected.filter(([, v]) => Number(v.actual) > 0);
  const totals = report.totals;
  const variance = Number(totals.variance ?? "0");
  // How many estimates an invoice has actually replaced. Read through a narrow type because the
  // generated client is regenerated from the OpenAPI document on its own schedule, and the panel
  // must not claim a variance on a report that carries none.
  const pairs = (report as { matched_pairs?: number }).matched_pairs ?? 0;
  const unforecast = Number(totals.unforecast_actual ?? "0");
  const missing = expected.filter(([, v]) => Number(v.actual) === 0).map(([k]) => typeLabel(k as never));

  const apply = () =>
    start(async () => {
      if (!containerId) return;
      const r = await applyEstimates(containerId);
      if (!r.ok) return void toast.error(r.error);
      const skipped = Object.entries(r.data.skipped);
      if (r.data.created === 0 && skipped.length === 0) toast.info(t("noRateCards"));
      else if (skipped.length) toast.warning(`${t("appliedSkipped", { created: r.data.created, skipped: skipped.length })} · ${skipped.map(([k, v]) => `${k}: ${v}`).join(" · ")}`, { duration: 8000 });
      else toast.success(t("applied", { created: r.data.created }));
      router.refresh();
    });

  return (
    <div className="bg-white rounded-lg border border-slate-200 px-4 py-3 flex flex-wrap items-start gap-x-8 gap-y-3">
      <div title={t("estimatedHint")}>
        <div className="text-xs text-slate-500 uppercase tracking-wider">{t("estimated")}</div>
        <div className="text-base font-semibold tabular-nums text-violet-800"><Money amount={totals.estimated ?? "0.00"} currency={currency} /></div>
      </div>
      <div>
        <div className="text-xs text-slate-500 uppercase tracking-wider">{t("actual")}</div>
        <div className="text-base font-semibold tabular-nums"><Money amount={totals.actual ?? "0.00"} currency={currency} /></div>
        {unforecast > 0 ? (
          <div className="text-[11px] text-slate-500 mt-0.5">{t("unforecastNote")} <Money amount={totals.unforecast_actual ?? "0.00"} currency={currency} /></div>
        ) : null}
      </div>
      <div title={t("allocatedHint")} className="border-l border-slate-200 pl-8">
        <div className="text-xs text-slate-500 uppercase tracking-wider">{t("allocated")}</div>
        <div className="text-base font-semibold tabular-nums"><Money amount={totals.allocated ?? "0.00"} currency={currency} /></div>
        <div className="text-[11px] text-slate-500 mt-0.5">{t("allocatedHint")}</div>
      </div>
      <div title={t("varianceHint")} className="border-l border-slate-200 pl-8">
        <div className="text-xs text-slate-500 uppercase tracking-wider">{t("variance")}</div>
        {pairs === 0 ? (
          <>
            <div className="text-base font-semibold tabular-nums text-slate-500">—</div>
            <div className="text-[11px] text-slate-500 mt-0.5">{t("varianceNone")}</div>
          </>
        ) : (
          <>
            <div className={`text-base font-semibold tabular-nums ${variance > 0 ? "text-red-700" : variance < 0 ? "text-emerald-700" : "text-slate-700"}`}>{variance > 0 ? "+" : ""}<Money amount={totals.variance ?? "0.00"} currency={currency} /></div>
            {/* The two numbers the variance is a difference of, so nobody has to trust it on its own. */}
            <div className="text-[11px] text-slate-500 mt-0.5 tabular-nums">
              <Money amount={totals.matched_estimated ?? "0.00"} currency={currency} /> → <Money amount={totals.matched_actual ?? "0.00"} currency={currency} /> · {t("varianceBasis", { count: pairs })}
            </div>
          </>
        )}
      </div>
      <div className="min-w-[12rem]">
        <div className="text-xs text-slate-500 uppercase tracking-wider" title={t("completenessHint")}>{t("completeness")}</div>
        {expected.length === 0 ? (
          <div className="text-sm text-slate-500">{t("noExpected")}</div>
        ) : (
          <div className="flex items-center gap-2">
            <div className="h-2 w-28 rounded bg-slate-200 overflow-hidden"><div className="h-2 bg-emerald-500" style={{ width: `${Math.round((invoiced.length / expected.length) * 100)}%` }} /></div>
            <span className="text-sm tabular-nums text-slate-700" title={missing.length ? missing.join(", ") : undefined}>{t("completenessDetail", { actual: invoiced.length, expected: expected.length })}</span>
          </div>
        )}
      </div>
      {containerId ? (
        <button type="button" onClick={apply} disabled={pending} className={`${btnSecondary} ml-auto`}>
          {pending ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Wand2 className="w-4 h-4 mr-2" />}{t("apply")}
        </button>
      ) : null}
    </div>
  );
}

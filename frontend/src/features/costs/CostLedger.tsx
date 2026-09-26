"use client";

import { useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Card, td, tdRight, th, thRight } from "@/components/layout/AppShell";
import ConfirmDialog from "@/components/shared/ConfirmDialog";
import { Money } from "@/components/shared/Money";
import { addCost, deleteCost } from "@/features/actions";
import type { LandedCostReport } from "@/lib/api/client";
import { dateShort, money, rate } from "@/lib/format";

type Cost = LandedCostReport["costs"][number];

export default function CostLedger({ report, currency, revalidate }: { report: LandedCostReport; currency: string; revalidate: string }) {
  const t = useTranslations("costs.ledger");
  const tc = useTranslations("common");
  const costTypeLabel = useTranslations("domain.costType");
  const methodLabel = useTranslations("domain.method");
  const scopeLabel = useTranslations("domain.scope");
  const fxSourceLabel = useTranslations("domain.fxSource");
  const te = useTranslations("estimates");
  const locale = useLocale();
  const [pending, startTransition] = useTransition();
  const [toDelete, setToDelete] = useState<Cost | null>(null);
  const lastTriggerRef = useRef<HTMLButtonElement | null>(null);
  const router = useRouter();

  // A manual allocation's percentages are not part of what addCost accepts, so that one case cannot
  // be recreated from the client — the undo toast is offered only when it would actually work.
  const canRecreate = (c: Cost) => c.allocation_method !== "MANUAL";

  const remove = (c: Cost) =>
    startTransition(async () => {
      const result = await deleteCost(c.id, revalidate);
      setToDelete(null);
      if (!result.ok) return void toast.error(result.error);
      if (canRecreate(c)) {
        toast.success(t("deleted"), { action: { label: t("undo"), onClick: () => undo(c) } });
      } else {
        toast.success(t("deleted"));
      }
      router.refresh();
    });

  const undo = (c: Cost) =>
    startTransition(async () => {
      const targetId = c.container_id ?? c.shipment_id ?? c.po_id ?? c.po_line_id;
      if (!targetId) return void toast.error(t("deleteFailed"));
      const result = await addCost({
        scope: c.scope,
        target_id: targetId,
        cost_type: c.cost_type,
        amount: c.amount,
        currency: c.currency,
        cost_date: c.cost_date,
        allocation_method: c.allocation_method,
        fx_rate: c.fx_rate,
        vendor: c.vendor ?? undefined,
        invoice_number: c.invoice_number ?? undefined,
        status: c.status,
        revalidate,
      });
      if (!result.ok) return void toast.error(result.error);
      toast.success(t("restored"));
      router.refresh();
    });

  return (
    <>
    <Card title={t("title")} right={<span className="text-xs text-slate-500">{t("recorded", { count: report.costs.length })}</span>}>
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-slate-50">
            <tr>
              <th className={th}>{t("th.date")}</th>
              <th className={th}>{t("th.type")}</th>
              <th className={th}>{t("th.scope")}</th>
              <th className={thRight}>{t("th.amount")}</th>
              <th className={thRight}>{t("th.inCurrency", { currency })}</th>
              <th className={th}>{t("th.method")}</th>
              <th className={th}>{t("th.vendor")}</th>
              <th className={th}></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200">
            {report.costs.length === 0 ? (
              <tr><td colSpan={8} className="px-5 py-8 text-center text-sm text-slate-500">{t("empty")}</td></tr>
            ) : (
              report.costs.map((c) => (
                <tr key={c.id} className="hover:bg-slate-50">
                  <td className={`${td} text-slate-600`}>{dateShort(c.cost_date, locale)}</td>
                  <td className={td}>
                    {costTypeLabel(c.cost_type)}
                    {c.status === "ESTIMATE" ? <span className={`ml-2 inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium uppercase tracking-wider ${c.closed_at ? "bg-slate-100 text-slate-600" : "bg-violet-100 text-violet-800"}`}>{c.closed_at ? te("closed") : te("statusEstimate")}</span> : null}
                    {c.supersedes_cost_id ? <span className="ml-2 text-[10px] text-slate-500 uppercase tracking-wider">↩ {te("statusEstimate").toLowerCase()}</span> : null}
                  </td>
                  <td className={`${td} text-slate-600`}>{scopeLabel(c.scope)}</td>
                  <td className={tdRight}><Money amount={c.amount} currency={c.currency} /></td>
                  <td className={tdRight}>
                    <Money amount={c.amount_base} currency={currency} className="font-medium" />
                    {c.currency !== currency ? <div className="text-xs text-slate-500">@ {rate(c.fx_rate, locale)} ({fxSourceLabel(c.fx_source as never)})</div> : null}
                  </td>
                  <td className={`${td} text-slate-600`}>{methodLabel(c.allocation_method)}</td>
                  <td className={`${td} text-slate-600 text-xs`}>{[c.vendor, c.invoice_number].filter(Boolean).join(" · ") || tc("empty")}</td>
                  <td className={td}>
                    <button
                      type="button"
                      disabled={pending}
                      onClick={(e) => { lastTriggerRef.current = e.currentTarget; setToDelete(c); }}
                      className="text-slate-500 hover:text-red-600"
                      aria-label={t("delete")}
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </Card>
    <ConfirmDialog
      open={toDelete !== null}
      title={t("deleteConfirmTitle")}
      body={toDelete ? t("deleteConfirmBody", { type: costTypeLabel(toDelete.cost_type), amount: money(toDelete.amount, toDelete.currency, locale), date: dateShort(toDelete.cost_date, locale) }) : ""}
      confirmLabel={t("deleteConfirmConfirm")}
      cancelLabel={t("deleteConfirmCancel")}
      pending={pending}
      onConfirm={() => toDelete && remove(toDelete)}
      onCancel={() => setToDelete(null)}
      returnFocusRef={lastTriggerRef}
    />
    </>
  );
}

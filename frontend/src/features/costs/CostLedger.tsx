"use client";

import { Fragment, useRef, useState, useTransition } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { Loader2, Trash2, Undo2 } from "lucide-react";
import { toast } from "sonner";
import { btnPrimary, btnSecondary, Card, input, label, td, tdRight, th, thRight } from "@/components/layout/AppShell";
import ConfirmDialog from "@/components/shared/ConfirmDialog";
import { Money } from "@/components/shared/Money";
import { addCost, addCostCredit, deleteCost, removeCostCredit } from "@/features/actions";
import type { LandedCostReport } from "@/lib/api/client";
import { amountPlaceholder, dateShort, money, parisToday, rate } from "@/lib/format";

type Cost = LandedCostReport["costs"][number];

/**
 * A refund entered on the cost it corrects: a credit note that came by post, a discount granted
 * afterwards. The API refuses what would make the cost wrong rather than less (more than it still
 * amounts to, another currency, the same credit note twice) and says it in the toast.
 */
function CreditForm({ cost, revalidate, onDone }: { cost: Cost; revalidate: string; onDone: () => void }) {
  const t = useTranslations("costs.ledger");
  const costTypeLabel = useTranslations("domain.costType");
  const locale = useLocale();
  const [pending, start] = useTransition();
  const router = useRouter();

  const submit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    start(async () => {
      const r = await addCostCredit(
        cost.id,
        { amount: String(f.get("amount") ?? ""), credit_number: String(f.get("credit_number") ?? ""), credit_date: String(f.get("credit_date") ?? "") },
        revalidate,
      );
      if (!r.ok) return void toast.error(r.error);
      toast.success(t("creditSaved", { amount: money(r.data.amount, r.data.currency, locale) }));
      onDone();
      router.refresh();
    });
  };

  return (
    <form onSubmit={submit} className="space-y-2">
      <div className="text-xs font-medium text-slate-700">{t("creditTitle", { type: costTypeLabel(cost.cost_type), amount: money(cost.amount, cost.currency, locale) })}</div>
      <p className="text-xs text-slate-500">{t("creditHint")}</p>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-[9rem_minmax(0,1fr)_10rem]">
        <div>
          <label className={label} htmlFor={`credit-amount-${cost.id}`}>{t("creditAmount", { currency: cost.currency })}</label>
          <input id={`credit-amount-${cost.id}`} name="amount" required autoFocus inputMode="decimal" placeholder={amountPlaceholder(locale)} className={`${input} text-right tabular-nums`} />
        </div>
        <div>
          <label className={label} htmlFor={`credit-number-${cost.id}`}>{t("creditNumber")}</label>
          <input id={`credit-number-${cost.id}`} name="credit_number" maxLength={64} className={input} />
        </div>
        <div>
          <label className={label} htmlFor={`credit-date-${cost.id}`}>{t("creditDate")}</label>
          <input id={`credit-date-${cost.id}`} name="credit_date" type="date" required defaultValue={parisToday()} className={input} />
        </div>
      </div>
      <div className="flex gap-2">
        <button type="submit" disabled={pending} className={btnPrimary}>{pending ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : null}{t("creditSubmit")}</button>
        <button type="button" disabled={pending} onClick={onDone} className={btnSecondary}>{t("creditCancel")}</button>
      </div>
    </form>
  );
}

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
  const [crediting, setCrediting] = useState<string | null>(null);
  const creditCost = report.costs.find((c) => c.id === crediting) ?? null;
  const lastTriggerRef = useRef<HTMLButtonElement | null>(null);
  const router = useRouter();

  const removeCredit = (c: Cost, creditId: string) =>
    startTransition(async () => {
      const result = await removeCostCredit(c.id, creditId, revalidate);
      if (!result.ok) return void toast.error(result.error);
      toast.success(t("creditRemoved", { amount: money(result.data.amount, result.data.currency, locale) }));
      router.refresh();
    });

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
                <Fragment key={c.id}>
                <tr className="hover:bg-slate-50">
                  <td className={`${td} text-slate-600`}>{dateShort(c.cost_date, locale)}</td>
                  <td className={td}>
                    {costTypeLabel(c.cost_type)}
                    {c.status === "ESTIMATE" ? <span className={`ml-2 inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium uppercase tracking-wider ${c.closed_at ? "bg-slate-100 text-slate-600" : "bg-violet-100 text-violet-800"}`}>{c.closed_at ? te("closed") : te("statusEstimate")}</span> : null}
                    {c.supersedes_cost_id ? <span className="ml-2 text-[10px] text-slate-500 uppercase tracking-wider">↩ {te("statusEstimate").toLowerCase()}</span> : null}
                  </td>
                  <td className={`${td} text-slate-600`}>{scopeLabel(c.scope)}</td>
                  <td className={tdRight}>
                    <Money amount={c.amount} currency={c.currency} />
                    {Number(c.credited) > 0 ? <div className="text-xs text-slate-500">{t("credited", { amount: money(c.credited, c.currency, locale) })}</div> : null}
                  </td>
                  <td className={tdRight}>
                    <Money amount={c.amount_base} currency={currency} className="font-medium" />
                    {c.currency !== currency ? <div className="text-xs text-slate-500">@ {rate(c.fx_rate, locale)} ({fxSourceLabel(c.fx_source as never)})</div> : null}
                  </td>
                  <td className={`${td} text-slate-600`}>{methodLabel(c.allocation_method)}</td>
                  <td className={`${td} text-slate-600 text-xs`}>{[c.vendor, c.invoice_number].filter(Boolean).join(" · ") || tc("empty")}</td>
                  <td className={td}>
                    <span className="flex items-center gap-3">
                      {/* A refund corrects money paid: an estimate has none to refund. */}
                      {c.status === "ACTUAL" ? (
                        <button
                          type="button"
                          disabled={pending}
                          onClick={() => setCrediting(crediting === c.id ? null : c.id)}
                          aria-expanded={crediting === c.id}
                          aria-controls={crediting === c.id ? "credit-form" : undefined}
                          aria-label={t("creditAddLabel")}
                          className="text-xs text-blue-700 hover:underline disabled:opacity-50"
                        >
                          {t("creditAdd")}
                        </button>
                      ) : null}
                      <button
                        type="button"
                        disabled={pending}
                        onClick={(e) => { lastTriggerRef.current = e.currentTarget; setToDelete(c); }}
                        className="text-slate-500 hover:text-red-600"
                        aria-label={t("delete")}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </span>
                  </td>
                </tr>
                {c.credits?.length ? (
                  <tr className="bg-slate-50/60">
                    <td colSpan={8} className="px-4 py-2">
                      <ul className="space-y-1 text-xs text-slate-600">
                        {c.credits.map((cr) => (
                          <li key={cr.id} className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
                            <span className="tabular-nums">{t("creditItem", { number: cr.credit_number ?? t("creditNoNumber"), date: dateShort(cr.credit_date, locale), amount: money(cr.amount, c.currency, locale) })}</span>
                            {cr.invoice_id ? (
                              // Read from a credit note: taken back by reopening that document, which says why on it.
                              <Link href={`/invoices/${cr.invoice_id}`} className="text-blue-700 hover:underline">{t("creditFromDocument")}</Link>
                            ) : (
                              <button type="button" disabled={pending} onClick={() => removeCredit(c, cr.id)} className="inline-flex items-center gap-1 text-slate-500 hover:text-red-600 disabled:opacity-50">
                                <Undo2 className="w-3.5 h-3.5" aria-hidden />{t("creditRemove")}
                              </button>
                            )}
                          </li>
                        ))}
                      </ul>
                    </td>
                  </tr>
                ) : null}
                </Fragment>
              ))
            )}
          </tbody>
        </table>
      </div>
      {/* Below the table, not in a row of it: inside the horizontal scroll the form's fields and its
          button ran off the screen on a laptop and on a phone. */}
      {creditCost ? (
        <div id="credit-form" className="border-t border-slate-200 bg-blue-50/40 px-4 py-3">
          <CreditForm key={creditCost.id} cost={creditCost} revalidate={revalidate} onDone={() => setCrediting(null)} />
        </div>
      ) : null}
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

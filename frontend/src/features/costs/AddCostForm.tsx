"use client";

import { useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { Loader2, Plus } from "lucide-react";
import { toast } from "sonner";
import { btnPrimary, input, label } from "@/components/layout/AppShell";
import { addCost } from "@/features/actions";
import { COST_TYPES, USER_METHODS, type CostScope } from "@/lib/domain";
import { amountPlaceholder } from "@/lib/format";

export default function AddCostForm({ scope, targetId, shipmentId, currency, revalidate }: { scope: CostScope; targetId: string; shipmentId?: string | null; currency: string; revalidate: string }) {
  const t = useTranslations("costs.form");
  const costTypeLabel = useTranslations("domain.costType");
  const methodLabel = useTranslations("domain.method");
  const locale = useLocale();
  const [pending, startTransition] = useTransition();
  const [cur, setCur] = useState(currency);
  const [costType, setCostType] = useState("OCEAN_FREIGHT");
  const formRef = useRef<HTMLFormElement>(null);
  const router = useRouter();
  const today = new Date().toISOString().slice(0, 10);

  const submit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const chosenScope = String(f.get("scope") ?? scope) as CostScope;
    startTransition(async () => {
      const result = await addCost({
        scope: chosenScope,
        target_id: chosenScope === "SHIPMENT" && shipmentId ? shipmentId : targetId,
        cost_type: String(f.get("cost_type")),
        amount: String(f.get("amount") ?? ""),
        currency: String(f.get("currency") ?? currency).toUpperCase(),
        cost_date: String(f.get("cost_date") ?? today),
        allocation_method: String(f.get("allocation_method") ?? "") || undefined,
        fx_rate: String(f.get("fx_rate") ?? "") || undefined,
        vendor: String(f.get("vendor") ?? "") || undefined,
        invoice_number: String(f.get("invoice_number") ?? "") || undefined,
        status: (String(f.get("status") ?? "ACTUAL") as "ACTUAL" | "ESTIMATE"),
        revalidate,
      });
      if (!result.ok) return void toast.error(result.error);
      toast.success(t("added"));
      formRef.current?.reset();
      setCur(currency);
      router.refresh();
    });
  };

  return (
    <form ref={formRef} onSubmit={submit} className="p-5 space-y-3">
      {shipmentId ? (
        <div>
          <label className={label} htmlFor="scope">{t("appliesTo")}</label>
          <select id="scope" name="scope" defaultValue={scope} className={input}>
            <option value="CONTAINER">{t("thisContainer")}</option>
            <option value="SHIPMENT">{t("wholeShipment")}</option>
          </select>
        </div>
      ) : null}
      <div>
        <label className={label} htmlFor="cost_type">{t("costType")}</label>
        <select id="cost_type" name="cost_type" value={costType} onChange={(e) => setCostType(e.target.value)} className={input}>
          {COST_TYPES.map((c) => <option key={c} value={c}>{costTypeLabel(c)}</option>)}
        </select>
      </div>
      <div className="grid grid-cols-3 gap-2">
        <div className="col-span-2">
          <label className={label} htmlFor="amount">{t("amount")}</label>
          <input id="amount" name="amount" inputMode="decimal" required placeholder={amountPlaceholder(locale)} className={`${input} tabular-nums`} />
        </div>
        <div>
          <label className={label} htmlFor="currency">{t("currency")}</label>
          <input id="currency" name="currency" value={cur} onChange={(e) => setCur(e.target.value.toUpperCase())} maxLength={3} className={`${input} uppercase font-mono`} />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className={label} htmlFor="status">{t("status")}</label>
          <select id="status" name="status" defaultValue="ACTUAL" className={input}>
            <option value="ACTUAL">{t("statusActual")}</option>
            <option value="ESTIMATE">{t("statusEstimate")}</option>
          </select>
        </div>
        <div>
          <label className={label} htmlFor="cost_date">{t("invoiceDate")}</label>
          <input id="cost_date" name="cost_date" type="date" defaultValue={today} required className={input} />
        </div>
        <div>
          <label className={label} htmlFor="fx_rate">{cur !== currency ? t("fxRateWith", { from: cur, to: currency }) : t("fxRate")}</label>
          <input id="fx_rate" name="fx_rate" inputMode="decimal" placeholder={cur === currency ? t("fxNotApplicable") : t("fxEcb")} disabled={cur === currency} className={`${input} tabular-nums`} />
        </div>
      </div>
      {costType !== "CUSTOMS_DUTY" ? (
        <div>
          <label className={label} htmlFor="allocation_method">{t("method")}</label>
          <select id="allocation_method" name="allocation_method" defaultValue="" className={input}>
            <option value="">{t("methodDefault")}</option>
            {USER_METHODS.map((m) => <option key={m} value={m}>{methodLabel(m)}</option>)}
          </select>
        </div>
      ) : (
        <p className="text-xs text-slate-500">{t("dutyNote")}</p>
      )}
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className={label} htmlFor="vendor">{t("vendor")}</label>
          <input id="vendor" name="vendor" className={input} />
        </div>
        <div>
          <label className={label} htmlFor="invoice_number">{t("invoiceNumber")}</label>
          <input id="invoice_number" name="invoice_number" className={input} />
        </div>
      </div>
      <button type="submit" disabled={pending} className={`${btnPrimary} w-full`}>
        {pending ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Plus className="w-4 h-4 mr-2" />}
        {t("submit")}
      </button>
    </form>
  );
}

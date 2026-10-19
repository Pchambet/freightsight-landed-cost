"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";
import { AlertTriangle } from "lucide-react";
import { btnPrimary, btnSecondary, input, label } from "@/components/layout/AppShell";
import { setPurchaseOrderRate } from "@/features/purchase-orders/actions";
import { dateShort, rate } from "@/lib/format";

/**
 * An order's exchange rate, and the one place to set it by hand: a currency the ECB does not quote
 * (VND, AED, BDT) has no other way in, and without it the order has no value. Open from the start
 * while the rate is missing or still to be checked; behind a button once there is one. The rate says
 * where it came from. The API refuses what is not a rate.
 */
export default function RateForm({
  poId,
  currency,
  base,
  current,
  rateDate,
  forDay = null,
  source,
  unconfirmed,
  needsDate,
  canEdit,
}: {
  poId: string;
  currency: string;
  base: string;
  current: string | null;
  rateDate: string | null;
  /** The day a rate typed here is for, when the order has one: its rate date, else its order date. */
  forDay?: string | null;
  /** Where the rate came from (`fx_source`): the ECB, typed on the order, the order file. */
  source: string | null;
  /** A rate of 1 the import wrote when it had none, before a missing rate was left empty (`rate_unconfirmed`). */
  unconfirmed: boolean;
  /** The order has no date of its own: the day the rate is for may be given (the API does not require it). */
  needsDate: boolean;
  /** A viewer reads the rate; only a member who may edit orders gets the form. */
  canEdit: boolean;
}) {
  const t = useTranslations("purchaseOrder.rate");
  const tSource = useTranslations("domain.fxSource");
  const locale = useLocale();
  const router = useRouter();
  const [open, setOpen] = useState(current === null || unconfirmed);
  const [value, setValue] = useState("");
  const [date, setDate] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();

  return (
    <section id="taux" className="scroll-mt-6 rounded-xl border border-slate-200 bg-white shadow-card px-5 py-4 mb-6">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-sm font-semibold text-slate-900">{t("title")}</h2>
        {current !== null ? (
          <p className="text-sm text-slate-700 tabular-nums">
            {t("current", { currency, rate: rate(current, locale), base })}
            {rateDate ? <span className="ml-2 text-xs text-slate-500">{t("dated", { date: dateShort(rateDate, locale) })}</span> : null}
            {source && tSource.has(source) ? <span className="ml-2 text-xs text-slate-500">{t("source", { source: tSource(source) })}</span> : null}
          </p>
        ) : null}
      </div>
      {current === null ? (
        <p role="note" className="mt-2 flex items-start gap-2 text-sm text-amber-900">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" aria-hidden />
          {t("missing", { currency })}
        </p>
      ) : unconfirmed ? (
        <p role="note" className="mt-2 flex items-start gap-2 text-sm text-amber-900">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" aria-hidden />
          {t("unconfirmed", { currency, base })}
        </p>
      ) : null}
      {!canEdit ? null : open ? (
        <form
          className="mt-3 flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            setError(null);
            start(async () => {
              const r = await setPurchaseOrderRate(poId, value, needsDate ? date || null : null);
              if (!r.ok) return void setError(r.error);
              toast.success(t("saved"));
              setValue("");
              setOpen(false);
              router.refresh();
            });
          }}
        >
          <div>
            {/* The day the rate is for: the order's date (or the rate's own), which the saved rate is dated with. */}
            <label className={label} htmlFor="po-rate">{forDay ? t("labelDated", { currency, base, date: dateShort(forDay, locale) }) : t("label", { currency, base })}</label>
            <input
              id="po-rate"
              inputMode="decimal"
              autoComplete="off"
              required
              value={value}
              onChange={(e) => setValue(e.target.value)}
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? "po-rate-error" : "po-rate-hint"}
              className={`${input} !w-40 text-right tabular-nums`}
            />
          </div>
          {needsDate ? (
            <div>
              <label className={label} htmlFor="po-rate-date">{t("date")}</label>
              <input id="po-rate-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} className={`${input} !w-auto`} />
            </div>
          ) : null}
          <button type="submit" disabled={pending || value.trim() === ""} className={btnPrimary}>{pending ? t("saving") : t("save")}</button>
          {current !== null ? (
            <button type="button" onClick={() => { setOpen(false); setError(null); }} className={btnSecondary}>{t("cancel")}</button>
          ) : null}
          {error ? <p id="po-rate-error" role="alert" className="basis-full text-sm text-red-700">{error}</p> : null}
          <p id="po-rate-hint" className="basis-full text-xs text-slate-500 max-w-2xl">{t("hint")}{needsDate ? ` ${t("dateHint")}` : ""}</p>
        </form>
      ) : (
        <button type="button" onClick={() => setOpen(true)} className={`${btnSecondary} mt-3`}>{t("change")}</button>
      )}
    </section>
  );
}

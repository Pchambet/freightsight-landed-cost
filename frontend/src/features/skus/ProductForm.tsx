"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";
import { btnPrimary, input, label } from "@/components/layout/AppShell";
import { saveProduct, type ProductInput } from "@/features/actions";
import { amountPlaceholder } from "@/lib/format";

/**
 * The product sheet of one SKU: what an order line will take by default when it says nothing
 * (designation, HS code, duty rate, unit weight and volume), and the selling price the margin is
 * read against. Saving it never moves a landed cost already computed — the API copies these values
 * onto NEW order lines only.
 */
export default function ProductForm({ initial, currency, revalidate }: { initial: ProductInput; currency: string; revalidate: string }) {
  const t = useTranslations("skus.product");
  const locale = useLocale();
  const router = useRouter();
  const [form, setForm] = useState(initial);
  const [pending, startTransition] = useTransition();
  const set = (k: keyof ProductInput) => (e: React.ChangeEvent<HTMLInputElement>) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const dirty = (Object.keys(form) as (keyof ProductInput)[]).some((k) => form[k] !== initial[k]);

  return (
    <form
      className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3"
      onSubmit={(e) => {
        e.preventDefault();
        startTransition(async () => {
          const result = await saveProduct(form, revalidate);
          if (!result.ok) return void toast.error(result.error);
          toast.success(t("saved"));
          router.refresh();
        });
      }}
    >
      <div className="sm:col-span-2 lg:col-span-3">
        <label className={label} htmlFor="p-description">{t("description")}</label>
        <input id="p-description" className={input} value={form.description} onChange={set("description")} maxLength={300} />
      </div>
      <div>
        <label className={label} htmlFor="p-price">{t("salePrice", { currency })}</label>
        <input id="p-price" className={input} inputMode="decimal" placeholder={amountPlaceholder(locale)} value={form.sale_price} onChange={set("sale_price")} />
        <p className="mt-1 text-xs text-slate-500">{t("salePriceHint")}</p>
      </div>
      <div>
        <label className={label} htmlFor="p-hs">{t("hsCode")}</label>
        <input id="p-hs" className={`${input} font-mono`} inputMode="numeric" placeholder="4011 10 00" value={form.hs_code} onChange={set("hs_code")} maxLength={14} />
      </div>
      <div>
        <label className={label} htmlFor="p-duty">{t("dutyRate")}</label>
        <input id="p-duty" className={input} inputMode="decimal" placeholder={locale === "fr" ? "4,5" : "4.5"} value={form.duty_rate_pct} onChange={set("duty_rate_pct")} />
      </div>
      <div>
        <label className={label} htmlFor="p-weight">{t("unitWeight")}</label>
        <input id="p-weight" className={input} inputMode="decimal" value={form.unit_weight_kg} onChange={set("unit_weight_kg")} />
      </div>
      <div>
        <label className={label} htmlFor="p-volume">{t("unitVolume")}</label>
        <input id="p-volume" className={input} inputMode="decimal" value={form.unit_volume_cbm} onChange={set("unit_volume_cbm")} />
      </div>
      <div className="sm:col-span-2 lg:col-span-3 flex flex-wrap items-center gap-3">
        <button type="submit" disabled={pending || !dirty} className={btnPrimary}>{pending ? t("saving") : t("save")}</button>
        <p className="text-xs text-slate-500 max-w-xl">{t("scopeHint")}</p>
      </div>
    </form>
  );
}

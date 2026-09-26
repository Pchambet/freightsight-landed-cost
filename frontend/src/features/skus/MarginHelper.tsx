"use client";

import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { money, pct } from "@/lib/format";

/**
 * The question behind the whole product: "at what price do I keep my margin, now that I know what
 * this unit really costs?" The margin is on the selling price (taux de marque), the way a French
 * CFO states it: price = cost ÷ (1 − margin). Arithmetic here is a what-if on screen — nothing is
 * stored, nothing feeds a landed cost — so plain numbers are fine.
 */
export default function MarginHelper({ unitFob, unitLanded, salePrice, currency }: { unitFob: number; unitLanded: number; salePrice: number | null; currency: string }) {
  const t = useTranslations("skus.margin");
  const locale = useLocale();
  const [target, setTarget] = useState(35);
  const needed = target < 100 ? unitLanded / (1 - target / 100) : null;
  const onFob = salePrice && salePrice > 0 ? ((salePrice - unitFob) / salePrice) * 100 : null;
  const onLanded = salePrice && salePrice > 0 ? ((salePrice - unitLanded) / salePrice) * 100 : null;

  return (
    <div className="space-y-5">
      {onFob !== null && onLanded !== null ? (
        <div className="grid grid-cols-2 gap-3">
          <div className="rounded-lg border border-slate-200 px-4 py-3">
            <div className="text-xs text-slate-500">{t("onFob")}</div>
            <div className="mt-1 text-xl font-semibold text-slate-500 line-through decoration-1 figure-proportional">{pct(onFob, locale)}</div>
            <div className="text-xs text-slate-500">{t("onFobHint")}</div>
          </div>
          <div className={`rounded-lg border px-4 py-3 ${onLanded < 0 ? "border-red-200 bg-red-50" : "border-emerald-200 bg-emerald-50"}`}>
            <div className="text-xs text-slate-600">{t("onLanded")}</div>
            <div className={`mt-1 text-xl font-semibold figure-proportional ${onLanded < 0 ? "text-red-800" : "text-emerald-900"}`}>{pct(onLanded, locale)}</div>
            <div className="text-xs text-slate-600">{t("onLandedHint", { points: new Intl.NumberFormat(locale === "fr" ? "fr-FR" : "en-GB", { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(onFob - onLanded) })}</div>
          </div>
        </div>
      ) : (
        <p className="text-sm text-slate-600">{t("noPrice")}</p>
      )}

      <div>
        <label htmlFor="target-margin" className="flex items-baseline justify-between text-sm">
          <span className="text-slate-700">{t("target")}</span>
          <span className="font-semibold text-slate-900 tabular-nums">{target} %</span>
        </label>
        <input id="target-margin" type="range" min={5} max={70} step={1} value={target} onChange={(e) => setTarget(Number(e.target.value))} className="mt-2 w-full accent-blue-600" />
        <div className="mt-3 flex items-baseline justify-between gap-3 rounded-lg bg-slate-50 px-4 py-3">
          <span className="text-sm text-slate-600">{t("needed")}</span>
          <span className="text-xl font-semibold text-slate-900 figure-proportional">{needed !== null ? money(needed, currency, locale) : "—"}</span>
        </div>
        {salePrice && needed !== null ? (
          <p className="mt-2 text-xs text-slate-500">
            {salePrice >= needed ? t("priceHolds", { price: money(salePrice, currency, locale) }) : t("priceShort", { price: money(salePrice, currency, locale), gap: money(needed - salePrice, currency, locale) })}
          </p>
        ) : null}
      </div>
    </div>
  );
}

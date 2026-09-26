"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";
import { btnSecondary, input, label } from "@/components/layout/AppShell";
import { saveAssumedCoefficient } from "@/features/actions";

/**
 * "What do you multiply your purchase price by?" — the one figure a company knows from memory and
 * the audit measures against. Asked once, kept in the company's settings, changeable here.
 */
export default function AssumedCoefficientForm({ current, revalidate }: { current: string | null; revalidate: string }) {
  const t = useTranslations("costAudit.assumed");
  const locale = useLocale();
  const shown = current === null ? "" : locale === "fr" ? current.replace(".", ",").replace(/0+$/, "").replace(/[,.]$/, "") : current.replace(/0+$/, "").replace(/\.$/, "");
  const [value, setValue] = useState(shown);
  const [pending, start] = useTransition();
  const router = useRouter();
  return (
    <form
      className="flex flex-wrap items-end gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        start(async () => {
          const r = await saveAssumedCoefficient(value, revalidate);
          if (!r.ok) return void toast.error(r.error);
          toast.success(value.trim() ? t("saved") : t("cleared"));
          router.refresh();
        });
      }}
    >
      <div>
        <label className={label} htmlFor="assumed-coefficient">{t("label")}</label>
        <input id="assumed-coefficient" inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} placeholder={locale === "fr" ? "1,15" : "1.15"} className={`${input} !w-28 text-right tabular-nums`} />
      </div>
      <button type="submit" disabled={pending || value === shown} className={btnSecondary}>{pending ? t("saving") : t("save")}</button>
      <p className="basis-full text-xs text-slate-500 max-w-2xl">{t("hint")}</p>
    </form>
  );
}

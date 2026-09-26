"use client";

import { useTransition } from "react";
import { useLocale, useTranslations } from "next-intl";
import { Languages } from "lucide-react";
import { LOCALES } from "@/i18n/config";
import { setLocale } from "@/i18n/actions";

/** FR / EN, remembered in a cookie. No locale in the URL, so links and bookmarks stay stable. */
export default function LocaleSwitcher() {
  const locale = useLocale();
  const t = useTranslations("common");
  const [pending, startTransition] = useTransition();
  const name: Record<string, string> = { fr: t("french"), en: t("english") };

  return (
    <label className="flex items-center gap-1 text-slate-500">
      <Languages className="w-4 h-4" aria-hidden />
      <span className="sr-only">{t("language")}</span>
      <select
        value={locale}
        disabled={pending}
        onChange={(e) => startTransition(async () => await setLocale(e.target.value))}
        className="bg-transparent text-xs text-slate-600 border-none focus:ring-0 cursor-pointer py-0 pr-5"
      >
        {LOCALES.map((l) => (
          <option key={l} value={l}>
            {name[l] ?? l}
          </option>
        ))}
      </select>
    </label>
  );
}

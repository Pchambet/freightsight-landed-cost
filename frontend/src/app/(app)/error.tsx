"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { useEffect } from "react";
import { AlertTriangle } from "lucide-react";
import { btnPrimary, btnSecondary } from "@/components/layout/AppShell";
import { site } from "@/lib/site";

export default function AppError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  const t = useTranslations("errors.page");
  const isApi = error.message === "api_error";

  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="flex items-center justify-center py-16">
      <div className="bg-white p-8 rounded-lg border border-slate-200 text-center max-w-md w-full">
        <div className="w-12 h-12 bg-amber-50 text-amber-700 rounded-full flex items-center justify-center mx-auto mb-4">
          <AlertTriangle className="w-6 h-6" aria-hidden />
        </div>
        <h1 className="text-lg font-semibold mb-2">{isApi ? t("apiTitle") : t("title")}</h1>
        <p className="text-slate-500 mb-6 text-sm">{isApi ? t("apiBody") : t("body")}</p>
        <div className="flex flex-col sm:flex-row gap-2 justify-center">
          <button type="button" onClick={reset} className={btnPrimary}>{t("retry")}</button>
          <Link href={site.appHome} className={btnSecondary}>{t("back")}</Link>
        </div>
      </div>
    </div>
  );
}

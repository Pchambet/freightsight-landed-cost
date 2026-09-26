import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { SearchX } from "lucide-react";
import { site } from "@/lib/site";
import { btnPrimary } from "@/components/layout/AppShell";

export default async function AppNotFound() {
  const t = await getTranslations("errors.notFound");
  return (
    <div className="flex items-center justify-center py-16">
      <div className="bg-white p-8 rounded-lg border border-slate-200 text-center max-w-md w-full">
        <div className="w-12 h-12 bg-slate-100 text-slate-600 rounded-full flex items-center justify-center mx-auto mb-4">
          <SearchX className="w-6 h-6" aria-hidden />
        </div>
        <h1 className="text-lg font-semibold mb-2">{t("title")}</h1>
        <p className="text-slate-500 mb-6 text-sm">{t("body")}</p>
        <Link href={site.appHome} className={`${btnPrimary} w-full`}>{t("back")}</Link>
      </div>
    </div>
  );
}

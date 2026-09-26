import { getTranslations } from "next-intl/server";

/** One answer for a link that never existed, has expired or was withdrawn — and a real 404 status. */
export default async function SharedReportGone() {
  const t = await getTranslations("sheet.gone");
  return (
    <div className="min-h-screen bg-slate-100 px-4 py-16">
      <div className="mx-auto max-w-md rounded-xl border border-slate-200 bg-white shadow-card px-6 py-10 text-center">
        <h1 className="text-lg font-semibold text-slate-900">{t("title")}</h1>
        <p className="mt-2 text-sm text-slate-600">{t("body")}</p>
      </div>
    </div>
  );
}

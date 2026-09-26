import type { Metadata } from "next";
import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";
import { CalendarCheck, LockKeyhole } from "lucide-react";
import { PageHeader } from "@/components/layout/AppShell";
import ApiErrorNotice from "@/components/shared/ApiErrorNotice";
import Panel from "@/components/ui/Panel";
import { periodTitle, signedMoney } from "@/features/periods/format";
import { api } from "@/lib/api/client";
import { dateShort, money } from "@/lib/format";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("periods");
  return { title: t("title") };
}

export default async function PeriodsPage() {
  const [t, locale, res] = await Promise.all([getTranslations("periods"), getLocale(), api.GET("/api/v1/periods")]);
  if (res.error || !res.data) {
    return (
      <>
        <PageHeader title={t("title")} />
        <ApiErrorNotice error={res.error} retryHref="/periods" />
      </>
    );
  }
  const { periods, base_currency: currency } = res.data;
  const ratio = (v: string | null | undefined) => (v === null || v === undefined ? "—" : new Intl.NumberFormat(locale === "fr" ? "fr-FR" : "en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(v)));
  const fmt = (v: number) => money(v, currency, locale);

  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")} />

      <div className="rounded-xl border border-slate-200 bg-white shadow-card px-5 py-4 mb-6 flex items-start gap-3 text-sm text-slate-700">
        <CalendarCheck className="w-5 h-5 mt-0.5 shrink-0 text-blue-700" aria-hidden />
        <p className="max-w-3xl">{t("principle")}</p>
      </div>

      <Panel className="[&>div]:p-0">
        {periods.length === 0 ? (
          <p className="px-5 py-12 text-center text-sm text-slate-500">{t("empty")}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="text-xs text-slate-500 border-b border-slate-200 bg-slate-50/60">
                  <th className="px-5 py-2.5 text-left font-medium">{t("th.month")}</th>
                  <th className="px-3 py-2.5 text-left font-medium">{t("th.status")}</th>
                  <th className="px-3 py-2.5 text-right font-medium">{t("th.containers")}</th>
                  <th className="px-3 py-2.5 text-right font-medium">{t("th.fob")}</th>
                  <th className="px-3 py-2.5 text-right font-medium">{t("th.landed")}</th>
                  <th className="px-3 py-2.5 text-right font-medium">{t("th.coef")}</th>
                  <th className="px-5 py-2.5 text-right font-medium">{t("th.drift")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {periods.map((p) => {
                  const closed = p.status === "closed";
                  const drift = Number(p.drift ?? 0);
                  return (
                    <tr key={p.period} className="hover:bg-slate-50">
                      <td className="px-5 py-3 whitespace-nowrap"><Link href={`/periods/${p.period}`} className="font-medium text-blue-700 hover:underline">{periodTitle(p.period, locale)}</Link></td>
                      <td className="px-3 py-3 whitespace-nowrap">
                        {closed ? (
                          <span className="inline-flex items-center gap-1.5 text-slate-700"><LockKeyhole className="w-3.5 h-3.5 text-slate-500" aria-hidden />{t("closedOn", { date: dateShort(p.closed_at, locale) })}{p.closed_by_name ? <span className="text-xs text-slate-500">· {p.closed_by_name}</span> : null}</span>
                        ) : (
                          <span className="rounded-md bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-800 ring-1 ring-inset ring-blue-600/20">{t("open")}</span>
                        )}
                      </td>
                      <td className="px-3 py-3 text-right tabular-nums text-slate-600">{p.containers}</td>
                      <td className="px-3 py-3 text-right tabular-nums text-slate-600">{money(p.fob, currency, locale)}</td>
                      <td className="px-3 py-3 text-right tabular-nums font-medium text-slate-900">{money(p.landed, currency, locale)}</td>
                      <td className="px-3 py-3 text-right tabular-nums text-slate-600">{ratio(p.coefficient)}</td>
                      <td className="px-5 py-3 text-right tabular-nums whitespace-nowrap">
                        {!closed ? <span className="text-slate-500">—</span> : drift === 0 ? <span className="text-slate-500">{t("noDrift")}</span> : Number(p.drift_outstanding ?? p.drift ?? 0) === 0 ? (
                          <span className="text-slate-600">{signedMoney(p.drift!, fmt)} <span className="text-xs text-emerald-800">· {t("posted")}</span></span>
                        ) : (
                          <span className="font-medium text-amber-800">{signedMoney(p.drift!, fmt)} <span className="font-normal text-xs text-slate-500">· {t("driftContainers", { count: p.drift_containers ?? 0 })}</span></span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  );
}

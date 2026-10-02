import type { Metadata } from "next";
import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";
import { Card, PageHeader, btnSecondary, td, tdRight, th, thRight } from "@/components/layout/AppShell";
import Breadcrumb from "@/components/shared/Breadcrumb";
import { Money } from "@/components/shared/Money";
import ScrollRegion from "@/components/shared/ScrollRegion";
import WorkflowNextStep from "@/features/workflow/WorkflowNextStep";
import { varianceHubStep } from "@/features/workflow/steps";
import { api, type VarianceReport } from "@/lib/api/client";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("variance");
  return { title: t("title") };
}

function shiftMonth(period: string, delta: number): string {
  const [y, m] = period.split("-").map(Number);
  const d = new Date(Date.UTC(y, m - 1 + delta, 1));
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

function Table({ rows, currency, title, thLabel, thEst, thAct, thVar }: { rows: VarianceReport["by_cost_type"]; currency: string; title: string; thLabel: string; thEst: string; thAct: string; thVar: string }) {
  return (
    <Card title={title}>
      <ScrollRegion label={title}>
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-slate-50"><tr><th className={th}>{thLabel}</th><th className={thRight}>{thEst}</th><th className={thRight}>{thAct}</th><th className={thRight}>{thVar}</th></tr></thead>
          <tbody className="divide-y divide-slate-200">
            {rows.map((r) => {
              const v = Number(r.variance);
              return (
                <tr key={r.key}>
                  <td className={td}>{r.label}</td>
                  <td className={`${tdRight} text-violet-800`}><Money amount={r.estimated} currency={currency} /></td>
                  <td className={tdRight}><Money amount={r.actual} currency={currency} /></td>
                  <td className={`${tdRight} font-medium ${v > 0 ? "text-red-700" : v < 0 ? "text-emerald-700" : ""}`}>{v > 0 ? "+" : ""}<Money amount={r.variance} currency={currency} /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </ScrollRegion>
    </Card>
  );
}

export default async function VariancePage({ searchParams }: { searchParams: Promise<{ period?: string }> }) {
  const [{ period: p }, t, tr, tt, locale] = await Promise.all([searchParams, getTranslations("variance"), getTranslations("reports"), getTranslations("domain.costType"), getLocale()]);
  const now = new Date();
  const period = p && /^\d{4}-\d{2}$/.test(p) ? p : `${now.getUTCFullYear()}-${String(now.getUTCMonth() + 1).padStart(2, "0")}`;
  const res = await api.GET("/api/v1/reports/variance", { params: { query: { period } } });
  const r = res.data;
  const cur = r?.base_currency ?? "EUR";
  const monthLabel = new Intl.DateTimeFormat(locale === "fr" ? "fr-FR" : "en-GB", { month: "long", year: "numeric", timeZone: "UTC" }).format(new Date(`${period}-01T00:00:00Z`));
  const byType = (r?.by_cost_type ?? []).map((row) => ({ ...row, label: tt.has(row.key as never) ? tt(row.key as never) : row.label }));
  const v = Number(r?.variance ?? "0");
  const hubStep = varianceHubStep(r?.pairs ?? 0);

  return (
    <>
      {/* The h1 right below already says "Écarts du mois" — the breadcrumb only needs the back link. */}
      <Breadcrumb backHref="/reports" backLabel={tr("title")} />
      <PageHeader
        title={t("title")}
        subtitle={t("subtitle")}
        actions={
          <div className="flex items-center gap-2 text-sm">
            <Link href={`/reports/variance?period=${shiftMonth(period, -1)}`} className="px-2 py-1 rounded-md hover:bg-slate-100">‹</Link>
            <span className="font-medium capitalize min-w-[9rem] text-center">{monthLabel}</span>
            <Link href={`/reports/variance?period=${shiftMonth(period, 1)}`} className="px-2 py-1 rounded-md hover:bg-slate-100">›</Link>
          </div>
        }
      />
      <WorkflowNextStep step={hubStep} />
      <div className="grid grid-cols-3 gap-3 mb-6">
        {[["estimated", r?.estimated, "text-violet-800"], ["actual", r?.actual, ""], ["variance", r?.variance, v > 0 ? "text-red-700" : v < 0 ? "text-emerald-700" : ""]].map(([k, val, cls]) => (
          <div key={k as string} className="bg-white rounded-lg border border-slate-200 px-4 py-3">
            <div className="text-xs text-slate-500 uppercase tracking-wider">{t(`th.${k as string}`)}</div>
            <div className={`text-lg font-semibold tabular-nums mt-1 ${cls}`}>{k === "variance" && v > 0 ? "+" : ""}<Money amount={(val as string) ?? "0.00"} currency={cur} /></div>
          </div>
        ))}
      </div>
      {/* The population the three tiles above rest on, said in words: they used to be read against a
          container's own "estimated / actual / variance", which counted something else entirely. */}
      <p className="text-sm text-slate-500 mb-1">{t("pairs", { count: r?.pairs ?? 0 })}</p>
      <p className="text-xs text-slate-500 mb-4 max-w-3xl">{t("basis")}</p>
      {(r?.pairs ?? 0) === 0 ? (
        <Card>
          <div className="px-5 py-8 text-center space-y-3">
            <p className="text-sm text-slate-600">{t("empty")}</p>
            <p className="text-xs text-slate-500 max-w-lg mx-auto">{t("emptyHint")}</p>
            <div className="flex flex-wrap justify-center gap-2 pt-2">
              <Link href="/containers" className={btnSecondary}>{t("backContainers")}</Link>
              <Link href="/invoices#depot" className={btnSecondary}>{t("depositInvoice")}</Link>
            </div>
          </div>
        </Card>
      ) : (
        <div className="grid gap-6 xl:grid-cols-2 items-start">
          <Table rows={byType} currency={cur} title={t("byType")} thLabel={t("th.label")} thEst={t("th.estimated")} thAct={t("th.actual")} thVar={t("th.variance")} />
          <Table rows={r?.by_purchase_order ?? []} currency={cur} title={t("byPo")} thLabel={t("th.label")} thEst={t("th.estimated")} thAct={t("th.actual")} thVar={t("th.variance")} />
        </div>
      )}
    </>
  );
}

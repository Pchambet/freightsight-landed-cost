import type { Metadata } from "next";
import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";
import { Card, PageHeader, btnPrimary, td, tdRight, th, thRight } from "@/components/layout/AppShell";
import ScrollRegion from "@/components/shared/ScrollRegion";
import UndoImportButton from "@/features/imports/UndoImportButton";
import AuditPreparation from "@/features/report/AuditPreparation";
import { presetRange } from "@/features/report/auditPeriods";
import WorkflowNextStep from "@/features/workflow/WorkflowNextStep";
import { importsHubStep } from "@/features/workflow/steps";
import { api, type ImportJobResponse } from "@/lib/api/client";
import { dateShort, money } from "@/lib/format";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("imports");
  return { title: t("title") };
}

const STATUS_STYLE: Record<string, string> = {
  PARSED: "bg-slate-100 text-slate-700",
  VALIDATED: "bg-blue-50 text-blue-700",
  DONE: "bg-emerald-50 text-emerald-700",
  FAILED: "bg-red-50 text-red-700",
};

export default async function ImportsPage() {
  const [{ data }, t, tc, locale, org] = await Promise.all([
    api.GET("/api/v1/imports"),
    getTranslations("imports"),
    getTranslations("common"),
    getLocale(),
    api.GET("/api/v1/organization"),
  ]);
  const jobs = data ?? [];
  const hubStep = importsHubStep(jobs);
  const base = org.data?.base_currency ?? "EUR";
  // What an import of this kind did, in one line of the history.
  const result = (j: ImportJobResponse) => {
    const r = j.report;
    switch (j.kind) {
      case "PRODUCTS":
        return t("resultProducts", { created: r.products_created ?? 0, updated: r.products_updated ?? 0 });
      case "CONTAINERS":
        return t("resultContainers", { created: r.containers_created ?? 0, updated: r.containers_updated ?? 0, dated: r.containers_dated ?? 0 });
      case "COSTS":
        return t("resultCosts", { count: r.costs_created ?? 0, amount: money(r.money?.total_base ?? "0", base, locale), vat: r.money?.by_type?.some((x) => x.cost_type === "IMPORT_VAT") ? "yes" : "no" });
      default:
        return t("result", { pos: r.purchase_orders_created ?? 0, lines: r.lines_created ?? 0, containers: r.containers_created ?? 0, loads: r.loads_created ?? 0 });
    }
  };
  const quarter = presetRange("lastQuarter");
  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")} actions={<Link href="/imports/new" className={btnPrimary}>{t("new")}</Link>} />
      <WorkflowNextStep step={hubStep} />
      <AuditPreparation periodFrom={quarter.from} periodTo={quarter.to} showPeriod />
      <Card>
        <ScrollRegion label={t("history")}>
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50"><tr><th className={th}>{t("th.date")}</th><th className={th}>{t("th.file")}</th><th className={th}>{t("th.kind")}</th><th className={th}>{t("th.status")}</th><th className={thRight}>{t("th.rows")}</th><th className={thRight}>{t("th.errors")}</th><th className={th}>{t("th.result")}</th><th className={th}><span className="sr-only">{t("th.actions")}</span></th></tr></thead>
            <tbody className="divide-y divide-slate-200">
              {jobs.length === 0 ? (
                <tr><td colSpan={8} className="px-5 py-14 text-center text-sm text-slate-500">{t("empty")} <Link href="/imports/new" className="text-blue-600 hover:underline font-medium">{t("emptyLink")}</Link> {t("emptySuffix")}</td></tr>
              ) : (
                jobs.map((j) => (
                  // The anchor a « file already imported » refusal leads to.
                  <tr key={j.id} id={`import-${j.id}`} className="scroll-mt-24 target:bg-amber-50">
                    <td className={`${td} text-slate-600`}>{dateShort(j.created_at, locale)}</td>
                    {/* An ERP-synced job has no uploaded file: its synthetic name is not shown as one (B23). */}
                    <td className={td}>{j.source === "erp_sync" ? t("source.erp_sync") : (j.original_filename ?? tc("empty"))}</td>
                    <td className={`${td} text-xs text-slate-600`}>{t(`kind.${j.kind}`)}</td>
                    <td className={td}>
                      {j.undone_at ? (
                        <span className="inline-flex rounded-md px-2 py-0.5 text-xs font-medium bg-slate-100 text-slate-600">{t("undoneOn", { date: dateShort(j.undone_at, locale) })}</span>
                      ) : (
                        <span className={`inline-flex rounded-md px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[j.status]}`}>{t(`status.${j.status}`)}</span>
                      )}
                    </td>
                    <td className={tdRight}>{j.row_count}</td>
                    <td className={`${tdRight} ${j.error_count ? "text-amber-700" : ""}`}>{j.error_count}</td>
                    <td className={`${td} text-xs text-slate-600`}>
                      {j.status === "DONE"
                        ? result(j)
                        : j.status === "PARSED" || j.status === "VALIDATED"
                          ? <Link href={`/imports/new?job=${j.id}`} className="text-blue-600 hover:underline">{t("continue")}</Link>
                          : tc("empty")}
                    </td>
                    <td className={td}>{j.can_undo ? <UndoImportButton importId={j.id} costCount={j.report.costs_created ?? 0} estimatesReplaced={j.report.estimates_replaced ?? 0} /> : null}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </ScrollRegion>
      </Card>
    </>
  );
}

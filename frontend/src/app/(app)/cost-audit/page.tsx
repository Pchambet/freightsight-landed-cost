import type { Metadata } from "next";
import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";
import { Download } from "lucide-react";
import { PageHeader, btnSecondary, input, label } from "@/components/layout/AppShell";
import ApiErrorNotice from "@/components/shared/ApiErrorNotice";
import Panel from "@/components/ui/Panel";
import AssumedCoefficientForm from "@/features/report/AssumedCoefficientForm";
import AuditPreparation from "@/features/report/AuditPreparation";
import AuditSheet from "@/features/report/AuditSheet";
import PrintButton from "@/features/report/PrintButton";
import ShareDialog from "@/features/report/ShareDialog";
import { AUDIT_PRESETS, isDay, presetRange, type AuditPreset } from "@/features/report/auditPeriods";
import { api } from "@/lib/api/client";
import { dateShort } from "@/lib/format";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("costAudit");
  return { title: t("title") };
}

type Search = { preset?: string; from?: string; to?: string };

/**
 * The audit of a period, in the application: choose the window, give the coefficient you apply,
 * read the document, then print it, share it or take the findings away as a spreadsheet.
 */
export default async function CostAuditPage({ searchParams }: { searchParams: Promise<Search> }) {
  const [sp, t, locale] = await Promise.all([searchParams, getTranslations("costAudit"), getLocale()]);
  const custom = isDay(sp.from) && isDay(sp.to);
  const preset: AuditPreset | null = custom ? null : (AUDIT_PRESETS as readonly string[]).includes(sp.preset ?? "") ? (sp.preset as AuditPreset) : "lastQuarter";
  const { from, to } = custom ? { from: sp.from!, to: sp.to! } : presetRange(preset!);
  const here = custom ? `/cost-audit?from=${from}&to=${to}` : `/cost-audit?preset=${preset}`;

  const [audit, org] = await Promise.all([
    api.GET("/api/v1/reports/audit", { params: { query: { period_from: from, period_to: to } } }),
    api.GET("/api/v1/organization"),
  ]);

  const periodPicker = (
    <div className="flex flex-wrap items-end gap-3 mb-6 print:hidden">
      <nav aria-label={t("presets.label")} className="inline-flex flex-wrap rounded-lg border border-slate-200 bg-white p-0.5 text-sm shadow-card">
        {AUDIT_PRESETS.map((p) => (
          <Link key={p} href={`/cost-audit?preset=${p}`} aria-current={p === preset ? "page" : undefined} className={`px-3 py-1.5 rounded-md transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${p === preset ? "bg-slate-900 text-white font-medium" : "text-slate-600 hover:bg-slate-100"}`}>
            {t(`presets.${p}`)}
          </Link>
        ))}
      </nav>
      <form method="get" className="flex flex-wrap items-end gap-2">
        <div><label className={label} htmlFor="audit-from">{t("presets.from")}</label><input id="audit-from" name="from" type="date" defaultValue={from} className={input} /></div>
        <div><label className={label} htmlFor="audit-to">{t("presets.to")}</label><input id="audit-to" name="to" type="date" defaultValue={to} className={input} /></div>
        <button type="submit" className={btnSecondary}>{t("presets.apply")}</button>
      </form>
    </div>
  );

  if (audit.error || !audit.data) {
    return (
      <>
        <PageHeader title={t("title")} subtitle={t("subtitle")} />
        {periodPicker}
        <ApiErrorNotice error={audit.error} retryHref={here} />
      </>
    );
  }
  const a = audit.data;
  const label_ = t("shareLabel", { from: dateShort(from, locale), to: dateShort(to, locale) });

  return (
    <>
      <PageHeader
        title={t("title")}
        subtitle={t("subtitle")}
        actions={
          <div className="flex flex-wrap items-center gap-2 print:hidden">
            <a href={`/api/exports/audit-findings?period_from=${from}&period_to=${to}&locale=${locale}`} className={btnSecondary}><Download className="w-4 h-4 mr-1.5" aria-hidden />{t("export")}</a>
            <PrintButton label={t("print")} />
            <ShareDialog subject={{ kind: "audit", periodFrom: from, periodTo: to }} subjectLabel={label_} currency={a.base_currency} estimatedOnly={false} />
          </div>
        }
      />
      {periodPicker}
      <AuditPreparation periodFrom={from} periodTo={to} />
      <Panel title={t("assumed.title")} subtitle={a.assumed_coefficient === null ? t("assumed.subtitleMissing") : t("assumed.subtitle")} className="mb-6 print:hidden">
        <AssumedCoefficientForm current={a.assumed_coefficient ?? null} revalidate="/cost-audit" />
      </Panel>
      <AuditSheet
        audit={a}
        locale={locale === "en" ? "en" : "fr"}
        header={{ issuer: org.data?.name ?? "", generatedAt: new Date().toISOString(), sampleData: org.data?.has_sample_data ?? false, linkContainers: true }}
      />
    </>
  );
}

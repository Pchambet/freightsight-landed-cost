import type { Metadata } from "next";
import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";
import { Receipt } from "lucide-react";
import { Card, PageHeader, btnPrimary, td, tdRight, th, thRight } from "@/components/layout/AppShell";
import ConfidenceBadge from "@/components/shared/ConfidenceBadge";
import UploadForm from "@/features/invoices/UploadForm";
import WorkflowNextStep from "@/features/workflow/WorkflowNextStep";
import { invoicesHubStep } from "@/features/workflow/steps";
import { api, type InvoiceSummary } from "@/lib/api/client";
import type { InvoiceStatus } from "@/lib/domain";
import { dateShort, money } from "@/lib/format";
import { MAX_UPLOAD_BYTES, fileSize } from "@/lib/uploads";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("invoices");
  return { title: t("title") };
}

const STATUS_STYLE: Record<InvoiceStatus, string> = {
  UPLOADED: "bg-slate-100 text-slate-700",
  EXTRACTING: "bg-blue-100 text-blue-800",
  NEEDS_REVIEW: "bg-amber-100 text-amber-800",
  CONFIRMED: "bg-emerald-100 text-emerald-800",
  FAILED: "bg-red-100 text-red-800",
  REJECTED: "bg-slate-100 text-slate-600",
};

export default async function InvoicesPage({ searchParams }: { searchParams: Promise<{ filter?: string }> }) {
  const [{ filter }, t, statusLabel, locale] = await Promise.all([searchParams, getTranslations("invoices"), getTranslations("domain.invoiceStatus"), getLocale()]);
  const query = filter === "review" ? { invoice_status: "NEEDS_REVIEW" as const } : filter === "confirmed" ? { invoice_status: "CONFIRMED" as const } : {};
  const [res, reviewRes] = await Promise.all([
    api.GET("/api/v1/invoices", { params: { query } }),
    api.GET("/api/v1/invoices", { params: { query: { invoice_status: "NEEDS_REVIEW" } } }),
  ]);
  const invoices: InvoiceSummary[] = res.data ?? [];
  const reviewList = reviewRes.data ?? [];
  const review = reviewList.length;
  const hubStep = invoicesHubStep(review, reviewList[0]?.id);

  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle", { review, total: invoices.length })} />
      <WorkflowNextStep step={hubStep} />
      <Card title={t("upload.title")} right={<span className="text-xs text-slate-500 max-w-md text-right">{t("upload.hint", { max: fileSize(MAX_UPLOAD_BYTES, locale) })}</span>} className="mb-6">
        <div id="depot" className="scroll-mt-6">
          <UploadForm />
        </div>
      </Card>
      <div className="flex gap-1 text-sm mb-3">
        {(["all", "review", "confirmed"] as const).map((f) => (
          <Link key={f} href={f === "all" ? "/invoices" : `/invoices?filter=${f}`} className={`px-3 py-1.5 rounded-md ${(filter ?? "all") === f ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"}`}>{t(`filter.${f}`)}</Link>
        ))}
      </div>
      <Card>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50">
              <tr>
                <th className={th}>{t("th.vendor")}</th><th className={th}>{t("th.number")}</th><th className={th}>{t("th.date")}</th>
                <th className={thRight}>{t("th.total")}</th><th className={th}>{t("th.status")}</th><th className={thRight}>{t("th.confidence")}</th><th className={th}>{t("th.file")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200">
              {invoices.length === 0 ? (
                <tr><td colSpan={7} className="px-5 py-14 text-center text-slate-500">
                  <Receipt className="w-10 h-10 mx-auto mb-3 text-slate-300" aria-hidden />
                  <p className="text-sm text-slate-600 mb-4">{t("empty")}</p>
                  <Link href="#depot" className={btnPrimary}>{t("emptyAction")}</Link>
                </td></tr>
              ) : invoices.map((i) => (
                <tr key={i.id} className="hover:bg-slate-50">
                  <td className={td}><Link href={`/invoices/${i.id}`} className="font-medium text-blue-600 hover:underline">{i.vendor ?? "—"}</Link></td>
                  <td className={`${td} font-mono text-slate-600`}>{i.invoice_number ?? "—"}</td>
                  <td className={`${td} text-slate-600`}>{dateShort(i.invoice_date, locale)}</td>
                  <td className={tdRight}>{i.total_amount && i.currency ? money(i.total_amount, i.currency, locale) : "—"}</td>
                  <td className={td}><span className={`inline-flex px-2 py-0.5 rounded text-xs font-medium ${STATUS_STYLE[i.status]}`}>{statusLabel(i.status)}</span></td>
                  <td className={tdRight}><ConfidenceBadge value={i.confidence} band={(i as { confidence_band?: "HIGH" | "MEDIUM" | "LOW" }).confidence_band} confirmed={i.status === "CONFIRMED"} /></td>
                  <td className={`${td} text-xs text-slate-500 max-w-[16rem] truncate`}>{i.filename ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}

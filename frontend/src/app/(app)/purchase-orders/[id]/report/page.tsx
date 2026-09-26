import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getLocale, getTranslations } from "next-intl/server";
import Breadcrumb from "@/components/shared/Breadcrumb";
import LandedCostSheet from "@/features/report/LandedCostSheet";
import PrintButton from "@/features/report/PrintButton";
import ShareDialog from "@/features/report/ShareDialog";
import { api } from "@/lib/api/client";

type Params = { params: Promise<{ id: string }> };

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("sheet");
  return { title: t("actions.preview") };
}

/**
 * The sender's preview: the same sheet the recipient will open, fed with the live report. Printing
 * this page gives the document alone — the shell, the breadcrumb and the button stay on screen.
 */
export default async function PurchaseOrderReportPage({ params }: Params) {
  const { id } = await params;
  const [t, locale, po, report, org] = await Promise.all([
    getTranslations("sheet"),
    getLocale(),
    api.GET("/api/v1/purchase-orders/{po_id}", { params: { path: { po_id: id } } }),
    api.GET("/api/v1/landed-costs/purchase-orders/{po_id}", { params: { path: { po_id: id } } }),
    api.GET("/api/v1/organization"),
  ]);
  if (!po.data || !report.data) notFound();

  return (
    <>
      <div className="print:hidden flex flex-wrap items-center justify-between gap-3 mb-4">
        <Breadcrumb backHref={`/purchase-orders/${id}`} backLabel={po.data.po_number} />
        <div className="flex flex-wrap items-center gap-2">
          <PrintButton label={t("actions.print")} />
          <ShareDialog subject={{ kind: "purchase_order", id }} subjectLabel={po.data.po_number} currency={report.data.base_currency} estimatedOnly={report.data.costs.filter((c) => !c.closed_at).length > 0 && report.data.costs.filter((c) => !c.closed_at).every((c) => c.status === "ESTIMATE")} />
        </div>
      </div>
      <LandedCostSheet
        locale={locale === "en" ? "en" : "fr"}
        report={report.data}
        header={{
          issuer: org.data?.name ?? "",
          subjectType: "purchase_order",
          subjectLabel: po.data.po_number,
          shipmentReference: null,
          generatedAt: new Date().toISOString(),
          sampleData: org.data?.has_sample_data ?? false,
        }}
      />
    </>
  );
}

import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getLocale, getTranslations } from "next-intl/server";
import Link from "next/link";
import { AlertTriangle, FileText, Info } from "lucide-react";
import { PageHeader, btnSecondary, td, tdRight, th, thRight } from "@/components/layout/AppShell";
import Breadcrumb from "@/components/shared/Breadcrumb";
import MetricLabel from "@/components/shared/MetricLabel";
import CollapsibleSection from "@/components/shared/CollapsibleSection";
import { Money } from "@/components/shared/Money";
import ScrollRegion from "@/components/shared/ScrollRegion";
import AddCostForm from "@/features/costs/AddCostForm";
import AllocationPanel from "@/features/costs/AllocationPanel";
import CostBreakdown from "@/features/costs/CostBreakdown";
import EstimatePanel from "@/features/estimates/EstimatePanel";
import CostLedger from "@/features/costs/CostLedger";
import OpenOnHash from "@/components/shared/OpenOnHash";
import WorkflowNextStep from "@/features/workflow/WorkflowNextStep";
import { purchaseOrderDetailStep } from "@/features/workflow/steps";
import { api } from "@/lib/api/client";
import { dateShort, pct, qty } from "@/lib/format";

type Params = { params: Promise<{ id: string }> };

const API_ERROR = "api_error";

// `LandedCostReport.notes` — see the same type in the container page for why it is typed locally.
type ReportNote = { cost_id: string; code: string; message: string; load_ids: string[]; load_labels: string[] };

async function load(id: string) {
  const po = await api.GET("/api/v1/purchase-orders/{po_id}", { params: { path: { po_id: id } } });
  if (po.response.status === 404 || po.response.status === 422) return null;
  if (po.error || !po.data) throw new Error(API_ERROR);
  const [rep, summaries, containers] = await Promise.all([
    api.GET("/api/v1/landed-costs/purchase-orders/{po_id}", { params: { path: { po_id: id } } }),
    api.GET("/api/v1/purchase-orders"),
    api.GET("/api/v1/containers"),
  ]);
  if (rep.error || !rep.data) throw new Error(API_ERROR);
  const summary = summaries.data?.find((p) => p.id === id);
  // The container to send the reader to next: of those carrying this order, the one that already
  // has costs — a unit cost is what the link promises, and an empty container has none to show.
  const carrying = (summary?.container_numbers ?? [])
    .map((n) => containers.data?.find((c) => c.container_number === n))
    .filter((c) => c !== undefined);
  const firstContainer = carrying.find((c) => Number(c.allocated_base) > 0) ?? carrying[0];
  return {
    po: po.data,
    report: rep.data,
    firstContainer: firstContainer ? { id: firstContainer.id, number: firstContainer.container_number } : undefined,
  };
}

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const { id } = await params;
  const data = await load(id).catch(() => null);
  const t = await getTranslations("purchaseOrder");
  return { title: data ? data.po.po_number : t("fallbackTitle") };
}

export default async function PurchaseOrderPage({ params }: Params) {
  const { id } = await params;
  const data = await load(id);
  if (!data) notFound();
  const { po, report, firstContainer } = data;
  const [t, tc, errorCode, tg, ts, locale] = await Promise.all([
    getTranslations("purchaseOrder"),
    getTranslations("common"),
    getTranslations("errors"),
    getTranslations("glossary"),
    getTranslations("sheet"),
    getLocale(),
  ]);
  const cur = report.base_currency;
  const revalidate = `/purchase-orders/${po.id}`;
  const step = purchaseOrderDetailStep(report, firstContainer);

  return (
    <>
      {/* po.po_number is already the h1 right below — no need to repeat it in the breadcrumb. */}
      <Breadcrumb backHref="/purchase-orders" backLabel={t("breadcrumb")} />
      <PageHeader
        title={po.po_number}
        subtitle={<span>{po.supplier_name ?? t("noSupplier")} · {po.currency}{po.currency !== cur ? ` @ ${po.fx_rate} ${cur}` : ""}{po.order_date ? ` · ${t("ordered", { date: dateShort(po.order_date, locale) })}` : ""}{po.incoterm ? ` · ${po.incoterm}` : ""}</span>}
        actions={report.lines.length > 0 ? <Link href={`/purchase-orders/${po.id}/report`} className={btnSecondary}><FileText className="w-4 h-4 mr-1.5" aria-hidden />{ts("actions.open")}</Link> : null}
      />

      <WorkflowNextStep step={step} />

      {report.warnings.length > 0 ? (
        <div className="rounded-md border border-amber-200 bg-amber-50 text-amber-800 text-sm px-4 py-3 space-y-1 mb-6">
          {report.warnings.map((w) => (
            <div key={w.cost_id} className="flex gap-2">
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
              <span>
                {errorCode.has(w.code) ? errorCode(w.code) : w.message}
                {w.load_labels?.length ? <span className="text-amber-700/80"> {w.load_labels.join(", ")}</span> : null}
              </span>
            </div>
          ))}
        </div>
      ) : null}

      {/* `report.notes`: a rule applied to a cost that WAS allocated — kept apart from `warnings`,
          never carries an amount. See the container page for the same block. */}
      {(report as { notes?: ReportNote[] }).notes?.length ? (
        <div className="rounded-md border border-slate-200 bg-slate-50 text-slate-700 text-sm px-4 py-3 space-y-1 mb-6">
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-1">{t("notesTitle")}</div>
          {((report as { notes?: ReportNote[] }).notes ?? []).map((n) => (
            <div key={n.cost_id} className="flex gap-2">
              <Info className="w-4 h-4 mt-0.5 shrink-0 text-slate-400" />
              <span>
                {errorCode.has(n.code) ? errorCode(n.code) : n.message}
                {n.load_labels?.length ? <span className="text-slate-500"> {n.load_labels.join(", ")}</span> : null}
              </span>
            </div>
          ))}
        </div>
      ) : null}

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
        {(["fob", "allocated", "vat", "landed"] as const).map((k) => (
          <div key={k} className="bg-white rounded-lg border border-slate-200 px-4 py-3">
            <MetricLabel label={t(`totals.${k}`)} hint={tg(k)} />
            <div className="text-lg font-semibold tabular-nums mt-1"><Money amount={report.totals[k]} currency={cur} /></div>
          </div>
        ))}
      </div>

      <div className="space-y-6 mb-6">
        <CostBreakdown report={report} />
        <EstimatePanel report={report} currency={cur} />
        <section id="ventilation" className="scroll-mt-6">
          <AllocationPanel key={report.costs.map((c) => c.id).join(",")} poId={po.id} report={report} />
        </section>
      </div>

      <div className="space-y-3">
        <CollapsibleSection title={t("linesCard")} defaultOpen>
          <ScrollRegion label={t("linesCard")}>
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50"><tr><th className={th}>{t("th.lineNo")}</th><th className={th}>{t("th.sku")}</th><th className={thRight}>{t("th.quantity")}</th><th className={thRight}>{t("th.unitPrice")}</th><th className={thRight}>{t("th.weight")}</th><th className={thRight}>{t("th.volume")}</th><th className={thRight}>{t("th.duty")}</th></tr></thead>
              <tbody className="divide-y divide-slate-200">
                {po.lines.map((l) => (
                  <tr key={l.id}>
                    <td className={td}>{l.line_no}</td>
                    <td className={td}><div>{l.sku ?? tc("empty")}</div><div className="text-xs text-slate-500">{l.description}</div></td>
                    <td className={tdRight}>{qty(l.quantity, locale)}</td>
                    <td className={tdRight}>{qty(l.unit_price, locale)}</td>
                    <td className={`${tdRight} text-slate-500`}>{l.unit_weight_kg ? qty(l.unit_weight_kg, locale) : tc("empty")}</td>
                    <td className={`${tdRight} text-slate-500`}>{l.unit_volume_cbm ? qty(l.unit_volume_cbm, locale) : tc("empty")}</td>
                    <td className={`${tdRight} text-slate-500`}>{l.duty_rate ? pct(Number(l.duty_rate) * 100, locale) : tc("empty")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollRegion>
        </CollapsibleSection>

        <CollapsibleSection title={t("addCostCard")} hint={t("sections.addCostHint")}>
          <div className="p-5">
            <AddCostForm scope="PO" targetId={po.id} currency={cur} revalidate={revalidate} />
          </div>
        </CollapsibleSection>

        <CollapsibleSection id="journal" title={t("sections.ledger")} hint={t("sections.ledgerHint")}>
          <div className="p-5">
            <CostLedger report={report} currency={cur} revalidate={revalidate} />
          </div>
        </CollapsibleSection>
        {/* « #journal », « #chargements »: where a preparation item's fix is made, opened on arrival. */}
        <OpenOnHash />
      </div>
    </>
  );
}

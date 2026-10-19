import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getLocale, getTranslations } from "next-intl/server";
import Link from "next/link";
import { Box, FileText, Info, LockKeyhole } from "lucide-react";
import { PageHeader, btnSecondary } from "@/components/layout/AppShell";
import Breadcrumb from "@/components/shared/Breadcrumb";
import CollapsibleSection from "@/components/shared/CollapsibleSection";
import { MilestoneBadge, RiskBadge } from "@/components/shared/Badges";
import AllocationPanel from "@/features/costs/AllocationPanel";
import CostBreakdown from "@/features/costs/CostBreakdown";
import ReportTotals from "@/features/costs/ReportTotals";
import UnallocatedWarnings from "@/features/costs/UnallocatedWarnings";
import FobMissing from "@/features/report/FobMissing";
import UnitCostHighlight from "@/features/costs/UnitCostHighlight";
import CostLedger from "@/features/costs/CostLedger";
import OpenOnHash from "@/components/shared/OpenOnHash";
import AddCostForm from "@/features/costs/AddCostForm";
import LoadsEditor from "@/features/containers/LoadsEditor";
import TrackingPanel from "@/features/containers/TrackingPanel";
import TimelinePanel from "@/features/containers/TimelinePanel";
import AuditTrail from "@/features/audit/AuditTrail";
import EstimatePanel from "@/features/estimates/EstimatePanel";
import LandedCostPush from "@/features/erp/LandedCostPush";
import WorkflowNextStep from "@/features/workflow/WorkflowNextStep";
import { containerWorkflowStep } from "@/features/workflow/steps";
import { api, type EtaHistoryResponse, type LandedCostReport, type LoadResponse, type PurchaseOrderSummary, type Schemas, type TrackingEventResponse } from "@/lib/api/client";
import { demurrageRelevant } from "@/lib/domain";
import { periodTitle, signedMoney } from "@/features/periods/format";
import { dateShort, daysUntil, money } from "@/lib/format";

type Params = { params: Promise<{ id: string }> };

const API_ERROR = "api_error";
const TOTALS = ["fob", "allocated", "vat", "landed", "unallocated"] as const;

async function load(id: string): Promise<{ container: Schemas["ContainerDetail"]; report: LandedCostReport; loads: LoadResponse[]; pos: PurchaseOrderSummary[]; shipmentRef: string | null; events: TrackingEventResponse[]; etaHistory: EtaHistoryResponse[]; subscription: Schemas["TrackingSubscriptionResponse"] | null; erpConnected: boolean; invoicesToReview: number } | null> {
  const c = await api.GET("/api/v1/containers/{container_id}", { params: { path: { container_id: id } } });
  if (c.response.status === 404 || c.response.status === 422) return null;
  if (c.error || !c.data) throw new Error(API_ERROR);
  const [r, l, p, s, ev, eh, sub, erp, inv] = await Promise.all([
    api.GET("/api/v1/landed-costs/containers/{container_id}", { params: { path: { container_id: id } } }),
    api.GET("/api/v1/containers/{container_id}/loads", { params: { path: { container_id: id } } }),
    api.GET("/api/v1/purchase-orders"),
    c.data.shipment_id ? api.GET("/api/v1/shipments/{shipment_id}", { params: { path: { shipment_id: c.data.shipment_id } } }) : Promise.resolve(null),
    api.GET("/api/v1/containers/{container_id}/events", { params: { path: { container_id: id } } }),
    api.GET("/api/v1/containers/{container_id}/eta-history", { params: { path: { container_id: id } } }),
    api.GET("/api/v1/containers/{container_id}/tracking", { params: { path: { container_id: id } } }),
    api.GET("/api/v1/erp/connection"),
    api.GET("/api/v1/invoices", { params: { query: { invoice_status: "NEEDS_REVIEW" } } }),
  ]);
  if (r.error || !r.data || l.error || !l.data) throw new Error(API_ERROR);
  return {
    container: c.data,
    report: r.data,
    loads: l.data,
    pos: p.data ?? [],
    shipmentRef: s?.data?.reference ?? null,
    events: ev.data ?? [],
    etaHistory: eh.data ?? [],
    subscription: sub.response.status === 200 ? (sub.data ?? null) : null,
    erpConnected: erp.response.status === 200,
    invoicesToReview: inv.data?.length ?? 0,
  };
}

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const { id } = await params;
  const data = await load(id).catch(() => null);
  const t = await getTranslations("container");
  return { title: data ? data.container.container_number : t("fallbackTitle") };
}

export default async function ContainerPage({ params }: Params) {
  const { id: containerId } = await params;
  const data = await load(containerId);
  if (!data) notFound();
  const { container, report, loads, pos, shipmentRef, events, etaHistory, subscription, erpConnected, invoicesToReview } = data;
  const [t, errorCode, tp, ts, tm, locale] = await Promise.all([getTranslations("container"), getTranslations("errors"), getTranslations("periods.container"), getTranslations("sheet"), getTranslations("fobMissing"), getLocale()]);
  // The first order of this box that has no exchange rate: where the missing value is set.
  const unrated = report.lines.find((ln) => ln.fob_missing);
  const cur = report.base_currency;
  const revalidate = `/container/${container.id}`;
  const step = containerWorkflowStep(container, loads, report, invoicesToReview);

  return (
    <>
      {/* container.container_number is already the h1 right below — no need to repeat it. */}
      <Breadcrumb backHref="/containers" backLabel={t("breadcrumb")} />

      <PageHeader
        title={<span className="font-mono flex items-center gap-3"><Box className="w-6 h-6 text-blue-600" />{container.container_number}</span>}
        subtitle={
          <span className="flex items-center gap-3 flex-wrap">
            <MilestoneBadge milestone={container.milestone} />
            <RiskBadge risk={container.dnd_risk} days={demurrageRelevant(container) ? daysUntil(container.last_free_day) : null} />
            {container.last_free_day ? <span>{t("lastFreeDay", { date: dateShort(container.last_free_day, locale) })}</span> : null}
            {shipmentRef ? <span>{t("shipment", { reference: shipmentRef })}</span> : null}
          </span>
        }
        actions={report.lines.length > 0 ? <Link href={`/container/${container.id}/report`} className={btnSecondary}><FileText className="w-4 h-4 mr-1.5" aria-hidden />{ts("actions.open")}</Link> : null}
      />

      {/* A closed month keeps this container's landed cost as it went to the books: say so, and say
          what has moved since, before anyone edits a cost without knowing. */}
      {container.closed_period ? (
        <div className="rounded-xl border border-slate-200 bg-white shadow-card px-5 py-3.5 mb-6 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm print:hidden">
          <LockKeyhole className="w-4 h-4 shrink-0 text-slate-500" aria-hidden />
          <span className="text-slate-700">
            {tp("frozen", { month: periodTitle(container.closed_period, locale, true), frozen: money(container.frozen_landed ?? "0", cur, locale) })}{" "}
            {/* No drift can be said while one of its orders has no exchange rate: today's figure is unknown. */}
            {container.drift === null || container.drift === undefined ? (report.fob_missing ? <span className="text-amber-800">{tm("line")}</span> : <span className="text-slate-500">{tp("noDrift")}</span>) : Number(container.drift) === 0 ? <span className="text-slate-500">{tp("noDrift")}</span> : <span className="font-medium text-amber-800">{tp("drift", { drift: signedMoney(container.drift, (v) => money(v, cur, locale)) })}</span>}
          </span>
          <Link href={`/periods/${container.closed_period}`} className="ml-auto text-blue-700 hover:underline">{tp("see")}</Link>
        </div>
      ) : null}

      <WorkflowNextStep step={step} />

      <UnallocatedWarnings report={report} />

      {/* `report.notes`: a rule applied to a cost that WAS allocated (e.g. duty spread on customs
          value for lines with no tariff rate) — kept apart from `warnings` on purpose, since a note
          carries no amount and is never "non ventilé". */}
      {report.notes?.length ? (
        <div className="rounded-md border border-slate-200 bg-slate-50 text-slate-700 text-sm px-4 py-3 space-y-1 mb-6">
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-1">{t("notesTitle")}</div>
          {(report.notes ?? []).map((n) => (
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

      <FobMissing lines={report.fob_missing_lines} href={unrated ? `/purchase-orders/${unrated.po_id}#taux` : "/purchase-orders"} className="mb-6" />

      <ReportTotals report={report} keys={TOTALS} labels={Object.fromEntries(TOTALS.map((k) => [k, t(`totals.${k}`)])) as Record<(typeof TOTALS)[number], string>} />

      <div className="space-y-6 mb-6">
        <CostBreakdown report={report} />
        <UnitCostHighlight report={report} />
        <EstimatePanel report={report} currency={cur} containerId={container.id} />
        <section id="ventilation" className="scroll-mt-6">
          <AllocationPanel key={report.costs.map((c) => c.id).join(",")} containerId={container.id} report={report} />
        </section>
      </div>

      <div className="space-y-3">
        <CollapsibleSection id="suivi" title={t("sections.tracking")} hint={t("sections.trackingHint")}>
          <div className="p-5 space-y-6">
            <TrackingPanel container={container} subscription={subscription} />
            <TimelinePanel containerId={container.id} events={events} etaHistory={etaHistory} />
          </div>
        </CollapsibleSection>

        <CollapsibleSection id="chargements" title={t("sections.loads")} hint={t("sections.loadsHint")} defaultOpen={loads.length === 0}>
          <div className="p-5">
            <LoadsEditor containerId={container.id} loads={loads} purchaseOrders={pos} />
          </div>
        </CollapsibleSection>

        <CollapsibleSection id="ajouter-cout" title={t("sections.addCost")} hint={t("sections.addCostHint")}>
          <div className="p-5">
            <AddCostForm scope="CONTAINER" targetId={container.id} shipmentId={container.shipment_id} currency={cur} revalidate={revalidate} />
          </div>
        </CollapsibleSection>

        <CollapsibleSection id="journal" title={t("sections.ledger")} hint={t("sections.ledgerHint")}>
          <div className="p-5">
            <CostLedger report={report} currency={cur} revalidate={revalidate} />
          </div>
        </CollapsibleSection>
        {/* « #journal », « #chargements »: where a preparation item's fix is made, opened on arrival. */}
        <OpenOnHash />

        {erpConnected ? (
          <CollapsibleSection title={t("sections.erp")}>
            <div className="p-5">
              <LandedCostPush key={report.costs.map((c) => `${c.id}:${c.status ?? ""}:${c.amount_base ?? ""}`).join(",")} containerId={container.id} currency={cur} embedded />
            </div>
          </CollapsibleSection>
        ) : null}

        <CollapsibleSection title={t("sections.audit")}>
          <div className="p-5">
            <AuditTrail entityType="container" entityId={container.id} />
          </div>
        </CollapsibleSection>
      </div>
    </>
  );
}

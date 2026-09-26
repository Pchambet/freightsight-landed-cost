import type { Metadata } from "next";
import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";
import { Check, ExternalLink, RotateCcw } from "lucide-react";
import { PageHeader, btnPrimary, btnSecondary } from "@/components/layout/AppShell";
import ApiErrorNotice from "@/components/shared/ApiErrorNotice";
import Panel from "@/components/ui/Panel";
import ReviewPanel, { type Targets } from "@/features/invoices/ReviewPanel";
import LandedCostSheet from "@/features/report/LandedCostSheet";
import PrintButton from "@/features/report/PrintButton";
import ShareDialog from "@/features/report/ShareDialog";
import ContainerStep from "@/features/start/ContainerStep";
import { InvoiceDrop, ManualCosts, ReadingWatch } from "@/features/start/CostsStep";
import OrderStep from "@/features/start/OrderStep";
import { api } from "@/lib/api/client";

type Search = { po?: string; container?: string; invoice?: string; step?: string };

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("start");
  return { title: t("title") };
}

const UUID = /^[0-9a-f-]{36}$/i;
const parisToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Paris", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());

/**
 * The guided first file: an order, its container, the charges, the landed cost — one screen that
 * advances, its whole state in the URL. Every step writes real data through the ordinary actions,
 * so leaving halfway leaves nothing to lose and a reload resumes where it stopped.
 */
export default async function StartPage({ searchParams }: { searchParams: Promise<Search> }) {
  const [sp, t, locale] = await Promise.all([searchParams, getTranslations("start"), getLocale()]);
  const poId = UUID.test(sp.po ?? "") ? sp.po! : null;
  const containerId = poId && UUID.test(sp.container ?? "") ? sp.container! : null;
  const invoiceId = containerId && UUID.test(sp.invoice ?? "") ? sp.invoice! : null;
  const stepNo = !poId ? 1 : !containerId ? 2 : sp.step === "result" ? 4 : 3;
  const base = containerId ? `/start?po=${poId}&container=${containerId}` : poId ? `/start?po=${poId}` : "/start";

  const steps = ["order", "container", "costs", "result"] as const;
  const stepper = (
    <ol className="flex flex-wrap gap-x-6 gap-y-2 text-sm mb-6" aria-label={t("steps")}>
      {steps.map((s, i) => {
        const n = i + 1;
        const state = n < stepNo ? "done" : n === stepNo ? "current" : "todo";
        const href = state === "done" ? (n === 1 ? "/start" : n === 2 ? `/start?po=${poId}` : base) : null;
        const body = (
          <>
            <span className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-semibold ${state === "done" ? "bg-emerald-600 text-white" : state === "current" ? "bg-slate-900 text-white" : "bg-slate-200 text-slate-600"}`} aria-hidden>
              {state === "done" ? <Check className="w-3.5 h-3.5" /> : n}
            </span>
            <span className={state === "current" ? "font-medium text-slate-900" : "text-slate-600"}>{t(`step.${s}`)}</span>
          </>
        );
        return (
          <li key={s} aria-current={state === "current" ? "step" : undefined} className="flex items-center gap-2">
            {href ? <Link href={href} className="flex items-center gap-2 hover:underline">{body}</Link> : body}
          </li>
        );
      })}
    </ol>
  );

  // Step 1 — the order.
  if (stepNo === 1) {
    const pos = await api.GET("/api/v1/purchase-orders");
    return (
      <>
        <PageHeader title={t("title")} subtitle={t("subtitle")} />
        {stepper}
        <Panel title={t("order.title")} subtitle={t("order.subtitle")}>
          <OrderStep existing={(pos.data ?? []).map((p) => ({ id: p.id, label: p.supplier_name ? `${p.po_number} · ${p.supplier_name}` : p.po_number }))} />
        </Panel>
      </>
    );
  }

  const po = await api.GET("/api/v1/purchase-orders/{po_id}", { params: { path: { po_id: poId! } } });
  if (po.error || !po.data) {
    return (
      <>
        <PageHeader title={t("title")} />
        <ApiErrorNotice error={po.error} retryHref={base} />
      </>
    );
  }
  const poLabel = po.data.supplier_name ? `${po.data.po_number} · ${po.data.supplier_name}` : po.data.po_number;

  // Step 2 — the container.
  if (stepNo === 2) {
    return (
      <>
        <PageHeader title={t("title")} subtitle={t("forOrder", { order: poLabel })} />
        {stepper}
        <Panel title={t("container.title")} subtitle={t("container.subtitle", { lines: po.data.lines.length })}>
          <ContainerStep poId={poId!} today={parisToday()} />
        </Panel>
      </>
    );
  }

  const [container, report, org, status] = await Promise.all([
    api.GET("/api/v1/containers/{container_id}", { params: { path: { container_id: containerId! } } }),
    api.GET("/api/v1/landed-costs/containers/{container_id}", { params: { path: { container_id: containerId! } } }),
    api.GET("/api/v1/organization"),
    api.GET("/api/v1/service-status"),
  ]);
  if (container.error || !container.data || report.error || !report.data) {
    return (
      <>
        <PageHeader title={t("title")} />
        <ApiErrorNotice error={container.error ?? report.error} retryHref={base} />
      </>
    );
  }
  const cur = report.data.base_currency;
  const arrivedOn = (container.data.discharged_at ?? container.data.ata ?? "").slice(0, 10) || parisToday();
  const hasCosts = report.data.costs.some((c) => !c.closed_at);
  const workerAlive = (status.data?.checks ?? []).find((c) => c.key === "background_jobs")?.state !== "degraded";

  // Step 3 — the charges.
  if (stepNo === 3) {
    const invoice = invoiceId ? await api.GET("/api/v1/invoices/{invoice_id}", { params: { path: { invoice_id: invoiceId } } }) : null;
    const inv = invoice?.data ?? null;
    const reading = inv ? inv.status === "UPLOADED" || inv.status === "EXTRACTING" : false;
    const targets: Targets = {
      SHIPMENT: [],
      CONTAINER: [{ id: container.data.id, label: container.data.container_number }],
      PO: [{ id: po.data.id, label: po.data.po_number }],
      PO_LINE: po.data.lines.map((l) => ({ id: l.id, label: `${po.data.po_number} #${l.line_no}${l.sku ? ` · ${l.sku}` : ""}` })),
    };
    return (
      <>
        <PageHeader title={t("title")} subtitle={t("forContainer", { order: poLabel, container: container.data.container_number })} />
        {stepper}
        <div className="space-y-6">
          {inv ? (
            <Panel title={t("costs.invoiceTitle", { name: inv.filename ?? inv.invoice_number ?? "" })} subtitle={reading ? undefined : t("costs.reviewSubtitle")} className={reading ? "" : "[&>div:last-child]:p-0"}>
              {reading ? <ReadingWatch workerAlive={workerAlive} /> : inv.status === "FAILED" ? <p className="text-sm text-amber-800">{t("costs.readingFailed")}</p> : <ReviewPanel invoice={inv} targets={targets} />}
            </Panel>
          ) : (
            <Panel title={t("costs.title")} subtitle={t("costs.subtitle")}>
              <InvoiceDrop base={base} containerId={container.data.id} />
            </Panel>
          )}
          <Panel title={t("costs.manualTitle")} subtitle={t("costs.manualSubtitle")}>
            <ManualCosts base={base} containerId={container.data.id} costDate={arrivedOn} baseCurrency={cur} />
          </Panel>
          {hasCosts ? (
            <div className="flex flex-wrap items-center gap-3">
              <Link href={`${base}&step=result`} className={btnPrimary}>{t("costs.seeResult")}</Link>
              <span className="text-sm text-slate-500">{t("costs.seeResultHint")}</span>
            </div>
          ) : null}
        </div>
      </>
    );
  }

  // Step 4 — the landed cost.
  return (
    <>
      <PageHeader
        title={t("title")}
        subtitle={t("resultSubtitle")}
        actions={
          <div className="flex flex-wrap items-center gap-2 print:hidden">
            <PrintButton label={t("result.print")} />
            <ShareDialog subject={{ kind: "container", id: container.data.id }} subjectLabel={container.data.container_number} currency={cur} estimatedOnly={hasCosts && report.data.costs.filter((c) => !c.closed_at).every((c) => c.status === "ESTIMATE")} />
          </div>
        }
      />
      <div className="print:hidden">{stepper}</div>
      <LandedCostSheet
        locale={locale === "en" ? "en" : "fr"}
        report={report.data}
        header={{ issuer: org.data?.name ?? "", subjectType: "container", subjectLabel: container.data.container_number, shipmentReference: null, generatedAt: new Date().toISOString(), sampleData: org.data?.has_sample_data ?? false }}
      />
      <div className="mt-6 flex flex-wrap items-center gap-3 print:hidden">
        <Link href={`/container/${container.data.id}`} className={btnSecondary}><ExternalLink className="w-4 h-4 mr-1.5" aria-hidden />{t("result.openContainer")}</Link>
        <Link href={base} className={btnSecondary}>{t("result.moreCosts")}</Link>
        <Link href="/start" className="inline-flex items-center gap-1.5 text-sm text-blue-700 hover:underline"><RotateCcw className="w-4 h-4" aria-hidden />{t("result.again")}</Link>
      </div>
    </>
  );
}

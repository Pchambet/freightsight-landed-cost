import { getTranslations } from "next-intl/server";
import BreakdownBar from "@/components/charts/BreakdownBar";
import { COST_FAMILIES, familyTotal, foldFamilies, type CostFamily } from "@/components/charts/families";
import ScrollRegion from "@/components/shared/ScrollRegion";
import type { LandedCostReport } from "@/lib/api/client";
import { dateShort, money, qty, rate } from "@/lib/format";

export type SheetHeader = {
  issuer: string;
  subjectType: "container" | "purchase_order";
  subjectLabel: string;
  shipmentReference?: string | null;
  generatedAt: string;
  expiresAt?: string | null;
  sampleData?: boolean;
  /** The sender chose to leave vendors and invoice numbers out: the snapshot does not hold them. */
  redacted?: boolean;
};

/**
 * The landed cost of one container or one order, as a document that leaves the application: sent to
 * a buyer, an accountant, a prospect — people with no account. It is read in the order of their
 * questions (the figure, where it comes from, per SKU, which costs, what is still unknown), fits an
 * A4 page or two, and says of every figure whether it is invoiced or still an estimate.
 *
 * One component for the public page and for the sender's preview: what is checked before sending is
 * what is received. The language is the document's (`locale`), never the visitor's cookie.
 */
export default async function LandedCostSheet({ header, report, locale }: { header: SheetHeader; report: LandedCostReport; locale: "fr" | "en" }) {
  const [t, tf, tt, tm] = await Promise.all([
    getTranslations({ locale, namespace: "sheet" }),
    getTranslations({ locale, namespace: "costFamily" }),
    getTranslations({ locale, namespace: "domain.costType" }),
    getTranslations({ locale, namespace: "domain.method" }),
  ]);
  const cur = report.base_currency;
  const fob = Number(report.totals.fob);
  const landed = Number(report.totals.landed);
  const families = foldFamilies(report.by_cost_type);
  const fees = familyTotal(families);
  const ratio = (v: number) => new Intl.NumberFormat(locale === "fr" ? "fr-FR" : "en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v);
  const familyLabels = Object.fromEntries(COST_FAMILIES.map((f) => [f, tf(f)])) as Record<CostFamily, string>;
  // A superseded estimate is history, not part of this cost: only what counts today is listed.
  const costs = report.costs.filter((c) => !c.closed_at);
  const kept = costs.filter((c) => c.cost_type !== "IMPORT_VAT");
  const estimated = Number(report.totals.estimated ?? 0);
  const vat = Number(report.totals.vat ?? 0);
  const unallocated = Number(report.totals.unallocated ?? 0);
  const label = "text-[10px] font-medium uppercase tracking-wider text-slate-500";
  const thc = "px-3 py-2 text-left text-[10px] font-medium uppercase tracking-wider text-slate-500";
  const tdc = "px-3 py-2 align-top";

  return (
    <article className="mx-auto max-w-[52rem] bg-white text-slate-900 rounded-xl border border-slate-200 shadow-card print:shadow-none print:border-0 print:rounded-none print:max-w-none [print-color-adjust:exact] [-webkit-print-color-adjust:exact]">
      <header className="px-8 pt-8 pb-6 border-b border-slate-200 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className={label}>{t("title")}</div>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">
            {t(header.subjectType === "container" ? "container" : "order")} <span className="font-mono">{header.subjectLabel}</span>
          </h1>
          {header.shipmentReference ? <div className="mt-1 text-sm text-slate-600">{t("shipment", { reference: header.shipmentReference })}</div> : null}
        </div>
        <div className="text-right text-sm">
          <div className="font-semibold">{header.issuer}</div>
          <div className="text-slate-600">{t("computedOn", { date: dateShort(header.generatedAt, locale) })}</div>
          {header.expiresAt ? <div className="text-xs text-slate-500">{t("validUntil", { date: dateShort(header.expiresAt, locale) })}</div> : null}
        </div>
      </header>

      {header.sampleData ? <p className="mx-8 mt-5 rounded-md border border-violet-200 bg-violet-50 px-3 py-2 text-xs text-violet-950">{t("sample")}</p> : null}

      <section className="px-8 py-6 grid grid-cols-2 sm:grid-cols-4 gap-5 print:break-inside-avoid">
        <div className="col-span-2 sm:col-span-1">
          <div className={label}>{t("landed")}</div>
          <div className="mt-1 text-2xl font-semibold figure-proportional">{money(report.totals.landed, cur, locale)}</div>
        </div>
        <div>
          <div className={label}>{t("fob")}</div>
          <div className="mt-1 text-lg font-semibold text-slate-700 figure-proportional">{money(report.totals.fob, cur, locale)}</div>
        </div>
        <div>
          <div className={label}>{t("fees")}</div>
          <div className="mt-1 text-lg font-semibold text-slate-700 figure-proportional">{money(fees, cur, locale)}</div>
        </div>
        <div>
          <div className={label}>{t("coef")}</div>
          <div className="mt-1 text-lg font-semibold text-slate-700 figure-proportional">{fob > 0 ? ratio(landed / fob) : "—"}</div>
        </div>
      </section>

      {fob > 0 && fees > 0 ? (
        <section className="px-8 pb-6 print:break-inside-avoid">
          <h2 className="text-sm font-semibold mb-3">{t("breakdown")}</h2>
          <BreakdownBar fob={fob} families={families} currency={cur} locale={locale} fobLabel={t("fob")} familyLabels={familyLabels} ofFobLabel={t("ofFob")} />
        </section>
      ) : null}

      <section className="px-8 pb-6">
        <h2 className="text-sm font-semibold mb-2">{t("perSku")}</h2>
        <ScrollRegion label={t("perSku")} className="rounded-lg border border-slate-200">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-50">
              <tr>
                <th className={thc}>{t("th.order")}</th>
                <th className={thc}>{t("th.sku")}</th>
                <th className={`${thc} text-right`}>{t("th.quantity")}</th>
                <th className={`${thc} text-right`}>{t("th.unitFob")}</th>
                <th className={`${thc} text-right`}>{t("th.unitLanded")}</th>
                <th className={`${thc} text-right`}>{t("th.lineLanded")}</th>
                <th className={`${thc} text-right`}>{t("th.coef")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {report.lines.map((l) => {
                const q = Number(l.quantity);
                return (
                  <tr key={l.load_id} className="print:break-inside-avoid">
                    <td className={`${tdc} whitespace-nowrap`}>{l.po_number} <span className="text-slate-500">#{l.line_no}</span>{header.subjectType === "purchase_order" ? <div className="text-xs font-mono text-slate-500">{l.container_number}</div> : null}</td>
                    <td className={tdc}><span className="font-mono">{l.sku ?? "—"}</span>{l.description ? <div className="text-xs text-slate-500">{l.description}</div> : null}</td>
                    <td className={`${tdc} text-right tabular-nums`}>{qty(l.quantity, locale)}</td>
                    <td className={`${tdc} text-right tabular-nums text-slate-600`}>{q > 0 ? money(Number(l.fob) / q, cur, locale) : "—"}</td>
                    <td className={`${tdc} text-right tabular-nums font-semibold`}>{money(l.unit_landed_cost, cur, locale)}</td>
                    <td className={`${tdc} text-right tabular-nums text-slate-600`}>{money(l.landed, cur, locale)}</td>
                    <td className={`${tdc} text-right tabular-nums text-slate-600`}>{Number(l.fob) > 0 ? ratio(Number(l.landed) / Number(l.fob)) : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </ScrollRegion>
      </section>

      <section className="px-8 pb-6">
        <h2 className="text-sm font-semibold mb-2">{t("costs")}</h2>
        {kept.length === 0 ? (
          <p className="text-sm text-slate-600">{t("noCosts")}</p>
        ) : (
          <ScrollRegion label={t("costs")} className="rounded-lg border border-slate-200">
            <table className="min-w-full text-sm">
              <thead className="bg-slate-50">
                <tr>
                  <th className={thc}>{t("th.cost")}</th>
                  {header.redacted ? null : <th className={thc}>{t("th.source")}</th>}
                  <th className={thc}>{t("th.method")}</th>
                  <th className={`${thc} text-right`}>{t("th.amount")}</th>
                  <th className={`${thc} text-right`}>{t("th.amountBase", { currency: cur })}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {kept.map((c) => (
                  <tr key={c.id} className="print:break-inside-avoid">
                    <td className={tdc}>
                      {tt.has(c.cost_type) ? tt(c.cost_type) : c.cost_type}
                      {c.status === "ESTIMATE" ? <span className="ml-2 rounded bg-violet-50 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-violet-800 ring-1 ring-inset ring-violet-600/20">{t("estimate")}</span> : null}
                    </td>
                    {header.redacted ? null : <td className={`${tdc} text-slate-600`}>{[c.vendor, c.invoice_number].filter(Boolean).join(" · ") || "—"}<div className="text-xs text-slate-500">{dateShort(c.cost_date, locale)}</div></td>}
                    <td className={`${tdc} text-slate-600`}>{c.allocation_method && tm.has(c.allocation_method) ? tm(c.allocation_method) : "—"}</td>
                    <td className={`${tdc} text-right tabular-nums text-slate-600 whitespace-nowrap`}>{money(c.amount, c.currency, locale)}{c.currency !== cur && c.fx_rate ? <div className="text-xs text-slate-500">{t("fx", { rate: rate(c.fx_rate, locale), date: dateShort(c.fx_date, locale) })}</div> : null}</td>
                    <td className={`${tdc} text-right tabular-nums font-medium whitespace-nowrap`}>{money(c.amount_base, cur, locale)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollRegion>
        )}
      </section>

      <section className="px-8 pb-6 print:break-inside-avoid">
        <h2 className="text-sm font-semibold mb-2">{t("caveats")}</h2>
        <ul className="list-disc pl-5 space-y-1 text-sm text-slate-700">
          <li>{estimated > 0 ? t("caveat.estimated", { amount: money(estimated, cur, locale) }) : t("caveat.allInvoiced")}</li>
          {unallocated > 0 ? <li className="text-amber-800">{t("caveat.unallocated", { amount: money(unallocated, cur, locale) })}</li> : null}
          <li>{vat > 0 ? t("caveat.vat", { amount: money(vat, cur, locale) }) : t("caveat.vatNone")}</li>
          <li>{t("caveat.fx")}</li>
        </ul>
      </section>

      <footer className="px-8 py-4 border-t border-slate-200 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
        <span>{t("footer")}</span>
        <span className="font-medium text-slate-700">FreightSight</span>
      </footer>
    </article>
  );
}

import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";
import { CheckCircle2 } from "lucide-react";
import CollapsibleSection from "@/components/shared/CollapsibleSection";
import { skuHref } from "@/features/skus/href";
import { api, type Schemas } from "@/lib/api/client";
import { dateShort, money } from "@/lib/format";

type Item = Schemas["PreparationItem"];
type Example = Schemas["PreparationExample"];

/** « 0,00 € concernés » says nothing: an amount is shown when there is money at stake. Read as text, never as a float. */
const isZero = (amount: string) => /^-?0*(\.0*)?$/.test(amount);

const EXAMPLE_HREF: Record<Example["kind"], (id: string) => string> = {
  container: (id) => `/container/${id}`,
  invoice: (id) => `/invoices/${id}`,
  purchase_order: (id) => `/purchase-orders/${id}`,
  sku: (id) => skuHref(id),
  import: (id) => `/imports#import-${id}`,
};

/**
 * What fixes a cause, as a link: the file to import, the page where the fix is made. Where the fix is
 * on one object — a container's type, its duty method — the first example is where it leads.
 */
function remedy(item: Item): { key: string; href: string } | null {
  const first = item.examples?.[0];
  const onFirst = (fallback: string) => (first ? EXAMPLE_HREF[first.kind](first.id) : fallback);
  switch (item.code) {
    case "CONTAINERS_WITHOUT_DATE":
    case "CONTAINERS_WITHOUT_ROUTE":
    case "CLOCKS_RUNNING":
      return { key: "importContainers", href: "/imports/new?kind=CONTAINERS" };
    case "CONTAINERS_WITHOUT_GOODS":
      return { key: "importOrders", href: "/imports/new?kind=PURCHASE_ORDERS" };
    case "CONTAINERS_WITHOUT_COST":
    case "CONTAINERS_WITHOUT_FREIGHT":
      return { key: "dropInvoices", href: "/invoices#depot" };
    case "CONTAINERS_WITHOUT_DUTY":
      return { key: "importCosts", href: "/imports/new?kind=COSTS" };
    case "INVOICES_NOT_READ":
      return { key: "seeInvoices", href: "/invoices" };
    case "INVOICES_TO_REVIEW":
      return { key: "reviewFirst", href: first ? `/invoices/${first.id}` : "/invoices?filter=review" };
    case "COSTS_UNALLOCATED":
      // The duty rate comes with the order lines: a price list imported after them lends it to none.
      if (item.params?.reason === "MISSING_DUTY_RATE") return { key: "reimportOrdersWithRates", href: "/imports/new?kind=PURCHASE_ORDERS" };
      if (item.params?.reason === "NO_TARGET") return { key: "loadContainer", href: `${onFirst("/containers")}#chargements` };
      return { key: "openContainer", href: onFirst("/containers") };
    case "LINES_WITHOUT_DUTY_RATE":
      return { key: "reimportOrdersWithRates", href: "/imports/new?kind=PURCHASE_ORDERS" };
    case "SAME_INVOICE_TWICE":
    case "DUTY_GAP":
      return { key: "openContainer", href: onFirst("/containers") };
    case "DUTY_SPREAD_ON_CIF":
      return { key: "setTheoreticalDuty", href: onFirst("/containers") };
    case "CONTAINERS_WITHOUT_SIZE":
      return { key: "setType", href: onFirst("/containers") };
    case "COSTS_TYPED_OTHER":
      return { key: "seeCosts", href: `${onFirst("/containers")}#journal` };
    case "CREDIT_NOTES_SKIPPED":
    case "COST_ROWS_REFUSED":
      return { key: "openImport", href: onFirst("/imports") };
    case "CREDIT_NOTE_REVERSES_COST":
      return { key: "deleteReversedCost", href: `${onFirst("/containers")}#journal` };
    case "PO_SPLIT":
      return { key: "openOrder", href: onFirst("/purchase-orders") };
    case "NO_SALE_PRICE":
      return { key: "importPrices", href: "/imports/new?kind=PRODUCTS" };
    case "NO_ASSUMED_COEFFICIENT":
      return { key: "setCoefficient", href: "/cost-audit#assumed-coefficient" };
    default:
      return null;
  }
}

/**
 * What is still missing for a period's audit to be right — the working list, never part of the
 * document that leaves (which states only what it could not measure). What would make the audit wrong
 * comes first, then what it will not be able to say; each line says how many, how much money when
 * that means something, a few of the objects concerned, and what fixes it.
 */
export default async function AuditPreparation({ periodFrom, periodTo, showPeriod = false }: { periodFrom: string; periodTo: string; showPeriod?: boolean }) {
  const [res, t, terr, locale] = await Promise.all([
    api.GET("/api/v1/reports/audit/preparation", { params: { query: { period_from: periodFrom, period_to: periodTo } } }),
    getTranslations("preparation"),
    getTranslations("errors"),
    getLocale(),
  ]);
  if (res.error || !res.data) return null; // the audit page says what failed; this list is an aid, not the result
  const { items, base_currency } = res.data;
  const blocking = items.filter((i) => i.severity === "blocking");
  const limits = items.filter((i) => i.severity === "limits");

  const sentence = (i: Item) => {
    const reason = i.params?.reason && terr.has(i.params.reason) ? terr(i.params.reason) : (i.params?.reason ?? "");
    return t(`code.${i.code}`, { count: i.count, reason, pct: i.params?.pct ?? "" });
  };
  const row = (i: Item) => {
    const fix = remedy(i);
    // The sentence already says how many; these are a few of them to open (the count does not always
    // count the same objects: unallocated costs are shown by the containers that carry them).
    const shown = (i.examples ?? []).slice(0, 3);
    return (
      <li key={`${i.code}-${i.params?.reason ?? ""}`} className="py-3 first:pt-0 last:pb-0">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <p className="text-sm text-slate-800">
            {sentence(i)}
            <span className="ml-2 text-xs text-slate-500">{t(`section.${i.section}`)}</span>
          </p>
          {i.amount_base && !isZero(i.amount_base) ? <span className="text-sm font-medium tabular-nums text-slate-900">{t("amount", { amount: money(i.amount_base, base_currency, locale) })}</span> : null}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          {shown.map((e) => (
            <Link key={e.id} href={EXAMPLE_HREF[e.kind](e.id)} className="font-mono text-blue-700 underline underline-offset-2 hover:no-underline">{e.label}</Link>
          ))}
          {fix ? <Link href={fix.href} className="font-medium text-blue-700 underline underline-offset-2 hover:no-underline">{t(`remedy.${fix.key}`)} →</Link> : null}
        </div>
      </li>
    );
  };

  const period = showPeriod ? `${t("period", { from: dateShort(periodFrom, locale), to: dateShort(periodTo, locale) })} · ` : "";
  return (
    // Open while something would make the audit wrong; folded once only its limits remain.
    <div className="mb-6 print:hidden">
      <CollapsibleSection id="preparation" title={t("title")} hint={`${period}${t("summary", { blocking: blocking.length, limits: limits.length })}`} defaultOpen={blocking.length > 0}>
        <div className="px-5 py-4 space-y-5">
          <p className="text-xs text-slate-500">
            {t("subtitle")}
            {showPeriod ? (
              <>
                {" "}
                <Link href="/cost-audit" className="text-blue-700 underline underline-offset-2 hover:no-underline">{t("otherPeriod")}</Link>
              </>
            ) : null}
          </p>
          {items.length === 0 ? (
            <p className="flex items-center gap-2 text-sm text-emerald-800"><CheckCircle2 className="w-4 h-4" aria-hidden />{t("clear")}</p>
          ) : null}
          {blocking.length ? (
            <div>
              <h2 className="text-xs font-semibold uppercase tracking-wider text-red-700">{t("blocking")}</h2>
              <ul className="mt-2 divide-y divide-slate-100">{blocking.map(row)}</ul>
            </div>
          ) : null}
          {limits.length ? (
            <div>
              <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-600">{t("limits")}</h2>
              <ul className="mt-2 divide-y divide-slate-100">{limits.map(row)}</ul>
            </div>
          ) : null}
        </div>
      </CollapsibleSection>
    </div>
  );
}

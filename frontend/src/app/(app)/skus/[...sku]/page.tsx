import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getLocale, getTranslations } from "next-intl/server";
import { PageHeader } from "@/components/layout/AppShell";
import UnitCostTrend, { type TrendPoint } from "@/components/charts/UnitCostTrend";
import { COST_FAMILIES, type CostFamily } from "@/components/charts/families";
import ApiErrorNotice from "@/components/shared/ApiErrorNotice";
import Breadcrumb from "@/components/shared/Breadcrumb";
import Panel from "@/components/ui/Panel";
import StatTile from "@/components/ui/StatTile";
import ChangeBadge from "@/features/skus/ChangeBadge";
import CostShift from "@/features/skus/CostShift";
import MarginHelper from "@/features/skus/MarginHelper";
import ProductForm from "@/features/skus/ProductForm";
import { skuFromSegments, skuHref } from "@/features/skus/href";
import { api } from "@/lib/api/client";
import { dateShort, decimalDisplay, money, pct, qty } from "@/lib/format";

type Params = { params: Promise<{ sku: string[] }> };

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  return { title: skuFromSegments((await params).sku) };
}

/** "0.045" (the stored fraction) → "4,5" (the percentage a person types), without a float in between. */
function fractionToPercent(raw: string | null | undefined, locale: string): string {
  if (raw === null || raw === undefined || raw === "") return "";
  const [int, frac = ""] = raw.split(".");
  const digits = `${int}${frac.padEnd(2, "0")}`;
  const cut = int.length + 2;
  const out = `${digits.slice(0, cut).replace(/^0+(?=\d)/, "")}.${digits.slice(cut)}`.replace(/\.?0*$/, "");
  return decimalDisplay(out === "" ? "0" : out, locale);
}

/** "24.9000" → "24.90", "17.5000" → "17.5" : the API's storage precision is not something to make a person read. */
function trimZeros(raw: string | null | undefined, keep: number): string | null {
  if (raw === null || raw === undefined || !raw.includes(".")) return raw ?? null;
  const [int, frac] = raw.split(".");
  const cut = frac.replace(/0+$/, "").padEnd(keep, "0");
  return cut ? `${int}.${cut}` : int;
}

/** "12/06" in French, "12 Jun" in English: two arrivals of the same month must not share a label. */
function dayMonth(isoDate: string, locale: string): string {
  return new Intl.DateTimeFormat(locale === "fr" ? "fr-FR" : "en-GB", locale === "fr" ? { day: "2-digit", month: "2-digit", timeZone: "UTC" } : { day: "2-digit", month: "short", timeZone: "UTC" }).format(new Date(isoDate));
}

export default async function SkuPage({ params }: Params) {
  const sku = skuFromSegments((await params).sku);
  const [t, tf, locale, detail, sheets] = await Promise.all([
    getTranslations("skus"),
    getTranslations("costFamily"),
    getLocale(),
    api.GET("/api/v1/reports/skus/detail", { params: { query: { sku } } }),
    api.GET("/api/v1/products", { params: { query: { q: sku, limit: 20 } } }),
  ]);
  if (detail.response.status === 404) notFound();
  if (detail.error || !detail.data) {
    return (
      <>
        <Breadcrumb backHref="/skus" backLabel={t("title")} />
        <PageHeader title={<span className="font-mono">{sku}</span>} />
        <ApiErrorNotice error={detail.error} retryHref={skuHref(sku)} />
      </>
    );
  }
  const { summary: s, arrivals, base_currency: currency } = detail.data;
  // `containers` reached the API a commit after this page: an API one deploy behind answers without it.
  const boxes = detail.data.containers ?? [];
  const sheet = sheets.data?.products.find((p) => p.sku === sku) ?? null;
  const familyLabels = Object.fromEntries(COST_FAMILIES.map((f) => [f, tf(f)])) as Record<CostFamily, string>;
  const ratio = (v: number) => new Intl.NumberFormat(locale === "fr" ? "fr-FR" : "en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v);

  // One point per CONTAINER, not per order line: a SKU on two lines of the same container (a
  // restock and a back-order) is one arrival. These are the API's own container figures — the two
  // last landed ones are exactly what `change_pct` compares.
  const dated = boxes.filter((b) => b.arrived_on !== null);
  const points: TrendPoint[] = dated.map((b) => ({
    key: b.container_id,
    label: dayMonth(b.arrived_on!, locale),
    title: `${b.container_number} · ${dateShort(b.arrived_on, locale)}`,
    fob: Number(b.unit_fob),
    landed: Number(b.unit_landed),
    estimated: b.arrival_is_estimate || Number(b.estimated) > 0,
    note: [t("trend.qty", { qty: qty(b.quantity, locale) }), b.arrival_is_estimate ? t("trend.eta") : null].filter(Boolean).join(" · "),
    href: `/container/${b.container_id}#couts-unitaires`,
  }));
  const landedOnes = dated.filter((b) => !b.arrival_is_estimate);
  const [previous, last] = landedOnes.slice(-2);
  const salePrice = s.sale_price !== null && s.margin_pct !== null ? Number(s.sale_price) : null;

  return (
    <>
      <Breadcrumb backHref="/skus" backLabel={t("title")} />
      <PageHeader
        title={<span className="font-mono">{s.sku}</span>}
        subtitle={[s.description, t("detail.arrivals", { count: s.arrivals }), s.last_arrival_on ? t("lastArrival", { date: dateShort(s.last_arrival_on, locale) }) : null].filter(Boolean).join(" · ")}
      />

      <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 mb-6">
        <StatTile label={t("detail.unitLanded")} hint={t("detail.unitLandedHint")} value={money(s.unit_landed, currency, locale)} sub={t("detail.coef", { coef: Number(s.unit_fob) > 0 ? ratio(Number(s.unit_landed) / Number(s.unit_fob)) : "—" })} />
        <StatTile label={t("detail.last")} hint={t("kpi.changeHint")} value={s.last_unit_landed !== null ? money(s.last_unit_landed, currency, locale) : "—"} sub={s.change_pct !== null ? <><ChangeBadge value={s.change_pct} locale={locale} /> <span className="ml-1">{t("detail.vsPrevious")}</span></> : t("detail.oneArrival")} spark={landedOnes.map((a) => Number(a.unit_landed))} />
        <StatTile label={t("detail.unitFob")} value={money(s.unit_fob, currency, locale)} sub={t("detail.quantity", { qty: qty(s.quantity, locale) })} />
        <StatTile
          label={t("detail.margin")}
          hint={t("kpi.marginHint")}
          value={s.margin_pct !== null ? pct(s.margin_pct, locale) : "—"}
          tone={s.margin_pct !== null && Number(s.margin_pct) < 0 ? "danger" : "default"}
          sub={
            s.margin_unit !== null && s.sale_price !== null
              ? [t("detail.marginSub", { unit: money(s.margin_unit, currency, locale), price: money(s.sale_price, currency, locale) }), s.last_margin_pct != null ? t("detail.marginLast", { margin: pct(s.last_margin_pct, locale) }) : null].filter(Boolean).join(" · ")
              : s.sale_price !== null
                ? t("detail.marginOtherCurrency")
                : t("kpi.noPrice")
          }
          href={s.sale_price === null ? "#fiche" : undefined}
        />
      </div>

      <div className="grid gap-6 xl:grid-cols-3 items-start mb-6">
        <Panel title={t("trend.title")} subtitle={t("trend.subtitle")} className="xl:col-span-2">
          {points.length === 0 ? <p className="text-sm text-slate-500">{t("trend.empty")}</p> : <UnitCostTrend points={points} currency={currency} locale={locale} labels={{ landed: t("trend.landed"), fob: t("trend.fob"), approach: t("trend.approach"), estimated: t("estimated") }} />}
        </Panel>

        <Panel title={t("shift.title")} subtitle={previous && last ? t("shift.subtitle", { from: previous.container_number, to: last.container_number }) : undefined}>
          {previous && last ? (
            <CostShift previous={previous} last={last} currency={currency} locale={locale} fobLabel={t("shift.fob")} familyLabels={familyLabels} totalLabel={t("shift.total")} />
          ) : (
            <p className="text-sm text-slate-500">{t("shift.empty")}</p>
          )}
        </Panel>
      </div>

      <div className="grid gap-6 xl:grid-cols-3 items-start mb-6">
        <Panel title={t("arrivalsTable.title")} className="xl:col-span-2 [&>div:last-child]:p-0">
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="text-xs text-slate-500 border-y border-slate-200 bg-slate-50/60">
                  <th className="px-5 py-2.5 text-left font-medium">{t("arrivalsTable.arrived")}</th>
                  <th className="px-3 py-2.5 text-left font-medium">{t("arrivalsTable.container")}</th>
                  <th className="px-3 py-2.5 text-left font-medium">{t("arrivalsTable.order")}</th>
                  <th className="px-3 py-2.5 text-right font-medium">{t("th.quantity")}</th>
                  <th className="px-3 py-2.5 text-right font-medium">{t("th.unitFob")}</th>
                  <th className="px-3 py-2.5 text-right font-medium">{t("th.unitLanded")}</th>
                  <th className="px-5 py-2.5 text-right font-medium">{t("arrivalsTable.coef")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {[...arrivals].reverse().map((a) => (
                  <tr key={a.load_id} className="hover:bg-slate-50">
                    <td className="px-5 py-2.5 whitespace-nowrap text-slate-600">
                      {a.arrived_on ? dateShort(a.arrived_on, locale) : "—"}
                      {a.arrival_is_estimate ? <span className="ml-1.5 text-xs text-slate-500">{t("trend.eta")}</span> : null}
                    </td>
                    <td className="px-3 py-2.5 whitespace-nowrap"><Link href={`/container/${a.container_id}`} className="font-mono text-blue-700 hover:underline">{a.container_number}</Link></td>
                    <td className="px-3 py-2.5 whitespace-nowrap">
                      <Link href={`/purchase-orders/${a.po_id}`} className="text-blue-700 hover:underline">{a.po_number}</Link>
                      {a.supplier_name ? <div className="text-xs text-slate-500 max-w-44 truncate">{a.supplier_name}</div> : null}
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-slate-600">{qty(a.quantity, locale)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-slate-600">{money(a.unit_fob, currency, locale)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums font-medium text-slate-900 whitespace-nowrap">
                      {money(a.unit_landed, currency, locale)}
                      {Number(a.estimated) > 0 ? <span className="ml-1.5 rounded bg-violet-50 px-1 py-0.5 text-[11px] font-medium text-violet-800">{t("estimated")}</span> : null}
                    </td>
                    <td className="px-5 py-2.5 text-right tabular-nums text-slate-600">{Number(a.fob) > 0 ? ratio(Number(a.landed) / Number(a.fob)) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>

        <Panel title={t("margin.title")} subtitle={t("margin.subtitle")}>
          <MarginHelper unitFob={Number(s.unit_fob)} unitLanded={Number(s.last_unit_landed ?? s.unit_landed)} salePrice={salePrice} currency={currency} />
        </Panel>
      </div>

      <Panel id="fiche" title={t("product.title")} subtitle={sheet ? t("product.subtitleExisting") : t("product.subtitleNew")} className="scroll-mt-20">
        <ProductForm
          key={sheet?.updated_at ?? "new"}
          currency={currency}
          revalidate={skuHref(sku)}
          initial={{
            sku,
            description: sheet?.description ?? s.description ?? "",
            hs_code: sheet?.hs_code ?? "",
            duty_rate_pct: fractionToPercent(sheet?.duty_rate, locale),
            unit_weight_kg: decimalDisplay(trimZeros(sheet?.unit_weight_kg, 0), locale),
            unit_volume_cbm: decimalDisplay(trimZeros(sheet?.unit_volume_cbm, 0), locale),
            sale_price: decimalDisplay(trimZeros(sheet?.sale_price, 2), locale),
          }}
        />
      </Panel>
    </>
  );
}

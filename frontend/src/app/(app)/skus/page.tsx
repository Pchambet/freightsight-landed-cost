import type { Metadata } from "next";
import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";
import { Search, Tags } from "lucide-react";
import { PageHeader, btnSecondary, input } from "@/components/layout/AppShell";
import ApiErrorNotice from "@/components/shared/ApiErrorNotice";
import Panel from "@/components/ui/Panel";
import StatTile from "@/components/ui/StatTile";
import ChangeBadge from "@/features/skus/ChangeBadge";
import CreateSheetsButton from "@/features/skus/CreateSheetsButton";
import { skuHref } from "@/features/skus/href";
import { api } from "@/lib/api/client";
import { dateShort, money, pct, qty } from "@/lib/format";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("skus");
  return { title: t("title") };
}

const SORTS = ["landed", "change", "margin", "sku"] as const;
type Sort = (typeof SORTS)[number];
const n = (v: string | null | undefined) => (v === null || v === undefined ? null : Number(v));

export default async function SkusPage({ searchParams }: { searchParams: Promise<{ q?: string; sort?: string }> }) {
  const [sp, t, locale] = await Promise.all([searchParams, getTranslations("skus"), getLocale()]);
  const q = (sp.q ?? "").trim().slice(0, 80);
  const sort: Sort = (SORTS as readonly string[]).includes(sp.sort ?? "") ? (sp.sort as Sort) : "landed";
  const res = await api.GET("/api/v1/reports/skus", { params: { query: q ? { q } : {} } });
  if (res.error || !res.data) {
    return (
      <>
        <PageHeader title={t("title")} />
        <ApiErrorNotice error={res.error} retryHref="/skus" />
      </>
    );
  }
  const currency = res.data.base_currency;
  const skus = [...res.data.skus].sort((a, b) => {
    switch (sort) {
      case "change":
        return (n(b.change_pct) ?? -Infinity) - (n(a.change_pct) ?? -Infinity);
      case "margin":
        return (n(a.margin_pct) ?? Infinity) - (n(b.margin_pct) ?? Infinity);
      case "sku":
        return a.sku.localeCompare(b.sku);
      default:
        return Number(b.landed) - Number(a.landed);
    }
  });
  const moved = res.data.skus.filter((s) => s.change_pct !== null);
  const rising = [...moved].sort((a, b) => Number(b.change_pct) - Number(a.change_pct))[0];
  const falling = [...moved].sort((a, b) => Number(a.change_pct) - Number(b.change_pct))[0];
  const priced = res.data.skus.filter((s) => s.margin_pct !== null);
  const thinnest = [...priced].sort((a, b) => Number(a.margin_pct) - Number(b.margin_pct))[0];
  const sortHref = (s: Sort) => `/skus?${new URLSearchParams({ ...(q ? { q } : {}), ...(s !== "landed" ? { sort: s } : {}) }).toString()}`;

  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")} actions={<CreateSheetsButton />} />

      {res.data.skus.length > 0 && !q ? (
        <div className="grid gap-4 grid-cols-1 sm:grid-cols-3 mb-6">
          <StatTile label={t("kpi.count")} value={res.data.skus.length} sub={t("kpi.countSub", { priced: priced.length })} />
          {rising && Number(rising.change_pct) > 0 ? (
            <StatTile label={t("kpi.rising")} hint={t("kpi.changeHint")} value={<span className="font-mono text-xl">{rising.sku}</span>} sub={<ChangeBadge value={rising.change_pct} locale={locale} />} href={skuHref(rising.sku)} />
          ) : falling ? (
            <StatTile label={t("kpi.falling")} hint={t("kpi.changeHint")} value={<span className="font-mono text-xl">{falling.sku}</span>} sub={<ChangeBadge value={falling.change_pct} locale={locale} />} href={skuHref(falling.sku)} />
          ) : (
            <StatTile label={t("kpi.rising")} hint={t("kpi.changeHint")} value="—" sub={t("kpi.noChange")} />
          )}
          {thinnest ? (
            <StatTile label={t("kpi.thinnest")} hint={t("kpi.marginHint")} value={<span className="font-mono text-xl">{thinnest.sku}</span>} sub={t("kpi.thinnestSub", { margin: pct(thinnest.margin_pct!, locale) })} tone={Number(thinnest.margin_pct) < 0 ? "danger" : "default"} href={skuHref(thinnest.sku)} />
          ) : (
            <StatTile label={t("kpi.thinnest")} hint={t("kpi.marginHint")} value="—" sub={t("kpi.noPrice")} />
          )}
        </div>
      ) : null}

      <Panel className="[&>div]:p-0">
        <div className="flex flex-wrap items-center gap-3 px-5 py-3 border-b border-slate-200">
          <form method="get" role="search" className="relative flex-1 min-w-52 max-w-sm">
            <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" aria-hidden />
            <input name="q" defaultValue={q} placeholder={t("searchPlaceholder")} aria-label={t("searchPlaceholder")} className={`${input} pl-9`} />
            {sort !== "landed" ? <input type="hidden" name="sort" value={sort} /> : null}
          </form>
          <nav aria-label={t("sortBy")} className="flex items-center gap-1 text-xs text-slate-500">
            <span className="mr-1">{t("sortBy")}</span>
            {SORTS.map((s) => (
              <Link key={s} href={sortHref(s)} aria-current={s === sort ? "true" : undefined} className={`rounded-md px-2 py-1 transition-colors ${s === sort ? "bg-slate-900 text-white font-medium" : "hover:bg-slate-100 text-slate-600"}`}>
                {t(`sort.${s}`)}
              </Link>
            ))}
          </nav>
        </div>

        {skus.length === 0 ? (
          <div className="py-14 text-center max-w-md mx-auto px-5">
            <Tags className="w-8 h-8 mx-auto text-slate-400" aria-hidden />
            <h2 className="mt-3 font-semibold text-slate-900">{q ? t("empty.noMatchTitle", { q }) : t("empty.title")}</h2>
            <p className="mt-1 text-sm text-slate-600">{q ? t("empty.noMatch") : t("empty.body")}</p>
            <div className="mt-4">{q ? <Link href="/skus" className={btnSecondary}>{t("empty.clear")}</Link> : <Link href="/imports/new" className={btnSecondary}>{t("empty.import")}</Link>}</div>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="text-xs text-slate-500 border-b border-slate-200 bg-slate-50/60">
                  <th className="px-5 py-2.5 text-left font-medium">{t("th.sku")}</th>
                  <th className="px-3 py-2.5 text-right font-medium">{t("th.arrivals")}</th>
                  <th className="px-3 py-2.5 text-right font-medium">{t("th.quantity")}</th>
                  <th className="px-3 py-2.5 text-right font-medium">{t("th.unitFob")}</th>
                  <th className="px-3 py-2.5 text-right font-medium">{t("th.unitLanded")}</th>
                  <th className="px-3 py-2.5 text-right font-medium">{t("th.last")}</th>
                  <th className="px-3 py-2.5 text-right font-medium">{t("th.change")}</th>
                  <th className="px-3 py-2.5 text-right font-medium">{t("th.salePrice")}</th>
                  <th className="px-5 py-2.5 text-right font-medium">{t("th.margin")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {skus.map((s) => (
                  <tr key={s.sku} className="hover:bg-slate-50">
                    <td className="px-5 py-3 max-w-72">
                      <Link href={skuHref(s.sku)} className="font-mono font-medium text-blue-700 hover:underline">{s.sku}</Link>
                      {s.has_estimates ? <span className="ml-2 rounded bg-violet-50 px-1.5 py-0.5 text-[11px] font-medium text-violet-800 ring-1 ring-inset ring-violet-600/20">{t("estimated")}</span> : null}
                      {s.description ? <div className="text-xs text-slate-500 truncate">{s.description}</div> : null}
                    </td>
                    <td className="px-3 py-3 text-right tabular-nums text-slate-600" title={s.last_arrival_on ? t("lastArrival", { date: dateShort(s.last_arrival_on, locale) }) : undefined}>{s.arrivals}</td>
                    <td className="px-3 py-3 text-right tabular-nums text-slate-600">{qty(s.quantity, locale)}</td>
                    <td className="px-3 py-3 text-right tabular-nums text-slate-600">{money(s.unit_fob, currency, locale)}</td>
                    <td className="px-3 py-3 text-right tabular-nums font-medium text-slate-900">{money(s.unit_landed, currency, locale)}</td>
                    <td className="px-3 py-3 text-right tabular-nums text-slate-600">{s.last_unit_landed !== null ? money(s.last_unit_landed, currency, locale) : "—"}</td>
                    <td className="px-3 py-3 text-right"><ChangeBadge value={s.change_pct} locale={locale} /></td>
                    <td className="px-3 py-3 text-right tabular-nums text-slate-600">{s.sale_price !== null ? money(s.sale_price, currency, locale) : <Link href={`${skuHref(s.sku)}#fiche`} className="text-xs text-blue-700 hover:underline">{t("setPrice")}</Link>}</td>
                    <td className={`px-5 py-3 text-right tabular-nums font-medium ${s.margin_pct !== null && Number(s.margin_pct) < 0 ? "text-red-700" : "text-slate-900"}`}>{s.margin_pct !== null ? pct(s.margin_pct, locale) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  );
}

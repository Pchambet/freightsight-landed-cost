import type { Metadata } from "next";
import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";
import { AlertTriangle, ArrowRight, CheckCircle2, FileSpreadsheet, FileText, FileWarning, Hourglass, PackageOpen, Plug, Receipt, Sparkles } from "lucide-react";
import { PageHeader, btnPrimary, btnSecondary } from "@/components/layout/AppShell";
import BreakdownBar from "@/components/charts/BreakdownBar";
import StackedColumns, { type StackedColumn } from "@/components/charts/StackedColumns";
import { COST_FAMILIES, foldFamilies, type CostFamily } from "@/components/charts/families";
import ApiErrorNotice from "@/components/shared/ApiErrorNotice";
import Panel from "@/components/ui/Panel";
import StatTile, { type Delta } from "@/components/ui/StatTile";
import LoadSampleDataButton from "@/features/containers/LoadSampleDataButton";
import RankBars, { type RankRow } from "@/features/overview/RankBars";
import { WINDOWS, monthLabel, monthTitle, windows, type WindowMonths } from "@/features/overview/period";
import { skuHref } from "@/features/skus/href";
import { api } from "@/lib/api/client";
import { dateShort, money, pct, qty } from "@/lib/format";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("overview");
  return { title: t("title") };
}

const num = (v: string | number | null | undefined) => {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : 0;
};

export default async function OverviewPage({ searchParams }: { searchParams: Promise<{ months?: string; notice?: string }> }) {
  const [sp, t, tf, locale] = await Promise.all([searchParams, getTranslations("overview"), getTranslations("costFamily"), getLocale()]);
  const months: WindowMonths = (WINDOWS as readonly number[]).includes(Number(sp.months)) ? (Number(sp.months) as WindowMonths) : 6;
  const { current } = windows(months);
  const cur = { period_from: current.from, period_to: current.to };

  // One read, one run of the engine: the overview carries the monthly and per-supplier buckets, the
  // heaviest SKUs and the containers at risk, each tested on the API side against its detailed report.
  const overview = await api.GET("/api/v1/reports/overview", { params: { query: cur } });
  if (overview.error || !overview.data) {
    return (
      <>
        <PageHeader title={t("title")} />
        <ApiErrorNotice error={overview.error} retryHref={`/overview?months=${months}`} />
      </>
    );
  }

  const o = overview.data;
  const currency = o.base_currency;
  // The headline figures come from the API's own overview (same numbers as the detailed reports, by
  // test); only what it does not carry — the charges, per month and per family — is read from the
  // landed-cost report of the same dates.
  const fob = num(o.current.fob);
  const landed = num(o.current.landed);
  const fees = Object.entries(o.by_cost_type ?? {}).reduce((sum, [type, amount]) => (type === "IMPORT_VAT" ? sum : sum + num(amount as string)), 0);
  const coef = o.current.coefficient !== null ? num(o.current.coefficient) : null;
  const coefBefore = o.previous.coefficient !== null ? num(o.previous.coefficient) : null;
  const hasData = fob > 0 || fees > 0;

  const familyLabels = Object.fromEntries(COST_FAMILIES.map((f) => [f, tf(f)])) as Record<CostFamily, string>;
  const ratio = (v: number) => new Intl.NumberFormat(locale === "fr" ? "fr-FR" : "en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v);
  const signed = (v: number, digits = 1) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${new Intl.NumberFormat(locale === "fr" ? "fr-FR" : "en-GB", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(Math.abs(v))}`;
  const versus = t("versus", { months });
  // A "before" only compares if it was a period of activity too: against the first weeks of a
  // history, every figure would read "+2 500 %" — true, and useless.
  const comparable = o.previous.containers >= Math.max(2, Math.ceil(o.current.containers / 3));
  /** Relative change, or nothing when there is no "before" to compare with. */
  const change = (now: number, then: number, upIsGood: boolean | null): Delta | null => {
    if (!comparable || then <= 0) return null;
    const p = ((now - then) / then) * 100;
    const direction = Math.abs(p) < 0.05 ? "flat" : p > 0 ? "up" : "down";
    return { text: `${signed(p)} %`, direction, good: upIsGood === null ? null : direction === "up" ? upIsGood : !upIsGood, versus };
  };

  // One column per calendar month of the window, arrivals or not: a month without a container is a
  // fact of the period, not a hole to close up.
  const bucket = new Map((o.by_month ?? []).map((b) => [b.key, b]));
  const yearTurns = new Set(current.months.map((k) => k.slice(0, 4))).size > 1;
  const columns: StackedColumn[] = current.months.map((key) => {
    const b = bucket.get(key);
    const bFob = num(b?.fob);
    return {
      key,
      label: monthLabel(key, locale, yearTurns),
      title: monthTitle(key, locale),
      families: foldFamilies(b?.by_cost_type as Record<string, string> | undefined),
      note: b ? t("monthNote", { loads: b.load_count, coef: bFob > 0 ? ratio(num(b.landed) / bFob) : "—" }) : t("monthEmpty"),
      href: `/reports?group_by=cost_type&from=${key}-01&to=${key}-${String(new Date(Date.UTC(Number(key.slice(0, 4)), Number(key.slice(5, 7)), 0)).getUTCDate()).padStart(2, "0")}`,
    };
  });
  const coefSpark = current.months.map((k) => bucket.get(k)).filter((b) => b && num(b.fob) > 0).map((b) => num(b!.landed) / num(b!.fob));
  const undated = bucket.get("unknown");

  const suppliers: RankRow[] = (o.by_supplier ?? [])
    .filter((b) => num(b.fob) > 0)
    .map((b) => ({
      key: b.key,
      label: b.key === "unknown" ? t("unknownSupplier") : b.label,
      value: (num(b.allocated) / num(b.fob)) * 100,
      display: ratio(num(b.landed) / num(b.fob)),
      sub: t("supplierSub", { fees: money(b.allocated, currency, locale), fob: money(b.fob, currency, locale) }),
      href: `/reports?group_by=supplier&from=${current.from}&to=${current.to}`,
    }))
    .sort((x, y) => y.value - x.value)
    .slice(0, 6);

  const skus = o.skus ?? [];

  // What needs a human today, most urgent first. Independent of the period shown.
  const atRisk = [...(o.at_risk ?? [])].sort((x, y) => (x.last_free_day ?? "9999").localeCompare(y.last_free_day ?? "9999"));
  const todoRows: { key: string; icon: "review" | "failed" | "estimates" | "uncosted" | "unloaded"; count: number; href: string }[] = [
    { key: "review", icon: "review" as const, count: o.todo.invoices_to_review, href: "/invoices" },
    { key: "failed", icon: "failed" as const, count: o.todo.invoices_failed, href: "/invoices" },
    { key: "estimates", icon: "estimates" as const, count: o.todo.estimates_awaiting_invoice, href: "/reports/variance" },
    { key: "uncosted", icon: "uncosted" as const, count: o.todo.containers_without_cost, href: "/containers" },
    { key: "unloaded", icon: "unloaded" as const, count: o.todo.containers_without_loads, href: "/containers" },
  ].filter((r) => r.count > 0);
  const todo = atRisk.length + todoRows.length;
  const TODO_ICON = { review: Receipt, failed: FileWarning, estimates: Hourglass, uncosted: FileText, unloaded: PackageOpen };
  const hasContainers = o.current.containers + o.previous.containers + o.todo.containers_without_cost + o.todo.containers_without_loads + atRisk.length > 0;

  const paid = num(o.current.demurrage_paid);
  const avoided = num(o.current.demurrage_avoided);
  const variance = num(o.current.variance);

  return (
    <>
      <PageHeader
        title={t("title")}
        subtitle={t("subtitle", { from: dateShort(current.from, locale), to: dateShort(current.to, locale) })}
        actions={
          <nav aria-label={t("window")} className="inline-flex rounded-lg border border-slate-200 bg-white p-0.5 text-sm shadow-card">
            {WINDOWS.map((w) => (
              <Link
                key={w}
                href={`/overview?months=${w}`}
                aria-current={w === months ? "page" : undefined}
                className={`px-3 py-1.5 rounded-md transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${w === months ? "bg-slate-900 text-white font-medium" : "text-slate-600 hover:bg-slate-100"}`}
              >
                {t("windowMonths", { months: w })}
              </Link>
            ))}
          </nav>
        }
      />

      {sp.notice === "org-changed" ? (
        <div role="status" className="rounded-xl border border-amber-300 bg-amber-50 px-5 py-4 mb-6 flex items-start gap-3 text-sm">
          <AlertTriangle className="w-5 h-5 mt-0.5 shrink-0 text-amber-600" aria-hidden />
          <div className="min-w-0 flex-1">
            <p className="font-medium text-amber-950">{t("orgChanged.title")}</p>
            <p className="mt-0.5 text-amber-900">{t("orgChanged.body")}</p>
          </div>
          <Link href="/overview" className="shrink-0 font-medium text-amber-950 underline underline-offset-2 hover:no-underline">{t("orgChanged.dismiss")}</Link>
        </div>
      ) : null}

      <Panel
        title={t("todo.title")}
        right={todo === 0 ? null : <span className="rounded-full bg-amber-100 text-amber-900 px-2 py-0.5 font-medium tabular-nums">{todo}</span>}
        className="mb-6"
      >
        {todo === 0 ? (
          <p className="flex items-center gap-2 text-sm text-slate-600"><CheckCircle2 className="w-4 h-4 text-emerald-600" aria-hidden />{t("todo.none")}</p>
        ) : (
          <ul className="-my-2 divide-y divide-slate-100">
            {atRisk.map((c) => {
              const over = c.days_over ?? 0;
              const left = c.days_left ?? null;
              const late = over > 0 || c.dnd_risk === "INCURRING";
              return (
                <li key={c.container_id} className="py-2.5 flex items-center gap-3 text-sm">
                  <AlertTriangle className={`w-4 h-4 shrink-0 ${late ? "text-red-600" : "text-amber-600"}`} aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="font-mono font-medium text-slate-900">{c.container_number}</span>{" "}
                    <span className="text-slate-600">
                      {c.last_free_day === null || c.last_free_day === undefined || left === null
                        ? t("todo.exposedNoDate")
                        : late
                          ? t("todo.exposedLate", { days: over, date: dateShort(c.last_free_day, locale) })
                          : t("todo.exposedLeft", { days: Math.max(left, 0), date: dateShort(c.last_free_day, locale) })}
                    </span>
                    {num(c.amount_at_risk) > 0 ? <span className="ml-2 font-medium text-red-700 tabular-nums whitespace-nowrap">{t("todo.soFar", { amount: money(c.amount_at_risk, currency, locale) })}</span> : null}
                  </span>
                  <Link href={`/container/${c.container_id}#suivi`} className="shrink-0 inline-flex items-center gap-1 text-blue-700 hover:underline">{t("todo.open")}<ArrowRight className="w-3.5 h-3.5" aria-hidden /></Link>
                </li>
              );
            })}
            {todoRows.map((r) => {
              const Icon = TODO_ICON[r.icon];
              return (
                <li key={r.key} className="py-2.5 flex items-center gap-3 text-sm">
                  <Icon className={`w-4 h-4 shrink-0 ${r.key === "failed" ? "text-red-600" : r.key === "review" ? "text-blue-600" : "text-slate-500"}`} aria-hidden />
                  <span className="min-w-0 flex-1 text-slate-600">{t(`todo.${r.key}`, { count: r.count })}</span>
                  <Link href={r.href} className="shrink-0 inline-flex items-center gap-1 text-blue-700 hover:underline">{t(`todo.${r.key}Action`)}<ArrowRight className="w-3.5 h-3.5" aria-hidden /></Link>
                </li>
              );
            })}
          </ul>
        )}
      </Panel>

      {!hasData && !hasContainers ? (
        // A company's first visit: three ways in, the fastest one first.
        <section aria-labelledby="welcome-title" className="mb-6">
          <h2 id="welcome-title" className="text-lg font-semibold text-slate-900">{t("welcome.title")}</h2>
          <p className="mt-1 text-sm text-slate-600 max-w-2xl">{t("welcome.body")}</p>
          <div className="mt-5 grid gap-4 lg:grid-cols-3">
            <div className="rounded-xl border border-blue-200 bg-blue-50/60 p-5 flex flex-col">
              <Sparkles className="w-5 h-5 text-blue-700" aria-hidden />
              <h3 className="mt-3 font-semibold text-slate-900">{t("welcome.sample.title")}</h3>
              <p className="mt-1 text-sm text-slate-600 flex-1">{t("welcome.sample.body")}</p>
              <div className="mt-4"><LoadSampleDataButton profile="history" primary /></div>
            </div>
            <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-card flex flex-col">
              <FileSpreadsheet className="w-5 h-5 text-slate-700" aria-hidden />
              <h3 className="mt-3 font-semibold text-slate-900">{t("welcome.start.title")}</h3>
              <p className="mt-1 text-sm text-slate-600 flex-1">{t("welcome.start.body")}</p>
              <div className="mt-4 flex flex-wrap items-center gap-3">
                <Link href="/start" className={btnSecondary}>{t("welcome.start.action")}</Link>
                <Link href="/imports/new" className="text-sm text-blue-700 hover:underline">{t("welcome.import.action")}</Link>
              </div>
            </div>
            <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-card flex flex-col">
              <Plug className="w-5 h-5 text-slate-700" aria-hidden />
              <h3 className="mt-3 font-semibold text-slate-900">{t("welcome.erp.title")}</h3>
              <p className="mt-1 text-sm text-slate-600 flex-1">{t("welcome.erp.body")}</p>
              <div className="mt-4"><Link href="/settings#erp" className={btnSecondary}>{t("welcome.erp.action")}</Link></div>
            </div>
          </div>
        </section>
      ) : !hasData ? (
        <Panel className="mb-6">
          <div className="py-10 text-center max-w-md mx-auto">
            <PackageOpen className="w-8 h-8 mx-auto text-slate-400" aria-hidden />
            <h2 className="mt-3 font-semibold text-slate-900">{t("empty.title")}</h2>
            <p className="mt-1 text-sm text-slate-600">{t("empty.noArrivals", { months })}</p>
            <div className="mt-4 flex flex-wrap justify-center gap-2 text-sm">
              <Link href="/containers" className={btnPrimary}>{t("empty.goContainers")}</Link>
              {months !== 12 ? <Link href="/overview?months=12" className={btnSecondary}>{t("empty.widen")}</Link> : null}
            </div>
          </div>
        </Panel>
      ) : (
        <>
          <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 mb-6">
            <StatTile
              label={t("kpi.coef")}
              hint={t("kpi.coefHint")}
              value={coef !== null ? ratio(coef) : "—"}
              sub={coef !== null ? t("kpi.coefSub", { pct: pct((coef - 1) * 100, locale) }) : undefined}
              delta={comparable && coef !== null && coefBefore !== null ? { text: signed(coef - coefBefore, 2), direction: Math.abs(coef - coefBefore) < 0.005 ? "flat" : coef > coefBefore ? "up" : "down", good: coef <= coefBefore, versus } : null}
              spark={coefSpark}
            />
            <StatTile label={t("kpi.fees")} hint={t("kpi.feesHint")} value={money(fees, currency, locale)} sub={t("kpi.feesSub", { fob: money(fob, currency, locale), containers: o.current.containers })} href={`/reports?group_by=cost_type&from=${current.from}&to=${current.to}`} />
            <StatTile
              label={t("kpi.variance")}
              hint={t("kpi.varianceHint")}
              value={o.current.variance_pairs > 0 ? `${variance > 0 ? "+" : variance < 0 ? "−" : ""}${money(Math.abs(variance), currency, locale)}` : "—"}
              tone={o.current.variance_pairs > 0 && variance > 0 ? "warning" : "default"}
              sub={o.current.variance_pairs > 0 ? t("kpi.varianceSub", { pairs: o.current.variance_pairs, estimated: money(o.current.variance_estimated, currency, locale) }) : t("kpi.varianceNone")}
              href="/reports/variance"
            />
            <StatTile
              label={t("kpi.dnd")}
              hint={t("kpi.dndHint")}
              value={money(paid, currency, locale)}
              tone={paid > 0 ? "danger" : "default"}
              delta={comparable ? change(paid, num(o.previous.demurrage_paid), false) : null}
              sub={[avoided > 0 ? t("kpi.dndAvoided", { amount: money(avoided, currency, locale) }) : null, num(o.todo.amount_at_risk) > 0 ? t("kpi.dndAtRisk", { amount: money(o.todo.amount_at_risk, currency, locale) }) : null].filter(Boolean).join(" · ") || undefined}
              href="/reports#dnd"
            />
          </div>

          <div className="grid gap-6 xl:grid-cols-3 mb-6 items-start">
            <Panel
              title={t("structure.title")}
              subtitle={t("structure.subtitle")}
              right={<Link href={`/reports?group_by=month&from=${current.from}&to=${current.to}`} className="text-blue-700 hover:underline">{t("seeTable")}</Link>}
              className="xl:col-span-2"
            >
              <StackedColumns columns={columns} currency={currency} locale={locale} familyLabels={familyLabels} totalLabel={t("structure.total")} />
              {undated && num(undated.allocated) > 0 ? <p className="mt-3 text-xs text-slate-500">{t("structure.undated", { amount: money(undated.allocated, currency, locale) })}</p> : null}
            </Panel>

            <Panel title={t("breakdown.title")} subtitle={t("breakdown.subtitle", { landed: money(landed, currency, locale) })}>
              <BreakdownBar fob={fob} families={foldFamilies(o.by_cost_type as Record<string, string> | undefined)} currency={currency} locale={locale} fobLabel={t("breakdown.fob")} familyLabels={familyLabels} ofFobLabel={t("breakdown.ofFob")} dense />
            </Panel>
          </div>

          <div className="grid gap-6 xl:grid-cols-2 items-start">
            <Panel title={t("suppliers.title")} subtitle={t("suppliers.subtitle")}>
              <RankBars rows={suppliers} empty={t("suppliers.empty")} />
            </Panel>

            <Panel title={t("skus.title")} subtitle={t("skus.subtitle")} right={<Link href="/skus" className="text-blue-700 hover:underline">{t("skus.all")}</Link>}>
              {skus.length === 0 ? (
                <p className="text-sm text-slate-500">{t("skus.empty")}</p>
              ) : (
                <div className="-mx-5 -mb-5 overflow-x-auto">
                  <table className="min-w-full text-sm">
                    <thead>
                      <tr className="text-xs text-slate-500 border-b border-slate-200">
                        <th className="px-5 py-2 text-left font-medium">{t("skus.th.sku")}</th>
                        <th className="px-3 py-2 text-right font-medium">{t("skus.th.quantity")}</th>
                        <th className="px-3 py-2 text-right font-medium">{t("skus.th.unitFob")}</th>
                        <th className="px-3 py-2 text-right font-medium">{t("skus.th.unitLanded")}</th>
                        <th className="px-5 py-2 text-right font-medium">{t("skus.th.coef")}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {skus.map((s) => {
                        const q = num(s.quantity);
                        return (
                          <tr key={s.sku} className="hover:bg-slate-50">
                            <td className="px-5 py-2.5"><Link href={skuHref(s.sku)} className="font-mono text-blue-700 hover:underline">{s.sku}</Link></td>
                            <td className="px-3 py-2.5 text-right tabular-nums text-slate-600">{qty(s.quantity, locale)}</td>
                            <td className="px-3 py-2.5 text-right tabular-nums text-slate-600">{q > 0 ? money(num(s.fob) / q, currency, locale) : "—"}</td>
                            <td className="px-3 py-2.5 text-right tabular-nums font-medium text-slate-900">{money(s.unit_landed_cost, currency, locale)}</td>
                            <td className="px-5 py-2.5 text-right tabular-nums text-slate-600">{num(s.fob) > 0 ? ratio(num(s.landed) / num(s.fob)) : "—"}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </Panel>
          </div>
        </>
      )}
    </>
  );
}

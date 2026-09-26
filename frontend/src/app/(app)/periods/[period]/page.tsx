import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getLocale, getTranslations } from "next-intl/server";
import { CheckCircle2, Download, Hourglass, LockKeyhole } from "lucide-react";
import { PageHeader, btnSecondary } from "@/components/layout/AppShell";
import BreakdownBar from "@/components/charts/BreakdownBar";
import { COST_FAMILIES, foldFamilies, type CostFamily } from "@/components/charts/families";
import ApiErrorNotice from "@/components/shared/ApiErrorNotice";
import Breadcrumb from "@/components/shared/Breadcrumb";
import Panel from "@/components/ui/Panel";
import StatTile from "@/components/ui/StatTile";
import AcknowledgeDrift from "@/features/periods/AcknowledgeDrift";
import PeriodActions from "@/features/periods/PeriodActions";
import { isPeriodKey, periodTitle, signedMoney } from "@/features/periods/format";
import { api } from "@/lib/api/client";
import { dateShort, dateTimeShort, money } from "@/lib/format";

type Params = { params: Promise<{ period: string }> };

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const [{ period }, locale] = await Promise.all([params, getLocale()]);
  return { title: isPeriodKey(period) ? periodTitle(period, locale) : period };
}

export default async function PeriodPage({ params }: Params) {
  const { period } = await params;
  if (!isPeriodKey(period)) notFound();
  const [t, tf, tt, locale, res, me] = await Promise.all([
    getTranslations("periods"),
    getTranslations("costFamily"),
    getTranslations("domain.costType"),
    getLocale(),
    api.GET("/api/v1/periods/{period}", { params: { path: { period } } }),
    api.GET("/api/v1/me"),
  ]);
  if (res.response.status === 404) notFound();
  const title = periodTitle(period, locale);
  if (res.error || !res.data) {
    return (
      <>
        <Breadcrumb backHref="/periods" backLabel={t("title")} />
        <PageHeader title={title} />
        <ApiErrorNotice error={res.error} retryHref={`/periods/${period}`} />
      </>
    );
  }
  const { summary: s, readiness, drift_lines: lines, base_currency: currency } = res.data;
  const closed = s.status === "closed";
  const role = me.data?.role;
  const fmt = (v: number) => money(v, currency, locale);
  const ratio = s.coefficient === null || s.coefficient === undefined ? "—" : new Intl.NumberFormat(locale === "fr" ? "fr-FR" : "en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(s.coefficient));
  const familyLabels = Object.fromEntries(COST_FAMILIES.map((f) => [f, tf(f)])) as Record<CostFamily, string>;
  const drift = Number(s.drift ?? 0);
  const outstanding = Number(s.drift_outstanding ?? s.drift ?? 0);
  const acknowledged = Number(s.drift_acknowledged ?? 0);
  const accruals = res.data.accruals ?? [];
  const canDecide = role === "OWNER" || role === "ADMIN";
  const estimatedTotal = Number(readiness?.estimated_total ?? 0);
  const loose = readiness ? readiness.invoices_to_review + readiness.unallocated_costs + readiness.containers_without_cost : 0;

  return (
    <>
      <Breadcrumb backHref="/periods" backLabel={t("title")} />
      <PageHeader
        title={<span className="flex items-center gap-2.5">{closed ? <LockKeyhole className="w-5 h-5 text-slate-500" aria-hidden /> : null}{title}</span>}
        subtitle={closed ? t("detail.closedSubtitle", { date: dateShort(s.closed_at, locale), by: s.closed_by_name ?? "—" }) : t("detail.openSubtitle", { count: s.containers })}
        actions={<PeriodActions period={period} title={periodTitle(period, locale, true)} status={closed ? "closed" : "open"} canClose={canDecide} canReopen={canDecide} estimated={estimatedTotal > 0 ? money(estimatedTotal, currency, locale) : null} />}
      />

      <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 mb-6">
        <StatTile label={closed ? t("detail.frozenLanded") : t("detail.liveLanded")} hint={closed ? t("detail.frozenHint") : undefined} value={money(s.landed, currency, locale)} sub={t("detail.onFob", { fob: money(s.fob, currency, locale) })} />
        <StatTile label={t("detail.coef")} value={ratio} sub={t("detail.containers", { count: s.containers })} />
        {closed ? (
          <StatTile
            label={t("detail.drift")}
            hint={t("detail.driftHint")}
            value={signedMoney(s.drift ?? "0", fmt)}
            tone={outstanding !== 0 ? "warning" : "default"}
            sub={drift === 0 ? t("detail.driftNone") : outstanding === 0 ? t("detail.driftPosted") : acknowledged !== 0 ? t("detail.driftPartly", { outstanding: signedMoney(outstanding, fmt) }) : t("driftContainers", { count: s.drift_containers ?? 0 })}
          />
        ) : (
          <StatTile label={t("detail.estimated")} hint={t("detail.estimatedHint")} value={money(estimatedTotal, currency, locale)} tone={estimatedTotal > 0 ? "warning" : "default"} sub={estimatedTotal > 0 ? t("detail.estimatedSub", { count: readiness?.containers_with_estimates.length ?? 0 }) : t("detail.estimatedNone")} />
        )}
        {closed ? (
          <StatTile label={t("detail.liveNow")} value={money(s.live_landed, currency, locale)} sub={t("detail.liveNowSub")} />
        ) : (
          <StatTile label={t("detail.loose")} value={loose} tone={loose > 0 ? "warning" : "default"} sub={loose > 0 ? t("detail.looseSub") : t("detail.looseNone")} />
        )}
      </div>

      <div className="grid gap-6 xl:grid-cols-3 items-start mb-6">
        <Panel title={closed ? t("detail.breakdownFrozen") : t("detail.breakdownLive")} className="xl:col-span-2">
          <BreakdownBar fob={Number(s.fob)} families={foldFamilies(s.by_cost_type as Record<string, string>)} currency={currency} locale={locale} fobLabel={t("detail.fob")} familyLabels={familyLabels} ofFobLabel={t("detail.ofFob")} />
        </Panel>

        {closed ? (
          <Panel title={t("exports.title")} subtitle={t("exports.subtitle")}>
            <div className="flex flex-col gap-2">
              <a href={`/api/exports/period-close?period=${period}&locale=${locale}`} className={btnSecondary}><Download className="w-4 h-4 mr-2" aria-hidden />{t("exports.close")}</a>
              <a href={`/api/exports/sku-costs?period=${period}&basis=last&locale=${locale}`} className={btnSecondary}><Download className="w-4 h-4 mr-2" aria-hidden />{t("exports.skuLast")}</a>
              <a href={`/api/exports/sku-costs?period=${period}&basis=average&locale=${locale}`} className={btnSecondary}><Download className="w-4 h-4 mr-2" aria-hidden />{t("exports.skuAverage")}</a>
              {accruals.length > 0 ? <a href={`/api/exports/period-accruals?period=${period}&locale=${locale}`} className={btnSecondary}><Download className="w-4 h-4 mr-2" aria-hidden />{t("exports.accruals")}</a> : null}
              {drift !== 0 || lines.length > 0 ? <a href={`/api/exports/period-drift?period=${period}&locale=${locale}`} className={btnSecondary}><Download className="w-4 h-4 mr-2" aria-hidden />{t("exports.drift")}</a> : null}
            </div>
            <p className="mt-3 text-xs text-slate-500">{t("exports.skuHint")}</p>
          </Panel>
        ) : readiness ? (
          <Panel title={t("ready.title")} subtitle={t("ready.subtitle")}>
            <ul className="space-y-2 text-sm">
              {[
                { n: readiness.containers_with_estimates.length, key: "estimates", href: "/reports/variance" },
                { n: readiness.invoices_to_review, key: "invoices", href: "/invoices?filter=review" },
                { n: readiness.unallocated_costs, key: "unallocated", href: "/containers" },
                { n: readiness.containers_without_cost, key: "nocost", href: "/containers?view=nocost" },
              ].map((r) => (
                <li key={r.key} className="flex items-start gap-2.5">
                  {r.n === 0 ? <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0 text-emerald-600" aria-hidden /> : <Hourglass className="w-4 h-4 mt-0.5 shrink-0 text-amber-600" aria-hidden />}
                  <span className="flex-1 text-slate-700">{t(`ready.${r.key}`, { count: r.n })}</span>
                  {r.n > 0 ? <Link href={r.href} className="shrink-0 text-blue-700 hover:underline">{t("ready.see")}</Link> : null}
                </li>
              ))}
            </ul>
          </Panel>
        ) : null}
      </div>

      {!closed && readiness && readiness.containers_with_estimates.length > 0 ? (
        <Panel title={t("estimates.title")} subtitle={t("estimates.subtitle")} className="[&>div:last-child]:p-0">
          <table className="min-w-full text-sm">
            <tbody className="divide-y divide-slate-100 border-t border-slate-200">
              {readiness.containers_with_estimates.map((c) => (
                <tr key={c.container_id} className="hover:bg-slate-50">
                  <td className="px-5 py-2.5"><Link href={`/container/${c.container_id}`} className="font-mono text-blue-700 hover:underline">{c.container_number}</Link></td>
                  <td className="px-5 py-2.5 text-right tabular-nums font-medium text-slate-900">{money(c.estimated, currency, locale)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      ) : null}

      <Panel
        title={t("accruals.title")}
        subtitle={closed ? t("accruals.subtitleClosed") : t("accruals.subtitleOpen")}
        right={accruals.length > 0 ? <span className="text-sm font-semibold text-slate-900 tabular-nums">{money(res.data.accruals_total, currency, locale)}</span> : null}
        className={`mb-6 ${accruals.length > 0 ? "[&>div:last-child]:p-0" : ""}`}
      >
        {accruals.length === 0 ? (
          <p className="flex items-center gap-2 text-sm text-slate-600"><CheckCircle2 className="w-4 h-4 text-emerald-600" aria-hidden />{t("accruals.none")}</p>
        ) : (
          <>
            <div className="overflow-x-auto border-t border-slate-200">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="text-xs text-slate-500 bg-slate-50/60 border-b border-slate-200">
                    <th className="px-5 py-2 text-left font-medium">{t("accruals.th.container")}</th>
                    <th className="px-3 py-2 text-left font-medium">{t("accruals.th.arrived")}</th>
                    <th className="px-3 py-2 text-left font-medium">{t("accruals.th.cost")}</th>
                    <th className="px-5 py-2 text-right font-medium">{t("accruals.th.amount")}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {accruals.map((a, i) => (
                    <tr key={`${a.container_id}-${a.cost_type}-${i}`} className="hover:bg-slate-50">
                      <td className="px-5 py-2.5"><Link href={`/container/${a.container_id}`} className="font-mono text-blue-700 hover:underline">{a.container_number}</Link></td>
                      <td className="px-3 py-2.5 text-slate-600 whitespace-nowrap">{dateShort(a.arrived_on, locale)}{a.arrived_on && a.arrived_on.slice(0, 7) < period ? <span className="ml-1.5 text-xs text-amber-800">{t("accruals.earlier")}</span> : null}</td>
                      <td className="px-3 py-2.5 text-slate-700">{tt.has(a.cost_type) ? tt(a.cost_type) : a.cost_type}</td>
                      <td className="px-5 py-2.5 text-right tabular-nums font-medium text-slate-900">{money(a.amount_base, currency, locale)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="px-5 py-3 border-t border-slate-100 flex flex-wrap items-center justify-between gap-3">
              <p className="text-xs text-slate-500 max-w-2xl">{t("accruals.rules")}</p>
              {!closed ? <a href={`/api/exports/period-accruals?period=${period}&locale=${locale}`} className={btnSecondary}><Download className="w-4 h-4 mr-2" aria-hidden />{t("exports.accruals")}</a> : null}
            </div>
          </>
        )}
      </Panel>

      {closed ? (
        <Panel title={t("driftTable.title")} subtitle={lines.length === 0 ? undefined : t("driftTable.subtitle")} className={lines.length === 0 ? "" : "[&>div:last-child]:p-0"}>
          {lines.length === 0 ? (
            <p className="flex items-center gap-2 text-sm text-slate-600"><CheckCircle2 className="w-4 h-4 text-emerald-600" aria-hidden />{t("driftTable.none")}</p>
          ) : (
            <ul className="divide-y divide-slate-100 border-t border-slate-200">
              {lines.map((l) => (
                <li key={l.container_id} className="px-5 py-3.5">
                  <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-sm">
                    <Link href={`/container/${l.container_id}`} className="font-mono font-medium text-blue-700 hover:underline">{l.container_number}</Link>
                    <span className="text-slate-600">{t(`driftTable.reason.${l.reason === "left_period" || l.reason === "joined_period" ? l.reason : "costs"}`)}</span>
                    <span className="ml-auto tabular-nums text-slate-500">{money(l.frozen_landed, currency, locale)} → {money(l.live_landed, currency, locale)}</span>
                    <span className="tabular-nums font-semibold text-amber-800">{signedMoney(l.difference, fmt)}</span>
                  </div>
                  {l.costs_changed.length > 0 || l.costs_deleted > 0 ? (
                    <ul className="mt-2 space-y-1 text-xs text-slate-600">
                      {l.costs_changed.map((c) => (
                        <li key={c.cost_id} className="flex flex-wrap gap-x-2">
                          <span className="text-slate-900">{tt.has(c.cost_type) ? tt(c.cost_type) : c.cost_type}</span>
                          <span className="tabular-nums">{money(c.amount_base, currency, locale)}</span>
                          {c.vendor || c.invoice_number ? <span className="text-slate-500">{[c.vendor, c.invoice_number].filter(Boolean).join(" · ")}</span> : null}
                          <span className="text-slate-500">{t("driftTable.changedAt", { when: dateTimeShort(c.changed_at, locale) })}</span>
                        </li>
                      ))}
                      {l.costs_deleted > 0 ? <li className="text-slate-500">{t("driftTable.deleted", { count: l.costs_deleted })}</li> : null}
                    </ul>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
          {lines.length > 0 ? (
            <div className="px-5 py-4 border-t border-slate-200 bg-slate-50/60">
              {s.acknowledged_at ? <p className="text-sm text-slate-700 mb-3">{t("ack.state", { amount: signedMoney(acknowledged, fmt), date: dateShort(s.acknowledged_at, locale) })}{s.acknowledged_note ? <span className="text-slate-500"> — {s.acknowledged_note}</span> : null}</p> : null}
              {outstanding !== 0 ? (
                canDecide ? <AcknowledgeDrift period={period} outstanding={signedMoney(outstanding, fmt)} /> : <p className="text-xs text-slate-500">{t("ack.needsRole")}</p>
              ) : (
                <p className="flex items-center gap-2 text-sm text-emerald-800"><CheckCircle2 className="w-4 h-4" aria-hidden />{t("ack.quiet")}</p>
              )}
            </div>
          ) : null}
        </Panel>
      ) : null}
    </>
  );
}

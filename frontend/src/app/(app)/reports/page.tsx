import type { Metadata } from "next";
import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";
import { Download } from "lucide-react";
import { Card, PageHeader, btnSecondary, input, label, td, tdRight, th, thRight } from "@/components/layout/AppShell";
import BreakdownBar from "@/components/charts/BreakdownBar";
import StackedColumns from "@/components/charts/StackedColumns";
import StackedRows from "@/components/charts/StackedRows";
import { COST_FAMILIES, foldFamilies, type CostFamily } from "@/components/charts/families";
import { Money } from "@/components/shared/Money";
import Panel from "@/components/ui/Panel";
import ScrollRegion from "@/components/shared/ScrollRegion";
import { monthLabel, monthTitle } from "@/features/overview/period";
import WorkflowNextStep from "@/features/workflow/WorkflowNextStep";
import { reportsHubStep } from "@/features/workflow/steps";
import { api, type DndAnalysis, type LandedCostAnalysis } from "@/lib/api/client";
import { dateShort, daysUntil, money, pct, qty } from "@/lib/format";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("reports");
  return { title: t("title") };
}

const GROUPS = ["supplier", "route", "month", "cost_type"] as const;
type Group = (typeof GROUPS)[number];

export default async function ReportsPage({ searchParams }: { searchParams: Promise<{ group_by?: string; from?: string; to?: string }> }) {
  const [sp, t, tc, tt, trisk, tfam, locale] = await Promise.all([searchParams, getTranslations("reports"), getTranslations("common"), getTranslations("domain.costType"), getTranslations("domain.risk"), getTranslations("costFamily"), getLocale()]);
  const groupBy: Group = (GROUPS as readonly string[]).includes(sp.group_by ?? "") ? (sp.group_by as Group) : "supplier";
  const isDate = (v?: string) => (v && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : undefined);
  const period = { period_from: isDate(sp.from), period_to: isDate(sp.to) };
  const [lc, dnd, containersRes] = await Promise.all([
    api.GET("/api/v1/reports/landed-cost", { params: { query: { group_by: groupBy, ...period } } }),
    api.GET("/api/v1/reports/dnd", { params: { query: period } }),
    api.GET("/api/v1/containers"),
  ]);
  const a: LandedCostAnalysis | undefined = lc.data;
  const d: DndAnalysis | undefined = dnd.data;
  const topContainer = containersRes.data?.find((c) => c.container_number === "MSCU4821990") ?? containersRes.data?.[0];
  const hubStep = reportsHubStep(a?.skus ?? [], topContainer ? { id: topContainer.id, number: topContainer.container_number } : undefined);
  const cur = a?.base_currency ?? "EUR";
  const bucketLabel = (b: { key: string; label: string }) => (groupBy === "cost_type" && tt.has(b.key as never) ? tt(b.key as never) : b.label === "unknown route" ? t("unknownRoute") : b.label);
  const familyLabels = Object.fromEntries(COST_FAMILIES.map((f) => [f, tfam(f)])) as Record<CostFamily, string>;
  const ratio = (v: number) => new Intl.NumberFormat(locale === "fr" ? "fr-FR" : "en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v);
  const csvQuery = new URLSearchParams({ locale, ...(period.period_from ? { period_from: period.period_from } : {}), ...(period.period_to ? { period_to: period.period_to } : {}) }).toString();
  // Why a line's "avoided" is what it is, from the API's rule code and its own figures — no longer a
  // parse of an English sentence.
  const dndRuleText = (l: { rule_code: string; days?: number | null; daily_rate?: string | null }) => {
    switch (l.rule_code) {
      case "DND_AVOIDED_ESTIMATED":
        return l.days != null && l.daily_rate != null ? t("dndRule.estimable", { days: l.days, rate: money(l.daily_rate, cur, locale) }) : t("dndRule.unknown");
      case "DND_AVOIDED_NO_RATE":
        return t("dndRule.noRateCard", { demurrage: tt("DEMURRAGE") });
      case "DND_AVOIDED_NO_ALERT":
        return t("dndRule.noAlert");
      default:
        return t("dndRule.unknown");
    }
  };

  // `DndAtRiskLine.rule`, unlike the line above, is already one of three machine codes (the API no
  // longer sends an English sentence here) — the front just formats its own params
  // (`last_free_day`, `dnd_risk`) into the matching sentence instead of pattern-matching prose.
  const atRiskRuleText = (row: { rule: string; last_free_day: string | null; dnd_risk: string }) => {
    switch (row.rule) {
      case "DND_AT_RISK_OVERDUE":
        return t("dndRule.lfdOver", { date: dateShort(row.last_free_day, locale) });
      case "DND_AT_RISK_DAYS_LEFT": {
        const days = Math.max(daysUntil(row.last_free_day) ?? 0, 0);
        return t("dndRule.lfdLeft", { date: dateShort(row.last_free_day, locale), days });
      }
      case "DND_AT_RISK_NO_LFD":
        return t("dndRule.riskNoLfd", { risk: trisk.has(row.dnd_risk.toUpperCase() as never) ? trisk(row.dnd_risk.toUpperCase() as never) : row.dnd_risk });
      default:
        return t("dndRule.unknown");
    }
  };

  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")} actions={<Link href="/reports/variance" className="text-sm text-blue-600 hover:underline">{t("variance")}</Link>} />

      <WorkflowNextStep step={hubStep} />

      <form method="get" className="flex flex-wrap items-end gap-3 mb-6">
        <div><label className={label} htmlFor="group_by">{t("groupBy")}</label><select id="group_by" name="group_by" defaultValue={groupBy} className={input}>{GROUPS.map((g) => <option key={g} value={g}>{t(`group.${g}`)}</option>)}</select></div>
        <div><label className={label} htmlFor="from">{t("from")}</label><input id="from" name="from" type="date" defaultValue={period.period_from ?? ""} className={input} /></div>
        <div><label className={label} htmlFor="to">{t("to")}</label><input id="to" name="to" type="date" defaultValue={period.period_to ?? ""} className={input} /></div>
        <button type="submit" className={btnSecondary}>{t("apply")}</button>
      </form>

      {a && a.buckets.length > 0 ? (
        <Panel title={t(`chart.${groupBy}.title`)} subtitle={t(`chart.${groupBy}.subtitle`)} className="mb-6">
          {groupBy === "month" ? (
            <StackedColumns
              columns={a.buckets.filter((b) => b.key !== "unknown").map((b) => ({ key: b.key, label: monthLabel(b.key, locale, true), title: monthTitle(b.key, locale), families: foldFamilies(b.by_cost_type as Record<string, string>), note: t("chart.loads", { count: b.load_count }) }))}
              currency={cur}
              locale={locale}
              familyLabels={familyLabels}
              totalLabel={t("chart.total")}
            />
          ) : groupBy === "cost_type" ? (
            <BreakdownBar fob={Number(a.totals.fob)} families={foldFamilies(a.totals.by_cost_type as Record<string, string>)} currency={cur} locale={locale} fobLabel={t("chart.fob")} familyLabels={familyLabels} ofFobLabel={t("chart.ofFob")} />
          ) : (
            <StackedRows
              rows={a.buckets.map((b) => ({ key: b.key, label: bucketLabel(b), fob: Number(b.fob), families: foldFamilies(b.by_cost_type as Record<string, string>), display: Number(b.fob) > 0 ? ratio(Number(b.landed) / Number(b.fob)) : "—" }))}
              currency={cur}
              locale={locale}
              familyLabels={familyLabels}
              ofFobLabel={t("chart.ofFob")}
            />
          )}
        </Panel>
      ) : null}

      <Card title={t("landed")} className="mb-6">
        <ScrollRegion label={t("landed")}>
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50"><tr><th className={th}>{t("th.label")}</th><th className={thRight}>{t("th.loads")}</th><th className={thRight}>{t("th.quantity")}</th><th className={thRight}>{t("th.fob")}</th><th className={thRight}>{t("th.allocated")}</th><th className={thRight}>{t("th.landed")}</th><th className={thRight}>{t("th.freightShare")}</th></tr></thead>
            <tbody className="divide-y divide-slate-200">
              {!a || a.buckets.length === 0 ? <tr><td colSpan={7} className="px-5 py-8 text-center text-sm text-slate-500">{t("empty")}</td></tr> : (
                <>
                  {a.buckets.map((b) => (
                    <tr key={b.key} className="hover:bg-slate-50">
                      <td className={td}>{bucketLabel(b)}</td>
                      <td className={tdRight}>{b.load_count}</td>
                      <td className={tdRight}>{qty(b.quantity, locale)}</td>
                      <td className={tdRight}><Money amount={b.fob} currency={cur} /></td>
                      <td className={tdRight}><Money amount={b.allocated} currency={cur} className="text-blue-700" /></td>
                      <td className={`${tdRight} font-medium`}><Money amount={b.landed} currency={cur} /></td>
                      <td className={tdRight}>{b.freight_share_pct != null ? pct(b.freight_share_pct, locale) : "—"}</td>
                    </tr>
                  ))}
                  <tr className="bg-slate-50 font-medium">
                    <td className={td}>{tc("total")}</td><td className={tdRight}>{a.totals.load_count}</td><td className={tdRight}>{qty(a.totals.quantity, locale)}</td>
                    <td className={tdRight}><Money amount={a.totals.fob} currency={cur} /></td><td className={tdRight}><Money amount={a.totals.allocated} currency={cur} /></td><td className={tdRight}><Money amount={a.totals.landed} currency={cur} /></td>
                    <td className={tdRight}>{a.totals.freight_share_pct != null ? pct(a.totals.freight_share_pct, locale) : "—"}</td>
                  </tr>
                </>
              )}
            </tbody>
          </table>
        </ScrollRegion>
      </Card>

      <div className="grid gap-6 xl:grid-cols-2 items-start mb-6">
        <Card title={t("skus")} right={<span className="text-xs text-slate-500 max-w-xs text-right">{t("skuAverageHint")}</span>}>
          <ScrollRegion label={t("skus")}>
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50"><tr><th className={th}>{t("th.sku")}</th><th className={thRight}>{t("th.quantity")}</th><th className={thRight}>{t("th.fob")}</th><th className={thRight}>{t("th.landed")}</th><th className={thRight}>{t("th.unit")}</th></tr></thead>
              <tbody className="divide-y divide-slate-200">
                {!a || a.skus.length === 0 ? <tr><td colSpan={5} className="px-5 py-6 text-center text-sm text-slate-500">{t("empty")}</td></tr> : a.skus.map((s) => (
                  <tr key={s.sku}><td className={`${td} font-mono`}>{s.sku}</td><td className={tdRight}>{qty(s.quantity, locale)}</td><td className={tdRight}><Money amount={s.fob} currency={cur} /></td><td className={tdRight}><Money amount={s.landed} currency={cur} /></td><td className={`${tdRight} font-medium text-emerald-700`}><Money amount={s.unit_landed_cost} currency={cur} /></td></tr>
                ))}
              </tbody>
            </table>
          </ScrollRegion>
        </Card>

        <Card title={t("dnd")} right={<span className="text-xs text-slate-500 max-w-sm text-right">{t("dndHint", { demurrage: tt("DEMURRAGE") })}</span>}>
          <div className="px-5 py-3 flex gap-8 border-b border-slate-200 text-sm">
            <div><div className="text-xs text-slate-500 uppercase tracking-wider">{t("th.paid")}</div><div className="font-semibold tabular-nums text-red-700"><Money amount={d?.paid ?? "0.00"} currency={cur} /></div></div>
            <div><div className="text-xs text-slate-500 uppercase tracking-wider">{t("th.avoided")}</div><div className="font-semibold tabular-nums text-emerald-700"><Money amount={d?.avoided ?? "0.00"} currency={cur} /></div></div>
          </div>
          {(d?.at_risk?.length ?? 0) > 0 ? (
            <div className="px-5 py-3 border-b border-amber-200 bg-amber-50/60 text-sm">
              <div className="text-xs font-semibold uppercase tracking-wider text-amber-900 mb-2">{t("atRiskTitle")}</div>
              <ul className="space-y-2">
                {d!.at_risk!.map((row) => (
                  <li key={row.container_id} className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                    <Link href={`/container/${row.container_id}#suivi`} className="font-mono text-blue-700 hover:underline">{row.container_number}</Link>
                    <span className="text-xs text-amber-800">{atRiskRuleText(row)}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50"><tr><th className={th}>{t("th.container")}</th><th className={thRight}>{t("th.paid")}</th><th className={thRight}>{t("th.avoided")}</th><th className={th}>{t("th.rule")}</th></tr></thead>
              <tbody className="divide-y divide-slate-200">
                {!d || d.lines.length === 0 ? <tr><td colSpan={4} className="px-5 py-6 text-center text-sm text-slate-500">{t("dndEmpty")}</td></tr> : d.lines.map((l) => (
                  <tr key={l.container_id}><td className={td}><Link href={`/container/${l.container_id}`} className="font-mono text-blue-600 hover:underline">{l.container_number}</Link></td><td className={tdRight}><Money amount={l.paid} currency={cur} /></td><td className={tdRight}>{l.avoided != null ? <Money amount={l.avoided} currency={cur} /> : <span className="text-slate-500">{t("notEstimable")}</span>}</td><td className={`${td} text-xs text-slate-500 whitespace-normal max-w-md`}>{dndRuleText(l)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      <Card title={t("exports")} right={<span className="text-xs text-slate-500 max-w-md text-right">{t("exportsHint")}</span>}>
        <div className="p-5 flex flex-wrap gap-3">
          {(["landed-costs", "costs", "purchase-orders", "containers"] as const).map((n) => (
            <a key={n} href={`/api/exports/${n}?${csvQuery}`} className={btnSecondary}><Download className="w-4 h-4 mr-2" />{t(`export.${n}`)}</a>
          ))}
        </div>
      </Card>
    </>
  );
}

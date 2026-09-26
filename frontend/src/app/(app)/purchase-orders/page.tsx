import type { Metadata } from "next";
import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { Search } from "lucide-react";
import { Card, PageHeader, btnSecondary, input, td, tdRight, th, thRight } from "@/components/layout/AppShell";
import { Money } from "@/components/shared/Money";
import WorkflowNextStep from "@/features/workflow/WorkflowNextStep";
import { purchaseOrdersHubStep } from "@/features/workflow/steps";
import { api } from "@/lib/api/client";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("purchaseOrders");
  return { title: t("title") };
}

const fold = (v: string) => v.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase().replace(/[\s-]+/g, "");

export default async function PurchaseOrdersPage({ searchParams }: { searchParams: Promise<{ q?: string; view?: string }> }) {
  const sp = await searchParams;
  const [p, o, t, tc] = await Promise.all([
    api.GET("/api/v1/purchase-orders"),
    api.GET("/api/v1/organization"),
    getTranslations("purchaseOrders"),
    getTranslations("common"),
  ]);
  const pos = p.data ?? [];
  const cur = o.data?.base_currency ?? "EUR";
  const hubStep = purchaseOrdersHubStep(pos);
  // Same work-list pattern as the containers board: the filter lives in the URL. The command
  // palette sends a supplier hit here as `?q=<supplier name>`.
  const q = (sp.q ?? "").trim().slice(0, 80);
  const view = sp.view === "unloaded" ? "unloaded" : "all";
  const unloaded = pos.filter((po) => po.container_numbers.length === 0);
  const needle = fold(q);
  const rows = (view === "unloaded" ? unloaded : pos).filter((po) => needle === "" || [po.po_number, po.supplier_name ?? "", ...po.container_numbers].some((v) => fold(v).includes(needle)));
  const href = (next: { view?: "all" | "unloaded" }) => {
    const params = new URLSearchParams();
    if ((next.view ?? view) === "unloaded") params.set("view", "unloaded");
    if (q) params.set("q", q);
    const qs = params.toString();
    return qs ? `/purchase-orders?${qs}` : "/purchase-orders";
  };
  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle", { count: pos.length })} actions={<Link href="/imports/new" className={btnSecondary}>{t("importCta")}</Link>} />
      <WorkflowNextStep step={hubStep} />
      <Card>
        {pos.length > 0 ? (
          <div className="flex flex-wrap items-center gap-3 px-4 py-3 border-b border-slate-200">
            <nav aria-label={t("views.label")} className="flex items-center gap-1 text-sm">
              {(["all", "unloaded"] as const).map((v) => (
                <Link key={v} href={href({ view: v })} aria-current={v === view ? "true" : undefined} className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${v === view ? "bg-slate-900 text-white font-medium" : "text-slate-600 hover:bg-slate-100"}`}>
                  {t(`views.${v}`)}
                  <span className={`tabular-nums text-xs ${v === view ? "text-slate-300" : "text-slate-500"}`}>{v === "all" ? pos.length : unloaded.length}</span>
                </Link>
              ))}
            </nav>
            <form method="get" role="search" className="relative ml-auto w-full sm:w-64">
              <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" aria-hidden />
              <input name="q" defaultValue={q} placeholder={t("views.search")} aria-label={t("views.search")} className={`${input} pl-9`} />
              {view === "unloaded" ? <input type="hidden" name="view" value="unloaded" /> : null}
            </form>
          </div>
        ) : null}
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50">
              <tr>
                <th className={th}>{t("th.po")}</th>
                <th className={th}>{t("th.supplier")}</th>
                <th className={th}>{t("th.currency")}</th>
                <th className={thRight}>{t("th.lines")}</th>
                <th className={thRight}>{t("th.fob", { currency: cur })}</th>
                <th className={th}>{t("th.containers")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200">
              {pos.length === 0 ? (
                <tr><td colSpan={6} className="px-5 py-14 text-center text-sm text-slate-500">{t("empty")} <Link href="/imports/new" className="text-blue-600 hover:underline">{t("emptyLink")}</Link> {t("emptySuffix")}</td></tr>
              ) : rows.length === 0 ? (
                <tr><td colSpan={6} className="px-5 py-10 text-center text-sm text-slate-500">{q ? t("views.noMatch", { q }) : t("views.emptyView")} <Link href="/purchase-orders" className="text-blue-700 hover:underline">{t("views.showAll")}</Link></td></tr>
              ) : (
                rows.map((po) => (
                  <tr key={po.id} className="hover:bg-slate-50">
                    <td className={td}><Link href={`/purchase-orders/${po.id}`} className="font-medium text-blue-600 hover:underline">{po.po_number}</Link></td>
                    <td className={`${td} text-slate-600`}>{po.supplier_name ?? tc("empty")}</td>
                    <td className={`${td} font-mono text-xs`}>{po.currency}</td>
                    <td className={tdRight}>{po.line_count}</td>
                    <td className={tdRight}><Money amount={po.fob_base} currency={cur} /></td>
                    <td className={`${td} font-mono text-xs text-slate-600`}>{po.container_numbers.join(", ") || <span className="text-slate-500">{t("notLoaded")}</span>}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}

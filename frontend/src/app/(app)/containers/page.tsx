import type { Metadata } from "next";
import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";
import { ArrowDown, ArrowUp, ArrowUpDown, LockKeyhole, Search } from "lucide-react";
import { Card, PageHeader, input, td, tdRight, th, thRight } from "@/components/layout/AppShell";
import { MilestoneBadge, RiskBadge } from "@/components/shared/Badges";
import { Money } from "@/components/shared/Money";
import MetricLabel from "@/components/shared/MetricLabel";
import LoadSampleDataButton from "@/features/containers/LoadSampleDataButton";
import NewContainerForm from "@/features/containers/NewContainerForm";
import OnboardingChecklist from "@/features/onboarding/OnboardingChecklist";
import { periodTitle } from "@/features/periods/format";
import WorkflowNextStep from "@/features/workflow/WorkflowNextStep";
import { containersHubStep } from "@/features/workflow/steps";
import { api, type ContainerSummary } from "@/lib/api/client";
import { demurrageRelevant } from "@/lib/domain";
import { dateShort, daysUntil } from "@/lib/format";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("containers");
  return { title: t("title") };
}

async function load(): Promise<{ containers: ContainerSummary[]; currency: string; invoicesToReview: number; confirmedInvoiceCount: number; purchaseOrderCount: number; error: "api" | "unreachable" | null }> {
  try {
    const [c, o, inv, confirmed, pos] = await Promise.all([
      api.GET("/api/v1/containers"),
      api.GET("/api/v1/organization"),
      api.GET("/api/v1/invoices", { params: { query: { invoice_status: "NEEDS_REVIEW" } } }),
      api.GET("/api/v1/invoices", { params: { query: { invoice_status: "CONFIRMED" } } }),
      api.GET("/api/v1/purchase-orders"),
    ]);
    if (c.error || !c.data) return { containers: [], currency: "EUR", invoicesToReview: 0, confirmedInvoiceCount: 0, purchaseOrderCount: 0, error: "api" };
    return { containers: c.data, currency: o.data?.base_currency ?? "EUR", invoicesToReview: inv.data?.length ?? 0, confirmedInvoiceCount: confirmed.data?.length ?? 0, purchaseOrderCount: pos.data?.length ?? 0, error: null };
  } catch {
    return { containers: [], currency: "EUR", invoicesToReview: 0, confirmedInvoiceCount: 0, purchaseOrderCount: 0, error: "unreachable" };
  }
}

const CLOSED = ["DELIVERED", "GATE_IN_EMPTY_RETURN"];
const RISKY = ["MEDIUM", "HIGH", "INCURRING"];

/** Money strings from the API are summed as integers of cents to stay exact. */
function sumCents(values: string[]): string {
  const cents = values.reduce((acc, v) => acc + Math.round(Number(v) * 100), 0);
  return (cents / 100).toFixed(2);
}

type Todo = { container: ContainerSummary; kind: "lfd" | "lfdOver" | "noLoads" | "noCost"; date?: string | null };

function buildTodos(containers: ContainerSummary[]): Todo[] {
  const todos: Todo[] = [];
  for (const c of containers.filter((x) => !CLOSED.includes(x.milestone))) {
    const days = daysUntil(c.last_free_day);
    if (demurrageRelevant(c)) {
      if (days !== null && days < 0) todos.push({ container: c, kind: "lfdOver", date: c.last_free_day });
      else if (days !== null && days <= 7) todos.push({ container: c, kind: "lfd", date: c.last_free_day });
    }
    if (c.load_count === 0) todos.push({ container: c, kind: "noLoads" });
    else if (!c.cost_types_present?.length) todos.push({ container: c, kind: "noCost" });
  }
  const riskOrder = (c: ContainerSummary) => (RISKY.includes(c.dnd_risk) && demurrageRelevant(c) ? 0 : 1);
  const order = { lfdOver: 0, lfd: 1, noLoads: 2, noCost: 3 };
  return todos.sort(
    (a, b) =>
      riskOrder(a.container) - riskOrder(b.container) ||
      order[a.kind] - order[b.kind] ||
      (a.date ?? "").localeCompare(b.date ?? ""),
  );
}

const VIEWS = ["active", "exposed", "nocost", "closed", "all"] as const;
type View = (typeof VIEWS)[number];
const SORTS = ["container", "lfd", "fob", "allocated"] as const;
type SortKey = (typeof SORTS)[number];
type Search = { view?: string; q?: string; sort?: string; dir?: string };

/** Same folding as the search API: case, accents, and the spaces and dashes typed inside a number. */
const fold = (v: string) => v.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase().replace(/[\s-]+/g, "");

export default async function ContainersPage({ searchParams }: { searchParams: Promise<Search> }) {
  const sp = await searchParams;
  const [{ containers, currency, invoicesToReview, confirmedInvoiceCount, purchaseOrderCount, error }, tp, t, tc, costType, tg, locale] = await Promise.all([
    load(),
    getTranslations("periods.container"),
    getTranslations("containers"),
    getTranslations("common"),
    getTranslations("domain.costType"),
    getTranslations("glossary"),
    getLocale(),
  ]);
  const active = containers.filter((c) => !CLOSED.includes(c.milestone));
  const atRisk = active.filter((c) => RISKY.includes(c.dnd_risk));
  const noCost = active.filter((c) => c.load_count > 0 && !c.cost_types_present?.length);
  const nextLfd = atRisk.map((c) => c.last_free_day).filter((d): d is string => !!d).sort()[0] ?? null;
  const todos = buildTodos(containers);
  const demoContainer = containers.find((c) => c.container_number === "MSCU4821990") ?? containers[0];
  const atRiskAtTerminal = active
    .filter((c) => RISKY.includes(c.dnd_risk) && demurrageRelevant(c))
    .sort((a, b) => (a.last_free_day ?? "").localeCompare(b.last_free_day ?? ""));
  // The board is a work list: what is still moving by default, the rest one click away, and the
  // whole state in the URL so that a filtered list can be bookmarked, shared and reloaded.
  const inView: Record<View, ContainerSummary[]> = {
    active,
    exposed: atRiskAtTerminal,
    nocost: noCost,
    closed: containers.filter((c) => CLOSED.includes(c.milestone)),
    all: containers,
  };
  const view: View = (VIEWS as readonly string[]).includes(sp.view ?? "") ? (sp.view as View) : active.length > 0 ? "active" : "all";
  const q = (sp.q ?? "").trim().slice(0, 60);
  const sort: SortKey = (SORTS as readonly string[]).includes(sp.sort ?? "") ? (sp.sort as SortKey) : "lfd";
  const dir: 1 | -1 = sp.dir === "desc" ? -1 : 1;
  const needle = fold(q);
  const compare: Record<SortKey, (a: ContainerSummary, b: ContainerSummary) => number> = {
    container: (a, b) => dir * a.container_number.localeCompare(b.container_number),
    // No last free day sorts last in both directions: "unknown" is never the most urgent.
    lfd: (a, b) => (a.last_free_day ? 0 : 1) - (b.last_free_day ? 0 : 1) || dir * (a.last_free_day ?? "").localeCompare(b.last_free_day ?? ""),
    fob: (a, b) => dir * (Number(a.fob_base) - Number(b.fob_base)),
    allocated: (a, b) => dir * (Number(a.allocated_base) - Number(b.allocated_base)),
  };
  const rows = inView[view]
    .filter((c) => needle === "" || [c.container_number, c.shipment_reference ?? "", ...(c.po_numbers ?? [])].some((v) => fold(v).includes(needle)))
    .sort(compare[sort]);
  const href = (next: Partial<{ view: View; q: string; sort: SortKey; dir: "asc" | "desc" }>) => {
    const state = { view, q, sort, dir: dir === 1 ? "asc" : "desc", ...next };
    const params = new URLSearchParams();
    if (state.view !== "active") params.set("view", state.view);
    if (state.q) params.set("q", state.q);
    if (state.sort !== "lfd") params.set("sort", state.sort);
    if (state.dir !== "asc") params.set("dir", state.dir);
    const qs = params.toString();
    return qs ? `/containers?${qs}` : "/containers";
  };
  const sortHeader = (key: SortKey, labelText: string, right = false) => {
    const on = sort === key;
    const Arrow = on ? (dir === 1 ? ArrowUp : ArrowDown) : ArrowUpDown;
    return (
      <th className={right ? thRight : th} aria-sort={on ? (dir === 1 ? "ascending" : "descending") : undefined}>
        <Link href={href({ sort: key, dir: on && dir === 1 ? "desc" : "asc" })} className={`inline-flex items-center gap-1 hover:text-slate-900 ${on ? "text-slate-900" : ""}`}>
          {labelText}
          <Arrow className={`w-3 h-3 ${on ? "" : "opacity-40"}`} aria-hidden />
        </Link>
      </th>
    );
  };
  const onboardingDone = purchaseOrderCount > 0 && confirmedInvoiceCount > 0 && containers.some((c) => c.load_count > 0 && Number(c.allocated_base) > 0);

  const hubStep = containersHubStep(
    todos.map((x) => ({ containerId: x.container.id, containerNumber: x.container.container_number, kind: x.kind })),
    invoicesToReview,
    demoContainer ? { id: demoContainer.id, number: demoContainer.container_number } : undefined,
    atRiskAtTerminal.map((c) => ({ id: c.id, number: c.container_number })),
  );

  return (
    <>
      <PageHeader
        title={t("title")}
        subtitle={t("subtitle", { total: containers.length, active: active.length, atRisk: atRisk.length })}
        actions={<NewContainerForm />}
      />
      {error ? <div className="rounded-md border border-red-200 bg-red-50 text-red-700 text-sm px-4 py-3 mb-6">{error === "api" ? tc("apiError") : tc("apiUnreachable")}</div> : null}

      <OnboardingChecklist containers={containers} purchaseOrderCount={purchaseOrderCount} confirmedInvoiceCount={confirmedInvoiceCount} />
      {/* One piece of guidance at a time: the first-steps list while it lasts, the next step after. */}
      {hubStep && onboardingDone ? <WorkflowNextStep step={hubStep} /> : null}

      {containers.length > 0 ? (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4 mb-6">
            <Kpi label={t("kpi.fobActive")} tooltip={tg("fob")} hint={t("kpi.activeCount", { count: active.length })}>
              <Money amount={sumCents(active.map((c) => c.fob_base))} currency={currency} />
            </Kpi>
            <Kpi label={t("kpi.allocated")} tooltip={tg("allocated")} hint={t("kpi.ofActive", { count: active.length })}>
              <Money amount={sumCents(active.map((c) => c.allocated_base))} currency={currency} className="text-blue-700" />
            </Kpi>
            <Kpi
              label={t("kpi.atRisk")}
              tooltip={tg("atRisk")}
              hint={
                nextLfd
                  ? (daysUntil(nextLfd) ?? 0) < 0
                    ? t("kpi.nextLfdOver", { date: dateShort(nextLfd, locale) })
                    : t("kpi.nextLfd", { date: dateShort(nextLfd, locale) })
                  : t("kpi.noneAtRisk")
              }
              tone={atRisk.length ? "warn" : undefined}
            >
              {atRisk.length}
            </Kpi>
            <Kpi label={t("kpi.noCost")} tooltip={tg("noCost")} hint={t("kpi.ofActive", { count: active.length })} tone={noCost.length ? "warn" : undefined}>
              {noCost.length}
            </Kpi>
          </div>

        </>
      ) : null}

      <Card>
        {containers.length > 0 ? (
          <div className="flex flex-wrap items-center gap-3 px-4 py-3 border-b border-slate-200">
            <nav aria-label={t("views.label")} className="flex flex-wrap items-center gap-1 text-sm">
              {VIEWS.map((v) => (
                <Link
                  key={v}
                  href={href({ view: v })}
                  aria-current={v === view ? "true" : undefined}
                  className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${v === view ? "bg-slate-900 text-white font-medium" : "text-slate-600 hover:bg-slate-100"}`}
                >
                  {t(`views.${v}`)}
                  <span className={`tabular-nums text-xs ${v === view ? "text-slate-300" : v === "exposed" && inView[v].length > 0 ? "text-amber-700 font-medium" : "text-slate-500"}`}>{inView[v].length}</span>
                </Link>
              ))}
            </nav>
            <form method="get" role="search" className="relative ml-auto w-full sm:w-64">
              <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" aria-hidden />
              <input name="q" defaultValue={q} placeholder={t("views.search")} aria-label={t("views.search")} className={`${input} pl-9`} />
              {view !== "active" ? <input type="hidden" name="view" value={view} /> : null}
              {sort !== "lfd" ? <input type="hidden" name="sort" value={sort} /> : null}
              {dir === -1 ? <input type="hidden" name="dir" value="desc" /> : null}
            </form>
          </div>
        ) : null}
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50">
              <tr>
                {sortHeader("container", t("th.container"))}
                <th className={th}>{t("th.shipment")}</th>
                <th className={th}>{t("th.milestone")}</th>
                {sortHeader("lfd", t("th.lastFreeDay"))}
                <th className={th}>{t("th.risk")}</th>
                <th className={th}>{t("th.purchaseOrders")}</th>
                {sortHeader("fob", t("th.fob"), true)}
                {sortHeader("allocated", t("th.allocated"), true)}
                <th className={th}>{t("th.costs")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200">
              {containers.length === 0 ? (
                <tr>
                  <td colSpan={9} className="px-5 py-10">
                    <div className="max-w-3xl mx-auto">
                      <p className="text-sm font-semibold text-slate-800 text-center">{t("steps.title")}</p>
                      <ol className="mt-5 grid gap-4 sm:grid-cols-3 text-sm">
                        {(["one", "two", "three"] as const).map((k, i) => (
                          <li key={k} className="rounded-lg border border-slate-200 p-4">
                            <div className="w-6 h-6 rounded-full bg-slate-900 text-white text-xs font-semibold flex items-center justify-center">{i + 1}</div>
                            <div className="mt-3 font-medium text-slate-800">
                              {k === "one" ? <Link href="/imports/new" className="text-blue-600 hover:underline">{t("steps.one")}</Link> : t(`steps.${k}`)}
                            </div>
                            <p className="mt-1 text-xs text-slate-500 leading-relaxed">{t(`steps.${k}Hint`)}</p>
                          </li>
                        ))}
                      </ol>
                      <div className="mt-6 flex flex-col items-center gap-2 text-center">
                        <span className="text-xs uppercase tracking-wider text-slate-500">{t("steps.or")}</span>
                        <div className="flex flex-wrap justify-center gap-2">
                          <LoadSampleDataButton />
                          <LoadSampleDataButton profile="history" />
                        </div>
                        <p className="text-xs text-slate-500 max-w-md">{t("empty.sampleHint")}</p>
                      </div>
                    </div>
                  </td>
                </tr>
              ) : rows.length === 0 ? (
                <tr>
                  <td colSpan={9} className="px-5 py-10 text-center text-sm text-slate-500">
                    {q ? t("views.noMatch", { q }) : t("views.emptyView")}{" "}
                    <Link href="/containers?view=all" className="text-blue-700 hover:underline">{t("views.showAll")}</Link>
                  </td>
                </tr>
              ) : (
                rows.map((c) => (
                  <tr key={c.id} className="hover:bg-slate-50">
                    <td className={td}>
                      <Link href={`/container/${c.id}`} className="font-mono font-medium text-blue-600 hover:underline">{c.container_number}</Link>
                      {c.closed_period ? (
                        <span title={tp("lock", { month: periodTitle(c.closed_period, locale) })} className="ml-1.5 inline-flex align-middle text-slate-500">
                          <LockKeyhole className="w-3.5 h-3.5" aria-hidden />
                          <span className="sr-only">{tp("lock", { month: periodTitle(c.closed_period, locale) })}</span>
                        </span>
                      ) : null}
                    </td>
                    <td className={`${td} text-slate-600`}>{c.shipment_reference ?? tc("empty")}</td>
                    <td className={td}><MilestoneBadge milestone={c.milestone} /></td>
                    <td className={`${td} text-slate-600`}>{dateShort(c.last_free_day, locale)}</td>
                    <td className={td}><RiskBadge risk={c.dnd_risk} days={demurrageRelevant(c) ? daysUntil(c.last_free_day) : null} /></td>
                    <td className={`${td} text-slate-600`}>{c.po_numbers?.length ? c.po_numbers.join(", ") : <span className="text-slate-500">{t("none")}</span>}</td>
                    <td className={tdRight}><Money amount={c.fob_base} currency={currency} /></td>
                    <td className={tdRight}><Money amount={c.allocated_base} currency={currency} className="text-blue-700" /></td>
                    <td className={`${td} text-xs text-slate-500`}>
                      {c.cost_types_present?.length ? c.cost_types_present.map((k) => costType(k)).join(", ") : <span className="text-slate-500">{t("none")}</span>}
                    </td>
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

function Kpi({ label, tooltip, hint, tone, children }: { label: string; tooltip?: string; hint?: string; tone?: "warn"; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-lg border border-slate-200 px-5 py-4">
      <MetricLabel label={label} hint={tooltip} />
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${tone === "warn" ? "text-amber-700" : ""}`}>{children}</div>
      {hint ? <div className="text-xs text-slate-500 mt-0.5">{hint}</div> : null}
    </div>
  );
}

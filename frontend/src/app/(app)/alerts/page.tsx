import type { Metadata } from "next";
import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";
import { Bell } from "lucide-react";
import { Card, PageHeader } from "@/components/layout/AppShell";
import { MarkAllReadButton, MarkReadButton } from "@/features/alerts/AlertActions";
import { alertText } from "@/features/alerts/text";
import { api, type Schemas } from "@/lib/api/client";
import { dateTimeShort } from "@/lib/format";

type Alert = Schemas["AlertResponse"];

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("alerts");
  return { title: t("title") };
}

const SEVERITY_STYLE: Record<Alert["severity"], string> = {
  info: "bg-slate-100 text-slate-700",
  warning: "bg-amber-100 text-amber-800",
  critical: "bg-red-100 text-red-800",
};

export default async function AlertsPage({ searchParams }: { searchParams: Promise<{ all?: string }> }) {
  const [{ all }, t, kind, severity, risk, locale] = await Promise.all([
    searchParams,
    getTranslations("alerts"),
    getTranslations("domain.alertKind"),
    getTranslations("domain.alertSeverity"),
    getTranslations("domain.risk"),
    getLocale(),
  ]);
  const text = (a: Alert) => alertText(a, t as never, (code) => (risk.has(code) ? risk(code as never) : code), locale);
  const unreadOnly = all !== "1";
  const res = await api.GET("/api/v1/alerts", { params: { query: unreadOnly ? { unread: true } : {} } });
  const alerts: Alert[] = res.data ?? [];
  const unread = alerts.filter((a) => !a.read_at).length;

  // The API has no "superseded" flag (needs_other_lane), but every alert about the same container and
  // kind is a fresh read of the same underlying risk — the older one is grouped here client-side so
  // it stops reading as a second, unrelated problem (audit B40: two unread DND_RISK alerts for one
  // container, an "older" one already overtaken by a newer one for the same container and kind).
  const supersededIds = new Set<string>();
  const byContainerKind = new Map<string, Alert[]>();
  for (const a of alerts) {
    if (!a.container_id) continue;
    const key = `${a.container_id}:${a.kind}`;
    const group = byContainerKind.get(key) ?? [];
    group.push(a);
    byContainerKind.set(key, group);
  }
  for (const group of byContainerKind.values()) {
    if (group.length < 2) continue;
    const sorted = [...group].sort((x, y) => y.created_at.localeCompare(x.created_at));
    for (const a of sorted.slice(1)) supersededIds.add(a.id);
  }

  return (
    <>
      <PageHeader
        title={t("title")}
        subtitle={t("subtitle", { unread, total: alerts.length })}
        actions={
          <div className="flex items-center gap-3">
            <Link href={unreadOnly ? "/alerts?all=1" : "/alerts"} className="text-sm text-blue-600 hover:underline">{unreadOnly ? t("showAll") : t("unreadOnly")}</Link>
            {unread > 0 ? <MarkAllReadButton /> : null}
          </div>
        }
      />
      <Card>
        {alerts.length === 0 ? (
          <div className="px-5 py-14 text-center text-slate-500">
            <Bell className="w-10 h-10 mx-auto mb-3 text-slate-300" />
            <p className="text-sm text-slate-600">{t("empty")}</p>
          </div>
        ) : (
          <ul className="divide-y divide-slate-200">
            {alerts.map((a) => {
              const superseded = supersededIds.has(a.id);
              return (
                <li key={a.id} className={`px-5 py-3.5 flex gap-4 ${a.read_at || superseded ? "opacity-60" : ""}`}>
                  <span className={`mt-0.5 shrink-0 inline-flex px-2 py-0.5 rounded text-[11px] font-medium uppercase tracking-wider ${SEVERITY_STYLE[a.severity]}`}>{severity(a.severity)}</span>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-slate-900">
                      {text(a).title}
                      {superseded ? <span className="ml-2 text-xs font-normal text-slate-500">· {t("superseded")}</span> : null}
                    </div>
                    <div className="text-sm text-slate-600">{text(a).body}</div>
                    <div className="text-xs text-slate-500 mt-1 tabular-nums">{kind(a.kind)} · {dateTimeShort(a.created_at, locale)}</div>
                  </div>
                  <div className="flex flex-col items-end gap-1.5 shrink-0">
                    {a.container_id ? <Link href={`/container/${a.container_id}`} className="text-xs text-blue-600 hover:underline whitespace-nowrap">{t("openContainer")}</Link> : null}
                    {!a.read_at ? <MarkReadButton alertId={a.id} /> : null}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </>
  );
}

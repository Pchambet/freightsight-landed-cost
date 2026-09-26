import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";
import { Card } from "@/components/layout/AppShell";
import { auditDiff, auditEntityRef, type Formatters } from "@/features/audit/format";
import { api, type AuditEntry } from "@/lib/api/client";
import { dateShort, dateTimeShort, money, rate } from "@/lib/format";

type Json = Record<string, unknown> | null | undefined;

export async function AuditEntries({ entries, labels }: { entries: AuditEntry[]; labels?: Record<string, string> }) {
  const [t, action, costType, method, scope, milestone, erpDocumentState, fxSource, importKind, locale] = await Promise.all([
    getTranslations("audit"),
    getTranslations("domain.auditAction"),
    getTranslations("domain.costType"),
    getTranslations("domain.method"),
    getTranslations("domain.costScope"),
    getTranslations("domain.milestone"),
    getTranslations("domain.erpDocumentState"),
    getTranslations("domain.fxSource"),
    getTranslations("imports.kind"),
    getLocale(),
  ]);
  // A generic fallback for a field name or enum value this table has no label for yet: never the
  // raw key or the raw enum member.
  const formatters: Formatters = {
    field: (key: string) => (t.has(`fields.${key}`) ? t(`fields.${key}`) : t("otherField")),
    invoiceStatus: (code: string) => (t.has(`invoiceStatus.${code}`) ? t(`invoiceStatus.${code}`) : t("unknownValue")),
    costCount: (count: number) => t("costCount", { count }),
    money: (amount: string) => money(amount, "EUR", locale),
    rate: (amount: string) => rate(amount, locale),
    costType: (code: string) => (costType.has(code as never) ? costType(code as never) : t("unknownValue")),
    method: (code: string) => (method.has(code as never) ? method(code as never) : t("unknownValue")),
    scope: (code: string) => (scope.has(code as never) ? scope(code as never) : t("unknownValue")),
    milestone: (code: string) => (milestone.has(code as never) ? milestone(code as never) : t("unknownValue")),
    bool: (value: boolean) => (value ? t("yes") : t("no")),
    lineRef: (id: string) => t("fields.lineRef", { id }),
    documentState: (code: string) => (erpDocumentState.has(code as never) ? erpDocumentState(code as never) : t("unknownValue")),
    fxSource: (code: string) => (fxSource.has(code as never) ? fxSource(code as never) : t("unknownValue")),
    importKind: (code: string) => (importKind.has(code as never) ? importKind(code as never) : t("unknownValue")),
    date: (iso: string) => dateShort(iso, locale),
    dateTime: (iso: string) => dateTimeShort(iso, locale),
    label: (id: string) => labels?.[id],
  };
  const entityLabel = (type: string) => (t.has(`entity.${type}`) ? t(`entity.${type}`) : type);
  if (entries.length === 0) return <p className="px-5 py-6 text-sm text-slate-500">{t("empty")}</p>;
  return (
    <ul className="divide-y divide-slate-200">
      {entries.map((e) => {
        const key = e.action.replace(".", "_");
        // One field can mean two things: the costs an import wrote, the costs its undo deleted.
        const field = (name: string) => (t.has(`fieldsByAction.${key}.${name}`) ? t(`fieldsByAction.${key}.${name}`) : formatters.field(name));
        const changes = auditDiff(e.before as Json, e.after as Json, { ...formatters, field });
        const ref = auditEntityRef(e.entity_type, e.entity_id, e.before as Json, e.after as Json, entityLabel, formatters);
        return (
          <li key={e.id} className="px-5 py-3">
            <div className="flex items-baseline justify-between gap-3">
              <div className="text-sm font-medium text-slate-900">
                {action.has(key) ? action(key) : e.action}
                <span className="ml-2 text-xs font-normal text-slate-500">{ref}</span>
              </div>
              {/* A member the API can no longer name gets a plain label, never a uuid fragment. */}
              <div className="text-xs text-slate-500 tabular-nums whitespace-nowrap">{dateTimeShort(e.at, locale)} · {e.actor_user_id ? (e.actor_name ?? t("actorUnknown")) : t("system")}</div>
            </div>
            {changes.length ? (
              // A long field name wraps within its share instead of squeezing the value on a phone.
              <dl className="mt-1.5 grid gap-x-4 gap-y-0.5 text-xs" style={{ gridTemplateColumns: "fit-content(45%) 1fr" }}>
                {changes.slice(0, 12).map((c) => (
                  <div key={c.key} className="contents">
                    <dt className="text-slate-500">{c.label}</dt>
                    <dd className="text-slate-700 wrap-anywhere"><span className="text-slate-500 line-through decoration-slate-300">{c.from}</span> → {c.to}</dd>
                  </div>
                ))}
                {changes.length > 12 ? <div className="col-span-2 text-slate-500">…</div> : null}
              </dl>
            ) : <div className="text-xs text-slate-500 mt-0.5">{t("unchanged")}</div>}
          </li>
        );
      })}
    </ul>
  );
}

/** Compact history card for one entity, with a link to the full log. */
export default async function AuditTrail({ entityType, entityId }: { entityType: string; entityId: string }) {
  const t = await getTranslations("audit");
  const res = await api.GET("/api/v1/audit-log", { params: { query: { entity_type: entityType, entity_id: entityId, limit: 10 } } });
  const entries = res.data?.entries ?? [];
  const labels = res.data?.labels ?? {};
  return (
    <Card title={t("cardTitle")} right={<Link href={`/audit?entity_type=${entityType}&entity_id=${entityId}`} className="text-xs text-blue-600 hover:underline">{t("seeAll")}</Link>}>
      <AuditEntries entries={entries} labels={labels} />
    </Card>
  );
}

import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";
import { Card } from "@/components/layout/AppShell";
import { auditDiff, auditEntityRef, type Formatters } from "@/features/audit/format";
import { api, type AuditEntry } from "@/lib/api/client";
import { periodTitle } from "@/features/periods/format";
import { formatInvoiceNote } from "@/features/invoices/lineNotes";
import { dateShort, dateTimeShort, decimalDisplay, money, qty, rate } from "@/lib/format";

type Json = Record<string, unknown> | null | undefined;

/** Amounts in the organization's own currency, whatever the record. */
const BASE_AMOUNTS = new Set(["amount_base", "fob", "landed", "acknowledged_drift"]);
/** Amounts in the record's own currency (a cost's, an invoice's, the credit on a cost): its `currency`. */
const OWN_AMOUNTS = new Set(["amount", "credited", "total_amount"]);
const PLAIN_DECIMAL = /^-?\d+(\.\d+)?$/;

/**
 * Every amount of a record written with the currency it is in. The history used to print each one in
 * euros, so a credit on a cost invoiced in dollars read as euros. A record that does not say its
 * currency gets the figure without a symbol rather than a guessed one.
 */
function priced(record: Json, base: string, locale: string): Json {
  if (!record) return record;
  const own = typeof record.currency === "string" && record.currency ? record.currency : null;
  const out: Record<string, unknown> = { ...record };
  for (const [key, value] of Object.entries(record)) {
    if ((typeof value !== "string" && typeof value !== "number") || !PLAIN_DECIMAL.test(String(value))) continue;
    if (BASE_AMOUNTS.has(key)) out[key] = money(String(value), base, locale);
    else if (OWN_AMOUNTS.has(key)) out[key] = own ? money(String(value), own, locale) : decimalDisplay(String(value), locale);
  }
  return out;
}

export async function AuditEntries({ entries, labels }: { entries: AuditEntry[]; labels?: Record<string, string> }) {
  const [t, action, costType, method, scope, milestone, erpDocumentState, fxSource, importKind, noteT, locale, org] = await Promise.all([
    getTranslations("audit"),
    getTranslations("domain.auditAction"),
    getTranslations("domain.costType"),
    getTranslations("domain.method"),
    getTranslations("domain.costScope"),
    getTranslations("domain.milestone"),
    getTranslations("domain.erpDocumentState"),
    getTranslations("domain.fxSource"),
    getTranslations("imports.kind"),
    getTranslations("invoices.lineNotes"),
    getLocale(),
    api.GET("/api/v1/organization"),
  ]);
  const base = org.data?.base_currency ?? null;
  // A generic fallback for a field name or enum value this table has no label for yet: never the
  // raw key or the raw enum member.
  const formatters: Formatters = {
    field: (key: string) => (t.has(`fields.${key}`) ? t(`fields.${key}`) : t("otherField")),
    invoiceStatus: (code: string) => (t.has(`invoiceStatus.${code}`) ? t(`invoiceStatus.${code}`) : t("unknownValue")),
    costCount: (count: number) => t("costCount", { count }),
    creditCount: (count: number) => t("creditCount", { count }),
    // Amounts arrive already written in their currency (`priced`); a bare figure keeps no symbol.
    money: (amount: string) => (PLAIN_DECIMAL.test(amount) ? decimalDisplay(amount, locale) : amount),
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
    month: (key: string) => periodTitle(key, locale, true),
    decimal: (raw: string) => decimalDisplay(raw, locale),
    quantity: (raw: string) => qty(raw, locale),
    // A forced duplicate names its own currency; the organization's is only the fallback.
    invoiceNote: (note: string) => formatInvoiceNote(note, (k, v) => noteT(k as never, v as never), (code) => formatters.costType(code), locale, base ?? ""),
    itemCount: (count: number) => t("itemCount", { count }),
    pair: (name: string, value: string) => t("pair", { name, value }),
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
        const [before, after] = base ? [priced(e.before as Json, base, locale), priced(e.after as Json, base, locale)] : [e.before as Json, e.after as Json];
        const changes = auditDiff(before, after, { ...formatters, field });
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

import type { Metadata } from "next";
import Form from "next/form";
import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { Card, PageHeader, btnSecondary, input, label } from "@/components/layout/AppShell";
import { AuditEntries } from "@/features/audit/AuditTrail";
import { api } from "@/lib/api/client";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("audit");
  return { title: t("title") };
}

/** Every action the API writes, grouped as a person looks for them: costs, invoices, files, boxes, ERP, links, months, company. */
const ACTIONS = [
  "cost.created", "cost.updated", "cost.superseded", "cost.closed", "cost.deleted",
  "invoice.confirmed", "invoice.corrected", "invoice.rejected", "invoice.reopened",
  "import.committed", "import.undone",
  "container.loads_replaced", "container.tracking_event_added",
  "erp.landed_cost_pushed", "erp.push_forgotten",
  "container.shared", "purchase_order.shared", "audit.shared", "share.revoked",
  "period.closed", "period.reopened", "period.drift_acknowledged",
  "organization.updated", "organization.sample_data_deleted",
];

export default async function AuditPage({ searchParams }: { searchParams: Promise<{ entity_type?: string; entity_id?: string; action?: string; cursor?: string }> }) {
  const [sp, t, actionLabel] = await Promise.all([searchParams, getTranslations("audit"), getTranslations("domain.auditAction")]);
  // An empty field of the filter form means every action, not an action named "".
  const query = { entity_type: sp.entity_type, entity_id: sp.entity_id, action: sp.action || undefined, cursor: sp.cursor, limit: 50 };
  const res = await api.GET("/api/v1/audit-log", { params: { query } });
  const entries = res.data?.entries ?? [];
  const next = res.data?.next_cursor ?? null;
  const link = (patch: Record<string, string | undefined>) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries({ ...sp, ...patch })) if (v) q.set(k, v);
    return `/audit${q.toString() ? `?${q}` : ""}`;
  };

  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      {/* Twenty-four actions are a list to pick from, not a row of chips; the form works without script. */}
      <Form action="/audit" className="mb-3 flex flex-wrap items-end gap-2">
        {sp.entity_type ? <input type="hidden" name="entity_type" value={sp.entity_type} /> : null}
        {sp.entity_id ? <input type="hidden" name="entity_id" value={sp.entity_id} /> : null}
        <div className="w-full max-w-xs">
          <label htmlFor="audit-action" className={label}>{t("filterLabel")}</label>
          <select id="audit-action" name="action" defaultValue={sp.action ?? ""} key={sp.action ?? ""} className={input}>
            <option value="">{t("filterAll")}</option>
            {ACTIONS.map((a) => <option key={a} value={a}>{actionLabel(a.replaceAll(".", "_"))}</option>)}
          </select>
        </div>
        <button type="submit" className={btnSecondary}>{t("filterApply")}</button>
      </Form>
      <Card>
        <AuditEntries entries={entries} labels={res.data?.labels ?? {}} />
        {next ? <div className="px-5 py-3 border-t border-slate-200"><Link href={link({ cursor: next })} className="text-sm text-blue-600 hover:underline">{t("next")}</Link></div> : null}
      </Card>
    </>
  );
}

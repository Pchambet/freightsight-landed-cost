type Json = Record<string, unknown> | null | undefined;

export type Formatters = {
  field: (key: string) => string;
  invoiceStatus: (code: string) => string;
  costCount: (count: number) => string;
  money: (amount: string) => string;
  rate: (amount: string) => string;
  costType: (code: string) => string;
  method: (code: string) => string;
  scope: (code: string) => string;
  milestone: (code: string) => string;
  bool: (value: boolean) => string;
  lineRef: (id: string) => string;
  documentState: (code: string) => string;
  fxSource: (code: string) => string;
  importKind: (code: string) => string;
  date: (iso: string) => string;
  dateTime: (iso: string) => string;
  /** What an id is called on the other screens (container number, order number, "order · SKU"), when the API knows. */
  label: (id: string) => string | undefined;
};

const DATE_KEYS = new Set(["fx_date", "cost_date"]);
const DATE_TIME_KEYS = new Set(["closed_at", "created_at", "occurred_at", "forgotten_at"]);

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** First 8 hex chars of a uuid, the way the rest of the app already shortens ids for display. */
function shortId(v: string): string {
  return `${v.slice(0, 8)}…`;
}

function parseValue(v: unknown): unknown {
  if (typeof v !== "string") return v;
  const t = v.trim();
  if ((t.startsWith("[") && t.endsWith("]")) || (t.startsWith("{") && t.endsWith("}"))) {
    try {
      return JSON.parse(t);
    } catch {
      return v;
    }
  }
  return v;
}

/**
 * A record's before/after is whatever the domain model held — raw enum members, decimal strings,
 * uuids, booleans. This is the one place that turns that back into what a DAF (or an auditor) reads:
 * every enum through its existing `domain.*` translation, every amount and fx rate through the same
 * locale-aware formatters as the rest of the screen, every id shortened rather than shown in full.
 * This table used to be the one screen where the raw model leaked straight through.
 */
function formatValue(key: string, v: unknown, f: Formatters): string {
  const val = parseValue(v);
  if (val === null || val === undefined || val === "") return "—";
  if (typeof val === "boolean") return f.bool(val);
  if (key === "status" && typeof val === "string") return f.invoiceStatus(val);
  // The costs a confirmation or an import wrote, a reopening or an undo deleted: counted, never listed.
  if ((key === "cost_ids" || key === "costs_deleted") && Array.isArray(val)) return f.costCount(val.length);
  if (key === "kind" && typeof val === "string") return f.importKind(val);
  if ((key === "total_amount" || key === "amount" || key === "amount_base") && (typeof val === "string" || typeof val === "number")) return f.money(String(val));
  if (key === "fx_rate" && (typeof val === "string" || typeof val === "number")) return f.rate(String(val));
  if (key === "cost_type" && typeof val === "string") return f.costType(val);
  if (key === "allocation_method" && typeof val === "string") return f.method(val);
  if (key === "scope" && typeof val === "string") return f.scope(val);
  if (key === "milestone" && typeof val === "string") return f.milestone(val);
  if (key === "document_state" && typeof val === "string") return f.documentState(val);
  if (key === "fx_source" && typeof val === "string") return f.fxSource(val);
  if (DATE_KEYS.has(key) && typeof val === "string") return f.date(val);
  if (DATE_TIME_KEYS.has(key) && typeof val === "string") return f.dateTime(val);
  if (typeof val === "string" && UUID_RE.test(val)) return f.label(val) ?? shortId(val);
  // An empty list or object is nothing to show, not "[]"; a list of plain values reads as one.
  if (Array.isArray(val)) return val.length === 0 ? "—" : val.every((x) => x === null || typeof x !== "object") ? val.join(", ") : JSON.stringify(val);
  if (typeof val === "object") return Object.keys(val as object).length === 0 ? "—" : JSON.stringify(val);
  return String(val);
}

/** Keys whose value differs between before and after, with human-readable labels and values. */
export function auditDiff(before: Json, after: Json, f: Formatters): { key: string; label: string; from: string; to: string }[] {
  const b = (before ?? {}) as Record<string, unknown>;
  const a = (after ?? {}) as Record<string, unknown>;
  const keys = Array.from(new Set([...Object.keys(b), ...Object.keys(a)])).sort();
  return keys
    .filter((k) => JSON.stringify(b[k]) !== JSON.stringify(a[k]))
    .map((k) => ({
      key: k,
      // container_loads_replaced logs one entry per po_line_id, so the "field" is itself a uuid:
      // the page's `labels` names it "order · SKU" while the line still exists.
      label: UUID_RE.test(k) ? f.lineRef(f.label(k) ?? shortId(k)) : f.field(k),
      from: formatValue(k, b[k], f),
      to: formatValue(k, a[k], f),
    }))
    // Nothing to nothing (absent → empty list) is not a change a reader needs to see.
    .filter((c) => c.from !== c.to);
}

export function auditEntityRef(
  entityType: string,
  entityId: string | null | undefined,
  before: Json,
  after: Json,
  entityLabel: (type: string) => string,
  f: Pick<Formatters, "label" | "costType">,
): string {
  const payload = { ...(before ?? {}), ...(after ?? {}) } as Record<string, unknown>;
  if (entityType === "period" && typeof payload.period === "string") return `${entityLabel(entityType)} ${payload.period}`;
  if (entityType === "invoice") {
    const num = payload.invoice_number;
    if (typeof num === "string" && num) return `${entityLabel(entityType)} ${num}`;
  }
  // A cost has no number of its own: what names it is its type, which the payload still carries
  // after the cost itself is gone.
  if (entityType === "cost" && typeof payload.cost_type === "string") return `${entityLabel(entityType)} · ${f.costType(payload.cost_type)}`;
  const known = entityId ? f.label(entityId) : undefined;
  return known ? `${entityLabel(entityType)} ${known}` : entityLabel(entityType);
}

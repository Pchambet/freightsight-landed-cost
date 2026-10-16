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
  /** A calendar month key ("2026-09"), as words. */
  month: (key: string) => string;
  /** A plain decimal ("1.12") with the locale's separator. */
  decimal: (raw: string) => string;
  /** A quantity ("120.0000") the way every other screen shows one: "120". */
  quantity: (raw: string) => string;
  /** An invoice note code ("@forced_duplicate|…") as the sentence the review screen shows. */
  invoiceNote: (note: string) => string;
  /** A list of records, counted: "3 éléments". */
  itemCount: (count: number) => string;
  /** One field of a record inside a value: "Conteneurs : 2" in French, "Containers: 2" in English. */
  pair: (label: string, value: string) => string;
  /** What an id is called on the other screens (container number, order number, "order · SKU"), when the API knows. */
  label: (id: string) => string | undefined;
};

const DATE_KEYS = new Set(["fx_date", "cost_date", "invoice_date", "last_free_day"]);
const DATE_TIME_KEYS = new Set(["closed_at", "created_at", "occurred_at", "forgotten_at", "expires_at"]);
const MONEY_KEYS = new Set(["total_amount", "amount", "amount_base", "fob", "landed", "acknowledged_drift"]);
/** Ids that name nothing a person can open (a share link's row, a push's row): a hash on screen, never information. */
const HIDDEN_KEYS = new Set(["share_id", "push_id"]);
/** Settings are one JSON column; each one that moved is a change of its own, not a blob. */
const NESTED_KEYS = new Set(["settings"]);
/** Text a person typed or a system named (an invoice "2026.0412", a vendor, a note, a file "2026-09-30"): shown
 * as written, never re-read as a decimal or a date — "2026,0412" would be another invoice. */
const VERBATIM_KEYS = new Set(["invoice_number", "vendor", "odoo_name", "note", "close_reason", "forgotten_reason", "code", "name", "voyage", "vessel_name", "location_name", "file_name"]);

const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const ISO_DATE_TIME_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;
const DATE_RANGE_RE = /^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$/;
const DECIMAL_RE = /^-?\d+\.\d+$/;

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
  // What a person overruled to confirm an invoice: whole sentences, one after the other.
  if (key === "notes" && Array.isArray(val) && val.length > 0 && val.every((n) => typeof n === "string")) return val.map((n) => f.invoiceNote(n)).join(" ");
  if (key === "quantity" && (typeof val === "string" || typeof val === "number")) return f.quantity(String(val));
  if (MONEY_KEYS.has(key) && (typeof val === "string" || typeof val === "number")) return f.money(String(val));
  if (key === "fx_rate" && (typeof val === "string" || typeof val === "number")) return f.rate(String(val));
  if (key === "cost_type" && typeof val === "string") return f.costType(val);
  if (key === "allocation_method" && typeof val === "string") return f.method(val);
  if (key === "scope" && typeof val === "string") return f.scope(val);
  if (key === "milestone" && typeof val === "string") return f.milestone(val);
  if (key === "document_state" && typeof val === "string") return f.documentState(val);
  if (key === "fx_source" && typeof val === "string") return f.fxSource(val);
  if (key === "period" && typeof val === "string") return f.month(val);
  if (DATE_KEYS.has(key) && typeof val === "string") return f.date(val);
  if (DATE_TIME_KEYS.has(key) && typeof val === "string") return f.dateTime(val);
  if (typeof val === "string" && UUID_RE.test(val)) return f.label(val) ?? shortId(val);
  // Whatever key carries them, an ISO stamp, a "from..to" period or a decimal is never shown raw.
  if (typeof val === "string" && !VERBATIM_KEYS.has(key)) {
    const range = DATE_RANGE_RE.exec(val);
    if (range) return `${f.date(range[1])} – ${f.date(range[2])}`;
    if (ISO_DATE_TIME_RE.test(val)) return f.dateTime(val);
    if (ISO_DATE_RE.test(val)) return f.date(val);
    if (DECIMAL_RE.test(val)) return f.decimal(val);
  }
  // An empty list or object is nothing to show, not "[]"; a list of plain values reads as one, a list
  // of records is counted, a record reads as its labelled fields — never as JSON.
  if (Array.isArray(val)) return val.length === 0 ? "—" : val.every((x) => x === null || typeof x !== "object") ? val.map((x) => formatValue(key, x, f)).join(", ") : f.itemCount(val.length);
  if (typeof val === "object") {
    const entries = Object.entries(val as Record<string, unknown>);
    return entries.length === 0 ? "—" : entries.map(([k, v]) => f.pair(f.field(k), formatValue(k, v, f))).join(", ");
  }
  return String(val);
}

/** `settings: {...}` before and after become one entry per setting, the others dropped. */
function flatten(record: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(record)) {
    if (HIDDEN_KEYS.has(k)) continue;
    const parsed = NESTED_KEYS.has(k) ? parseValue(v) : v;
    if (NESTED_KEYS.has(k) && parsed && typeof parsed === "object" && !Array.isArray(parsed)) Object.assign(out, parsed);
    else out[k] = v;
  }
  return out;
}

/** Keys whose value differs between before and after, with human-readable labels and values. */
export function auditDiff(before: Json, after: Json, f: Formatters): { key: string; label: string; from: string; to: string }[] {
  const b = flatten((before ?? {}) as Record<string, unknown>);
  const a = flatten((after ?? {}) as Record<string, unknown>);
  const keys = Array.from(new Set([...Object.keys(b), ...Object.keys(a)])).sort();
  return keys
    .filter((k) => JSON.stringify(b[k]) !== JSON.stringify(a[k]))
    .map((k) => ({
      key: k,
      // container_loads_replaced logs one entry per po_line_id, so the "field" is itself a uuid:
      // the page's `labels` names it "order · SKU" while the line still exists.
      label: UUID_RE.test(k) ? f.lineRef(f.label(k) ?? shortId(k)) : f.field(k),
      // ...and its value is the quantity loaded from that line.
      from: formatValue(UUID_RE.test(k) ? "quantity" : k, b[k], f),
      to: formatValue(UUID_RE.test(k) ? "quantity" : k, a[k], f),
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
  f: Pick<Formatters, "label" | "costType" | "date" | "month">,
): string {
  const payload = { ...(before ?? {}), ...(after ?? {}) } as Record<string, unknown>;
  if (entityType === "period" && typeof payload.period === "string") return `${entityLabel(entityType)} ${f.month(payload.period)}`;
  // A shared audit is named by its period, which its share entry carries as "from..to".
  const range = entityType === "audit" && typeof payload.label === "string" ? DATE_RANGE_RE.exec(payload.label) : null;
  if (range) return `${entityLabel(entityType)} ${f.date(range[1])} – ${f.date(range[2])}`;
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

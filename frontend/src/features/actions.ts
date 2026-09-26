"use server";

import { revalidatePath } from "next/cache";
import { getLocale, getTranslations } from "next-intl/server";
import { monthTitle } from "@/features/overview/period";
import { api, problemMessage, type ImportJobResponse, type LandedCostReport, type Problem, type Schemas } from "@/lib/api/client";
import { protectAction } from "@/lib/auth";
import type { AllocationMethod, ContainerMilestone, CostScope, CostStatus, CostType, ImportKind, MilestoneCode, RateBasis } from "@/lib/domain";

export type ActionResult<T> = { ok: true; data: T } | { ok: false; error: string };

/** `fallbackKey` is a message key: the API's own English sentence is only a last resort. */
async function fail<T>(error: unknown, fallbackKey: string): Promise<ActionResult<T>> {
  const t = await getTranslations();
  return { ok: false, error: await problemMessage(error, t(fallbackKey)) };
}

/**
 * A POST call typed loosely enough to reach a route or a request field the generated client does
 * not carry yet (needs_other_lane: the lead regenerates `schema.d.ts` from the OpenAPI document).
 * `api.POST` itself stays fully typed for every route that already exists in it; this is only for
 * the few calls below that a route or field ahead of the regeneration needs, cast once here rather
 * than scattering `as never` through each call site. The double assertion is deliberate — the real
 * type and this one do not structurally overlap — and the result is cast back to the known real
 * response shape right after the call, so callers see ordinary, correct types.
 */
type RawPost = (path: string, options: { params: { path: Record<string, string> }; body?: unknown }) => Promise<{ data?: unknown; error?: unknown }>;
const rawPost = api.POST as unknown as RawPost;

function normalizeAmount(raw: string): string | null {
  const s = raw.trim().replace(/[\s  ]/g, "");
  if (!s) return null;
  let n = s;
  if (n.includes(",") && n.includes(".")) n = n.lastIndexOf(",") > n.lastIndexOf(".") ? n.replace(/\./g, "").replace(",", ".") : n.replace(/,/g, "");
  else if (n.includes(",")) {
    const [head, tail] = [n.slice(0, n.lastIndexOf(",")), n.slice(n.lastIndexOf(",") + 1)];
    n = tail.length <= 2 ? `${head}.${tail}` : n.replace(/,/g, "");
  }
  return /^\d+(\.\d{1,4})?$/.test(n) ? n : null;
}

// ---------------------------------------------------------------------------- costs

export async function addCost(input: {
  scope: CostScope;
  target_id: string;
  cost_type: string;
  amount: string;
  currency: string;
  cost_date: string;
  allocation_method?: string;
  fx_rate?: string;
  vendor?: string;
  invoice_number?: string;
  status?: CostStatus;
  revalidate: string;
}): Promise<ActionResult<{ id: string }>> {
  // Every other action had it: without it a cost typed in a tab left on another organization was
  // written into whichever organization the session cookie held (N2).
  await protectAction();
  const amount = normalizeAmount(input.amount);
  if (amount === null) {
    const t = await getTranslations("costs.form");
    return { ok: false, error: t("amountInvalid") };
  }
  const fx = input.fx_rate ? normalizeAmount(input.fx_rate) : null;
  const { data, error } = await api.POST("/api/v1/costs", {
    body: {
      scope: input.scope,
      target_id: input.target_id,
      cost_type: input.cost_type as CostType,
      status: input.status ?? "ACTUAL",
      amount,
      currency: input.currency,
      cost_date: input.cost_date,
      allocation_method: (input.allocation_method || null) as AllocationMethod | null,
      fx_rate: fx,
      vendor: input.vendor || null,
      invoice_number: input.invoice_number || null,
    },
  });
  if (error || !data) return await fail(error, "costs.form.failed");
  revalidatePath(input.revalidate);
  return { ok: true, data: { id: data.id } };
}

export async function deleteCost(costId: string, revalidate: string): Promise<ActionResult<null>> {
  await protectAction();
  const { error } = await api.DELETE("/api/v1/costs/{cost_id}", { params: { path: { cost_id: costId } } });
  if (error) return await fail(error, "costs.ledger.deleteFailed");
  revalidatePath(revalidate);
  return { ok: true, data: null };
}

export async function previewAllocation(input: {
  container_id?: string;
  po_id?: string;
  method_overrides: Record<string, string>;
}): Promise<ActionResult<LandedCostReport>> {
  await protectAction();
  const { data, error } = await api.POST("/api/v1/landed-costs/preview", {
    body: {
      container_id: input.container_id ?? null,
      po_id: input.po_id ?? null,
      method_overrides: input.method_overrides as Record<string, AllocationMethod>,
    },
  });
  if (error || !data) return await fail(error, "allocation.previewFailed");
  return { ok: true, data };
}

// ---------------------------------------------------------------------------- containers

export async function updateContainer(
  containerId: string,
  patch: { milestone?: ContainerMilestone; free_days_demurrage?: number | null; discharged_at?: string | null; iso_type?: string | null },
): Promise<ActionResult<null>> {
  await protectAction();
  const { error } = await api.PATCH("/api/v1/containers/{container_id}", {
    params: { path: { container_id: containerId } },
    body: patch,
  });
  if (error) return await fail(error, "tracking.failed");
  revalidatePath(`/container/${containerId}`);
  revalidatePath("/containers");
  return { ok: true, data: null };
}

export async function addTrackingEvent(
  containerId: string,
  event: {
    code: MilestoneCode;
    occurred_at: string;
    is_estimate: boolean;
    location_unlocode?: string | null;
    location_name?: string | null;
    vessel_name?: string | null;
    voyage?: string | null;
    note?: string | null;
  },
): Promise<ActionResult<null>> {
  await protectAction();
  const { error } = await api.POST("/api/v1/containers/{container_id}/events", {
    params: { path: { container_id: containerId } },
    body: event,
  });
  if (error) return await fail(error, "timeline.failed");
  revalidatePath(`/container/${containerId}`);
  revalidatePath("/containers");
  return { ok: true, data: null };
}

export async function markAlertRead(alertId: string): Promise<ActionResult<null>> {
  await protectAction();
  const { error } = await api.POST("/api/v1/alerts/{alert_id}/read", { params: { path: { alert_id: alertId } } });
  if (error) return await fail(error, "alerts.failed");
  revalidatePath("/alerts");
  revalidatePath("/", "layout");
  return { ok: true, data: null };
}

export async function markAllAlertsRead(): Promise<ActionResult<{ marked: number }>> {
  await protectAction();
  const { data, error } = await api.POST("/api/v1/alerts/read-all");
  if (error || !data) return await fail(error, "alerts.failed");
  revalidatePath("/alerts");
  revalidatePath("/", "layout");
  return { ok: true, data: { marked: data.marked } };
}

// ---------------------------------------------------------------------------- invoices

/**
 * An invoice opened on the file — or, when the very same file was already received (the API refuses
 * a re-upload on the content and names the document), `duplicateOf` is the invoice it opened then, so
 * the screen can go straight to it rather than stop at an error.
 */
export type UploadResult = { ok: true; data: { id: string } } | { ok: false; error: string; duplicateOf?: string };

export async function uploadInvoice(formData: FormData): Promise<UploadResult> {
  await protectAction();
  const file = formData.get("file");
  if (!(file instanceof File) || file.size === 0) return await fail(null, "invoices.upload.failed");
  const defaultContainer = formData.get("default_container_id");
  const { data, error, response } = await api.POST("/api/v1/invoices", {
    body: { file: file as unknown as string },
    bodySerializer: (b) => {
      const fd = new FormData();
      fd.append("file", b.file as unknown as File, file.name);
      // Lines on which the reading finds neither a container nor an order are proposed on this one.
      if (typeof defaultContainer === "string" && defaultContainer) fd.append("default_container_id", defaultContainer);
      return fd;
    },
  });
  if (response.status === 409) {
    const documentId = (error as (Problem & { document_id?: string }) | undefined)?.document_id;
    const existing = documentId ? (await api.GET("/api/v1/invoices")).data?.find((i) => i.document_id === documentId) : undefined;
    const t = await getTranslations();
    return { ok: false, error: await problemMessage(error, t("invoices.upload.duplicate")), duplicateOf: existing?.id };
  }
  if (error || !data) return await fail(error, "invoices.upload.failed");
  revalidatePath("/invoices");
  return { ok: true, data: { id: data.id } };
}

/**
 * The header the reader took, corrected by the person looking at the PDF — a date read as the number,
 * a forwarder not recognised. The number and the forwarder are what every guard against counting one
 * invoice twice compares, so this is the way out of a refusal that cannot be forced when the reading
 * was wrong. Only the fields that changed are sent; an emptied field is cleared.
 */
export async function correctInvoiceHeader(
  invoiceId: string,
  patch: { vendor?: string; invoice_number?: string; invoice_date?: string },
): Promise<ActionResult<null>> {
  await protectAction();
  const { error } = await api.PATCH("/api/v1/invoices/{invoice_id}", {
    params: { path: { invoice_id: invoiceId } },
    body: patch,
  });
  if (error) return await fail(error, "invoices.header.failed");
  revalidatePath(`/invoices/${invoiceId}`);
  revalidatePath("/invoices");
  return { ok: true, data: null };
}

export async function updateInvoiceLine(
  invoiceId: string,
  lineId: string,
  patch: { amount?: string | null; currency?: string | null; cost_type?: CostType | null; scope?: CostScope | null; target_id?: string | null; accepted?: boolean | null; description?: string | null },
): Promise<ActionResult<null>> {
  await protectAction();
  const body = { ...patch };
  if (typeof body.amount === "string") {
    const n = normalizeAmount(body.amount);
    if (!n) return await fail(null, "invoices.line.failed");
    body.amount = n;
  }
  const { error } = await api.PATCH("/api/v1/invoices/{invoice_id}/lines/{line_id}", {
    params: { path: { invoice_id: invoiceId, line_id: lineId } },
    body,
  });
  if (error) return await fail(error, "invoices.line.failed");
  revalidatePath(`/invoices/${invoiceId}`);
  return { ok: true, data: null };
}

/**
 * One forwarder invoice is nearly always about one container or one shipment, and most of its lines
 * are read right: attaching and accepting them one by one is the slowest part of a review. The patches run side by side on the server and the
 * page is revalidated once; a line that fails does not undo the others, the count says how many held.
 */
export async function patchInvoiceLines(invoiceId: string, lineIds: string[], patch: { scope: CostScope; target_id: string } | { accepted: boolean }): Promise<ActionResult<{ updated: number }>> {
  await protectAction();
  const results = await Promise.all(
    lineIds.slice(0, 200).map((lineId) =>
      api.PATCH("/api/v1/invoices/{invoice_id}/lines/{line_id}", { params: { path: { invoice_id: invoiceId, line_id: lineId } }, body: patch }),
    ),
  );
  revalidatePath(`/invoices/${invoiceId}`);
  const failed = results.find((r) => r.error);
  const updated = results.filter((r) => !r.error).length;
  if (failed && updated === 0) return await fail(failed.error, "invoices.line.failed");
  return { ok: true, data: { updated } };
}

export type ConfirmInvoiceResult =
  | { ok: true; data: { costsCreated: number; containers: { id: string; container_number: string }[]; notes: string[] } }
  // `forceable` answers INVOICE_ALREADY_RECORDED only: false when the API says the override cannot
  // hold (the very same vendor, number, type, currency and target are already on file — a vendor
  // does not reuse an invoice number), so the screen does not offer it.
  | { ok: false; error: string; code?: string; forceable?: boolean };

/**
 * `force` answers the DUPLICATE_COST refusal, and only that one: the caller has looked at the
 * duplicate note on the offending line and decided this invoice is a different charge. The action
 * hands the code back on failure (not just the translated sentence) so the review screen can tell a
 * duplicate refusal — offer "confirm anyway" — apart from every other reason confirming can fail.
 */
/**
 * `force` goes past a DUPLICATE_COST (the same charge already on the freight); `forceRecorded` past an
 * INVOICE_ALREADY_RECORDED (the same invoice number already carries a real cost), for when the person
 * knows it is another invoice — a number misread, two documents sharing one. Two separate doors: one
 * never opens the other.
 */
export async function confirmInvoice(invoiceId: string, force = false, forceRecorded = false): Promise<ConfirmInvoiceResult> {
  await protectAction();
  // The route only gained this optional body for the force-confirm case; the generated schema does
  // not carry it yet (needs_other_lane), hence `rawPost` — the response is the same
  // `InvoiceConfirmResponse` either way, cast back below.
  const raw = await rawPost("/api/v1/invoices/{invoice_id}/confirm", {
    params: { path: { invoice_id: invoiceId } },
    ...(force || forceRecorded ? { body: { ...(force ? { force: true } : {}), ...(forceRecorded ? { force_recorded: true } : {}) } } : {}),
  });
  const { data, error } = raw as { data?: Schemas["InvoiceConfirmResponse"]; error?: Problem | null };
  if (error || !data) {
    const t = await getTranslations();
    const code = error && typeof error === "object" ? (error as { code?: string | null }).code ?? undefined : undefined;
    const message = await problemMessage(error, t("invoices.detail.failed"));
    // A top-level boolean on the problem, like `existing_id`. An API that does not say keeps the
    // override, as before it said.
    const forceable = (error as (Problem & { forceable?: boolean }) | null | undefined)?.forceable !== false;
    return { ok: false, error: message, code: code ?? undefined, forceable };
  }
  revalidatePath(`/invoices/${invoiceId}`);
  revalidatePath("/invoices");
  revalidatePath("/containers");
  for (const c of data.containers ?? []) revalidatePath(`/container/${c.id}`);
  return {
    ok: true,
    data: {
      costsCreated: data.costs_created,
      containers: (data.containers ?? []).map((c) => ({ id: c.id, container_number: c.container_number })),
      notes: data.notes ?? [],
    },
  };
}

/**
 * `POST /invoices/{id}/lines` — a line the reviewer types in by hand, for a charge the extraction
 * missed entirely (see the "il manque X €" hint on the review screen). Not yet in the generated
 * schema (needs_other_lane), so the path and body are passed as the OpenAPI document will type them
 * once regenerated; `params.path` still drives the real URL at runtime.
 */
export async function addInvoiceLine(
  invoiceId: string,
  input: { description?: string; amount: string; currency?: string; cost_type?: CostType; scope?: CostScope; target_id?: string },
): Promise<ActionResult<null>> {
  await protectAction();
  const amount = normalizeAmount(input.amount);
  if (amount === null) {
    const t = await getTranslations("costs.form");
    return { ok: false, error: t("amountInvalid") };
  }
  const raw = await rawPost("/api/v1/invoices/{invoice_id}/lines", {
    params: { path: { invoice_id: invoiceId } },
    body: {
      description: input.description || null,
      amount,
      currency: input.currency || undefined,
      cost_type: input.cost_type || null,
      scope: input.scope || null,
      target_id: input.target_id || null,
    },
  });
  const { error } = raw as { error?: Problem | null };
  if (error) return await fail(error, "invoices.detail.addLineFailed");
  revalidatePath(`/invoices/${invoiceId}`);
  return { ok: true, data: null };
}

export async function rejectInvoice(invoiceId: string): Promise<ActionResult<null>> {
  await protectAction();
  const { error } = await api.POST("/api/v1/invoices/{invoice_id}/reject", { params: { path: { invoice_id: invoiceId } } });
  if (error) return await fail(error, "invoices.detail.failed");
  revalidatePath(`/invoices/${invoiceId}`);
  revalidatePath("/invoices");
  return { ok: true, data: null };
}

export async function retryInvoice(invoiceId: string): Promise<ActionResult<null>> {
  await protectAction();
  const { error } = await api.POST("/api/v1/invoices/{invoice_id}/retry", { params: { path: { invoice_id: invoiceId } } });
  if (error) return await fail(error, "invoices.detail.failed");
  revalidatePath(`/invoices/${invoiceId}`);
  revalidatePath("/invoices");
  return { ok: true, data: null };
}

export async function reopenInvoice(invoiceId: string): Promise<ActionResult<null>> {
  await protectAction();
  const { error } = await api.POST("/api/v1/invoices/{invoice_id}/reopen", { params: { path: { invoice_id: invoiceId } } });
  if (error) return await fail(error, "invoices.detail.failed");
  revalidatePath(`/invoices/${invoiceId}`);
  revalidatePath("/invoices");
  return { ok: true, data: null };
}

// ---------------------------------------------------------------------------- estimates

export async function applyEstimates(containerId: string): Promise<ActionResult<{ created: number; skipped: Record<string, string> }>> {
  await protectAction();
  const { data, error } = await api.POST("/api/v1/containers/{container_id}/estimates", { params: { path: { container_id: containerId } } });
  if (error || !data) return await fail(error, "estimates.failed");
  revalidatePath(`/container/${containerId}`);
  revalidatePath("/containers");
  return { ok: true, data: { created: data.created.length, skipped: data.skipped } };
}

export async function closeEstimate(costId: string, reason: string, revalidate: string): Promise<ActionResult<null>> {
  await protectAction();
  const { error } = await api.POST("/api/v1/costs/{cost_id}/close", { params: { path: { cost_id: costId } }, body: { reason: reason.trim() || "closed" } });
  if (error) return await fail(error, "estimates.failed");
  revalidatePath(revalidate);
  return { ok: true, data: null };
}

export async function addRateCard(input: { cost_type: CostType; scope: "CONTAINER" | "SHIPMENT"; basis: RateBasis; amount: string; currency: string; hs_code?: string; notes?: string }): Promise<ActionResult<null>> {
  await protectAction();
  const amount = normalizeAmount(input.amount);
  if (amount === null) return await fail(null, "rateCards.failed");
  const { error } = await api.POST("/api/v1/organization/rate-cards", {
    body: { cost_type: input.cost_type, scope: input.scope, basis: input.basis, amount, currency: input.currency.toUpperCase(), hs_code: input.hs_code || null, notes: input.notes || null },
  });
  if (error) return await fail(error, "rateCards.failed");
  revalidatePath("/settings");
  return { ok: true, data: null };
}

export async function deleteRateCard(cardId: string): Promise<ActionResult<null>> {
  await protectAction();
  const { error } = await api.DELETE("/api/v1/organization/rate-cards/{card_id}", { params: { path: { card_id: cardId } } });
  if (error) return await fail(error, "rateCards.failed");
  revalidatePath("/settings");
  return { ok: true, data: null };
}

// ---------------------------------------------------------------------------- erp

export async function connectErp(input: { url: string; database: string; login: string; api_key: string }): Promise<ActionResult<null>> {
  await protectAction();
  const { error } = await api.POST("/api/v1/erp/connection", { body: { kind: "ODOO", url: input.url.trim(), database: input.database.trim(), login: input.login.trim(), api_key: input.api_key } });
  if (error) return await fail(error, "erp.connectFailed");
  revalidatePath("/settings");
  return { ok: true, data: null };
}

export async function disconnectErp(): Promise<ActionResult<null>> {
  await protectAction();
  const { error } = await api.DELETE("/api/v1/erp/connection");
  if (error) return await fail(error, "erp.connectFailed");
  revalidatePath("/settings");
  return { ok: true, data: null };
}

export async function syncErp(full = false): Promise<ActionResult<{ orders: number; lines: number; status: string; error: string | null }>> {
  await protectAction();
  const { data, error } = await api.POST("/api/v1/erp/sync", { params: { query: { full } } });
  if (error || !data) return await fail(error, "erp.syncFailed");
  revalidatePath("/settings");
  revalidatePath("/purchase-orders");
  revalidatePath("/containers");
  return { ok: true, data: { orders: data.purchase_orders, lines: data.lines, status: data.status, error: data.error ?? null } };
}

export async function subscribeTracking(containerId: string): Promise<ActionResult<{ provider: string; status: string }>> {
  await protectAction();
  const { data, error } = await api.POST("/api/v1/containers/{container_id}/tracking", { params: { path: { container_id: containerId } }, body: { identifier_type: "container" } });
  if (error || !data) return await fail(error, "tracking.subscribeFailed");
  revalidatePath(`/container/${containerId}`);
  return { ok: true, data: { provider: data.provider, status: data.status } };
}

export async function unsubscribeTracking(containerId: string): Promise<ActionResult<null>> {
  await protectAction();
  const { error } = await api.DELETE("/api/v1/containers/{container_id}/tracking", { params: { path: { container_id: containerId } } });
  if (error) return await fail(error, "tracking.subscribeFailed");
  revalidatePath(`/container/${containerId}`);
  return { ok: true, data: null };
}

export async function createContainer(containerNumber: string, isoType?: string): Promise<ActionResult<{ id: string }>> {
  await protectAction();
  const { data, error } = await api.POST("/api/v1/containers", {
    body: { container_number: containerNumber.trim().toUpperCase(), ...(isoType ? { iso_type: isoType } : {}) },
  });
  if (error || !data) return await fail(error, "containers.new.failed");
  revalidatePath("/containers");
  return { ok: true, data: { id: data.id } };
}

export async function replaceLoads(
  containerId: string,
  loads: { po_line_id: string; quantity: string }[],
): Promise<ActionResult<null>> {
  await protectAction();
  const { error } = await api.PUT("/api/v1/containers/{container_id}/loads", {
    params: { path: { container_id: containerId } },
    body: loads,
  });
  if (error) return await fail(error, "loads.failed");
  revalidatePath(`/container/${containerId}`);
  revalidatePath("/containers");
  return { ok: true, data: null };
}

// ---------------------------------------------------------------------------- imports

/**
 * A file refused because the very same file was already imported (costs: replaying it would count
 * every line twice) — `existingId` is that import, so the screen can open it instead of stopping.
 */
export type ImportUploadResult = { ok: true; data: ImportJobResponse } | { ok: false; error: string; existingId?: string };

export async function createImport(formData: FormData): Promise<ImportUploadResult> {
  await protectAction();
  const file = formData.get("file");
  const kind = String(formData.get("kind") ?? "PURCHASE_ORDERS") as ImportKind;
  if (!(file instanceof File) || file.size === 0) {
    const t = await getTranslations("importWizard.upload");
    return { ok: false, error: t("chooseFirst") };
  }
  const body = new FormData();
  body.append("kind", kind);
  body.append("file", file, file.name);
  const { data, error } = await api.POST("/api/v1/imports", {
    body: body as unknown as { kind: ImportKind; file: string },
    bodySerializer: (b) => b as unknown as BodyInit,
  });
  if (error || !data) {
    const t = await getTranslations();
    const p = error as (Problem & { existing_id?: string }) | undefined;
    return { ok: false, error: await problemMessage(error, t("importWizard.upload.failed")), existingId: p?.code === "FILE_ALREADY_IMPORTED" ? p.existing_id : undefined };
  }
  return { ok: true, data };
}

/**
 * The preview: the file read with this mapping, written nowhere. For a costs file, `defaultCostType`
 * types every line whose type cannot be read, `labelTypes` types the lines of one wording at a time
 * (the labels exactly as the last preview's `unknown_labels` gave them), and `forceDuplicates` is a
 * person's word that lines refused as a possible double entry are other invoices; all are kept on the
 * job, so the commit replays exactly what was previewed.
 */
export async function validateImport(
  importId: string,
  mapping: Record<string, string>,
  options: { defaultCostType?: CostType | null; labelTypes?: Record<string, CostType>; forceDuplicates?: boolean } = {},
): Promise<ActionResult<ImportJobResponse> | { ok: false; error: string; oneOf: string[] }> {
  await protectAction();
  const { data, error } = await api.POST("/api/v1/imports/{import_id}/validate", {
    params: { path: { import_id: importId } },
    body: {
      mapping,
      ...(options.defaultCostType ? { default_cost_type: options.defaultCostType } : {}),
      ...(options.labelTypes && Object.keys(options.labelTypes).length ? { label_types: options.labelTypes } : {}),
      force_duplicates: options.forceDuplicates ?? false,
    },
  });
  if (error || !data) {
    const p = error as (Problem & { one_of?: string[] }) | undefined;
    // Columns that go together (a credit column needs its account): the screen names them by their headers.
    if (p?.code === "MAPPING_INCOMPLETE" && p.one_of?.length) return { ok: false, error: "", oneOf: p.one_of };
    if (p?.code === "INVOICE_LINE_ALREADY_RECORDED" && !p.errors?.length) {
      const t = await getTranslations("importWizard.review");
      return { ok: false, error: t("lineRecordedMeanwhile") };
    }
    return await fail(error, "importWizard.mapping.failed");
  }
  return { ok: true, data };
}

/**
 * The commit names the preview the person read (`previewKey`, from the job): what is written is that
 * preview and nothing else. On PREVIEW_CHANGED the books moved in between, or another tab previewed
 * the file again with other choices: nothing was written, and `report` is the new preview, with the
 * key that commits it — shown first, never committed behind the person's back.
 */
export type ImportCommitResult =
  | { ok: true; data: ImportJobResponse }
  | { ok: false; error: string; code?: string; report?: Schemas["ImportReport"]; previewKey?: string };

export async function commitImport(importId: string, onError: "skip_rows" | "abort", previewKey: string | null): Promise<ImportCommitResult> {
  await protectAction();
  const { data, error } = await api.POST("/api/v1/imports/{import_id}/commit", {
    params: { path: { import_id: importId } },
    body: { on_error: onError, ...(previewKey ? { preview_key: previewKey } : {}) },
  });
  if (error || !data) {
    const t = await getTranslations();
    const p = error as (Problem & { report?: Schemas["ImportReport"]; preview_key?: string }) | undefined;
    // A line of the file written elsewhere since the preview (the database's last word): a new
    // preview shows which one, where a generic « this invoice already has this cost » would not.
    if (p?.code === "INVOICE_LINE_ALREADY_RECORDED" && !p.errors?.length) return { ok: false, error: t("importWizard.review.lineRecordedMeanwhile"), code: p.code };
    const changed = p?.code === "PREVIEW_CHANGED";
    return { ok: false, error: await problemMessage(error, t("importWizard.review.failed")), code: p?.code ?? undefined, report: changed ? p.report : undefined, previewKey: changed ? p.preview_key : undefined };
  }
  // An import can date containers, create costs, match waiting invoices and fill sale prices.
  for (const path of ["/containers", "/purchase-orders", "/imports", "/invoices", "/skus", "/cost-audit", "/overview"]) revalidatePath(path);
  return { ok: true, data };
}

/**
 * Takes a costs import back: its costs are deleted, the estimates they had replaced are open again.
 * Refused, by name, when a cost went to the ERP or sits in a closed month.
 */
export async function undoImport(importId: string): Promise<ActionResult<{ costsDeleted: number; estimatesReopened: number }>> {
  await protectAction();
  const { data, error } = await api.DELETE("/api/v1/imports/{import_id}", { params: { path: { import_id: importId } } });
  if (error || !data) {
    // Two refusals name what stands in the way, as real lists: the ERP documents that carry some of
    // the costs (the invoice-reopen code, whose own sentence speaks of « cette facture »), and the
    // closed months — said in words, in the reader's language.
    const p = error as (Problem & { months?: string[] }) | undefined;
    const [t, locale] = await Promise.all([getTranslations("imports.undo"), getLocale()]);
    const list = (items: string[]) => new Intl.ListFormat(locale === "fr" ? "fr-FR" : "en-GB", { type: "conjunction" }).format(items);
    // One document carries several costs: it is counted, and named, once.
    const documents = [...new Set((p?.errors ?? []).map((e) => e.message.split("|")).filter((x) => x[0] === "@pushed_cost").map((x) => x[1]).filter(Boolean))];
    if (p?.code === "COSTS_PUSHED_TO_ERP" && documents.length) {
      return { ok: false, error: t("pushed", { count: documents.length, documents: list(documents) }) };
    }
    // A later file of the same ledger recognised some of these lines and left them to this one.
    const later = (p as { files?: string[] } | undefined)?.files?.filter(Boolean) ?? [];
    if (p?.code === "IMPORT_RELIED_ON") {
      return { ok: false, error: later.length ? t("reliedOn", { count: later.length, files: list(later) }) : t("reliedOnUnnamed") };
    }
    if (p?.code === "IMPORT_PERIOD_CLOSED" && p.months?.length) {
      return { ok: false, error: t("periodClosed", { count: p.months.length, months: list(p.months.map((m) => monthTitle(m, locale))) }) };
    }
    return await fail(error, "imports.undo.failed");
  }
  for (const path of ["/containers", "/imports", "/cost-audit", "/overview"]) revalidatePath(path);
  return { ok: true, data: { costsDeleted: data.costs_deleted, estimatesReopened: data.estimates_reopened } };
}

// ---------------------------------------------------------------------------- demo data

/** `reference`: one shipment, to learn the screens. `history`: six months of arrivals, to see the trends. */
export async function loadSampleData(profile: "reference" | "history" = "reference"): Promise<ActionResult<{ containerIds: string[] }>> {
  await protectAction();
  const { data, error } = await api.POST("/api/v1/organization/sample-data", { params: { query: { profile } } });
  if (error || !data) return await fail(error, "containers.sample.failed");
  revalidatePath("/overview");
  revalidatePath("/containers");
  revalidatePath("/purchase-orders");
  return { ok: true, data: { containerIds: data.container_ids } };
}

// ---------------------------------------------------------------------------- product catalogue

/** "4,5" (a percentage, as typed) → "0.045" (the fraction the API stores). String arithmetic: no float touches a rate. */
function percentToFraction(raw: string): string | null {
  const n = normalizeAmount(raw);
  if (n === null) return null;
  const [int, frac = ""] = n.split(".");
  const digits = int.padStart(3, "0");
  const out = `${digits.slice(0, -2)}.${digits.slice(-2)}${frac}`.replace(/^0+(?=\d)/, "").replace(/\.?0+$/, "");
  return out === "" ? "0" : out;
}

export type ProductInput = { sku: string; description: string; hs_code: string; duty_rate_pct: string; unit_weight_kg: string; unit_volume_cbm: string; sale_price: string };

/**
 * Create the product sheet of a SKU, or update it (the API upserts on the SKU). A blank field is
 * sent as null — "not known" — which is not the same as 0: a 0 % duty rate is a rate.
 */
export async function saveProduct(input: ProductInput, revalidate: string): Promise<ActionResult<null>> {
  await protectAction();
  const t = await getTranslations("skus.product");
  const amount = (raw: string): string | null | undefined => (raw.trim() === "" ? null : (normalizeAmount(raw) ?? undefined));
  const duty = input.duty_rate_pct.trim() === "" ? null : (percentToFraction(input.duty_rate_pct) ?? undefined);
  const weight = amount(input.unit_weight_kg);
  const volume = amount(input.unit_volume_cbm);
  const price = amount(input.sale_price);
  if (duty === undefined || weight === undefined || volume === undefined || price === undefined) return { ok: false, error: t("numberInvalid") };
  const { error } = await api.POST("/api/v1/products", {
    body: {
      sku: input.sku,
      description: input.description.trim() || null,
      hs_code: input.hs_code.replace(/[\s.]/g, "") || null,
      duty_rate: duty,
      unit_weight_kg: weight,
      unit_volume_cbm: volume,
      sale_price: price,
    },
  });
  if (error) return await fail(error, "skus.product.failed");
  revalidatePath(revalidate);
  revalidatePath("/skus");
  return { ok: true, data: null };
}

/** One sheet per SKU already seen on an order line, filled with what the orders last said about it. */
export async function createProductsFromOrders(): Promise<ActionResult<{ created: number; skipped: number }>> {
  await protectAction();
  const { data, error } = await api.POST("/api/v1/products/from-orders", {});
  if (error || !data) return await fail(error, "skus.product.failed");
  revalidatePath("/skus");
  return { ok: true, data: { created: data.created, skipped: data.skipped } };
}

export type SampleBlocker = { field: string; code: string };

/**
 * Remove what the sample data created, and only that. A refusal (409) deletes nothing and names, one
 * by one, the records of the user's own that lean on a sample object — the screen needs the codes,
 * not a single translated sentence, to say which container or order to look at.
 */
export async function deleteSampleData(): Promise<{ ok: true; deleted: number } | { ok: false; error: string; blockers: SampleBlocker[] }> {
  await protectAction();
  const { data, error } = await api.DELETE("/api/v1/organization/sample-data", {});
  if (error || !data) {
    const problem = error as Problem | undefined;
    const blockers = problem?.code === "SAMPLE_DATA_IN_USE" ? (problem.errors ?? []).map((e) => ({ field: e.field, code: e.code })) : [];
    const t = await getTranslations();
    return { ok: false, error: await problemMessage(blockers.length ? { code: problem?.code } : error, t("sample.deleteFailed")), blockers };
  }
  for (const path of ["/overview", "/containers", "/purchase-orders", "/invoices", "/reports", "/skus", "/alerts", "/settings"]) revalidatePath(path);
  return { ok: true, deleted: Object.values(data.deleted ?? {}).reduce((sum: number, n) => sum + (typeof n === "number" ? n : 0), 0) };
}

// ---------------------------------------------------------------------------- shared reports

/** What a link opens: one container's or one order's landed cost, or the audit of a period. */
export type ShareSubject = { kind: "container" | "purchase_order"; id: string } | { kind: "audit"; periodFrom: string; periodTo: string };
export type ShareSummary = Schemas["ShareSummary"];

/** The token comes back this once and is stored nowhere readable: the caller shows the link at once. */
export async function createShare(subject: ShareSubject, expiresInDays: number, redactVendors: boolean): Promise<ActionResult<{ path: string; expiresAt: string; landed: string | null; generatedAt: string }>> {
  await protectAction();
  const common = { expires_in_days: Math.min(Math.max(Math.round(expiresInDays), 1), 365), redact_vendors: redactVendors };
  const { data, error } =
    subject.kind === "audit"
      ? await api.POST("/api/v1/reports/audit/share", { body: { ...common, period_from: subject.periodFrom, period_to: subject.periodTo } })
      : subject.kind === "container"
        ? await api.POST("/api/v1/containers/{container_id}/share", { params: { path: { container_id: subject.id } }, body: common })
        : await api.POST("/api/v1/purchase-orders/{po_id}/share", { params: { path: { po_id: subject.id } }, body: common });
  if (error || !data) return await fail(error, "share.createFailed");
  return { ok: true, data: { path: data.path, expiresAt: data.expires_at, landed: data.landed ?? null, generatedAt: data.generated_at } };
}

export async function listShares(subject: ShareSubject): Promise<ActionResult<ShareSummary[]>> {
  await protectAction();
  const { data, error } =
    subject.kind === "audit"
      ? await api.GET("/api/v1/reports/audit/shares")
      : subject.kind === "container"
        ? await api.GET("/api/v1/containers/{container_id}/shares", { params: { path: { container_id: subject.id } } })
        : await api.GET("/api/v1/purchase-orders/{po_id}/shares", { params: { path: { po_id: subject.id } } });
  if (error || !data) return await fail(error, "share.listFailed");
  return { ok: true, data };
}

export async function revokeShare(shareId: string): Promise<ActionResult<null>> {
  await protectAction();
  const { error } = await api.DELETE("/api/v1/shares/{share_id}", { params: { path: { share_id: shareId } } });
  if (error) return await fail(error, "share.revokeFailed");
  return { ok: true, data: null };
}

// ---------------------------------------------------------------------------- period close

/** Freeze a month's landed costs as they stand. Nothing is locked afterwards: what moves is reported as drift. */
export async function closePeriod(period: string): Promise<ActionResult<null>> {
  await protectAction();
  const { error } = await api.POST("/api/v1/periods/{period}/close", { params: { path: { period } } });
  if (error) return await fail(error, "periods.closeFailed");
  for (const path of ["/periods", `/periods/${period}`, "/containers"]) revalidatePath(path);
  return { ok: true, data: null };
}

/**
 * The gap since closing has been posted to the books (as an adjustment in the current month): the
 * month stops asking. It asks again if a cost lands after this — the API keeps the two apart.
 */
export async function acknowledgePeriodDrift(period: string, note: string): Promise<ActionResult<null>> {
  await protectAction();
  const { error } = await api.POST("/api/v1/periods/{period}/acknowledge-drift", { params: { path: { period } }, body: { note: note.trim().slice(0, 500) || null } });
  if (error) return await fail(error, "periods.ackFailed");
  for (const path of ["/periods", `/periods/${period}`]) revalidatePath(path);
  return { ok: true, data: null };
}

/** The frozen figures are discarded (the audit trail keeps them). */
export async function reopenPeriod(period: string): Promise<ActionResult<null>> {
  await protectAction();
  const { error } = await api.DELETE("/api/v1/periods/{period}/close", { params: { path: { period } } });
  if (error) return await fail(error, "periods.reopenFailed");
  for (const path of ["/periods", `/periods/${period}`, "/containers"]) revalidatePath(path);
  return { ok: true, data: null };
}

// ---------------------------------------------------------------------------- audit

/**
 * The coefficient the company applies from memory ("FOB × 1,15"), which the audit measures against.
 * PATCH /organization merges `settings`: the one key is sent, and an empty value (null) removes it.
 */
export async function saveAssumedCoefficient(raw: string, revalidate: string): Promise<ActionResult<null>> {
  await protectAction();
  const t = await getTranslations("costAudit.assumed");
  const typed = raw.trim().replace(",", ".");
  if (typed !== "" && !/^\d(\.\d{1,4})?$/.test(typed)) return { ok: false, error: t("invalid") };
  const { error } = await api.PATCH("/api/v1/organization", { body: { settings: { assumed_coefficient: typed === "" ? null : typed } } });
  if (error) return await fail(error, "costAudit.assumed.failed");
  revalidatePath(revalidate);
  revalidatePath("/settings");
  return { ok: true, data: null };
}

// ---------------------------------------------------------------------------- settings

export async function updateOrganization(input: {
  name?: string;
  base_currency?: string;
  locale?: "fr" | "en";
  default_allocation_method?: string;
  free_days_demurrage?: number;
  free_days_detention?: number;
  settings?: Record<string, unknown>;
}): Promise<ActionResult<null>> {
  await protectAction();
  const { error } = await api.PATCH("/api/v1/organization", {
    body: { ...input, default_allocation_method: input.default_allocation_method as AllocationMethod | undefined },
  });
  if (error) return await fail(error, "settings.failed");
  revalidatePath("/settings");
  revalidatePath("/containers");
  return { ok: true, data: null };
}

export type ErpLandedCostPreview = Schemas["ErpLandedCostPreview"];
export type ErpPushResponse = Schemas["ErpPushResponse"];

/** What would be created in the ERP for this container. Reads the ERP, writes nothing. */
export async function previewErpLandedCost(containerId: string): Promise<ActionResult<ErpLandedCostPreview>> {
  await protectAction();
  const { data, error } = await api.GET("/api/v1/containers/{container_id}/erp/landed-cost/preview", { params: { path: { container_id: containerId } } });
  if (error || !data) return await fail(error, "erp.push.previewFailed");
  return { ok: true, data };
}

/** Creates the landed cost in the ERP, in draft. Pushing the same costs twice returns the first push. */
export async function pushErpLandedCost(containerId: string): Promise<ActionResult<ErpPushResponse>> {
  await protectAction();
  const { data, error } = await api.POST("/api/v1/containers/{container_id}/erp/landed-cost", { params: { path: { container_id: containerId } } });
  if (error || !data) return await fail(error, "erp.push.pushFailed");
  revalidatePath(`/container/${containerId}`);
  return { ok: true, data };
}

/** Every document ever created in the ERP for this container, newest first, forgotten ones marked. */
export async function listErpPushes(containerId: string): Promise<ActionResult<ErpPushResponse[]>> {
  await protectAction();
  const { data, error } = await api.GET("/api/v1/containers/{container_id}/erp/pushes", { params: { path: { container_id: containerId } } });
  if (error || !data) return await fail(error, "erp.push.previewFailed");
  return { ok: true, data };
}

/**
 * Forget a push whose draft is gone from the ERP, so its costs can be pushed again. Not a deletion:
 * the row stays, marked, with the reason, and the audit log gets an entry. Refused while the
 * document still exists over there, with its name so the person can go and look.
 */
export async function forgetErpPush(containerId: string, pushId: string, reason: string): Promise<ActionResult<ErpPushResponse>> {
  await protectAction();
  const { data, error } = await api.DELETE("/api/v1/containers/{container_id}/erp/pushes/{push_id}", { params: { path: { container_id: containerId, push_id: pushId }, query: { reason: reason.trim() || undefined } } });
  if (error || !data) {
    const p = error as { code?: string; document?: { name?: string; state?: string } } | undefined;
    if (p?.code === "ERP_DOCUMENT_STILL_THERE") {
      const t = await getTranslations("erp.pushes");
      return { ok: false, error: t("stillThere", { name: p.document?.name ?? "?", state: p.document?.state ?? "" }) };
    }
    if (p?.code === "ERP_FORGET_REASON_REQUIRED") {
      const t = await getTranslations("erp.pushes");
      return { ok: false, error: t("reasonRequired", { name: p.document?.name ?? "?", state: p.document?.state ?? "" }) };
    }
    return await fail(error, "erp.pushes.forgetFailed");
  }
  revalidatePath(`/container/${containerId}`);
  return { ok: true, data };
}

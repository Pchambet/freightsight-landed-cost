"use server";

import { revalidatePath } from "next/cache";
import { getTranslations } from "next-intl/server";
import { api, problemMessage, type Problem } from "@/lib/api/client";
import { protectAction } from "@/lib/auth";

/** What the guided first file answers: the id to move on with, or a sentence — and, when the thing
 * already exists, its id, so the step can offer to carry on with it instead of failing. */
export type StepResult = { ok: true; id: string } | { ok: false; error: string; existingId?: string };

/** "12 500,50" / "12,500.50" / "12500.5" → "12500.5". Null when it is not a number a person typed. */
function decimal(raw: string): string | null {
  let n = raw.trim().replace(/[\s  ]/g, "");
  if (!n) return null;
  if (n.includes(",") && n.includes(".")) n = n.lastIndexOf(",") > n.lastIndexOf(".") ? n.replace(/\./g, "").replace(",", ".") : n.replace(/,/g, "");
  else if (n.includes(",")) n = n.replace(",", ".");
  return /^\d+(\.\d+)?$/.test(n) ? n : null;
}

/** "4,5" (percent, as typed) → "0.045", by moving the point on the string. */
function percentToFraction(raw: string): string | null {
  const n = decimal(raw);
  if (n === null) return null;
  const [int, frac = ""] = n.split(".");
  const digits = int.padStart(3, "0");
  const out = `${digits.slice(0, -2)}.${digits.slice(-2)}${frac}`.replace(/^0+(?=\d)/, "").replace(/\.?0+$/, "");
  return out === "" ? "0" : out;
}

export type OrderLineInput = { sku: string; description: string; quantity: string; unit_price: string; unit_weight_kg: string; unit_volume_cbm: string; duty_rate_pct: string };

export async function createOrder(input: { po_number: string; supplier_name: string; currency: string; order_date: string; lines: OrderLineInput[] }): Promise<StepResult> {
  await protectAction();
  const t = await getTranslations("start.order");
  const rows = input.lines.filter((l) => [l.sku, l.description, l.quantity, l.unit_price].some((v) => v.trim() !== ""));
  if (!input.po_number.trim()) return { ok: false, error: t("needNumber") };
  if (rows.length === 0) return { ok: false, error: t("needLine") };
  const lines = [];
  for (const [i, l] of rows.entries()) {
    const quantity = decimal(l.quantity);
    const unit_price = decimal(l.unit_price);
    const weight = l.unit_weight_kg.trim() ? decimal(l.unit_weight_kg) : null;
    const volume = l.unit_volume_cbm.trim() ? decimal(l.unit_volume_cbm) : null;
    const duty = l.duty_rate_pct.trim() ? percentToFraction(l.duty_rate_pct) : null;
    if (quantity === null || Number(quantity) <= 0 || unit_price === null || (l.unit_weight_kg.trim() && weight === null) || (l.unit_volume_cbm.trim() && volume === null) || (l.duty_rate_pct.trim() && duty === null)) {
      return { ok: false, error: t("badLine", { line: i + 1 }) };
    }
    lines.push({ line_no: i + 1, sku: l.sku.trim() || null, description: l.description.trim() || null, quantity, unit_price, unit_weight_kg: weight, unit_volume_cbm: volume, duty_rate: duty });
  }
  const { data, error } = await api.POST("/api/v1/purchase-orders", {
    body: { po_number: input.po_number.trim(), supplier_name: input.supplier_name.trim() || null, currency: input.currency, order_date: input.order_date || null, lines },
  });
  if (error || !data) {
    const p = error as (Problem & { existing_id?: string }) | undefined;
    return { ok: false, error: await problemMessage(error, t("failed")), existingId: p?.code === "PURCHASE_ORDER_EXISTS" ? p.existing_id : undefined };
  }
  revalidatePath("/purchase-orders");
  return { ok: true, id: data.id };
}

/**
 * The container of the file: created (or, when it exists already and the user said so, reused), dated,
 * and loaded with what is LEFT of the order — a line partly loaded elsewhere only brings its remainder,
 * and the loads the container already carries are kept.
 */
export async function createContainerForOrder(input: { container_number: string; arrived_on: string; iso_type?: string; po_id: string; reuseId?: string }): Promise<StepResult> {
  await protectAction();
  const t = await getTranslations("start.container");
  let containerId = input.reuseId;
  if (!containerId) {
    const created = await api.POST("/api/v1/containers", { body: { container_number: input.container_number.trim().toUpperCase(), ...(input.iso_type ? { iso_type: input.iso_type } : {}) } });
    if (created.error || !created.data) {
      const p = created.error as (Problem & { existing_id?: string }) | undefined;
      return { ok: false, error: await problemMessage(created.error, t("failed")), existingId: p?.code === "CONTAINER_EXISTS" ? p.existing_id : undefined };
    }
    containerId = created.data.id;
  }
  // The arrival date is what puts the file in a month: without it the container is in no report. A
  // container reused as it is gets the type chosen here too; a new one already has it.
  const patch = { ...(input.arrived_on ? { ata: `${input.arrived_on}T12:00:00Z` } : {}), ...(input.reuseId && input.iso_type ? { iso_type: input.iso_type } : {}) };
  if (Object.keys(patch).length) {
    const dated = await api.PATCH("/api/v1/containers/{container_id}", { params: { path: { container_id: containerId } }, body: patch });
    if (dated.error) return { ok: false, error: await problemMessage(dated.error, t("failed")) };
  }

  const [po, existing, everything] = await Promise.all([
    api.GET("/api/v1/purchase-orders/{po_id}", { params: { path: { po_id: input.po_id } } }),
    api.GET("/api/v1/containers/{container_id}/loads", { params: { path: { container_id: containerId } } }),
    api.GET("/api/v1/containers"),
  ]);
  if (po.error || !po.data) return { ok: false, error: await problemMessage(po.error, t("failed")) };
  // What each line of this order already has on board, anywhere.
  const loaded = new Map<string, number>();
  const others = (everything.data ?? []).filter((c) => (c.po_numbers ?? []).includes(po.data.po_number));
  const theirLoads = await Promise.all(others.map((c) => api.GET("/api/v1/containers/{container_id}/loads", { params: { path: { container_id: c.id } } })));
  for (const r of theirLoads) for (const l of r.data ?? []) loaded.set(l.po_line_id, (loaded.get(l.po_line_id) ?? 0) + Number(l.quantity));
  const keep = (existing.data ?? []).map((l) => ({ po_line_id: l.po_line_id, quantity: l.quantity }));
  const mine = new Set(keep.map((l) => l.po_line_id));
  const added = po.data.lines
    .filter((l) => !mine.has(l.id))
    .map((l) => ({ po_line_id: l.id, quantity: Number(l.quantity) - (loaded.get(l.id) ?? 0) }))
    .filter((l) => l.quantity > 0)
    .map((l) => ({ po_line_id: l.po_line_id, quantity: String(Number(l.quantity.toFixed(4))) }));
  if (added.length > 0) {
    const put = await api.PUT("/api/v1/containers/{container_id}/loads", { params: { path: { container_id: containerId } }, body: [...keep, ...added] });
    if (put.error) return { ok: false, error: await problemMessage(put.error, t("loadFailed")) };
  } else if (keep.length === 0) {
    return { ok: false, error: t("nothingLeft") };
  }
  for (const path of ["/containers", `/container/${containerId}`, "/overview"]) revalidatePath(path);
  return { ok: true, id: containerId };
}

export type ManualCost = { cost_type: "OCEAN_FREIGHT" | "CUSTOMS_DUTY" | "CUSTOMS_BROKERAGE" | "DRAYAGE"; amount: string; currency: string };

/** The four figures someone knows by heart when the forwarder's PDF is not at hand. */
export async function addManualCosts(containerId: string, costDate: string, costs: ManualCost[]): Promise<{ ok: true; created: number } | { ok: false; error: string }> {
  await protectAction();
  const t = await getTranslations("start.costs");
  const rows = costs.filter((c) => c.amount.trim() !== "");
  if (rows.length === 0) return { ok: false, error: t("needAmount") };
  let created = 0;
  for (const c of rows) {
    const amount = decimal(c.amount);
    if (amount === null || Number(amount) <= 0) return { ok: false, error: t("badAmount") };
    const { error } = await api.POST("/api/v1/costs", { body: { scope: "CONTAINER", target_id: containerId, cost_type: c.cost_type, status: "ACTUAL", amount, currency: c.currency, cost_date: costDate } });
    if (error) return { ok: false, error: await problemMessage(error, t("failed")) };
    created += 1;
  }
  for (const path of ["/containers", `/container/${containerId}`, "/overview"]) revalidatePath(path);
  return { ok: true, created };
}

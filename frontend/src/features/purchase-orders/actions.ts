"use server";

import { revalidatePath } from "next/cache";
import { getTranslations } from "next-intl/server";
import { api, problemMessage } from "@/lib/api/client";
import { protectAction } from "@/lib/auth";

export type RateResult = { ok: true } | { ok: false; error: string };

/** "0,9215" / "0.9215" / "27 400" → "0.9215" / "27400"; null when it is not a rate above zero. */
function rateValue(raw: string): string | null {
  const n = raw.trim().replace(/[\s  ]/g, "").replace(",", ".");
  if (!/^\d{1,10}(\.\d{1,8})?$/.test(n) || !/[1-9]/.test(n)) return null;
  return n;
}

/**
 * Set an order's exchange rate by hand: one rate for the whole order, which replaces the ECB's and
 * recomputes every landed cost it carries. The date is sent only when the order has none of its own.
 */
export async function setPurchaseOrderRate(poId: string, raw: string, date: string | null): Promise<RateResult> {
  await protectAction();
  const t = await getTranslations("purchaseOrder.rate");
  const fx_rate = rateValue(raw);
  if (fx_rate === null) return { ok: false, error: t("invalid") };
  const { error } = await api.PATCH("/api/v1/purchase-orders/{po_id}", {
    params: { path: { po_id: poId } },
    body: { fx_rate, ...(date ? { fx_date: date } : {}) },
  });
  if (error) return { ok: false, error: await problemMessage(error, t("failed")) };
  // Every screen that shows a figure built on this order's value.
  for (const path of [`/purchase-orders/${poId}`, "/purchase-orders", "/containers", "/overview", "/skus", "/reports", "/periods", "/cost-audit"]) revalidatePath(path);
  return { ok: true };
}

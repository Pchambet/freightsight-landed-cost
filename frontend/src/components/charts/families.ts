import type { CostType } from "@/lib/domain";

/**
 * Sixteen cost types are sixteen colours nobody can tell apart, so a chart reads FAMILIES: the six
 * questions an importer actually asks of a landed cost (what did the crossing cost, the port, the
 * customs, the last mile, the delays, the rest). A table still shows every type; a chart never does.
 *
 * The order is part of the contract: it is the stacking order and the order the palette was
 * validated in (globals.css, `--color-viz-*`). A family keeps its colour whatever else is on
 * screen — a chart that has no customs this month does not hand aqua to the next family.
 *
 * IMPORT_VAT is absent on purpose: it is recoverable, tracked for cash, and never part of a landed
 * cost — the same rule as the engine and every table of the app.
 */
export const COST_FAMILIES = ["freight", "port", "customs", "delivery", "dnd", "other"] as const;
export type CostFamily = (typeof COST_FAMILIES)[number];

export const FAMILY_OF: Partial<Record<CostType, CostFamily>> = {
  OCEAN_FREIGHT: "freight",
  AIR_FREIGHT: "freight",
  INSURANCE: "freight",
  ORIGIN_CHARGES: "port",
  THC: "port",
  BL_FEE: "port",
  CUSTOMS_DUTY: "customs",
  CUSTOMS_BROKERAGE: "customs",
  // A customs examination or a scan is triggered by customs and billed with the broker's fees.
  INSPECTION: "customs",
  DRAYAGE: "delivery",
  WAREHOUSING: "delivery",
  DEMURRAGE: "dnd",
  DETENTION: "dnd",
  BANK_FEES: "other",
  OTHER: "other",
};

/** Tailwind classes, spelled out so the compiler sees them. */
export const FAMILY_FILL: Record<CostFamily, string> = {
  freight: "fill-viz-freight",
  port: "fill-viz-port",
  customs: "fill-viz-customs",
  delivery: "fill-viz-delivery",
  dnd: "fill-viz-dnd",
  other: "fill-viz-other",
};
export const FAMILY_BG: Record<CostFamily, string> = {
  freight: "bg-viz-freight",
  port: "bg-viz-port",
  customs: "bg-viz-customs",
  delivery: "bg-viz-delivery",
  dnd: "bg-viz-dnd",
  other: "bg-viz-other",
};

export type FamilyAmounts = Record<CostFamily, number>;

/**
 * Fold an API `by_cost_type` map (decimal strings) into families. Numbers here feed GEOMETRY only —
 * a bar's height, a segment's width. Every amount a reader sees is formatted from the API's own
 * string, or from these sums when the API has no such total, and never feeds back into a calculation.
 */
export function foldFamilies(byCostType: Record<string, string | number> | null | undefined): FamilyAmounts {
  const out: FamilyAmounts = { freight: 0, port: 0, customs: 0, delivery: 0, dnd: 0, other: 0 };
  for (const [type, raw] of Object.entries(byCostType ?? {})) {
    if (type === "IMPORT_VAT") continue;
    const n = typeof raw === "number" ? raw : Number(raw);
    if (!Number.isFinite(n) || n === 0) continue;
    out[FAMILY_OF[type as CostType] ?? "other"] += n;
  }
  return out;
}

export function familyTotal(a: FamilyAmounts): number {
  return COST_FAMILIES.reduce((s, f) => s + a[f], 0);
}

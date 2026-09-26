import type { Schemas } from "@/lib/api/client";

export type ContainerMilestone = Schemas["ContainerMilestone"];
export type MilestoneCode = Schemas["MilestoneCode"];
/** Every event code a user may record by hand, in voyage order. */
export const MILESTONE_CODES: MilestoneCode[] = ["BOOKED", "GATE_OUT_EMPTY_ORIGIN", "GATE_IN_FULL_ORIGIN", "LOADED", "VESSEL_DEPARTED", "TRANSSHIPMENT_ARRIVED", "TRANSSHIPMENT_DISCHARGED", "TRANSSHIPMENT_LOADED", "TRANSSHIPMENT_DEPARTED", "VESSEL_ARRIVED", "DISCHARGED", "AVAILABLE_FOR_PICKUP", "GATE_OUT_FULL", "RAIL_LOADED", "RAIL_DEPARTED", "RAIL_ARRIVED", "RAIL_UNLOADED", "DELIVERED", "GATE_IN_EMPTY_RETURN"];
export type CostType = Schemas["CostType"];
export type CostScope = Schemas["CostScope"];
export const COST_SCOPES: CostScope[] = ["SHIPMENT", "CONTAINER", "PO", "PO_LINE"];
export type InvoiceStatus = Schemas["InvoiceStatus"];
export type CostStatus = Schemas["CostStatus"];
export type RateBasis = Schemas["RateBasis"];
export const RATE_BASES: RateBasis[] = ["FLAT", "PER_100KG", "PER_CBM", "PCT_OF_FOB"];
export type AllocationMethod = Schemas["AllocationMethod"];
export type DndRisk = Schemas["DndRisk"];
export type ImportKind = Schemas["ImportKind"];

/** A quarter is loaded in this order, each file building on the previous ones: after each, the next. */
export const NEXT_IMPORT_KIND: Partial<Record<ImportKind, ImportKind>> = { PRODUCTS: "PURCHASE_ORDERS", PURCHASE_ORDERS: "CONTAINERS", CONTAINERS: "COSTS" };

// Labels live in messages/*.json under `domain.*`, keyed by these API values: a component reads
// t(`domain.costType.${value}`). The lists below are only the order the user sees them in.

export const MILESTONES: ContainerMilestone[] = [
  "BOOKED",
  "GATE_IN_FULL_ORIGIN",
  "LOADED",
  "VESSEL_DEPARTED",
  "VESSEL_ARRIVED",
  "DISCHARGED",
  "AVAILABLE_FOR_PICKUP",
  "GATE_OUT_FULL",
  "DELIVERED",
  "GATE_IN_EMPTY_RETURN",
];

const CLOSED_MILESTONES: ContainerMilestone[] = ["DELIVERED", "GATE_IN_EMPTY_RETURN"];

/**
 * Still on the terminal, so a last-free-day overrun still means running demurrage: once a container
 * is gated out or closed, the day count next to its risk badge stops meaning anything (audit B2 — a
 * gated-out "Low risk" container still showed "4 days over", which reads as a contradiction).
 */
export function demurrageRelevant(c: { milestone: ContainerMilestone; gate_out_at?: string | null }): boolean {
  return !CLOSED_MILESTONES.includes(c.milestone) && !c.gate_out_at;
}

export const COST_TYPES: CostType[] = [
  "OCEAN_FREIGHT",
  "AIR_FREIGHT",
  "INSURANCE",
  "ORIGIN_CHARGES",
  "THC",
  "BL_FEE",
  "CUSTOMS_DUTY",
  "CUSTOMS_BROKERAGE",
  "IMPORT_VAT",
  "DRAYAGE",
  "DEMURRAGE",
  "DETENTION",
  "WAREHOUSING",
  "INSPECTION",
  "BANK_FEES",
  "OTHER",
];

/** Methods a user can pick for ordinary costs. Duty methods are chosen by the backend for CUSTOMS_DUTY. */
export const USER_METHODS: AllocationMethod[] = ["BY_VALUE", "BY_WEIGHT", "BY_VOLUME", "BY_QUANTITY"];

import type { Schemas } from "@/lib/api/client";

type KindInfo = Schemas["ImportKindInfo"];

const FILE_NAMES: Record<string, { fr: string; en: string }> = {
  PRODUCTS: { fr: "modele-tarif", en: "template-price-list" },
  PURCHASE_ORDERS: { fr: "modele-commandes", en: "template-orders" },
  CONTAINERS: { fr: "modele-suivi-conteneurs", en: "template-container-tracking" },
  COSTS: { fr: "modele-frais", en: "template-costs" },
  LEGACY_PO_CONTAINER: { fr: "modele-commandes-par-conteneur", en: "template-orders-by-container" },
};

/**
 * The columns an export brings as it is — a ledger's account and credit, an order line's total weight
 * and volume — read when a file has them, left out of the template, which is a simple list typed by
 * hand: one weight per piece, not two ways of saying it.
 */
const LEDGER_ONLY: Record<string, string[]> = { COSTS: ["account", "credit"], PURCHASE_ORDERS: ["line_weight_kg", "line_volume_cbm"] };

/**
 * An empty file with the columns an import of this kind reads, as the API names them (`header_fr` /
 * `header_en`, which it maps back at 100 % — the backend tests it). Headers only: an example row would
 * be imported as data the day someone forgets to delete it. French: a BOM and « ; », what Excel opens
 * as columns on a French machine; English: « , ».
 */
export function downloadImportTemplate(kind: KindInfo, locale: string) {
  const french = locale === "fr";
  const quote = (h: string) => (/[",;\n]/.test(h) ? `"${h.replace(/"/g, '""')}"` : h);
  const skip = new Set(LEDGER_ONLY[kind.kind] ?? []);
  const header = kind.fields.filter((f) => !skip.has(f.field)).map((f) => quote(french ? f.header_fr : f.header_en)).join(french ? ";" : ",");
  const blob = new Blob([`﻿${header}\r\n`], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${(FILE_NAMES[kind.kind] ?? { fr: "modele", en: "template" })[french ? "fr" : "en"]}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoked once the click has handed the file over: revoking in the same tick can cancel the
  // download in some browsers.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

import type { StackedColumn } from "@/components/charts/StackedColumns";
import type { TrendPoint } from "@/components/charts/UnitCostTrend";
import type { CostFamily, FamilyAmounts } from "@/components/charts/families";

/**
 * The figures behind the live product blocks of the public site. An invented importer, written so
 * that every number agrees with every other one (the six months add up to the breakdown, the
 * breakdown to the ratio): a visitor who checks the arithmetic must find it right. The site says
 * "données d'exemple" next to each block.
 */
export const DEMO_CURRENCY = "EUR";

export const FAMILY_LABELS_FR: Record<CostFamily, string> = {
  freight: "Fret et assurance",
  port: "Frais portuaires",
  customs: "Douane",
  delivery: "Livraison et entreposage",
  dnd: "Surestaries et détention",
  other: "Autres frais",
};

const month = (key: string, label: string, title: string, f: Partial<FamilyAmounts>, note: string): StackedColumn => ({
  key,
  label,
  title,
  note,
  families: { freight: 0, port: 0, customs: 0, delivery: 0, dnd: 0, other: 0, ...f },
});

export const DEMO_MONTHS: StackedColumn[] = [
  month("2026-04", "avr.", "avril 2026", { freight: 7200, port: 1100, customs: 5900, delivery: 1600 }, "3 conteneurs · coefficient 1,21"),
  month("2026-05", "mai", "mai 2026", { freight: 8400, port: 1200, customs: 6300, delivery: 1700 }, "3 conteneurs · coefficient 1,22"),
  month("2026-06", "juin", "juin 2026", { freight: 10900, port: 1300, customs: 6100, delivery: 1800, dnd: 640 }, "4 conteneurs · coefficient 1,26"),
  month("2026-07", "juil.", "juillet 2026", { freight: 9800, port: 1200, customs: 6800, delivery: 1900 }, "4 conteneurs · coefficient 1,24"),
  month("2026-08", "août", "août 2026", { freight: 8100, port: 1100, customs: 5700, delivery: 1500 }, "3 conteneurs · coefficient 1,21"),
  month("2026-09", "sept.", "septembre 2026", { freight: 8900, port: 1300, customs: 7200, delivery: 2000, dnd: 600 }, "4 conteneurs · coefficient 1,23"),
];

export const DEMO_FOB = 482000;
export const DEMO_TOTALS: FamilyAmounts = DEMO_MONTHS.reduce<FamilyAmounts>(
  (acc, m) => ({
    freight: acc.freight + m.families.freight,
    port: acc.port + m.families.port,
    customs: acc.customs + m.families.customs,
    delivery: acc.delivery + m.families.delivery,
    dnd: acc.dnd + m.families.dnd,
    other: acc.other + m.families.other,
  }),
  { freight: 0, port: 0, customs: 0, delivery: 0, dnd: 0, other: 0 },
);
export const DEMO_FEES = Object.values(DEMO_TOTALS).reduce((s, v) => s + v, 0);
export const DEMO_COEF = (DEMO_FOB + DEMO_FEES) / DEMO_FOB;

const arrival = (key: string, label: string, box: string, date: string, fob: number, landed: number, estimated = false): TrendPoint => ({
  key,
  label,
  title: `${box} · ${date}`,
  fob,
  landed,
  estimated,
});

/** One tyre reference over eight containers: the purchase price barely moves, the landed cost does. */
export const DEMO_SKU = "PNEU-20555R16-91V";
export const DEMO_TREND: TrendPoint[] = [
  arrival("1", "30/03", "MSCU4821994", "30/03/2026", 13.08, 15.93),
  arrival("2", "27/04", "MSCU5059564", "27/04/2026", 12.94, 15.14),
  arrival("3", "11/05", "MSCU5138750", "11/05/2026", 12.98, 16.39),
  arrival("4", "10/06", "MSCU5376321", "10/06/2026", 13.31, 17.6),
  arrival("5", "22/06", "HLXU5455513", "22/06/2026", 13.18, 15.75),
  arrival("6", "13/07", "CMAU5613890", "13/07/2026", 13.25, 16.62),
  arrival("7", "03/08", "MSCU5772277", "03/08/2026", 13.42, 15.56),
  arrival("8", "12/08", "CMAU5851461", "12/08/2026", 13.42, 17.31),
];
export const DEMO_SALE_PRICE = 24.9;

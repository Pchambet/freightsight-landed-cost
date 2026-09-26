import { NextIntlClientProvider } from "next-intl";
import { ArrowUpRight, CheckCircle2 } from "lucide-react";
import BreakdownBar from "@/components/charts/BreakdownBar";
import StackedColumns from "@/components/charts/StackedColumns";
import UnitCostTrend from "@/components/charts/UnitCostTrend";
import BrowserFrame from "@/features/marketing/BrowserFrame";
import { DEMO_COEF, DEMO_CURRENCY, DEMO_FEES, DEMO_FOB, DEMO_MONTHS, DEMO_SALE_PRICE, DEMO_SKU, DEMO_TOTALS, DEMO_TREND, FAMILY_LABELS_FR } from "@/features/marketing/demoData";
import MarginHelper from "@/features/skus/MarginHelper";
import fr from "../../../messages/fr.json";

/*
 * The public site shows the product itself, not pictures of it: these are the application's own
 * chart components, fed with the example figures of demoData.ts. They answer the pointer like the
 * real screens do, stay sharp on any display, and cannot drift from what the application looks like.
 * The site is written in French whatever the visitor's app locale: the blocks are pinned to "fr".
 */
const eur = (n: number, digits = 0) => new Intl.NumberFormat("fr-FR", { style: "currency", currency: DEMO_CURRENCY, minimumFractionDigits: digits, maximumFractionDigits: digits }).format(n);
const ratio = (n: number) => new Intl.NumberFormat("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n);

function Tile({ label, value, sub, tone }: { label: string; value: string; sub: React.ReactNode; tone?: "danger" }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-3.5 py-3">
      <div className="text-[10px] font-medium uppercase tracking-wider text-slate-500">{label}</div>
      <div className={`mt-1 text-xl font-semibold tracking-tight figure-proportional ${tone === "danger" ? "text-red-700" : "text-slate-900"}`}>{value}</div>
      <div className="mt-1 text-[11px] text-slate-500">{sub}</div>
    </div>
  );
}

/** Hero: the overview, as the application opens on it. */
export function OverviewShowcase() {
  return (
    <BrowserFrame path="vue d'ensemble">
      <div className="grid grid-cols-3 gap-2.5">
        <Tile
          label="Coefficient d'approche"
          value={ratio(DEMO_COEF)}
          sub={<><span className="inline-flex items-center gap-0.5 rounded bg-red-50 px-1 py-0.5 font-medium text-red-800"><ArrowUpRight className="w-3 h-3" aria-hidden />+0,03</span> <span className="whitespace-nowrap">vs 6 mois préc.</span></>}
        />
        <Tile label="Frais d'approche" value={eur(DEMO_FEES)} sub={`sur ${eur(DEMO_FOB)} d'achats`} />
        <Tile label="Surestaries payées" value={eur(DEMO_TOTALS.dnd)} sub="2 600 € évités" tone="danger" />
      </div>
      <div className="mt-3 rounded-lg border border-slate-200 bg-white p-4">
        <div className="text-xs font-semibold text-slate-900">Frais d&apos;approche par mois</div>
        <div className="text-[11px] text-slate-500 mb-3">Par mois d&apos;arrivée, hors prix d&apos;achat et hors TVA import.</div>
        <StackedColumns columns={DEMO_MONTHS} currency={DEMO_CURRENCY} locale="fr" familyLabels={FAMILY_LABELS_FR} totalLabel="Total des frais" height={150} />
      </div>
    </BrowserFrame>
  );
}

/** From purchase price to landed cost, the bar of a container or an order. */
export function BreakdownShowcase() {
  return (
    <BrowserFrame path="vue d'ensemble · 6 mois">
      <div className="rounded-lg border border-slate-200 bg-white p-4">
        <div className="flex items-start justify-between gap-3 mb-4">
          <div>
            <div className="text-xs font-semibold text-slate-900">Du prix d&apos;achat au coût rendu</div>
            <div className="text-[11px] text-slate-500">Six mois d&apos;arrivages, TVA import exclue.</div>
          </div>
          <div className="text-right">
            <div className="text-[10px] uppercase tracking-wider text-slate-500">Coefficient d&apos;approche</div>
            <div className="text-xl font-semibold leading-tight figure-proportional">{ratio(DEMO_COEF)}</div>
          </div>
        </div>
        <BreakdownBar fob={DEMO_FOB} families={DEMO_TOTALS} currency={DEMO_CURRENCY} locale="fr" fobLabel="Prix d'achat (FOB)" familyLabels={FAMILY_LABELS_FR} ofFobLabel="du FOB" dense />
      </div>
    </BrowserFrame>
  );
}

/** One reference, arrival after arrival. */
export function TrendShowcase() {
  return (
    <BrowserFrame path={`références · ${DEMO_SKU}`}>
      <div className="rounded-lg border border-slate-200 bg-white p-4">
        <div className="text-xs font-semibold text-slate-900">Coût unitaire, arrivage après arrivage</div>
        <div className="text-[11px] text-slate-500 mb-3">Un point par conteneur. L&apos;écart entre les deux courbes, ce sont les frais d&apos;approche par unité.</div>
        <UnitCostTrend points={DEMO_TREND} currency={DEMO_CURRENCY} locale="fr" labels={{ landed: "Rendu unitaire", fob: "Achat unitaire (FOB)", approach: "Frais d'approche par unité", estimated: "estimé" }} height={180} />
      </div>
    </BrowserFrame>
  );
}

/** The margin read on the purchase price against the real one — the visitor moves the target. */
export function MarginShowcase() {
  const last = DEMO_TREND[DEMO_TREND.length - 1];
  return (
    <BrowserFrame path={`références · ${DEMO_SKU}`}>
      <div className="rounded-lg border border-slate-200 bg-white p-4">
        <div className="text-xs font-semibold text-slate-900">Votre marge, au vrai coût</div>
        <div className="text-[11px] text-slate-500 mb-4">Vendu {eur(DEMO_SALE_PRICE, 2)} HT, acheté {eur(last.fob, 2)}, rendu {eur(last.landed, 2)}. Déplacez la marge visée.</div>
        <NextIntlClientProvider locale="fr" messages={{ skus: { margin: fr.skus.margin } }}>
          <MarginHelper unitFob={last.fob} unitLanded={last.landed} salePrice={DEMO_SALE_PRICE} currency={DEMO_CURRENCY} />
        </NextIntlClientProvider>
      </div>
    </BrowserFrame>
  );
}

/** A forwarder invoice, read and waiting for its two clicks. A still: there is nothing to save here. */
export function ReviewShowcase() {
  const lines: [string, string, string][] = [
    ["Fret maritime CNNGB / FRLEH", "Fret maritime", "2 412,60 €"],
    ["THC destination Le Havre", "Manutention portuaire (THC)", "262,00 €"],
    ["Dédouanement import", "Frais de dédouanement", "135,00 €"],
    ["Camionnage Le Havre – entrepôt", "Camionnage / livraison", "465,00 €"],
  ];
  return (
    <BrowserFrame path="factures · FA-2026-1112">
      <div className="rounded-lg border border-slate-200 bg-white overflow-hidden">
        <div className="flex items-center gap-2 border-b border-slate-200 px-4 py-2.5 text-[11px] font-medium text-emerald-800">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" aria-hidden />
          Les 4 lignes retenues sont prêtes à devenir des coûts
        </div>
        <ul className="divide-y divide-slate-100 text-xs">
          {lines.map(([label, type, amount]) => (
            <li key={label} className="flex items-center gap-3 px-4 py-2.5">
              <CheckCircle2 className="w-4 h-4 shrink-0 text-blue-600" aria-hidden />
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium text-slate-900">{label}</span>
                <span className="block truncate text-slate-500">{type} · conteneur CMAU6089031</span>
              </span>
              <span className="shrink-0 font-medium text-slate-900">{amount}</span>
            </li>
          ))}
        </ul>
        <div className="flex items-center justify-between gap-3 border-t border-slate-200 bg-slate-50 px-4 py-2.5 text-xs">
          <span className="text-emerald-800">Lignes retenues : 3 274,60 € = total HT de la facture</span>
          <span className="rounded-md bg-slate-900 px-3 py-1.5 font-medium text-white">Confirmer 4 lignes</span>
        </div>
      </div>
    </BrowserFrame>
  );
}

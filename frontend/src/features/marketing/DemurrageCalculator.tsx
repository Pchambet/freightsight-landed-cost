"use client";

import { useMemo, useState, useSyncExternalStore } from "react";
import { Check, Link2 } from "lucide-react";

/**
 * Order-of-magnitude demurrage for a 40' dry container, calendar days after free time, from the
 * 2026 public import tariffs of the main carriers calling at these ports (CMA CGM, MSC, Maersk).
 * Detention (the container not returned to the depot) and terminal storage come on top. This is
 * the single source of truth for the demurrage figure quoted anywhere on the site — see A5-1:
 * the landing banner and this calculator used to disagree by up to 15€ on the same claim.
 */
const PORTS = {
  ANR: { label: "Anvers", tiers: [60, 95, 145] },
  LEH: { label: "Le Havre", tiers: [55, 85, 130] },
  RTM: { label: "Rotterdam", tiers: [65, 100, 150] },
  MRS: { label: "Marseille-Fos", tiers: [55, 85, 130] },
} as const;
type PortCode = keyof typeof PORTS;

function perContainer(days: number, tiers: readonly number[]): number {
  let total = 0;
  for (let d = 1; d <= days; d++) total += d <= 5 ? tiers[0] : d <= 10 ? tiers[1] : tiers[2];
  return total;
}

const eur = (n: number) => new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(n);

type Figures = { port: PortCode; containers: number; days: number };

/** A shared link's own figures (/?port=ANR&c=3&d=6#calculateur), each one only if it makes sense. */
function fromQuery(search: string): Figures {
  const q = new URLSearchParams(search);
  const port = q.get("port");
  const c = Number(q.get("c"));
  const d = Number(q.get("d"));
  return {
    port: port && port in PORTS ? (port as PortCode) : "ANR",
    containers: c >= 1 && c <= 200 ? c : 3,
    days: d >= 1 && d <= 60 ? d : 6,
  };
}

const noSubscription = () => () => {};

/**
 * Rendered on the server with the default figures; a shared link's figures are read from the address
 * once the page runs in a browser, and the calculator starts again from them. Read with
 * useSearchParams, the calculator needed a Suspense boundary of its own, streamed in a second pass;
 * React 19.2 reveals such a segment on an animation frame, which a tab loaded in the background never
 * gets, so hydration took the boundary over and a hidden copy of the calculator, with the same ids,
 * stayed after `</main>` (`div#S:0[hidden]`). Harmless to a reader — the visible copy comes first —
 * but a boundary the calculator never needed.
 */
export default function DemurrageCalculator() {
  const search = useSyncExternalStore(noSubscription, () => window.location.search, () => "");
  return <Calculator key={search} initial={fromQuery(search)} />;
}

function Calculator({ initial }: { initial: Figures }) {
  const [port, setPort] = useState<PortCode>(initial.port);
  const [containers, setContainers] = useState(initial.containers);
  const [days, setDays] = useState(initial.days);
  const [copied, setCopied] = useState(false);

  const tiers = PORTS[port].tiers;
  const one = useMemo(() => perContainer(days, tiers), [days, tiers]);
  const total = one * containers;

  async function share() {
    const url = new URL(window.location.href);
    url.search = new URLSearchParams({ port, c: String(containers), d: String(days) }).toString();
    url.hash = "calculateur";
    window.history.replaceState(null, "", url.toString());
    try { await navigator.clipboard.writeText(url.toString()); setCopied(true); setTimeout(() => setCopied(false), 2000); } catch { /* clipboard blocked: the URL bar already holds the link */ }
  }

  const field = "w-full bg-slate-800 border border-slate-700 rounded-md px-3 py-2 text-sm text-white focus:ring-2 focus:ring-blue-500 outline-none";
  const label = "block text-xs font-medium text-slate-400 mb-1";

  return (
    <div className="grid gap-8 md:grid-cols-5 items-start">
      <div className="md:col-span-2 grid gap-4">
        <div>
          <label className={label} htmlFor="dd-port">Port de déchargement</label>
          <select id="dd-port" className={field} value={port} onChange={(e) => setPort(e.target.value as PortCode)}>
            {Object.entries(PORTS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </select>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className={label} htmlFor="dd-c">Conteneurs</label>
            <input id="dd-c" type="number" min={1} max={200} inputMode="numeric" className={field} value={containers} onChange={(e) => setContainers(Math.max(1, Math.min(200, Number(e.target.value) || 1)))} />
          </div>
          <div>
            <label className={label} htmlFor="dd-d">Jours au-delà de la franchise</label>
            <input id="dd-d" type="number" min={1} max={60} inputMode="numeric" className={field} value={days} onChange={(e) => setDays(Math.max(1, Math.min(60, Number(e.target.value) || 1)))} />
          </div>
        </div>
        <p className="text-xs text-slate-400 leading-relaxed">
          Ordre de grandeur pour un 40&apos; dry, jours calendaires après la franchise (le « free time », souvent 7 jours) —
          {" "}{tiers[0]} € les 5 premiers jours, {tiers[1]} € du 6<sup>e</sup> au 10<sup>e</sup>, {tiers[2]} € au-delà. Hors détention et stockage terminal, qui s&apos;ajoutent.
          {" "}Barèmes publics import des compagnies, 2026 (CMA CGM, MSC, Maersk) : ordre de grandeur indicatif, votre transitaire peut appliquer un barème différent.
        </p>
      </div>
      <div className="md:col-span-3 bg-slate-800/60 border border-slate-700 rounded-lg p-6">
        <div className="text-xs font-medium uppercase tracking-wider text-slate-400">Surestaries estimées</div>
        <div className="mt-1 text-5xl font-semibold tracking-tight text-white tabular-nums">{eur(total)}</div>
        <div className="mt-2 text-sm text-slate-300">
          soit {eur(one)} par conteneur, {days} jour{days > 1 ? "s" : ""} de trop à {PORTS[port].label}. Découvert, en général, sur la facture du transitaire six semaines plus tard.
        </div>
        <div className="mt-5 grid grid-cols-3 gap-3 text-sm">
          <div className="bg-slate-900/60 rounded-md p-3"><div className="text-slate-400 text-xs">Par jour, au tarif du jour {days}</div><div className="text-white tabular-nums font-medium">{eur((days <= 5 ? tiers[0] : days <= 10 ? tiers[1] : tiers[2]) * containers)}</div></div>
          <div className="bg-slate-900/60 rounded-md p-3"><div className="text-slate-400 text-xs">Si vous l&apos;aviez su à J-3</div><div className="text-white tabular-nums font-medium">{eur(perContainer(Math.max(0, days - 3), tiers) * containers)}</div></div>
          <div className="bg-slate-900/60 rounded-md p-3"><div className="text-slate-400 text-xs">Évitable</div><div className="text-emerald-400 tabular-nums font-medium">{eur(total - perContainer(Math.max(0, days - 3), tiers) * containers)}</div></div>
        </div>
        <button type="button" onClick={share} className="mt-5 inline-flex items-center gap-2 text-sm text-slate-300 hover:text-white">
          {copied ? <Check className="w-4 h-4 text-emerald-400" /> : <Link2 className="w-4 h-4" />}{copied ? "Lien copié" : "Copier le lien vers cette simulation"}
        </button>
      </div>
    </div>
  );
}

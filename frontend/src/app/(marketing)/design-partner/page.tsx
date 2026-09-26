import type { Metadata } from "next";
import { Ship } from "lucide-react";
import { site } from "@/lib/site";

const description = "Cinq PME importatrices, huit semaines, leurs vrais dossiers.";

export const metadata: Metadata = {
  title: "Programme design partner",
  description,
  alternates: { canonical: `${site.url}/design-partner` },
  openGraph: {
    title: "Programme design partner · FreightSight",
    description,
    url: `${site.url}/design-partner`,
    siteName: site.name,
    locale: "fr_FR",
    type: "website",
  },
  twitter: { card: "summary_large_image" },
};

/**
 * Printable one-pager (A4). The marketing header and footer hide on print, so the logo below is
 * screen-hidden and print-only: on screen the persistent header above already shows it, and
 * showing it twice on the same page read badly (A4 audit, row A8).
 */
export default function DesignPartnerPage() {
  return (
    <main className="max-w-3xl mx-auto px-5 py-12 print:py-0 print:max-w-none print:text-[11px] print:leading-snug">
      <div className="flex justify-end print:justify-between items-center">
        <div className="hidden print:flex items-center gap-2">
          <div className="bg-blue-600 p-1.5 rounded-md"><Ship className="w-4 h-4 text-white" /></div>
          <span className="text-lg font-semibold tracking-tight">Freight<span className="text-blue-600">Sight</span></span>
        </div>
        <div className="text-xs uppercase tracking-wider text-slate-500">Programme design partner · septembre 2026</div>
      </div>

      <h1 className="mt-8 print:mt-4 text-3xl print:text-2xl font-semibold tracking-tight text-balance">Le coût de revient rendu de vos importations, calculé sur vos vrais dossiers, en huit semaines.</h1>

      <div className="mt-8 print:mt-4 grid gap-8 print:gap-4 md:grid-cols-2 print:grid-cols-2">
        <section>
          <h2 className="text-sm font-semibold uppercase tracking-wider text-blue-700">Le problème</h2>
          <p className="mt-2 text-sm print:text-[11px] text-slate-700 leading-relaxed print:leading-snug">Une PME qui importe fixe ses prix sur le FOB et découvre son coût réel par commande 30 à 60 jours plus tard, quand arrivent les factures du transitaire, de la douane et les surestaries. Le prorata à la valeur fait dans Excel est faux pour tout ce qui est lourd ou volumineux, et personne ne recolle une facture sur trois conteneurs et cinq commandes.</p>
        </section>
        <section>
          <h2 className="text-sm font-semibold uppercase tracking-wider text-blue-700">Ce que fait FreightSight</h2>
          <p className="mt-2 text-sm print:text-[11px] text-slate-700 leading-relaxed print:leading-snug">Il importe vos commandes, rattache chaque coût à son périmètre (expédition, conteneur, commande, ligne) et le ventile jusqu&apos;à la ligne selon la méthode choisie : valeur, poids, volume, quantité, manuel. Les droits sont calculés sur la base CIF. Vous obtenez le coût rendu et le coût unitaire par SKU, en euros, au taux BCE du jour de facture, prêt pour l&apos;ERP.</p>
        </section>
        <section>
          <h2 className="text-sm font-semibold uppercase tracking-wider text-blue-700">Ce qu&apos;on vous demande</h2>
          <ul className="mt-2 text-sm print:text-[11px] text-slate-700 leading-relaxed print:leading-snug list-disc pl-5 space-y-1">
            <li>Une heure par semaine avec le fondateur, pendant huit semaines</li>
            <li>Dix factures transitaire et cinq commandes, anonymisées si vous préférez</li>
            <li>Vos exports actuels (Sage, EBP, Odoo, Excel), tels quels</li>
            <li>Un retour franc : ce qui est juste, ce qui ne l&apos;est pas, ce qui manque</li>
          </ul>
        </section>
        <section>
          <h2 className="text-sm font-semibold uppercase tracking-wider text-blue-700">Ce que vous recevez</h2>
          <ul className="mt-2 text-sm print:text-[11px] text-slate-700 leading-relaxed print:leading-snug list-disc pl-5 space-y-1">
            <li>Votre coût rendu par SKU sur vos dossiers, réconcilié avec votre Excel au centime</li>
            <li>Huit semaines gratuites, puis −50 % sur le plan Growth pendant douze mois</li>
            <li>Un accès direct au fondateur et votre mot sur la feuille de route</li>
            <li>La lecture de vos factures transitaire PDF et le connecteur Odoo, utilisables dès le départ ; le suivi automatique des conteneurs et les alertes par e-mail, activés à l&apos;ouverture de votre pilote</li>
          </ul>
        </section>
      </div>

      <section className="mt-8 print:mt-4">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-blue-700">Les huit semaines</h2>
        <div className="mt-3 grid grid-cols-4 gap-3 text-xs">
          {[
            ["S1", "Import de vos commandes et conteneurs, mapping de vos colonnes"],
            ["S2–S3", "Saisie des coûts des dossiers récents, premier coût par SKU, comparaison avec votre Excel"],
            ["S4–S6", "Suivi des conteneurs en cours, alertes dernier jour franc, lecture de vos factures"],
            ["S7–S8", "Bilan chiffré : écarts trouvés, surestaries évitées, décision sur la suite"],
          ].map(([w, t]) => (
            <div key={w} className="rounded-md border border-slate-200 p-3"><div className="font-semibold text-slate-900">{w}</div><div className="mt-1 text-slate-600 leading-relaxed">{t}</div></div>
          ))}
        </div>
      </section>

      <section className="mt-8 print:mt-4 grid gap-6 print:gap-4 md:grid-cols-2 print:grid-cols-2 text-sm">
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-wider text-blue-700">Vos données</h2>
          <p className="mt-2 text-slate-700 leading-relaxed">Hébergées à {site.hostingRegion}, isolées par société jusque dans la base de données, jamais utilisées pour entraîner un modèle ni partagées. Export et suppression sur simple demande. Détail sur la page sécurité.</p>
        </div>
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-wider text-blue-700">Profil recherché</h2>
          <p className="mt-2 text-slate-700 leading-relaxed">PME importatrice de 5 à 60 conteneurs par mois, avec un responsable supply, un COO ou un DAF qui veut un chiffre juste ce trimestre. Odoo, Sage, Cegid ou Pennylane. Négoce, maison et jardin, mobilier, pièces, épicerie fine, D2C.</p>
        </div>
      </section>

      <div className="mt-10 print:mt-5 border-t border-slate-200 pt-5 print:pt-3 flex flex-wrap items-center justify-between gap-3 text-sm">
        <div className="text-slate-700">Pierre Chambet, fondateur · <span className="text-slate-500">ancien intégrateur Odoo côté client</span></div>
        <div className="flex flex-col items-end gap-1.5">
          <a href={site.bookingUrl} className="inline-flex items-center bg-slate-900 text-white font-medium py-2 px-4 rounded-md print:bg-white print:text-slate-900 print:border print:border-slate-300">Réserver 20 minutes</a>
          <a href={site.quickCheckUrl} className="print:hidden text-slate-500 hover:underline text-xs">Ou envoyez une facture transitaire : coût rendu sous 24&nbsp;h.</a>
        </div>
      </div>
    </main>
  );
}

import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { ArrowRight, Check, FileSpreadsheet, Receipt, Sigma } from "lucide-react";
import DemurrageCalculator from "@/features/marketing/DemurrageCalculator";
import { BreakdownShowcase, MarginShowcase, OverviewShowcase, ReviewShowcase, TrendShowcase } from "@/features/marketing/ProductShowcase";
import { site } from "@/lib/site";

const description =
  "FreightSight ventile le fret, la douane et les frais de terminal sur chaque ligne de commande, conteneur par conteneur, à partir de vos factures. Estimé à l'embarquement, exact à la facture.";

export const metadata: Metadata = {
  title: { absolute: "FreightSight · Coût de revient rendu pour les PME importatrices" },
  description,
  alternates: { canonical: site.url },
  openGraph: {
    title: "FreightSight · Coût de revient rendu pour les PME importatrices",
    description,
    url: site.url,
    siteName: site.name,
    locale: "fr_FR",
    type: "website",
  },
  twitter: { card: "summary_large_image" },
};

const btnPrimary = "inline-flex items-center gap-2 bg-slate-900 hover:bg-slate-800 text-white text-sm font-medium py-2.5 px-5 rounded-md";
const btnGhost = "inline-flex items-center gap-2 text-sm font-medium py-2.5 px-5 rounded-md border border-slate-300 hover:bg-slate-50";

// No session check here: the public pages run outside the Clerk middleware so that a visitor with no
// cookie — a search engine, a link preview — gets the page itself (see proxy.ts). Someone signed in
// finds "Ouvrir l'application" in the header instead of being redirected.
export default function LandingPage() {
  return (
    <main>
      {/* Hero */}
      <section className="max-w-6xl mx-auto px-5 pt-16 pb-12 grid gap-10 lg:grid-cols-12 items-center">
        <div className="lg:col-span-6">
          <div className="text-xs font-medium uppercase tracking-wider text-blue-700">Pour les PME importatrices · 5 à 60 conteneurs par mois</div>
          <h1 className="mt-4 text-4xl md:text-5xl font-semibold tracking-tight leading-[1.05] text-balance">
            Le coût de revient rendu de vos importations, par commande et par SKU.
          </h1>
          <p className="mt-5 text-lg text-slate-600 leading-relaxed max-w-xl">
            FreightSight ventile le fret, la douane et les frais de terminal sur chaque ligne de commande, conteneur par conteneur, à partir de vos factures. Estimé à l&apos;embarquement, exact à la facture, écart affiché. Sans changer d&apos;ERP.
          </p>
          <div className="mt-7 flex flex-wrap gap-3">
            <a href={site.bookingUrl} className={btnPrimary}>Réserver 20 minutes <ArrowRight className="w-4 h-4" /></a>
            <Link href="#produit" className={btnGhost}>Voir comment ça marche</Link>
          </div>
          <p className="mt-4 text-sm text-slate-500">
            <a href={site.quickCheckUrl} className="text-blue-700 hover:underline font-medium">Envoyez une facture transitaire et le bon de commande</a> : vous recevez le coût rendu par référence sous 24&nbsp;h.
          </p>
          <ul className="mt-7 flex flex-wrap gap-x-5 gap-y-2 text-sm text-slate-500">
            {["Hébergé en Europe", "Données isolées par société", "Aucun modèle entraîné sur vos données"].map((t) => (
              <li key={t} className="inline-flex items-center gap-1.5"><Check className="w-4 h-4 text-emerald-600" />{t}</li>
            ))}
          </ul>
        </div>
        <div className="lg:col-span-6">
          <OverviewShowcase />
          <p className="mt-3 text-xs text-slate-500">L&apos;écran d&apos;accueil de l&apos;application, en vrai : survolez un mois. Données d&apos;exemple.</p>
        </div>
      </section>

      {/* Problem */}
      <section className="border-y border-slate-200 bg-slate-50">
        <div className="max-w-6xl mx-auto px-5 py-12 grid gap-8 md:grid-cols-3">
          {[
            ["J+30 à J+60", "C'est quand arrivent les factures du transitaire et les surestaries. Le prix de vente, lui, a été fixé sur le FOB."],
            ["55 à 150 € par jour", "Surestaries par conteneur une fois la franchise écoulée, selon le port et le palier (voir le calculateur ci-dessous). Trois conteneurs oubliés une semaine coûtent le prix d'une palette."],
            ["2 à 3 jours par mois", "Passés à recoller factures transitaire, DAU et bons de commande dans Excel, avec un prorata à la valeur qui est faux pour tout ce qui est lourd ou volumineux."],
          ].map(([n, t]) => (
            <div key={n}>
              <div className="text-3xl font-semibold tracking-tight tabular-nums">{n}</div>
              <p className="mt-2 text-sm text-slate-600 leading-relaxed">{t}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Product */}
      <section id="produit" className="max-w-6xl mx-auto px-5 py-20 scroll-mt-20">
        <h2 className="text-3xl font-semibold tracking-tight text-balance max-w-2xl">Le coefficient d&apos;approche que vous citez de tête, calculé sur vos vraies factures.</h2>
        <p className="mt-4 text-slate-600 max-w-2xl leading-relaxed">Chaque coût est rattaché à un seul périmètre, expédition, conteneur, commande ou ligne, et redescend jusqu&apos;aux lignes chargées selon la méthode que vous choisissez. Les droits sont calculés en seconde passe sur la valeur CIF, comme en douane. Ce qui ne peut pas être ventilé est signalé, jamais deviné.</p>

        <div className="mt-14 space-y-20">
          <div className="grid gap-10 lg:grid-cols-2 items-center">
            <div>
              <div className="text-xs font-medium uppercase tracking-wider text-blue-700">Par conteneur, par commande</div>
              <h3 className="mt-3 text-2xl font-semibold tracking-tight text-balance">Du prix d&apos;achat au coût rendu, en une barre.</h3>
              <p className="mt-3 text-slate-600 leading-relaxed">Le fret, les frais portuaires, la douane, la livraison, les surestaries : ce que chaque famille de frais ajoute au prix payé au fournisseur, en euros et en pourcentage du FOB. Une facture pour trois conteneurs et cinq commandes est ventilée au centime, à la valeur, au poids, au volume ou à la quantité.</p>
              <ul className="mt-5 space-y-2 text-sm text-slate-700">
                {["Cinq méthodes de ventilation, et un « et si » pour voir ce que chacune change avant de choisir", "Multi-devises au taux BCE du jour de facture, figé", "La TVA import est suivie pour la trésorerie et exclue du coût de revient"].map((t) => (
                  <li key={t} className="flex gap-2.5"><Check className="w-4 h-4 mt-0.5 text-emerald-600 shrink-0" />{t}</li>
                ))}
              </ul>
            </div>
            <BreakdownShowcase />
          </div>

          <div className="grid gap-10 lg:grid-cols-2 items-center">
            <div className="lg:order-2">
              <div className="text-xs font-medium uppercase tracking-wider text-blue-700">Par référence</div>
              <h3 className="mt-3 text-2xl font-semibold tracking-tight text-balance">Le prix d&apos;achat ne bouge presque pas. Le coût rendu, si.</h3>
              <p className="mt-3 text-slate-600 leading-relaxed">Une page par référence, un point par conteneur qui l&apos;a portée. Quand le dernier arrivage coûte 1,75 € de plus par unité, la page dit d&apos;où vient l&apos;écart : le fret, la douane, ou le fournisseur. Même quand la commande est répartie sur deux conteneurs.</p>
            </div>
            <div className="lg:order-1"><TrendShowcase /></div>
          </div>

          <div className="grid gap-10 lg:grid-cols-2 items-center">
            <div>
              <div className="text-xs font-medium uppercase tracking-wider text-blue-700">La marge</div>
              <h3 className="mt-3 text-2xl font-semibold tracking-tight text-balance">Votre tarif est fixé sur le FOB. Votre marge, elle, se joue au coût rendu.</h3>
              <p className="mt-3 text-slate-600 leading-relaxed">Renseignez le prix de vente d&apos;une référence : FreightSight met côte à côte la marge lue sur le prix d&apos;achat et la marge réelle, et calcule le prix à tenir pour la marge que vous visez. Essayez : le curseur est vivant.</p>
            </div>
            <MarginShowcase />
          </div>

          <div className="grid gap-10 lg:grid-cols-2 items-center">
            <div className="lg:order-2">
              <div className="text-xs font-medium uppercase tracking-wider text-blue-700">Les factures transitaire</div>
              <h3 className="mt-3 text-2xl font-semibold tracking-tight text-balance">Déposez le PDF. Relisez en deux clics.</h3>
              <p className="mt-3 text-slate-600 leading-relaxed">La facture est lue ligne par ligne par des règles, sans IA : type de frais, montant, conteneur concerné. Les doublons sont signalés, la somme des lignes est comparée au total HT, et rien n&apos;entre dans vos coûts sans votre validation.</p>
            </div>
            <div className="lg:order-1"><ReviewShowcase /></div>
          </div>
        </div>

        <figure className="mt-20 max-w-3xl">
          <div className="rounded-xl border border-slate-200 overflow-hidden bg-white">
            <Image src="/landing/signale.jpg" alt="Avertissement : base manquante sur une ligne, 1 200 € non ventilés, avec les totaux FOB, alloué, TVA import, rendu et non alloué" width={900} height={113} />
          </div>
          <figcaption className="mt-3 text-sm text-slate-600"><span className="font-medium text-slate-900">Ce qui ne peut pas être ventilé est signalé, jamais deviné.</span> Ici une ligne sans taux de droits : les 1 200 € de douane restent visibles en « non alloué » au lieu d&apos;être répartis au hasard.</figcaption>
        </figure>

        <div className="mt-16 grid gap-6 md:grid-cols-3">
          {[
            [FileSpreadsheet, "1 · Importez vos commandes", "CSV ou Excel, export Sage, EBP ou Odoo. Les accents, les points-virgules et les « 12 500,50 » passent. Le mapping des colonnes est mémorisé, chaque ligne en erreur est expliquée avant d'écrire quoi que ce soit."],
            [Receipt, "2 · Enregistrez les coûts", "Fret, THC, droits, camionnage, surestaries, en euros ou en dollars au taux BCE du jour de facture. Ou déposez la facture PDF du transitaire : elle est lue ligne par ligne, et rien n'entre dans les coûts sans votre validation."],
            [Sigma, "3 · Lisez le coût rendu", "Par conteneur, par commande, par référence, avec le coefficient d'approche, la part de chaque famille de frais et la marge au vrai coût."],
          ].map(([Icon, h, t]) => {
            const I = Icon as typeof FileSpreadsheet;
            return (
              <div key={h as string} className="rounded-lg border border-slate-200 p-5">
                <I className="w-5 h-5 text-blue-700" />
                <h3 className="mt-3 font-semibold">{h as string}</h3>
                <p className="mt-2 text-sm text-slate-600 leading-relaxed">{t as string}</p>
              </div>
            );
          })}
        </div>
      </section>

      {/* Calculator */}
      <section id="calculateur" className="bg-slate-900 text-white scroll-mt-20">
        <div className="max-w-6xl mx-auto px-5 py-20">
          <h2 className="text-3xl font-semibold tracking-tight text-balance max-w-2xl">Combien coûte un conteneur oublié à Anvers&nbsp;?</h2>
          <p className="mt-4 text-slate-300 max-w-2xl leading-relaxed">Le suivi de conteneurs ne vaut que par les euros qu&apos;il évite. FreightSight calcule le dernier jour franc de chaque conteneur et vous prévient à J-3, avec le montant en jeu.</p>
          <div className="mt-10"><DemurrageCalculator /></div>
        </div>
      </section>

      {/* Honesty */}
      <section className="max-w-6xl mx-auto px-5 py-20">
        <div className="grid gap-10 md:grid-cols-2">
          <div>
            <h2 className="text-2xl font-semibold tracking-tight">Ce qui est disponible aujourd&apos;hui</h2>
            <ul className="mt-5 space-y-2.5 text-sm text-slate-700">
              {[
                "Import CSV et Excel avec validation ligne par ligne",
                "Commandes réparties sur plusieurs conteneurs, conteneurs multi-commandes",
                "Cinq méthodes de ventilation, droits en seconde passe sur la base CIF",
                "Multi-devises au taux BCE figé à la date de facture",
                "Coût rendu et coût unitaire par ligne, par conteneur, par commande",
                "Suivi manuel des conteneurs avec dernier jour franc et niveau de risque",
                "Lecture des factures transitaire PDF par des règles, sans IA : ligne par ligne, doublons signalés, et rien n'entre dans les coûts sans votre validation",
                "Estimé à l'embarquement, recalé à la facture, écart affiché par commande",
                "Vue d'ensemble : coefficient d'approche, frais par mois et par famille, ce qui demande une décision aujourd'hui",
                "Une page par référence : coût unitaire par arrivage, explication de l'écart, marge au coût rendu et prix à tenir",
                "Barèmes de coûts et rapports pour le DAF, exportables en CSV",
                "Connecteur Odoo : lit vos commandes, écrit un brouillon de coût de revient",
                "Interface en français et en anglais, historique d'audit complet",
                "Comptes par société, isolation des données garantie en base, sauvegardes chiffrées chaque nuit, hébergement en Europe",
              ].map((t) => <li key={t} className="flex gap-2.5"><Check className="w-4 h-4 mt-0.5 text-emerald-600 shrink-0" />{t}</li>)}
            </ul>
            <p className="mt-5 text-xs text-slate-500">Connecteur Odoo : nécessite l&apos;accès API de votre Odoo (offre Custom, Odoo.sh ou on-premise). L&apos;offre Standard n&apos;expose pas cet accès ; l&apos;import CSV reste la voie d&apos;entrée dans ce cas.</p>
          </div>
          <div>
            <h2 className="text-2xl font-semibold tracking-tight">Activé avec les premiers pilotes</h2>
            <ul className="mt-5 space-y-2.5 text-sm text-slate-700">
              {[
                "Suivi automatique des conteneurs (événements, ETA, dernier jour franc), sans saisie manuelle",
                "Alertes par e-mail au dernier jour franc, avec le montant en jeu",
              ].map((t) => <li key={t} className="flex gap-2.5"><span className="w-4 h-4 mt-0.5 rounded-full border border-slate-300 shrink-0" />{t}</li>)}
            </ul>
            <p className="mt-5 text-sm text-slate-500">Ces deux briques sont construites et activées à l&apos;ouverture de votre pilote, pas dans plusieurs mois. Pas de « temps réel » par ailleurs : les factures arrivent à J+30, les surestaries à J+60. Le produit vend un chiffre estimé tout de suite, exact à la facture, et l&apos;écart entre les deux.</p>
          </div>
        </div>
      </section>

      {/* Founder */}
      <section className="max-w-6xl mx-auto px-5 pb-20">
        <div className="rounded-xl border border-slate-200 bg-slate-50 p-6 sm:p-8 flex flex-col sm:flex-row gap-6 items-start">
          <div className="w-14 h-14 rounded-full bg-slate-900 text-white flex items-center justify-center text-lg font-semibold shrink-0">PC</div>
          <div>
            <div className="font-semibold text-slate-900">Pierre Chambet, fondateur <span className="font-normal text-slate-500">· ancien intégrateur Odoo côté client</span></div>
            <p className="mt-2 text-sm text-slate-700 leading-relaxed max-w-2xl">Une PME qui importe fixe ses prix sur le FOB et découvre son coût réel par commande 30 à 60 jours plus tard, quand arrivent les factures du transitaire, de la douane et les surestaries. C&apos;est le problème que j&apos;ai vu de l&apos;intérieur, chez des clients Odoo, avant de le corriger avec FreightSight.</p>
          </div>
        </div>
      </section>

      {/* Design partner */}
      <section id="design-partner" className="border-y border-slate-200 bg-slate-50 scroll-mt-20">
        <div className="max-w-6xl mx-auto px-5 py-16 grid gap-10 lg:grid-cols-12 items-center">
          <div className="lg:col-span-7">
            <div className="text-xs font-medium uppercase tracking-wider text-blue-700">Programme design partner · 5 places</div>
            <h2 className="mt-3 text-3xl font-semibold tracking-tight text-balance">Huit semaines gratuites sur vos vrais dossiers, puis −50 % pendant douze mois.</h2>
            <div className="mt-6 grid gap-6 sm:grid-cols-2 text-sm">
              <div>
                <div className="font-semibold">Ce qu&apos;on vous demande</div>
                <ul className="mt-2 space-y-1.5 text-slate-600">
                  <li>Une heure par semaine avec le fondateur</li>
                  <li>Dix factures transitaire et cinq commandes, anonymisées si besoin</li>
                  <li>Un retour franc sur ce qui est juste et ce qui ne l&apos;est pas</li>
                </ul>
              </div>
              <div>
                <div className="font-semibold">Ce que vous recevez</div>
                <ul className="mt-2 space-y-1.5 text-slate-600">
                  <li>Votre coût rendu par SKU, réconcilié avec votre Excel</li>
                  <li>Un accès fondateur et votre mot sur la feuille de route</li>
                  <li>Le tarif partenaire, figé, quand la facturation démarre</li>
                </ul>
              </div>
            </div>
          </div>
          <div className="lg:col-span-5 flex flex-col gap-3">
            <a href={site.bookingUrl} className={btnPrimary + " justify-center"}>Réserver 20 minutes <ArrowRight className="w-4 h-4" /></a>
            <Link href="/design-partner" className={btnGhost + " justify-center"}>Lire le one-pager</Link>
            <p className="text-center text-sm text-slate-500">
              Ou <a href={site.quickCheckUrl} className="text-blue-700 hover:underline font-medium">envoyez une facture transitaire et le bon de commande</a> : vous recevez le coût rendu par référence sous 24&nbsp;h.
            </p>
          </div>
        </div>
      </section>

      {/* Pricing */}
      <section id="tarifs" className="max-w-6xl mx-auto px-5 py-20 scroll-mt-20">
        <h2 className="text-3xl font-semibold tracking-tight">Tarifs</h2>
        <p className="mt-3 text-slate-600 max-w-2xl">Par conteneur suivi et par mois. La lecture de factures est dans tous les plans. Tarifs indicatifs, figés avec les premiers clients.</p>
        <div className="mt-8 grid gap-5 md:grid-cols-3">
          {[
            ["Starter", "299 €", "10 conteneurs par mois", ["Import CSV / XLSX", "Ventilation et coût par SKU", "Lecture de factures transitaire", "Alertes dernier jour franc"]],
            ["Growth", "499 €", "40 conteneurs par mois", ["Tout Starter", "Connecteur Odoo", "Estimé → réel par commande"]],
            ["Scale", "999 €", "150 conteneurs par mois", ["Tout Growth", "Support prioritaire", "Accompagnement dédié à la mise en route"]],
          ].map(([name, price, vol, feats]) => (
            <div key={name as string} className={`rounded-lg border p-6 ${name === "Growth" ? "border-slate-900" : "border-slate-200"}`}>
              <div className="font-semibold">{name as string}</div>
              <div className="mt-2 text-3xl font-semibold tracking-tight tabular-nums">{price as string}<span className="text-sm font-normal text-slate-500"> / mois</span></div>
              <div className="text-sm text-slate-500">{vol as string}</div>
              <ul className="mt-4 space-y-1.5 text-sm text-slate-700">
                {(feats as string[]).map((f) => <li key={f} className="flex gap-2"><Check className="w-4 h-4 mt-0.5 text-emerald-600 shrink-0" />{f}</li>)}
              </ul>
            </div>
          ))}
        </div>
        <p className="mt-4 text-xs text-slate-500">Dépassement : 8 à 12 € par conteneur suivi au-delà du plan. Un incident de surestaries évité vaut 2 à 5 k€ ; un point de marge retrouvé sur 5 M€ d&apos;achats vaut 50 k€ par an.</p>
      </section>
    </main>
  );
}

import type { Metadata } from "next";
import ScrollRegion from "@/components/shared/ScrollRegion";
import { site } from "@/lib/site";

const description = "Où sont vos données, qui y accède, et ce que FreightSight ne fait pas.";

export const metadata: Metadata = {
  title: "Sécurité et données",
  description,
  alternates: { canonical: `${site.url}/securite` },
  openGraph: {
    title: "Sécurité et données · FreightSight",
    description,
    url: `${site.url}/securite`,
    siteName: site.name,
    locale: "fr_FR",
    type: "website",
  },
  twitter: { card: "summary_large_image" },
};

const SUBPROCESSORS = [
  ["Railway", "API et base de données PostgreSQL", "Amsterdam, Pays-Bas", "Toutes les données métier"],
  ["Vercel", "Rendu de l'interface (fonctions serveur région Paris, CDN mondial pour les fichiers statiques)", "Paris, France / CDN", "Données affichées à l'écran, en transit"],
  ["Clerk", "Authentification, organisations, invitations", "États-Unis, clauses contractuelles types", "E-mail, nom, appartenance à une société. Jamais une commande ni une facture."],
  ["Scaleway", "Sauvegardes chiffrées de la base de données", "Paris, France", "Une copie chiffrée (AES-256) de la base, chaque nuit ; la clé de chiffrement n'est pas chez Scaleway"],
  ["Sentry", "Supervision des erreurs", "Francfort, Allemagne", "Traces d'erreur, identifiants de requête. Jamais le contenu d'une facture ni d'une commande."],
  ["Frankfurter (taux BCE)", "Taux de change publics", "Union européenne", "Aucune donnée envoyée : lecture seule de taux publics"],
];

// Not live in production today (no Resend/Shipsgo key is configured). Listed now, ahead of the switch, not discovered after the fact by a DPO
// who cross-checks an alert e-mail's headers against this page (A4-5).
const SUBPROCESSORS_PENDING = [
  ["Shipsgo", "Suivi automatique des conteneurs", "Turquie", "Numéros de conteneur uniquement"],
  ["Resend", "Envoi des alertes par e-mail (dernier jour franc, surestaries)", "États-Unis, clauses contractuelles types", "Adresse e-mail du destinataire et contenu de l'alerte (référence conteneur ou commande)"],
];

export default function SecurityPage() {
  return (
    <main className="max-w-3xl mx-auto px-5 py-16">
      <div className="text-xs font-medium uppercase tracking-wider text-blue-700">Sécurité et données · mis à jour le 17 septembre 2026</div>
      <h1 className="mt-3 text-4xl font-semibold tracking-tight text-balance">Où sont vos données, qui y accède, et ce que nous ne faisons pas.</h1>
      <p className="mt-5 text-lg text-slate-600 leading-relaxed">Cette page est écrite pour votre DSI ou votre DPO. Elle décrit l&apos;état réel du service, pas une cible. Elle est mise à jour à chaque changement d&apos;architecture.</p>

      <h2 className="mt-12 text-xl font-semibold">Hébergement</h2>
      <p className="mt-3 text-slate-700 leading-relaxed">L&apos;API et la base de données tournent à {site.hostingRegion}, chez Railway. L&apos;interface est servie par Vercel, avec le rendu serveur exécuté dans la région de Paris. Les connexions sont chiffrées en TLS de bout en bout ; le stockage est chiffré au repos par l&apos;hébergeur.</p>

      <h2 className="mt-10 text-xl font-semibold">Isolation entre sociétés</h2>
      <p className="mt-3 text-slate-700 leading-relaxed">Chaque commande, conteneur, coût et import porte l&apos;identifiant de votre société. L&apos;application limite chaque requête à cette société, et la base de données l&apos;impose une seconde fois par des règles de sécurité au niveau des lignes (Row Level Security) : l&apos;utilisateur technique de l&apos;application n&apos;a pas le droit de les contourner. Un test automatisé vérifie à chaque déploiement qu&apos;une requête sans filtre ne renvoie aucune ligne d&apos;une autre société.</p>

      <h2 className="mt-10 text-xl font-semibold">Comptes et accès</h2>
      <p className="mt-3 text-slate-700 leading-relaxed">L&apos;authentification est confiée à Clerk : connexion par e-mail ou Google, double authentification disponible, organisations avec invitations et rôles. L&apos;API ne fait confiance qu&apos;à un jeton de session signé, vérifié à chaque appel contre les clés publiques de Clerk, et restreint au domaine de l&apos;application. Aucun identifiant de société ne transite dans les requêtes.</p>

      <h2 className="mt-10 text-xl font-semibold">Sous-traitants</h2>
      <ScrollRegion label="Sous-traitants" className="mt-4 rounded-lg border border-slate-200">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wider text-slate-500"><tr><th className="text-left px-4 py-2.5">Fournisseur</th><th className="text-left px-4 py-2.5">Rôle</th><th className="text-left px-4 py-2.5">Localisation</th><th className="text-left px-4 py-2.5">Données concernées</th></tr></thead>
          <tbody className="divide-y divide-slate-200">
            {SUBPROCESSORS.map((r) => <tr key={r[0]}>{r.map((c, i) => <td key={i} className={`px-4 py-3 align-top ${i === 0 ? "font-medium whitespace-nowrap" : "text-slate-700"}`}>{c}</td>)}</tr>)}
          </tbody>
        </table>
      </ScrollRegion>
      <h3 className="mt-6 text-sm font-semibold uppercase tracking-wider text-slate-500">À l&apos;activation du suivi automatique et des alertes</h3>
      <p className="mt-2 text-sm text-slate-500">Construits, pas encore branchés en production. Le jour où votre pilote les active, voici ce qui commence à recevoir des données :</p>
      <ScrollRegion label="Sous-traitants à l'activation du suivi automatique et des alertes" className="mt-3 rounded-lg border border-slate-200">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wider text-slate-500"><tr><th className="text-left px-4 py-2.5">Fournisseur</th><th className="text-left px-4 py-2.5">Rôle</th><th className="text-left px-4 py-2.5">Localisation</th><th className="text-left px-4 py-2.5">Données concernées</th></tr></thead>
          <tbody className="divide-y divide-slate-200">
            {SUBPROCESSORS_PENDING.map((r) => <tr key={r[0]}>{r.map((c, i) => <td key={i} className={`px-4 py-3 align-top ${i === 0 ? "font-medium whitespace-nowrap" : "text-slate-700"}`}>{c}</td>)}</tr>)}
          </tbody>
        </table>
      </ScrollRegion>
      <p className="mt-3 text-sm text-slate-500">Aujourd&apos;hui, la lecture des factures est faite par des règles, sans fournisseur d&apos;IA. Si une lecture assistée par IA est activée un jour, ce sera en option, société par société sur demande, et le fournisseur sera ajouté à cette page avant toute activation.</p>

      <h2 className="mt-10 text-xl font-semibold">Ce que nous ne faisons pas</h2>
      <ul className="mt-3 space-y-2 text-slate-700 list-disc pl-5">
        <li>Entraîner un modèle sur vos données, ni les utiliser pour un autre client.</li>
        <li>Revendre, partager ou croiser vos volumes, fournisseurs ou prix entre sociétés.</li>
        <li>Stocker vos identifiants bancaires : le service ne fait aucun paiement.</li>
        <li>Créer un coût sans qu&apos;un humain l&apos;ait validé, y compris pour une facture lue automatiquement.</li>
      </ul>

      <h2 className="mt-10 text-xl font-semibold">Vos droits sur vos données</h2>
      <p className="mt-3 text-slate-700 leading-relaxed">Vous pouvez demander à tout moment un export complet (CSV) et la suppression définitive de votre société. La suppression est exécutée sous 30 jours et confirmée par écrit. Un accord de traitement des données (DPA) reprenant cette page et la liste des sous-traitants est fourni à la signature du programme design partner.</p>

      <h2 className="mt-10 text-xl font-semibold">Sauvegardes et continuité</h2>
      <p className="mt-3 text-slate-700 leading-relaxed">Chaque nuit, une sauvegarde complète de la base est chiffrée (AES-256-GCM) puis déposée chez Scaleway à Paris, hors de l&apos;hébergeur principal, avec 30 sauvegardes quotidiennes et 12 mensuelles conservées. La clé de chiffrement est détenue par FreightSight, pas par Scaleway. La restauration est rejouée en intégration continue à chaque changement du code, sur la même version majeure de PostgreSQL que la production. Les migrations de schéma sont jouées avant chaque déploiement et le service refuse de démarrer si sa configuration de sécurité est incomplète.</p>

      <h2 className="mt-10 text-xl font-semibold">Questionnaire sécurité</h2>
      <p className="mt-3 text-slate-700 leading-relaxed">Envoyez votre questionnaire DPO ou DSI : réponse point par point sous cinq jours ouvrés. <a href={site.securityQuestionnaireUrl} className="text-blue-700 underline underline-offset-2 hover:no-underline">Nous contacter</a>.</p>
    </main>
  );
}

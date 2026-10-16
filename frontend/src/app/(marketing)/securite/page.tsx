import type { Metadata } from "next";
import Link from "next/link";
import SubprocessorTable from "@/features/marketing/SubprocessorTable";
import { SUBPROCESSORS, SUBPROCESSORS_PENDING } from "@/features/marketing/subprocessors";
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

export default function SecurityPage() {
  return (
    <main className="max-w-3xl mx-auto px-5 py-16">
      <div className="text-xs font-medium uppercase tracking-wider text-blue-700">Sécurité et données · mis à jour le 16 octobre 2026</div>
      <h1 className="mt-3 text-4xl font-semibold tracking-tight text-balance">Où sont vos données, qui y accède, et ce que nous ne faisons pas.</h1>
      <p className="mt-5 text-lg text-slate-600 leading-relaxed">Cette page est écrite pour votre DSI ou votre DPO. Elle décrit l&apos;état réel du service, pas une cible. Elle est mise à jour à chaque changement d&apos;architecture.</p>

      <h2 className="mt-12 text-xl font-semibold">Hébergement</h2>
      <p className="mt-3 text-slate-700 leading-relaxed">L&apos;API et la base de données tournent à {site.hostingRegion}, chez Railway. L&apos;interface est servie par Vercel, avec le rendu serveur exécuté dans la région de Paris. Les connexions sont chiffrées en TLS de bout en bout ; le stockage est chiffré au repos par l&apos;hébergeur.</p>

      <h2 className="mt-10 text-xl font-semibold">Isolation entre sociétés</h2>
      <p className="mt-3 text-slate-700 leading-relaxed">Chaque commande, conteneur, coût et import porte l&apos;identifiant de votre société. L&apos;application limite chaque requête à cette société, et la base de données l&apos;impose une seconde fois par des règles de sécurité au niveau des lignes (Row Level Security) : l&apos;utilisateur technique de l&apos;application n&apos;a pas le droit de les contourner. Un test automatisé vérifie à chaque déploiement qu&apos;une requête sans filtre ne renvoie aucune ligne d&apos;une autre société.</p>

      <h2 className="mt-10 text-xl font-semibold">Comptes et accès</h2>
      <p className="mt-3 text-slate-700 leading-relaxed">L&apos;authentification est confiée à Clerk : connexion par e-mail ou Google, double authentification disponible, organisations avec invitations et rôles. L&apos;API ne fait confiance qu&apos;à un jeton de session signé, vérifié à chaque appel contre les clés publiques de Clerk, et restreint au domaine de l&apos;application. Aucun identifiant de société ne transite dans les requêtes.</p>

      <h2 className="mt-10 text-xl font-semibold">Sous-traitants</h2>
      <SubprocessorTable label="Sous-traitants" rows={SUBPROCESSORS} className="mt-4" />
      <h3 className="mt-6 text-sm font-semibold uppercase tracking-wider text-slate-500">À l&apos;activation du suivi automatique et des alertes</h3>
      <p className="mt-2 text-sm text-slate-500">Construits, pas encore branchés en production. Le jour où votre pilote les active, voici ce qui commence à recevoir des données :</p>
      <SubprocessorTable label="Sous-traitants à l'activation du suivi automatique et des alertes" rows={SUBPROCESSORS_PENDING} className="mt-3" />
      <p className="mt-3 text-sm text-slate-500">La lecture des factures est faite par des règles, sans fournisseur d&apos;IA. Le code contient aussi une lecture assistée par IA, qui enverrait le texte des factures à Anthropic (États-Unis) : elle est désactivée en production, aucune clé n&apos;y est configurée. Avant toute activation, Anthropic sera ajouté à cette page avec son mécanisme de transfert, et chaque client en sera prévenu.</p>

      <h2 className="mt-10 text-xl font-semibold">Ce que nous ne faisons pas</h2>
      <ul className="mt-3 space-y-2 text-slate-700 list-disc pl-5">
        <li>Entraîner un modèle sur vos données, ni les utiliser pour un autre client.</li>
        <li>Revendre, partager ou croiser vos volumes, fournisseurs ou prix entre sociétés.</li>
        <li>Stocker vos identifiants bancaires : le service ne fait aucun paiement.</li>
        <li>Créer un coût sans qu&apos;un humain l&apos;ait validé, y compris pour une facture lue automatiquement.</li>
      </ul>

      <h2 className="mt-10 text-xl font-semibold">Vos droits sur vos données</h2>
      <p className="mt-3 text-slate-700 leading-relaxed">Vous pouvez demander à tout moment un export complet (CSV) et la suppression définitive de votre société. La suppression est exécutée sous 30 jours et confirmée par écrit ; les sauvegardes chiffrées (ci-dessous) en gardent une copie jusqu&apos;à leur expiration, douze mois au plus. Un accord de traitement des données (DPA) reprenant cette page et la liste des sous-traitants est fourni à la signature du programme design partner. Les données personnelles que FreightSight traite pour son propre compte (comptes, contacts) sont décrites dans la <Link href="/confidentialite" className="text-blue-700 underline underline-offset-2 hover:no-underline">politique de confidentialité</Link>.</p>

      <h2 className="mt-10 text-xl font-semibold">Sauvegardes et continuité</h2>
      <p className="mt-3 text-slate-700 leading-relaxed">Chaque nuit, une sauvegarde complète de la base est chiffrée (AES-256-GCM) puis déposée chez Scaleway à Paris, hors de l&apos;hébergeur principal, avec 30 sauvegardes quotidiennes et 12 mensuelles conservées. La clé de chiffrement est détenue par FreightSight, pas par Scaleway. La restauration est rejouée en intégration continue à chaque changement du back-end et chaque semaine, sur la même version majeure de PostgreSQL que la production, jusqu&apos;à relire les données restaurées avec les droits de l&apos;application. Les migrations de schéma sont jouées avant chaque déploiement et le service refuse de démarrer si sa configuration de sécurité est incomplète.</p>

      <h2 className="mt-10 text-xl font-semibold">Questionnaire sécurité</h2>
      <p className="mt-3 text-slate-700 leading-relaxed">Envoyez votre questionnaire DPO ou DSI : réponse point par point sous cinq jours ouvrés. <a href={site.securityQuestionnaireUrl} className="text-blue-700 underline underline-offset-2 hover:no-underline">Nous contacter</a>.</p>
    </main>
  );
}

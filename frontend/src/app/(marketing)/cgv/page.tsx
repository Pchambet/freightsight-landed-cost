import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { prose } from "@/features/marketing/legalProse";
import { site } from "@/lib/site";

// Conditions générales de vente, drafted with the owner's legal notes (which say why each clause reads
// as it does). Rendered only when the seller's identity is complete and the owner has dated the terms
// (site.termsEffectiveDate): terms that name no seller, or that nobody has approved, bind no one.
export const metadata: Metadata = {
  title: "Conditions générales de vente",
  description: "Les conditions dans lesquelles FreightSight vend son service aux professionnels.",
  alternates: { canonical: `${site.url}/cgv` },
};

export default function TermsPage() {
  if (!site.termsPublished) notFound();
  const { publisher } = site;
  return (
    <main className={prose.main}>
      <div className={prose.eyebrow}>Conditions générales de vente · applicables à compter du {site.termsEffectiveDate}</div>
      <h1 className={prose.h1}>Conditions générales de vente.</h1>

      <h2 className={prose.h2}>1. Parties et champ d&apos;application</h2>
      <p className={prose.p}>Ces conditions s&apos;appliquent à toute vente du service FreightSight par {publisher.legalName}, {publisher.legalForm}, SIREN {publisher.siren}, {publisher.address} (« FreightSight »), à un client professionnel (« le Client »). Le service est réservé aux professionnels ; il n&apos;est pas proposé aux consommateurs. Les conditions particulières d&apos;un devis ou d&apos;un bon de commande accepté prévalent sur ces conditions générales.</p>

      <h2 className={prose.h2}>2. Le service</h2>
      <p className={prose.p}>FreightSight est un logiciel accessible en ligne qui calcule le coût de revient rendu des importations du Client, par commande et par référence, à partir des données et documents que le Client y importe ou y saisit. Le périmètre souscrit (plan, volume de conteneurs, options, audit ponctuel) est celui du devis ou du bon de commande accepté. Le service est en développement actif : ses fonctions peuvent évoluer, sans retirer au Client une fonction essentielle du plan souscrit pendant sa durée.</p>

      <h2 className={prose.h2}>3. Commande, durée et résiliation</h2>
      <p className={prose.p}>La vente est formée par l&apos;acceptation écrite d&apos;un devis ou d&apos;un bon de commande. Sauf stipulation contraire de celui-ci, l&apos;abonnement est mensuel et se renouvelle chaque mois ; chaque partie peut y mettre fin par écrit (un e-mail suffit) avec effet à la fin du mois en cours. Chaque partie peut aussi résilier en cas de manquement grave de l&apos;autre, non réparé trente jours après une mise en demeure écrite.</p>

      <h2 className={prose.h2}>4. Prix et paiement</h2>
      <p className={prose.p}>Les prix sont ceux du devis ou du bon de commande accepté. Ils sont exprimés hors taxes et majorés de la TVA lorsque FreightSight y est assujetti. L&apos;abonnement est facturé chaque mois, d&apos;avance ; un audit ponctuel est facturé à la commande. Les factures sont payables à trente jours à compter de leur date, par virement, sans escompte pour paiement anticipé.</p>
      <p className={prose.p}>Tout retard de paiement entraîne de plein droit, dès le lendemain de l&apos;échéance et sans rappel préalable, des pénalités de retard au taux d&apos;intérêt appliqué par la Banque centrale européenne à son opération de refinancement la plus récente, majoré de dix points, ainsi qu&apos;une indemnité forfaitaire pour frais de recouvrement de 40 € (articles L441-10 et D441-5 du Code de commerce). Après une mise en demeure restée sans effet quinze jours, FreightSight peut suspendre l&apos;accès au service jusqu&apos;au paiement.</p>

      <h2 className={prose.h2}>5. Obligations du Client</h2>
      <p className={prose.p}>Le Client garde ses identifiants confidentiels et répond de l&apos;usage fait de ses comptes. Il garantit qu&apos;il a le droit d&apos;importer dans le service les données et documents qu&apos;il y place. Il vérifie les résultats avant de s&apos;en servir pour ses décisions de prix, d&apos;achat ou de comptabilité : FreightSight calcule à partir de ce qu&apos;il reçoit, signale ce qui manque au lieu de le deviner, et ne crée aucun coût sans la validation d&apos;une personne du Client.</p>

      <h2 className={prose.h2}>6. Données du Client</h2>
      <p className={prose.p}>Les données importées ou saisies par le Client restent sa propriété. FreightSight les traite comme sous-traitant, uniquement pour fournir le service, dans les conditions de l&apos;accord de traitement des données (article 28 du RGPD) signé avec le Client et de la <Link href="/securite" className={prose.link}>page sécurité</Link>. Le Client peut en obtenir à tout moment un export complet ; à la fin du contrat, elles sont supprimées sous trente jours, sauf demande écrite contraire du Client, et la suppression lui est confirmée par écrit ; les copies chiffrées des sauvegardes expirent ensuite selon leur cycle, douze mois au plus.</p>

      <h2 className={prose.h2}>7. Disponibilité</h2>
      <p className={prose.p}>FreightSight fait ses meilleurs efforts pour que le service soit accessible en continu et pour corriger rapidement les anomalies signalées ; il s&apos;agit d&apos;une obligation de moyens. Le service peut être interrompu pour maintenance, si possible en dehors des heures ouvrées et après information du Client.</p>

      <h2 className={prose.h2}>8. Responsabilité</h2>
      <p className={prose.p}>Chaque partie répond des dommages directs causés par sa faute prouvée. FreightSight ne répond pas des dommages indirects, notamment perte de chiffre d&apos;affaires, de marge ou de clientèle, ni des conséquences de données ou documents inexacts ou incomplets fournis par le Client. Toutes causes confondues, la responsabilité de FreightSight est limitée aux sommes payées par le Client au titre du service pendant les douze mois précédant le fait générateur. Ces limites ne s&apos;appliquent ni en cas de faute lourde ou dolosive, ni aux dommages que la loi interdit de limiter.</p>

      <h2 className={prose.h2}>9. Propriété intellectuelle</h2>
      <p className={prose.p}>Le logiciel, ses mises à jour et sa documentation restent la propriété de {publisher.legalName}. Le Client reçoit, pour la durée de son abonnement, un droit d&apos;utilisation non exclusif et non cessible du service, pour ses besoins internes.</p>

      <h2 className={prose.h2}>10. Confidentialité</h2>
      <p className={prose.p}>Chaque partie garde confidentielles les informations de l&apos;autre dont elle a connaissance à l&apos;occasion du contrat, pendant sa durée et trois ans après sa fin. FreightSight ne communique les données du Client à aucun tiers en dehors des sous-traitants listés sur la page sécurité, et ne les croise jamais avec celles d&apos;un autre client.</p>

      <h2 className={prose.h2}>11. Droit applicable et litiges</h2>
      <p className={prose.p}>Ces conditions sont soumises au droit français. En cas de différend, les parties cherchent d&apos;abord une solution amiable ; à défaut, le litige est porté devant la juridiction compétente selon les règles de droit commun.</p>

      <h2 className={prose.h2}>12. Version applicable</h2>
      <p className={prose.p}>La version applicable à une commande est celle en vigueur à la date de son acceptation. Ces conditions sont communiquées à tout professionnel qui en fait la demande.</p>
    </main>
  );
}

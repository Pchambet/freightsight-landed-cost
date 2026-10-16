import type { Metadata } from "next";
import Link from "next/link";
import ScrollRegion from "@/components/shared/ScrollRegion";
import SubprocessorTable from "@/features/marketing/SubprocessorTable";
import { prose } from "@/features/marketing/legalProse";
import { SUBPROCESSORS, SUBPROCESSORS_PENDING, type Subprocessor } from "@/features/marketing/subprocessors";
import { site } from "@/lib/site";

// Art. 13 GDPR information. Every statement here is checked against the code or the providers' own
// pages (the owner's legal notes give the source of each); change both together.
const UPDATED = "16 octobre 2026";
const description = "Quelles données personnelles FreightSight traite, pourquoi, combien de temps, chez qui, et vos droits.";

export const metadata: Metadata = {
  title: "Politique de confidentialité",
  description,
  alternates: { canonical: `${site.url}/confidentialite` },
  openGraph: {
    title: "Politique de confidentialité · FreightSight",
    description,
    url: `${site.url}/confidentialite`,
    siteName: site.name,
    locale: "fr_FR",
    type: "website",
  },
};

const PROCESSING = [
  {
    when: "Vous nous écrivez ou réservez un appel",
    data: "Adresse e-mail, nom, société, contenu du message ; pour l'essai « une facture, un coût rendu sous 24 h », les documents joints ; pour un rendez-vous, le créneau choisi.",
    why: "Répondre, organiser l'appel, réaliser l'essai demandé.",
    basis: "Intérêt légitime à répondre aux demandes professionnelles qui nous sont adressées (art. 6.1.f).",
    keep: "3 ans à compter de votre dernier message, puis supprimé ; plus tôt sur simple demande.",
  },
  {
    when: "Vous visitez le site",
    data: "Journaux techniques de l'hébergeur (adresse IP, page demandée, navigateur, date) ; en cas d'erreur, une trace d'erreur technique.",
    why: "Servir les pages, protéger le site, diagnostiquer une panne.",
    basis: "Intérêt légitime à faire fonctionner et sécuriser le site (art. 6.1.f).",
    keep: "Journaux : durée fixée par l'hébergeur (Vercel). Traces d'erreur : 90 jours au plus (Sentry).",
  },
  {
    when: "Vous utilisez l'application",
    data: "E-mail, nom, identifiant de connexion, société(s) et rôle ; le journal des actions (qui a importé, validé ou modifié quoi, et quand) ; à l'activation des alertes, l'e-mail des destinataires.",
    why: "Vous connecter, gérer les invitations et les rôles, garder la preuve de qui a validé un coût, envoyer les alertes.",
    basis: "Intérêt légitime à fournir le service souscrit par votre société et à en garder la traçabilité (art. 6.1.f).",
    keep: "Tant que votre société utilise le service ; supprimé avec les données de la société, sous 30 jours après la fin de son contrat ou sur sa demande. Une copie chiffrée subsiste dans les sauvegardes jusqu'à leur expiration : 30 jours pour les quotidiennes, 12 mois pour les mensuelles.",
  },
];

// The contact channels the site links to, which only the visitor's own e-mail or booking reaches.
const CONTACT_TOOLS: Subprocessor[] = [
  {
    name: "Google (Gmail)",
    role: "Messagerie qui reçoit les e-mails envoyés depuis le site",
    location: "Centres de données de Google, dont les États-Unis",
    transfer: "Google déclare recourir au Data Privacy Framework UE–États-Unis et aux clauses contractuelles types",
    data: "Vos e-mails et leurs pièces jointes",
  },
  ...(site.bookingUrl.startsWith("https://cal.com/")
    ? [{
        name: "Cal.com",
        role: "Prise de rendez-vous",
        location: "États-Unis",
        transfer: "Clauses contractuelles types de son accord de traitement",
        data: "Nom, e-mail, créneau choisi",
      }]
    : []),
];

export default function PrivacyPage() {
  const { publisher } = site;
  return (
    <main className={prose.main}>
      <div className={prose.eyebrow}>Politique de confidentialité · mise à jour le {UPDATED}</div>
      <h1 className={prose.h1}>Vos données personnelles chez FreightSight.</h1>
      <p className={prose.lead}>Ce que nous traitons, pourquoi, combien de temps, chez qui, et comment exercer vos droits. Cette page décrit le service tel qu&apos;il fonctionne aujourd&apos;hui.</p>

      <h2 className={prose.h2}>Qui est responsable</h2>
      <p className={prose.p}>
        {site.registered
          ? <>Le responsable du traitement est {publisher.legalName}, {publisher.legalForm}, SIREN {publisher.siren}, {publisher.address}.</>
          : <>Le responsable du traitement est Pierre Chambet, fondateur et éditeur de FreightSight.</>}{" "}
        Pour toute question sur vos données ou pour exercer vos droits : <a href={`mailto:${site.contactEmail}`} className={prose.link}>{site.contactEmail}</a>.
      </p>

      <h2 className={prose.h2}>Deux rôles différents</h2>
      <p className={prose.p}>Pour le site, les contacts et les comptes utilisateurs, FreightSight décide de ce qui est traité : il est responsable du traitement, et cette page s&apos;applique.</p>
      <p className={prose.p}>Pour les données que votre société importe ou saisit dans l&apos;application (commandes, conteneurs, factures, fournisseurs, coûts), c&apos;est votre société qui décide : elle est responsable du traitement, et FreightSight agit comme sous-traitant, sur ses instructions, dans le cadre d&apos;un accord de traitement des données (article 28 du RGPD). Si votre nom figure dans ces documents, adressez-vous à la société cliente ; une demande reçue par erreur lui est transmise.</p>

      <h2 className={prose.h2}>Ce que nous traitons, et pourquoi</h2>
      <ScrollRegion label="Traitements" className="mt-4 rounded-lg border border-slate-200">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wider text-slate-500">
            <tr>
              <th className="text-left px-4 py-2.5">Situation</th>
              <th className="text-left px-4 py-2.5">Données</th>
              <th className="text-left px-4 py-2.5">Finalité</th>
              <th className="text-left px-4 py-2.5">Base légale (RGPD)</th>
              <th className="text-left px-4 py-2.5">Conservation</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200">
            {PROCESSING.map((r) => (
              <tr key={r.when}>
                <td className="px-4 py-3 align-top font-medium">{r.when}</td>
                <td className="px-4 py-3 align-top text-slate-700">{r.data}</td>
                <td className="px-4 py-3 align-top text-slate-700">{r.why}</td>
                <td className="px-4 py-3 align-top text-slate-700">{r.basis}</td>
                <td className="px-4 py-3 align-top text-slate-700">{r.keep}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </ScrollRegion>
      <p className={prose.p}>Une adresse e-mail est nécessaire pour créer un compte : sans elle, l&apos;application ne peut pas être utilisée. Nous écrire ou réserver un appel reste facultatif.</p>
      <p className={prose.p}>Aucune décision automatisée ni aucun profilage. Aucune donnée n&apos;est vendue, louée ou utilisée pour entraîner un modèle. Si vous choisissez de vous connecter avec Google, Google sait que vous vous connectez à FreightSight, selon sa propre politique.</p>

      <h2 className={prose.h2}>Qui reçoit ces données</h2>
      <p className={prose.p}>À l&apos;intérieur de FreightSight, seul le fondateur y a accès. Les prestataires ci-dessous les traitent pour notre compte, chacun pour son rôle.</p>
      <SubprocessorTable label="Prestataires du service" rows={SUBPROCESSORS} className="mt-4" />
      <h3 className="mt-6 text-sm font-semibold uppercase tracking-wider text-slate-500">Pour les échanges avant de devenir client</h3>
      <p className="mt-2 text-sm text-slate-500">L&apos;adresse de contact est une messagerie Gmail, service grand public de Google, qui traite ces e-mails selon ses propres conditions.</p>
      <SubprocessorTable label="Prestataires des échanges avant de devenir client" rows={CONTACT_TOOLS} className="mt-3" />
      <h3 className="mt-6 text-sm font-semibold uppercase tracking-wider text-slate-500">Construits, pas encore activés</h3>
      <p className="mt-2 text-sm text-slate-500">Ils ne reçoivent rien aujourd&apos;hui. Le jour où les alertes par e-mail ou le suivi automatique des conteneurs sont activés :</p>
      <SubprocessorTable label="Prestataires pas encore activés" rows={SUBPROCESSORS_PENDING} className="mt-3" />
      <p className="mt-3 text-sm text-slate-500">La lecture des factures est faite par des règles, sans fournisseur d&apos;IA. Une lecture assistée par IA existe dans le code (elle enverrait le texte des factures à Anthropic, aux États-Unis) ; elle est désactivée, et Anthropic sera ajouté à cette page avant toute activation.</p>

      <h2 className={prose.h2}>Transferts hors de l&apos;Union européenne</h2>
      <p className={prose.p}>Plusieurs prestataires sont des sociétés américaines. Chaque transfert vers les États-Unis repose sur le cadre de protection des données UE–États-Unis (Data Privacy Framework, décision d&apos;adéquation de la Commission européenne du 10 juillet 2023) lorsque le prestataire y est certifié, et sur les clauses contractuelles types de la Commission européenne prévues par son accord de traitement ou ses conditions. Le mécanisme de chaque prestataire figure dans les tableaux ci-dessus ; une copie des garanties est disponible sur demande.</p>

      <h2 className={prose.h2}>Cookies</h2>
      <p className={prose.p}>Le site ne dépose aucun cookie de mesure d&apos;audience, de publicité ou de réseau social. Seuls des cookies strictement nécessaires sont utilisés, ce qui ne demande pas de consentement : les cookies de Clerk qui permettent de se connecter et de le rester, et, dans l&apos;application, un cookie qui retient la langue choisie pendant un an.</p>

      <h2 className={prose.h2}>Vos droits</h2>
      <p className={prose.p}>Vous pouvez demander l&apos;accès à vos données, leur rectification, leur effacement, la limitation de leur traitement, leur portabilité, et vous opposer à un traitement fondé sur notre intérêt légitime. Écrivez à <a href={`mailto:${site.contactEmail}`} className={prose.link}>{site.contactEmail}</a> : la réponse est donnée sous un mois, délai que le RGPD permet de prolonger de deux mois pour une demande complexe, en vous prévenant. Vous pouvez aussi nous indiquer des directives sur la conservation, l&apos;effacement et la communication de vos données après votre décès (article 85 de la loi Informatique et Libertés).</p>
      <p className={prose.p}>Si vous estimez que vos droits ne sont pas respectés, vous pouvez adresser une réclamation à la CNIL : <a href="https://www.cnil.fr/fr/plaintes" className={prose.link}>www.cnil.fr/fr/plaintes</a>.</p>

      <h2 className={prose.h2}>Sécurité</h2>
      <p className={prose.p}>Isolation de chaque société jusque dans la base de données, chiffrement en transit et au repos, sauvegardes chiffrées hors de l&apos;hébergeur principal : le détail est sur la <Link href="/securite" className={prose.link}>page sécurité</Link>.</p>

      <h2 className={prose.h2}>Modifications</h2>
      <p className={prose.p}>Cette page est mise à jour à chaque changement de prestataire ou de traitement ; la date en tête indique la dernière version. Un nouveau prestataire qui recevrait les données de votre société est annoncé à votre société avant de les recevoir.</p>
    </main>
  );
}

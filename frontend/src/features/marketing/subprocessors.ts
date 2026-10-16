// The one list of the service's sub-processors, read by /securite and /confidentialite so the two pages
// cannot disagree. Each transfer mechanism is the one the provider's own DPA or privacy policy states;
// the sources, and the date they were read, are in the owner's legal notes. A provider changes here first.

export type Subprocessor = {
  name: string;
  role: string;
  location: string;
  /** How data leaving the EU is covered, or why nothing leaves it. */
  transfer: string;
  data: string;
};

export const SUBPROCESSORS: Subprocessor[] = [
  {
    name: "Railway",
    role: "API, base de données PostgreSQL et tâches planifiées",
    location: "Amsterdam, Pays-Bas",
    transfer: "Railway Corporation est américaine : clauses contractuelles types de son accord de traitement",
    data: "Toutes les données de l'application",
  },
  {
    name: "Vercel",
    role: "Rendu de l'interface (fonctions serveur région Paris, CDN mondial pour les fichiers statiques)",
    location: "Paris, France / CDN mondial",
    transfer: "Vercel Inc. est américaine : Data Privacy Framework UE–États-Unis et clauses contractuelles types",
    data: "Données affichées à l'écran, en transit ; journaux techniques (adresse IP, page demandée)",
  },
  {
    name: "Clerk",
    role: "Authentification, organisations, invitations",
    location: "États-Unis",
    transfer: "Data Privacy Framework UE–États-Unis ; clauses contractuelles types si ce cadre ne peut plus être invoqué",
    data: "E-mail, nom, appartenance à une société. Jamais une commande ni une facture.",
  },
  {
    name: "Scaleway",
    role: "Sauvegardes chiffrées de la base de données",
    location: "Paris, France",
    transfer: "Aucun transfert hors de l'Union européenne",
    data: "Une copie chiffrée (AES-256) de la base, chaque nuit ; la clé de chiffrement n'est pas chez Scaleway",
  },
  {
    name: "Sentry",
    role: "Supervision des erreurs",
    location: "Francfort, Allemagne (région de données UE)",
    transfer: "Sentry (Functional Software Inc.) est américaine et garde aux États-Unis les métadonnées du compte : Data Privacy Framework, clauses contractuelles types en secours",
    data: "Traces d'erreur, identifiants de requête. Jamais le contenu d'une facture ni d'une commande.",
  },
  {
    name: "Frankfurter (taux BCE)",
    role: "Taux de change publics",
    location: "Sans objet",
    transfer: "Sans objet : aucune donnée envoyée",
    data: "Aucune donnée envoyée : lecture seule de taux publics",
  },
];

// Built, not switched on in production: listed ahead of the switch, not discovered after the fact by a
// DPO who cross-checks an alert e-mail's headers against /securite.
export const SUBPROCESSORS_PENDING: Subprocessor[] = [
  {
    name: "Resend",
    role: "Envoi des alertes par e-mail (dernier jour franc, surestaries)",
    location: "États-Unis",
    transfer: "Clauses contractuelles types et Data Privacy Framework UE–États-Unis",
    data: "Adresse e-mail du destinataire et contenu de l'alerte (référence conteneur ou commande)",
  },
  {
    name: "Shipsgo ou Terminal49",
    role: "Suivi automatique des conteneurs, selon le fournisseur activé",
    location: "Turquie (Shipsgo) / États-Unis (Terminal49)",
    transfer: "Sans objet : aucune donnée personnelle envoyée",
    data: "Numéro de conteneur, de connaissement ou de réservation, code transporteur, identifiant technique interne",
  },
];

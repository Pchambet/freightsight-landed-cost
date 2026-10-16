// Public site configuration. Safe for the client bundle: no secrets here.

// The address every mailto: link on the marketing site resolves to (also the fallback booking
// address below). One constant so the booking link and the low-commitment CTA never drift apart.
const CONTACT_EMAIL = "pierrechambet@gmail.com";

// Plain percent-encoding, not URLSearchParams: mailto's query component follows RFC 6068, and a
// mail client reading a "+" there shows a literal plus sign instead of a space.
function mailto(subject: string, body?: string): string {
  const params = [`subject=${encodeURIComponent(subject)}`];
  if (body) params.push(`body=${encodeURIComponent(body)}`);
  return `mailto:${CONTACT_EMAIL}?${params.join("&")}`;
}

// Who publishes the site, as article 6-III of the LCEN requires it. Every field stays empty until the
// owner has the facts (registration, address, phone): an empty field keeps /mentions-legales and /cgv
// at 404 and out of the footer and the sitemap, rather than publishing a legal page with a hole in it.
// What to fill, and from where, is listed in the owner's legal notes.
const publisher = {
  // "Pierre Chambet" for an entrepreneur individuel, the company name for a société.
  legalName: "",
  // As it must read, capital included for a company: "Entrepreneur individuel (micro-entreprise)",
  // "SASU au capital de 1 000 €".
  legalForm: "",
  siren: "",
  // Register and city for a company ("RCS Paris"); left empty for an entrepreneur individuel.
  registry: "",
  // Left empty under the VAT franchise (art. 293 B du CGI): there is no number to show.
  vatNumber: "",
  address: "",
  phone: "",
  publicationDirector: "",
  email: CONTACT_EMAIL,
};

// Who stores what the site serves: the pages (Vercel) and the application's data (Railway). Names and
// addresses as each publishes them in its own legal pages (sources in the owner's legal notes). Neither
// publishes a phone number there, and the LCEN asks for one: the owner asks each for it.
const hosts = [
  { name: "Vercel Inc.", role: "Hébergement du site et de l'interface", address: "440 N Barranca Avenue #4133, Covina, CA 91723, États-Unis", phone: "", url: "https://vercel.com" },
  { name: "Railway Corporation", role: "Hébergement de l'API et de la base de données (région Amsterdam, Pays-Bas)", address: "548 Market St PMB 68956, San Francisco, CA 94104, États-Unis", phone: "", url: "https://railway.com" },
];

// The publisher exists as a registered business: a name, a form, a SIREN and an address.
const registered = Boolean(publisher.legalName && publisher.legalForm && publisher.siren && publisher.address);
// Everything the LCEN asks of the legal notice is known.
const legalNoticeComplete =
  registered && Boolean(publisher.phone && publisher.publicationDirector) && hosts.every((h) => h.name && h.address && h.phone);
// The date from which the owner, having read the terms' reasoning and the /cgv page, applies them
// (e.g. "1er décembre 2026"). Empty: no terms are published, whatever the identity fields say.
const termsEffectiveDate = "";

export const site = {
  name: "FreightSight",
  tagline: "Le coût de revient rendu de vos importations, par commande et par SKU.",
  // Absolute origin, used for Open Graph URLs and the sitemap. Set NEXT_PUBLIC_SITE_URL on Vercel
  // once a custom domain replaces the *.vercel.app address.
  url: process.env.NEXT_PUBLIC_SITE_URL || "https://freight-sight-two.vercel.app",
  contactEmail: CONTACT_EMAIL,
  // Where the "Réserver 20 minutes" buttons go. Set NEXT_PUBLIC_BOOKING_URL (Cal.com, Calendly…)
  // on Vercel to replace the e-mail fallback.
  bookingUrl: process.env.NEXT_PUBLIC_BOOKING_URL || mailto("FreightSight – 20 minutes"),
  // Low-commitment CTA next to every booking button: no call to book, just a real invoice in and a
  // landed cost back within 24h.
  quickCheckUrl: mailto(
    "FreightSight – une facture, un coût rendu sous 24h",
    "Bonjour Pierre,\n\nVoici une facture transitaire et le bon de commande correspondant (en pièce jointe).\n\nPouvez-vous me renvoyer le coût rendu par référence ?\n\nMerci,",
  ),
  // Same address, for the /securite questionnaire link (it pointed at the booking
  // page, not an inbox a DPO can write to).
  securityQuestionnaireUrl: mailto("Questionnaire sécurité FreightSight"),
  appHome: "/overview",
  hostingRegion: "Amsterdam (UE)",
  publisher,
  hosts,
  registered,
  // /mentions-legales renders only when this is true, and /cgv only when the terms have a date too.
  legalNoticePublished: legalNoticeComplete,
  termsPublished: legalNoticeComplete && Boolean(termsEffectiveDate),
  termsEffectiveDate,
};

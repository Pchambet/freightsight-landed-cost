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
  // landed cost back within 24h (A4-8 / A4 rewrite proposal).
  quickCheckUrl: mailto(
    "FreightSight – une facture, un coût rendu sous 24h",
    "Bonjour Pierre,\n\nVoici une facture transitaire et le bon de commande correspondant (en pièce jointe).\n\nPouvez-vous me renvoyer le coût rendu par référence ?\n\nMerci,",
  ),
  // Same address, for the /securite questionnaire link (A4 audit A4-7: it pointed at the booking
  // page, not an inbox a DPO can write to).
  securityQuestionnaireUrl: mailto("Questionnaire sécurité FreightSight"),
  appHome: "/overview",
  hostingRegion: "Amsterdam (UE)",
};

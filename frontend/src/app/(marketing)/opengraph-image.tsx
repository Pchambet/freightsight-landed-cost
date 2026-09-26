import { ogImageContentType, ogImageSize, renderOgImage } from "@/features/marketing/ogImage";

export const alt = "FreightSight — coût de revient rendu pour les PME importatrices";
export const size = ogImageSize;
export const contentType = ogImageContentType;

export default function Image() {
  return renderOgImage(
    "Le coût de revient rendu de vos importations, par commande et par SKU.",
    "Pour les PME importatrices",
  );
}

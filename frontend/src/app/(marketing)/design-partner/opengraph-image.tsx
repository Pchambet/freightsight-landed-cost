import { ogImageContentType, ogImageSize, renderOgImage } from "@/features/marketing/ogImage";

export const alt = "FreightSight — programme design partner";
export const size = ogImageSize;
export const contentType = ogImageContentType;

export default function Image() {
  return renderOgImage(
    "Le coût de revient rendu de vos importations, calculé sur vos vrais dossiers, en huit semaines.",
    "Programme design partner",
  );
}

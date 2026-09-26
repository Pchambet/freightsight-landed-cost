import { ogImageContentType, ogImageSize, renderOgImage } from "@/features/marketing/ogImage";

export const alt = "FreightSight — sécurité et données";
export const size = ogImageSize;
export const contentType = ogImageContentType;

export default function Image() {
  return renderOgImage("Où sont vos données, qui y accède, et ce que nous ne faisons pas.", "Sécurité et données");
}

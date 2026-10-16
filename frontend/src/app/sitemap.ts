import type { MetadataRoute } from "next";
import { site } from "@/lib/site";

// The public marketing and legal pages only: every application route stays out of search results
// (see robots.ts, and proxy.ts which never lets a crawler through to them anyway). A legal page that
// is still a 404 (lib/site.ts) is left out.
export default function sitemap(): MetadataRoute.Sitemap {
  const lastModified = new Date();
  return [
    { url: site.url, lastModified, changeFrequency: "monthly", priority: 1 },
    { url: `${site.url}/design-partner`, lastModified, changeFrequency: "monthly", priority: 0.8 },
    { url: `${site.url}/securite`, lastModified, changeFrequency: "monthly", priority: 0.5 },
    { url: `${site.url}/confidentialite`, lastModified, changeFrequency: "yearly", priority: 0.2 },
    ...(site.legalNoticePublished ? [{ url: `${site.url}/mentions-legales`, lastModified, changeFrequency: "yearly" as const, priority: 0.2 }] : []),
    ...(site.termsPublished ? [{ url: `${site.url}/cgv`, lastModified, changeFrequency: "yearly" as const, priority: 0.2 }] : []),
  ];
}

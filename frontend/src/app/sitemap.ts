import type { MetadataRoute } from "next";
import { site } from "@/lib/site";

// The three public marketing pages only: every application route stays out of search results
// (see robots.ts, and proxy.ts which never lets a crawler through to them anyway).
export default function sitemap(): MetadataRoute.Sitemap {
  const lastModified = new Date();
  return [
    { url: site.url, lastModified, changeFrequency: "monthly", priority: 1 },
    { url: `${site.url}/design-partner`, lastModified, changeFrequency: "monthly", priority: 0.8 },
    { url: `${site.url}/securite`, lastModified, changeFrequency: "monthly", priority: 0.5 },
  ];
}

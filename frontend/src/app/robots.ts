import type { MetadataRoute } from "next";
import { site } from "@/lib/site";

// Only the three marketing pages are for search engines. Every application segment is listed
// (proxy.ts sends a crawler to /sign-in anyway): a new segment under (app) belongs here too.
// The sitemap URL follows site.url, so it moves with the domain instead of going stale.
export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: ["/$", "/securite", "/design-partner"],
      disallow: [
        "/r/",
        "/overview",
        "/start",
        "/skus",
        "/periods",
        "/cost-audit",
        "/containers",
        "/container/",
        "/purchase-orders",
        "/imports",
        "/invoices",
        "/reports",
        "/alerts",
        "/audit",
        "/settings",
        "/api/",
        "/sign-in",
        "/sign-up",
        "/choose-organization",
      ],
    },
    sitemap: `${site.url}/sitemap.xml`,
  };
}

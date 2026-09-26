import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

// Security headers for every response. No Content-Security-Policy yet: Clerk injects inline scripts
// and loads from its own domain, a nonce-based policy is planned with the Sentry setup.
const securityHeaders = [
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains; preload" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "SAMEORIGIN" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
];

const nextConfig: NextConfig = {
  poweredByHeader: false,
  experimental: {
    serverActions: {
      // Invoices and import files are uploaded through Server Actions. Next refuses a body over 1 MB
      // by default, and the refusal reaches the page as a crash, while a scanned invoice easily
      // weighs 2 MB. Set above the largest file the screens send (src/lib/uploads.ts, 4,000,000
      // bytes) plus the multipart envelope, and under what Vercel lets reach a function (4,400,000
      // bytes pass, 4,500,000 are refused: measured on production, 18 Sept 2026).
      bodySizeLimit: 4_300_000,
    },
  },
  async headers() {
    return [
      { source: "/(.*)", headers: securityHeaders },
      // A shared report: its URL is its credential. It must not leak through the Referer of an
      // outgoing link, be indexed, or be kept by a cache. (Later entries override earlier ones.)
      {
        source: "/r/:token*",
        headers: [
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "X-Robots-Tag", value: "noindex, nofollow" },
          { key: "Cache-Control", value: "private, no-store" },
        ],
      },
    ];
  },
};

export default withNextIntl(nextConfig);

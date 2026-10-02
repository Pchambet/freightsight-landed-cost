import { ImageResponse } from "next/og";

/**
 * Shared 1200x630 Open Graph / Twitter card renderer for the three public marketing pages
 * (none of them had a preview image, so a LinkedIn share showed nothing). No custom font
 * file and no external asset fetch: satori's bundled sans face keeps this dependency-free, and
 * Next automatically reuses this image for the Twitter card too when the page sets openGraph but
 * no twitter.images (see resolve-metadata.js's inheritFromMetadata).
 */
export const ogImageSize = { width: 1200, height: 630 };
export const ogImageContentType = "image/png";

// Same slate/blue pair as the marketing header and the calculator panel (bg-slate-900, text-blue-*).
const BRAND = { bg: "#0f172a", blue: "#2563eb", blueLight: "#93c5fd", white: "#ffffff" };

export function renderOgImage(headline: string, kicker?: string) {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          padding: 64,
          backgroundColor: BRAND.bg,
          color: BRAND.white,
          fontFamily: "sans-serif",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <div style={{ display: "flex", width: 44, height: 44, borderRadius: 10, backgroundColor: BRAND.blue }} />
          <div style={{ display: "flex", fontSize: 30, fontWeight: 600 }}>
            Freight<span style={{ color: BRAND.blueLight }}>Sight</span>
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 20, maxWidth: 1000 }}>
          {kicker ? (
            <div style={{ display: "flex", fontSize: 24, textTransform: "uppercase", letterSpacing: 2, color: BRAND.blueLight }}>
              {kicker}
            </div>
          ) : null}
          <div style={{ display: "flex", fontSize: 54, fontWeight: 600, lineHeight: 1.15 }}>{headline}</div>
        </div>
      </div>
    ),
    { ...ogImageSize },
  );
}

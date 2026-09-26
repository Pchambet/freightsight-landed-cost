/**
 * A SKU is the customer's own reference and may hold anything, a slash included ("KIT/2026-A").
 * The page is a catch-all route: each slash-separated part travels as its own path segment, encoded
 * on its own, so no host along the way has to let an encoded slash through.
 */
export function skuHref(sku: string): string {
  return `/skus/${sku.split("/").map(encodeURIComponent).join("/")}`;
}

/** The inverse, from the catch-all segments. Next hands them over still percent-encoded. */
export function skuFromSegments(segments: string[]): string {
  return segments
    .map((s) => {
      try {
        return decodeURIComponent(s);
      } catch {
        return s;
      }
    })
    .join("/");
}

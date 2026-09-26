/**
 * The largest file a screen sends in one upload: an invoice, an import file.
 *
 * Every upload goes through a Server Action, so through a Vercel function, and the platform caps a
 * function's request body: measured on production on 18 Sept 2026, 4,400,000 bytes reach the function
 * and 4,500,000 are answered 413 FUNCTION_PAYLOAD_TOO_LARGE before any of our code runs. The API takes
 * more (20 MB for a document, 10 MB for an import file): this is the platform's number, not the
 * product's. `serverActions.bodySizeLimit` in next.config.ts sits between this and the platform's cap,
 * with room for the multipart envelope.
 *
 * Decimal megabytes, as the Finder counts them: a file it shows as 4.1 MB is refused as 4.1 MB.
 */
export const MAX_UPLOAD_BYTES = 4_000_000;

export function isTooLarge(file: File | null): file is File {
  return file !== null && file.size > MAX_UPLOAD_BYTES;
}

/** "13 ko", "4,2 Mo", "4.2 MB": in the reader's language, rounded as the Finder rounds. */
export function fileSize(bytes: number, locale: string): string {
  const format = (unit: string, value: number) =>
    new Intl.NumberFormat(locale, { style: "unit", unit, maximumFractionDigits: 1 }).format(value);
  const kilobytes = Math.round(bytes / 1_000);
  if (kilobytes < 1_000) return format("kilobyte", Math.max(1, kilobytes));
  let tenths = Math.round(bytes / 100_000);
  // A file over the limit never reads as the limit itself ("this file is 4 MB: 4 MB at most").
  if (bytes > MAX_UPLOAD_BYTES && tenths * 100_000 <= MAX_UPLOAD_BYTES) tenths += 1;
  return format("megabyte", tenths / 10);
}

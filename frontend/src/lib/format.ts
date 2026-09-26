import { DEFAULT_LOCALE, type Locale } from "@/i18n/config";

/** BCP 47 tag behind an app locale: French uses the French conventions (12 500,50 €, 08/09/2026). */
function intl(locale: string): string {
  return locale === "fr" ? "fr-FR" : "en-GB";
}

/** Money arrives from the API as decimal strings ("1234.56"). Formatting only, never arithmetic on floats. */
export function money(amount: string | number | null | undefined, currency: string, locale: string = DEFAULT_LOCALE): string {
  if (amount === null || amount === undefined) return "—";
  const value = typeof amount === "number" ? amount : Number(amount);
  if (!Number.isFinite(value)) return "—";
  return new Intl.NumberFormat(intl(locale), {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

/**
 * Same two decimals as `money` everywhere on screen (the fiche conteneur used to show four, the
 * rapports two, for the same reference — see audit B10). The domain keeps more precision internally
 * (unit_landed_cost carries 4+ decimals so totals stay exact), this is display-only rounding.
 */
export function unitCost(amount: string | number, currency: string, locale: string = DEFAULT_LOCALE): string {
  return money(amount, currency, locale);
}

/** The placeholder for a free-typed amount field: "0,00" in French, "0.00" in English (audit B15 — the field accepts a comma but showed an English-dotted placeholder). */
export function amountPlaceholder(locale: string = DEFAULT_LOCALE): string {
  return locale === "fr" ? "0,00" : "0.00";
}

/**
 * A raw dot-decimal amount from the API ("3915.40"), shown with the locale's own separator in an
 * editable field — a comma in French (audit N11: the field sat mid-screen among French text but
 * showed the American dot). Display only: what the field sends back to the server action still goes
 * through `normalizeAmount` (features/actions.ts), which already accepts either separator.
 */
export function decimalDisplay(value: string | null | undefined, locale: string = DEFAULT_LOCALE): string {
  if (value === null || value === undefined) return "";
  return locale === "fr" ? value.replace(".", ",") : value;
}

/** An FX rate as recorded (up to 8 decimals, trailing zeros trimmed), locale-aware separator only — no currency symbol, it is a ratio. */
export function rate(value: string | number | null | undefined, locale: string = DEFAULT_LOCALE): string {
  if (value === null || value === undefined) return "—";
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return "—";
  return new Intl.NumberFormat(intl(locale), { minimumFractionDigits: 2, maximumFractionDigits: 6 }).format(n);
}

/** Three-tier reading of an extraction confidence score (0–1): what a DAF acts on, not the number itself.
 * Same thresholds as `confidence_band` in backend/app/domain/invoices/ports.py; the API's own band wins when present. */
export type ConfidenceTier = "high" | "medium" | "low";
export function confidenceTier(value: string | number | null | undefined): ConfidenceTier | null {
  if (value === null || value === undefined) return null;
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return null;
  if (n >= 0.8) return "high";
  if (n >= 0.5) return "medium";
  return "low";
}

export function qty(value: string | number, locale: string = DEFAULT_LOCALE): string {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return "—";
  return new Intl.NumberFormat(intl(locale), { maximumFractionDigits: 4 }).format(n);
}

export function pct(value: string | number, locale: string = DEFAULT_LOCALE): string {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${new Intl.NumberFormat(intl(locale), { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)} %`;
}

/** Numeric in French (08/09/2026), abbreviated month in English (08 Sep 2026). */
export function dateShort(value: string | null | undefined, locale: string = DEFAULT_LOCALE): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  const style: Intl.DateTimeFormatOptions =
    locale === ("fr" satisfies Locale)
      ? { day: "2-digit", month: "2-digit", year: "numeric" }
      : { day: "2-digit", month: "short", year: "numeric" };
  return new Intl.DateTimeFormat(intl(locale), style).format(d);
}

/**
 * Whole calendar days between "today" and a `YYYY-MM-DD` date. This renders server-side, so "today"
 * cannot be the browser's own midnight — it has to be a fixed civil calendar independent of the
 * server process's own TZ (which nothing here controls; see docker-compose, out of scope here).
 * The screens read that date against, and the app is sold to, France: comparing against a UTC
 * midnight read a day behind between 00:00 and 02:00 Paris time in summer, since UTC midnight is
 * still "yesterday evening" there (needs_other_lane flagged this drift against the backend, which
 * now counts in the port's own calendar). Reading Europe/Paris explicitly fixes that regardless of
 * how the server itself is configured.
 */
export function daysUntil(isoDate: string | null | undefined): number | null {
  if (!isoDate) return null;
  const [y, m, d] = isoDate.split("-").map(Number);
  if (!y || !m || !d) return null;
  const target = Date.UTC(y, m - 1, d);
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Paris", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date());
  const part = (type: string) => Number(parts.find((p) => p.type === type)?.value);
  const todayParis = Date.UTC(part("year"), part("month") - 1, part("day"));
  return Math.round((target - todayParis) / 86_400_000);
}

/** "09/09/2026 14:30" in French, "Sep 9, 2026, 2:30 PM" in English; always the browser-neutral UTC day/time of the ISO value. */
export function dateTimeShort(value: string | null | undefined, locale: string = DEFAULT_LOCALE): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat(locale === "fr" ? "fr-FR" : "en-GB", { dateStyle: "short", timeStyle: "short", timeZone: "UTC" }).format(d);
}

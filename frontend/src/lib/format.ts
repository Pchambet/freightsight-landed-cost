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
 * rapports two, for the same reference). The domain keeps more precision internally
 * (unit_landed_cost carries 4+ decimals so totals stay exact), this is display-only rounding.
 */
export function unitCost(amount: string | number, currency: string, locale: string = DEFAULT_LOCALE): string {
  return money(amount, currency, locale);
}

/** The placeholder for a free-typed amount field: "0,00" in French, "0.00" in English (the field accepts a comma but showed an English-dotted placeholder). */
export function amountPlaceholder(locale: string = DEFAULT_LOCALE): string {
  return locale === "fr" ? "0,00" : "0.00";
}

/**
 * A raw dot-decimal amount from the API ("3915.40"), shown with the locale's own separator in an
 * editable field — a comma in French (the field sat mid-screen among French text but
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
  // Eight, as recorded: a rate for a currency like the dong (0,00003650) has nothing left at six.
  return new Intl.NumberFormat(intl(locale), { minimumFractionDigits: 2, maximumFractionDigits: 8 }).format(n);
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

/** `digits`: the precision the figure has — a margin the API rounds to 0.1 is not shown as "20,60 %". */
export function pct(value: string | number, locale: string = DEFAULT_LOCALE, digits = 2): string {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${new Intl.NumberFormat(intl(locale), { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(n)} %`;
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
 * still "yesterday evening" there, while the backend counts in the port's own calendar. Reading Europe/Paris explicitly fixes that regardless of
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

/**
 * "09/09/2026 14:30" in French, "09/09/2026, 14:30" in English, read on the clock of Paris: these
 * pages render on a server whose own zone is UTC, and an action taken at 10:37 in Lyon must not be
 * logged as 08:37 (the share dialog, rendered in the browser, already said 10:37). Same zone as
 * `daysUntil`.
 */
export function dateTimeShort(value: string | null | undefined, locale: string = DEFAULT_LOCALE): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat(locale === "fr" ? "fr-FR" : "en-GB", { dateStyle: "short", timeStyle: "short", timeZone: "Europe/Paris" }).format(d);
}

/** The clock of Paris read for one instant, as zero-padded parts. */
function parisParts(instant: Date) {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Paris", hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }).formatToParts(instant);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((p) => p.type === type)?.value ?? "00";
  return { year: part("year"), month: part("month"), day: part("day"), hour: part("hour"), minute: part("minute"), second: part("second") };
}

/** Today on the clock of Paris (`YYYY-MM-DD`), the day `daysUntil` counts from: a date field's default. */
export function parisToday(now: Date = new Date()): string {
  const p = parisParts(now);
  return `${p.year}-${p.month}-${p.day}`;
}

/** Now on the clock of Paris, as a `datetime-local` value (`YYYY-MM-DDTHH:mm`). */
export function parisNowLocal(now: Date = new Date()): string {
  const p = parisParts(now);
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;
}

/**
 * A `datetime-local` value typed on the clock of Paris, as the UTC instant the API stores: the inverse
 * of `dateTimeShort`, so an event typed at 23:30 reads 23:30 afterwards, not 01:30 the next day. The
 * offset is Paris's own for that date (+1 in winter, +2 in summer), read again at the instant found so
 * a time typed across the change of hour lands on the right side of it.
 */
export function parisLocalToIso(local: string): string {
  const [date, time = "00:00"] = local.split("T");
  const [y, m, d] = date.split("-").map(Number);
  const [h, min] = time.split(":").map(Number);
  const wall = Date.UTC(y, m - 1, d, h, min);
  const offset = (instant: number) => {
    const p = parisParts(new Date(instant));
    return Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second) - instant;
  };
  return new Date(wall - offset(wall - offset(wall))).toISOString();
}

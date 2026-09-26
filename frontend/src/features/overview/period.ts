/**
 * The window a dashboard reads: whole calendar months, ending with the current one, plus the window
 * of the same length just before it (what every "vs" on the screen compares against). "Today" is
 * the Paris calendar day, like `daysUntil` (lib/format): the server's own clock zone is not ours.
 */
export const WINDOWS = [3, 6, 12] as const;
export type WindowMonths = (typeof WINDOWS)[number];

function parisToday(): { y: number; m: number } {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Paris", year: "numeric", month: "2-digit" }).formatToParts(new Date());
  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value);
  return { y: get("year"), m: get("month") };
}

const iso = (y: number, m: number, d: number) => `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
/** `m` may run below 1 or above 12: it is normalised here, in one place. */
function norm(y: number, m: number): { y: number; m: number } {
  const zero = y * 12 + (m - 1);
  return { y: Math.floor(zero / 12), m: (zero % 12) + 1 };
}
const lastDay = (y: number, m: number) => new Date(Date.UTC(y, m, 0)).getUTCDate();

export type Period = { from: string; to: string; months: string[] };

export function windows(months: WindowMonths): { current: Period; previous: Period } {
  const now = parisToday();
  const build = (endOffset: number): Period => {
    const end = norm(now.y, now.m - endOffset);
    const start = norm(end.y, end.m - (months - 1));
    const keys: string[] = [];
    for (let i = 0; i < months; i++) {
      const k = norm(start.y, start.m + i);
      keys.push(`${k.y}-${String(k.m).padStart(2, "0")}`);
    }
    return { from: iso(start.y, start.m, 1), to: iso(end.y, end.m, lastDay(end.y, end.m)), months: keys };
  };
  return { current: build(0), previous: build(months) };
}

/** "2026-04" → "avr." (and "janv. 27" when the year turns inside the window). */
export function monthLabel(key: string, locale: string, withYear: boolean): string {
  const [y, m] = key.split("-").map(Number);
  const d = new Date(Date.UTC(y, m - 1, 1));
  return new Intl.DateTimeFormat(locale === "fr" ? "fr-FR" : "en-GB", withYear ? { month: "short", year: "2-digit", timeZone: "UTC" } : { month: "short", timeZone: "UTC" }).format(d);
}

export function monthTitle(key: string, locale: string): string {
  const [y, m] = key.split("-").map(Number);
  return new Intl.DateTimeFormat(locale === "fr" ? "fr-FR" : "en-GB", { month: "long", year: "numeric", timeZone: "UTC" }).format(new Date(Date.UTC(y, m - 1, 1)));
}

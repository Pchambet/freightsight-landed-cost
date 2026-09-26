/** "2026-09" → "septembre 2026". The key is a calendar month, read in UTC so no zone can shift it. */
export function periodTitle(period: string, locale: string, inSentence = false): string {
  const [y, m] = period.split("-").map(Number);
  if (!y || !m) return period;
  const text = new Intl.DateTimeFormat(locale === "fr" ? "fr-FR" : "en-GB", { month: "long", year: "numeric", timeZone: "UTC" }).format(new Date(Date.UTC(y, m - 1, 1)));
  // French writes months in lower case: capitalised as a heading, left alone inside a sentence.
  return inSentence ? text : text.charAt(0).toUpperCase() + text.slice(1);
}

export const isPeriodKey = (v: string) => /^\d{4}-(0[1-9]|1[0-2])$/.test(v);

/** "+312,40 €" / "−18,00 €" / "0,00 €": a drift always wears its sign. */
export function signedMoney(amount: string | number, format: (v: number) => string): string {
  const n = Number(amount);
  if (!Number.isFinite(n) || n === 0) return format(0);
  return `${n > 0 ? "+" : "−"}${format(Math.abs(n))}`;
}

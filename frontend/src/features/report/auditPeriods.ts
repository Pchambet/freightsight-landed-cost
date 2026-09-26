/**
 * The windows an audit is usually asked for, in the Paris calendar (the server's own zone is not
 * ours — see `daysUntil` in lib/format). The default is the last closed quarter: that is what the
 * paid offer audits, and a quarter still running would compare a finished month with half of one.
 */
export const AUDIT_PRESETS = ["lastQuarter", "thisQuarter", "sixMonths", "twelveMonths"] as const;
export type AuditPreset = (typeof AUDIT_PRESETS)[number];

const iso = (y: number, m: number, d: number) => `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
const lastDay = (y: number, m: number) => new Date(Date.UTC(y, m, 0)).getUTCDate();
/** `m` may run outside 1..12: normalised in one place. */
const norm = (y: number, m: number) => {
  const zero = y * 12 + (m - 1);
  return { y: Math.floor(zero / 12), m: (zero % 12) + 1 };
};

export function parisToday(): { y: number; m: number; d: number; iso: string } {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Paris", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date());
  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value);
  const [y, m, d] = [get("year"), get("month"), get("day")];
  return { y, m, d, iso: iso(y, m, d) };
}

export function presetRange(preset: AuditPreset): { from: string; to: string } {
  const today = parisToday();
  const quarterStart = norm(today.y, today.m - ((today.m - 1) % 3));
  switch (preset) {
    case "lastQuarter": {
      const start = norm(quarterStart.y, quarterStart.m - 3);
      const end = norm(quarterStart.y, quarterStart.m - 1);
      return { from: iso(start.y, start.m, 1), to: iso(end.y, end.m, lastDay(end.y, end.m)) };
    }
    case "thisQuarter":
      return { from: iso(quarterStart.y, quarterStart.m, 1), to: today.iso };
    case "sixMonths": {
      const start = norm(today.y, today.m - 5);
      return { from: iso(start.y, start.m, 1), to: today.iso };
    }
    case "twelveMonths": {
      const start = norm(today.y, today.m - 11);
      return { from: iso(start.y, start.m, 1), to: today.iso };
    }
  }
}

export const isDay = (v: string | undefined): v is string => !!v && /^\d{4}-\d{2}-\d{2}$/.test(v) && !Number.isNaN(Date.parse(v));

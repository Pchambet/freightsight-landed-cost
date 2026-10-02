import { useTranslations } from "next-intl";
import { confidenceTier } from "@/lib/format";

const STYLE: Record<"high" | "medium" | "low" | "confirmed", string> = {
  high: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  medium: "bg-amber-50 text-amber-700 ring-amber-600/20",
  low: "bg-red-50 text-red-700 ring-red-600/20",
  confirmed: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
};

/**
 * What a DAF should act on ("lecture automatique · à relire") instead of a bare decimal like
 * "0.60". The exact score stays available as a tooltip for whoever wants it.
 *
 * `band` is the backend's own `confidence_band` (HIGH/MEDIUM/LOW at 0.8/0.5, not yet in the
 * generated schema — needs_other_lane), preferred over recomputing the tier here so the two never
 * disagree on the threshold; the local computation stays as the fallback for a caller that has not
 * been updated to pass it, or a response from before the field existed.
 *
 * `confirmed` overrides the tier wording once the invoice is confirmed: a confirmed line is no
 * longer "to review" even if it was read with medium/low confidence — the badge says so
 * instead of keeping the reading-quality label. `flagOnly` drops the "Lecture automatique/incertaine"
 * prefix for a caller that already states how the document was read right next to the badge (the
 * invoice detail header): showing both repeated "Lecture automatique" back to back.
 */
export default function ConfidenceBadge({
  value,
  band,
  confirmed,
  flagOnly,
  className,
}: {
  value: string | number | null | undefined;
  band?: "HIGH" | "MEDIUM" | "LOW" | null;
  confirmed?: boolean;
  flagOnly?: boolean;
  className?: string;
}) {
  const t = useTranslations("confidence");
  const tier = band ? (band.toLowerCase() as "high" | "medium" | "low") : confidenceTier(value);
  if (tier === null) return <span className={`text-slate-500 ${className ?? ""}`}>—</span>;
  const n = typeof value === "number" ? value : Number(value);
  const pctLabel = Number.isFinite(n) ? `${Math.round(n * 100)} %` : "";
  const label = confirmed ? t("confirmedFlag") : flagOnly ? (tier === "high" ? null : t("reviewFlag")) : t(tier);
  if (label === null) return null;
  return (
    <span
      title={t("scoreTitle", { value: pctLabel })}
      className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset whitespace-nowrap ${STYLE[confirmed ? "confirmed" : tier]} ${className ?? ""}`}
    >
      {label}
    </span>
  );
}

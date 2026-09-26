import { useTranslations } from "next-intl";
import type { ContainerMilestone, DndRisk } from "@/lib/domain";

const MILESTONE_STYLE: Partial<Record<ContainerMilestone, string>> = {
  BOOKED: "bg-slate-100 text-slate-700 ring-slate-500/20",
  GATE_IN_FULL_ORIGIN: "bg-slate-100 text-slate-700 ring-slate-500/20",
  LOADED: "bg-blue-50 text-blue-700 ring-blue-600/20",
  VESSEL_DEPARTED: "bg-blue-50 text-blue-700 ring-blue-600/20",
  VESSEL_ARRIVED: "bg-indigo-50 text-indigo-700 ring-indigo-600/20",
  DISCHARGED: "bg-amber-50 text-amber-700 ring-amber-600/20",
  AVAILABLE_FOR_PICKUP: "bg-amber-50 text-amber-700 ring-amber-600/20",
  GATE_OUT_FULL: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  DELIVERED: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  GATE_IN_EMPTY_RETURN: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
};

const RISK_STYLE: Record<DndRisk, string> = {
  NONE: "bg-slate-100 text-slate-600 ring-slate-500/20",
  LOW: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  MEDIUM: "bg-amber-50 text-amber-700 ring-amber-600/20",
  HIGH: "bg-orange-50 text-orange-700 ring-orange-600/20",
  INCURRING: "bg-red-50 text-red-700 ring-red-600/20",
};

const base = "inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset whitespace-nowrap";

export function MilestoneBadge({ milestone }: { milestone: ContainerMilestone }) {
  const t = useTranslations("domain.milestone");
  return <span className={`${base} ${MILESTONE_STYLE[milestone] ?? MILESTONE_STYLE.BOOKED}`}>{t(milestone)}</span>;
}

export function RiskBadge({ risk, days }: { risk: DndRisk; days?: number | null }) {
  const t = useTranslations();
  if (risk === "NONE") return <span className="text-xs text-slate-500">—</span>;
  const suffix =
    days === null || days === undefined
      ? ""
      : days < 0
        ? ` · ${t("containers.risk.over", { days: -days })}`
        : ` · ${t("containers.risk.left", { days })}`;
  return (
    <span className={`${base} ${RISK_STYLE[risk]}`}>
      {t(`domain.risk.${risk}`)}
      {suffix}
    </span>
  );
}

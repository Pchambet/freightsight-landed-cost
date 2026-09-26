import { getLocale, getTranslations } from "next-intl/server";
import { Card } from "@/components/layout/AppShell";
import AddEventForm from "@/features/containers/AddEventForm";
import type { EtaHistoryResponse, TrackingEventResponse } from "@/lib/api/client";
import { dateTimeShort } from "@/lib/format";

/** Chronology of tracking events (estimates greyed) and the ETA change log, newest first. */
export default async function TimelinePanel({ containerId, events, etaHistory }: { containerId: string; events: TrackingEventResponse[]; etaHistory: EtaHistoryResponse[] }) {
  const [t, code, locale] = await Promise.all([getTranslations("timeline"), getTranslations("domain.milestone"), getLocale()]);
  const ordered = [...events].sort((a, b) => b.occurred_at.localeCompare(a.occurred_at));
  const eta = [...etaHistory].sort((a, b) => b.recorded_at.localeCompare(a.recorded_at));

  return (
    <Card title={t("title")} right={<span className="text-xs text-slate-500">{t("count", { count: events.length })}</span>}>
      <div className="p-5 space-y-5">
        {ordered.length === 0 ? (
          <p className="text-sm text-slate-500">{t("empty")}</p>
        ) : (
          <ol className="relative border-l border-slate-200 ml-1.5 space-y-4">
            {ordered.map((e) => (
              <li key={e.id} className="pl-4">
                <span className={`absolute -left-[5px] mt-1.5 w-2.5 h-2.5 rounded-full border-2 border-white ${e.is_estimate ? "bg-slate-300" : "bg-blue-600"}`} />
                <div className="text-sm">
                  <span className={`font-medium ${e.is_estimate ? "text-slate-500" : "text-slate-900"}`}>{code(e.code)}</span>
                  {e.is_estimate ? <span className="ml-2 text-xs text-slate-500 uppercase tracking-wider">{t("estimate")}</span> : null}
                </div>
                <div className="text-xs text-slate-500 tabular-nums">
                  {dateTimeShort(e.occurred_at, locale)}
                  {e.location_name || e.location_unlocode ? ` · ${e.location_name ?? e.location_unlocode}` : ""}
                  {e.vessel_name ? ` · ${e.vessel_name}${e.voyage ? ` ${e.voyage}` : ""}` : ""}
                  {` · ${t("by", { source: e.source })}`}
                </div>
                {e.code === "UNKNOWN" && e.raw_description ? <div className="text-xs text-slate-500 italic">{e.raw_description}</div> : null}
              </li>
            ))}
          </ol>
        )}
        <AddEventForm containerId={containerId} />
        <div className="border-t border-slate-100 pt-4">
          <div className="text-xs font-medium uppercase tracking-wider text-slate-500 mb-2">{t("etaTitle")}</div>
          {eta.length === 0 ? (
            <p className="text-xs text-slate-500">{t("etaEmpty")}</p>
          ) : (
            <ul className="text-xs space-y-1.5">
              {eta.map((h) => (
                <li key={h.id} className="flex items-center gap-2">
                  <span className={`w-1.5 h-1.5 rounded-full ${h.severity === "warning" ? "bg-amber-500" : "bg-slate-300"}`} />
                  <span className="text-slate-700 tabular-nums">
                    {h.previous_eta ? t("etaChange", { from: dateTimeShort(h.previous_eta, locale), to: dateTimeShort(h.eta, locale) }) : t("etaFirst", { to: dateTimeShort(h.eta, locale) })}
                  </span>
                  {h.previous_eta ? <span className={`tabular-nums ${h.severity === "warning" ? "text-amber-700" : "text-slate-500"}`}>{Number(h.delta_hours) > 0 ? "+" : ""}{t("etaDelta", { hours: Number(h.delta_hours).toFixed(0) })}</span> : null}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Card>
  );
}

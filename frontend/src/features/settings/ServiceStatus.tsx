import { getLocale, getTranslations } from "next-intl/server";
import { AlertTriangle, CheckCircle2, CircleDashed } from "lucide-react";
import type { Schemas } from "@/lib/api/client";
import { dateTimeShort } from "@/lib/format";

type Check = Schemas["ServiceCheck"];

/**
 * What this deployment actually does, said to the customer in its own words: whether alert e-mails
 * leave, whether containers are tracked automatically, whether anything but rules reads an invoice,
 * whether the background jobs and the backups are alive. Three states and three tones — "ok" works
 * as described, "off" is not switched on here (grey: nothing is broken, it is turned on when a pilot
 * opens), "degraded" is switched on and failing (amber). The API never sends a variable name or a
 * key, and this screen never invents one: an unknown code falls back to a neutral sentence.
 */
export default async function ServiceStatus({ checks, checkedAt }: { checks: Check[]; checkedAt: string }) {
  const [t, locale] = await Promise.all([getTranslations("settings.service"), getLocale()]);
  const tone = {
    ok: { Icon: CheckCircle2, icon: "text-emerald-600", chip: "bg-emerald-50 text-emerald-800 ring-emerald-600/20" },
    off: { Icon: CircleDashed, icon: "text-slate-400", chip: "bg-slate-100 text-slate-700 ring-slate-500/20" },
    degraded: { Icon: AlertTriangle, icon: "text-amber-600", chip: "bg-amber-50 text-amber-800 ring-amber-600/20" },
  } as const;

  return (
    <div>
      <ul className="divide-y divide-slate-100">
        {checks.map((c) => {
          const { Icon, icon, chip } = tone[c.state as keyof typeof tone] ?? tone.degraded;
          const params = c.params ?? {};
          const when = params.seen_at || params.checked_at;
          const sentence = t.has(`code.${c.code}`)
            ? t(`code.${c.code}`, { providers: (params.providers ?? "").split(",").filter(Boolean).join(", ") || "—", when: when ? dateTimeShort(when, locale) : t("never") })
            : t("unknown");
          return (
            <li key={c.key} className="flex items-start gap-3 px-5 py-3.5">
              <Icon className={`w-4 h-4 mt-0.5 shrink-0 ${icon}`} aria-hidden />
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-slate-900">{t.has(`check.${c.key}`) ? t(`check.${c.key}`) : c.key}</div>
                <p className="mt-0.5 text-sm text-slate-600">{sentence}</p>
              </div>
              <span className={`shrink-0 rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${chip}`}>{t(`state.${c.state in tone ? c.state : "degraded"}`)}</span>
            </li>
          );
        })}
      </ul>
      <p className="px-5 py-3 border-t border-slate-100 text-xs text-slate-500">{t("checkedAt", { when: dateTimeShort(checkedAt, locale) })}</p>
    </div>
  );
}
